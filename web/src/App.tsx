import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Check,
  FileText,
  History,
  Loader2,
  MessageSquare,
  Play,
  RefreshCw,
  RotateCcw,
  Send,
} from 'lucide-react';
import {
  approveArtifact,
  abandonChapter,
  chatUpdateArtifact,
  loadDefaults,
  loadArtifact,
  loadArtifactDiff,
  loadSnapshot,
  recordMessage,
  retryNode,
  resumeRun,
  rewriteChapter,
  startRun,
} from './api';
import type { ArtifactPayload, RunForm, RuntimeArtifact, RuntimeSnapshot } from './types';
import './styles.css';

const STORAGE_KEY = 'inklink.web.form.v1';

const defaultForm: RunForm = {
  input_dir: 'novel',
  config_path: 'config.toml',
  log_root: 'logs',
  runtime_id: '',
  output_mode: 'output',
  continuation_mode: 'fixed',
  chapter_count: 1,
  ending_min_chapters: 3,
  ending_max_chapters: 8,
  start_chapter: '',
  min_chars: 800,
  max_chars: 1800,
  max_revision_rounds: '',
  auto_approve: false,
  notes: '',
  notes_file: '',
};

export default function App() {
  const [form, setForm] = useState<RunForm>(() => loadStoredForm());
  const [activeRuntimeId, setActiveRuntimeId] = useState('');
  const [snapshot, setSnapshot] = useState<RuntimeSnapshot | null>(null);
  const [artifact, setArtifact] = useState<ArtifactPayload | null>(null);
  const [status, setStatus] = useState('未连接');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [chapterAction, setChapterAction] = useState('');
  const [diffLeft, setDiffLeft] = useState('');
  const [diffRight, setDiffRight] = useState('');
  const [diffText, setDiffText] = useState('');

  const runtimeId = activeRuntimeId;
  const waiting = snapshot?.waiting_approval ?? null;
  const waitingArtifactId = waiting?.artifact_id ?? '';
  const waitingArtifactVersion = waiting?.artifact_version ?? null;
  const waitingArtifactType = waiting?.approval_type ?? '';
  const currentNodeId = stringValue(snapshot?.current_node?.node_id);
  const currentNodeStatus = stringValue(snapshot?.current_node?.status);
  const inferredChapter = inferChapterNumber(currentNodeId || waiting?.approval_id || '');
  const chapterActionValue = chapterAction || (inferredChapter ? String(inferredChapter) : '');
  const formErrors = useMemo(() => validateForm(form), [form]);

  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ ...form, runtime_id: '' }));
  }, [form]);

  useEffect(() => {
    loadDefaults()
      .then((defaults) => {
        setForm((current) => ({
          ...current,
          input_dir:
            current.input_dir === defaultForm.input_dir
              ? defaults.input_dir || current.input_dir
              : current.input_dir,
          config_path:
            current.config_path === defaultForm.config_path
              ? defaults.config_path || current.config_path
              : current.config_path,
          log_root:
            current.log_root === defaultForm.log_root
              ? defaults.log_root || current.log_root
              : current.log_root,
        }));
      })
      .catch(() => {
        // Defaults are a convenience; the form remains usable without them.
      });
  }, []);

  const refresh = useCallback(async () => {
    if (!runtimeId) return;
    const next = await loadSnapshot(runtimeId, form.log_root);
    setSnapshot(next);
  }, [form.log_root, runtimeId]);

  useEffect(() => {
    if (!runtimeId) return;
    let disposed = false;
    const interval = window.setInterval(() => {
      refresh().catch((error: unknown) => {
        if (!disposed) setStatus(errorMessage(error));
      });
    }, 1500);
    refresh().catch((error: unknown) => setStatus(errorMessage(error)));
    return () => {
      disposed = true;
      window.clearInterval(interval);
    };
  }, [refresh, runtimeId]);

  useEffect(() => {
    if (!runtimeId) return;
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const socket = new WebSocket(`${protocol}//${window.location.host}/api/runs/${runtimeId}/ws`);
    socket.onmessage = () => {
      refresh().catch((error: unknown) => setStatus(errorMessage(error)));
    };
    socket.onerror = () => setStatus('实时连接不可用，已使用轮询刷新。');
    return () => socket.close();
  }, [refresh, runtimeId]);

  useEffect(() => {
    if (!runtimeId || !waitingArtifactId) return;
    loadArtifact(runtimeId, form.log_root, waitingArtifactId, waitingArtifactVersion)
      .then((next) => {
        setArtifact(next);
        setDiffText('');
        setDiffLeft(next.version > 1 ? String(next.version - 1) : '');
        setDiffRight(String(next.version));
      })
      .catch((error: unknown) => setStatus(errorMessage(error)));
  }, [form.log_root, runtimeId, waitingArtifactId, waitingArtifactVersion]);

  const latestArtifacts = useMemo(() => latestArtifactVersions(snapshot?.artifacts ?? []), [snapshot]);

  async function runStart() {
    await execute('已提交新运行', async () => {
      const next = await startRun(form);
      setSnapshot(next);
      setArtifact(null);
      setDiffText('');
      if (next.runtime_id) {
        setActiveRuntimeId(next.runtime_id);
        setForm((current) => ({ ...current, runtime_id: next.runtime_id ?? '' }));
      }
    });
  }

  async function runResume() {
    await execute('已恢复运行', async () => {
      const next = await resumeRun(form);
      setSnapshot(next);
      setArtifact(null);
      setDiffText('');
      if (next.runtime_id) setActiveRuntimeId(next.runtime_id);
    });
  }

  function resetWorkspace() {
    setActiveRuntimeId('');
    setSnapshot(null);
    setArtifact(null);
    setMessage('');
    setChapterAction('');
    setDiffLeft('');
    setDiffRight('');
    setDiffText('');
    setStatus('已清空工作台，填写或粘贴运行 ID 后可重新续接。');
    setForm((current) => ({ ...current, runtime_id: '' }));
  }

  async function approveCurrent() {
    if (!runtimeId || !waiting || !waitingArtifactId || !waitingArtifactVersion) return;
    await execute('已批准产物', async () => {
      await approveArtifact(
        runtimeId,
        form.log_root,
        waiting.approval_id,
        waitingArtifactId,
        waitingArtifactVersion,
      );
      await refresh();
    });
  }

  async function recordCurrentMessage() {
    if (!runtimeId || !waiting || !message.trim()) return;
    await execute('已记录审批消息', async () => {
      await recordMessage(runtimeId, form.log_root, waiting.approval_id, message.trim());
      setMessage('');
      await refresh();
    });
  }

  async function updateCurrentArtifact() {
    if (!runtimeId || !waiting || !waitingArtifactId || !waitingArtifactType || !message.trim()) return;
    await execute('AI 已更新产物', async () => {
      const result = await chatUpdateArtifact(
        runtimeId,
        form.log_root,
        form.config_path,
        waiting.approval_id,
        waitingArtifactId,
        waitingArtifactType,
        message.trim(),
      );
      setMessage('');
      const nextArtifact = await loadArtifact(runtimeId, form.log_root, result.artifact_id, result.version);
      setArtifact(nextArtifact);
      await refresh();
    });
  }

  async function retryCurrentNode() {
    if (!runtimeId || !currentNodeId) return;
    await execute('已提交重试请求', async () => {
      const result = await retryNode(runtimeId, form.log_root, currentNodeId);
      setStatus(result.message);
      await refresh();
    });
  }

  async function abandonSelectedChapter() {
    const chapterNumber = Number(chapterActionValue);
    if (!runtimeId || !Number.isInteger(chapterNumber) || chapterNumber <= 0) return;
    await execute('已放弃该章节世代', async () => {
      const result = await abandonChapter(runtimeId, form.log_root, chapterNumber);
      setStatus(result.message);
      await refresh();
    });
  }

  async function rewriteSelectedChapter() {
    const chapterNumber = Number(chapterActionValue);
    if (!runtimeId || !Number.isInteger(chapterNumber) || chapterNumber <= 0) return;
    await execute('已提交重写请求', async () => {
      const result = await rewriteChapter(runtimeId, form.log_root, chapterNumber);
      setStatus(result.message);
      await refresh();
    });
  }

  async function openArtifact(item: RuntimeArtifact) {
    if (!runtimeId) return;
    await execute(`已打开 ${item.artifact_id}@${item.version}`, async () => {
      const next = await loadArtifact(runtimeId, form.log_root, item.artifact_id, item.version);
      setArtifact(next);
      setDiffText('');
      setDiffLeft(next.version > 1 ? String(next.version - 1) : '');
      setDiffRight(String(next.version));
    });
  }

  async function showArtifactDiff() {
    if (!runtimeId || !artifact || !isPositiveInteger(diffLeft) || !isPositiveInteger(diffRight)) {
      return;
    }
    await execute('已加载版本差异', async () => {
      const result = await loadArtifactDiff(
        runtimeId,
        form.log_root,
        artifact.artifact_id,
        Number(diffLeft),
        Number(diffRight),
      );
      setDiffText(result.diff || '两个版本没有差异。');
    });
  }

  async function execute(successMessage: string, action: () => Promise<void>) {
    setBusy(true);
    setStatus('处理中...');
    try {
      await action();
      setStatus(successMessage);
    } catch (error: unknown) {
      setStatus(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="app-shell">
      <aside className="setup-pane">
        <div className="brand-row">
          <div>
            <h1>墨连</h1>
            <p>长篇续写工作台</p>
          </div>
          {busy ? <Loader2 className="spin" size={22} /> : <FileText size={22} />}
        </div>

        <label>
          输入目录
          <input value={form.input_dir} onChange={(event) => update('input_dir', event.target.value)} />
        </label>
        <label>
          配置文件
          <input value={form.config_path} onChange={(event) => update('config_path', event.target.value)} />
        </label>
        <label>
          日志目录
          <input value={form.log_root} onChange={(event) => update('log_root', event.target.value)} />
        </label>
        <label>
          运行 ID
          <input
            value={form.runtime_id}
            onChange={(event) => updateRuntimeId(event.target.value)}
            placeholder="留空创建新运行；填写后点击续接"
          />
        </label>

        <div className="segmented">
          <button
            className={form.continuation_mode === 'fixed' ? 'active' : ''}
            onClick={() => update('continuation_mode', 'fixed')}
            type="button"
          >
            固定章数
          </button>
          <button
            className={form.continuation_mode === 'to_ending' ? 'active' : ''}
            onClick={() => update('continuation_mode', 'to_ending')}
            type="button"
          >
            续写到结局
          </button>
        </div>

        {form.continuation_mode === 'fixed' ? (
          <label>
            续写章数
            <input
              type="number"
              min={1}
              value={form.chapter_count}
              onChange={(event) => update('chapter_count', Number(event.target.value))}
            />
          </label>
        ) : (
          <div className="split-fields">
            <label>
              最少章节
              <input
                type="number"
                min={1}
                value={form.ending_min_chapters}
                onChange={(event) => update('ending_min_chapters', Number(event.target.value))}
              />
            </label>
            <label>
              最多章节
              <input
                type="number"
                min={1}
                value={form.ending_max_chapters}
                onChange={(event) => update('ending_max_chapters', Number(event.target.value))}
              />
            </label>
          </div>
        )}

        <div className="split-fields">
          <label>
            最少字数
            <input
              type="number"
              min={0}
              value={form.min_chars}
              onChange={(event) => update('min_chars', Number(event.target.value))}
            />
          </label>
          <label>
            最多字数
            <input
              type="number"
              min={0}
              value={form.max_chars}
              onChange={(event) => update('max_chars', Number(event.target.value))}
            />
          </label>
        </div>

        <div className="split-fields">
          <label>
            起始章节
            <input value={form.start_chapter} onChange={(event) => update('start_chapter', event.target.value)} />
          </label>
          <label>
            修订轮数
            <input
              value={form.max_revision_rounds}
              onChange={(event) => update('max_revision_rounds', event.target.value)}
            />
          </label>
        </div>

        <label>
          输出模式
          <select
            value={form.output_mode}
            onChange={(event) => update('output_mode', event.target.value as RunForm['output_mode'])}
          >
            <option value="output">写入运行目录</option>
            <option value="writeback">写回章节目录</option>
          </select>
        </label>

        <label className="checkbox-line">
          <input
            type="checkbox"
            checked={form.auto_approve}
            onChange={(event) => update('auto_approve', event.target.checked)}
          />
          自动批准后续审批点
        </label>

        <label>
          本次 notes
          <textarea value={form.notes} onChange={(event) => update('notes', event.target.value)} />
        </label>
        <label>
          notes 文件
          <input value={form.notes_file} onChange={(event) => update('notes_file', event.target.value)} />
        </label>

        <div className="button-row">
          <button type="button" onClick={runStart} disabled={busy || formErrors.length > 0}>
            <Play size={16} /> 开始
          </button>
          <button type="button" onClick={runResume} disabled={busy || !form.runtime_id.trim()}>
            <RefreshCw size={16} /> 续接
          </button>
        </div>
        <button type="button" className="secondary-action" onClick={resetWorkspace} disabled={busy}>
          清空工作台
        </button>
        {formErrors.length > 0 ? (
          <ul className="form-errors">
            {formErrors.map((error) => (
              <li key={error}>{error}</li>
            ))}
          </ul>
        ) : null}
        <p className="status-line">{status}</p>
      </aside>

      <section className="workspace">
        <header className="workspace-header">
          <div>
            <p className="eyebrow">{snapshot?.status ?? '未开始'}</p>
            <h2>{snapshot?.latest_message ?? '等待启动运行'}</h2>
          </div>
          <div className="runtime-chip">{runtimeId || '未连接运行'}</div>
        </header>

        <section className="run-summary">
          <div>
            <span>阶段</span>
            <strong>{snapshot?.current_phase_label ?? '未开始'}</strong>
          </div>
          <div>
            <span>后台</span>
            <strong>{snapshot?.pipeline_running ? '运行中' : '未运行'}</strong>
          </div>
          <div>
            <span>当前节点</span>
            <strong>{currentNodeId || '无'}</strong>
          </div>
          <div>
            <span>节点状态</span>
            <strong>{currentNodeStatus || snapshot?.status || '未开始'}</strong>
          </div>
        </section>

        <section className="phase-strip">
          {(snapshot?.phase_statuses ?? []).map((phase) => (
            <div className={`phase ${phase.current ? 'current' : ''}`} key={phase.key}>
              <span>{phase.label}</span>
              <strong>{phase.completed}/{phase.total || 0}</strong>
            </div>
          ))}
        </section>

        {snapshot?.stale_hint ? <p className="warning-line">{snapshot.stale_hint}</p> : null}
        {snapshot?.state_error ? <p className="warning-line">{snapshot.state_error}</p> : null}

        <section className="data-grid">
          <div className="log-panel">
            <div className="panel-title">
              <History size={17} /> 最近事件
            </div>
            <div className="event-list">
              {(snapshot?.events ?? []).slice(-18).map((event, index) => (
                <div className="event-line" key={`${event.timestamp ?? index}-${index}`}>
                  <time>{event.timestamp ?? '--'}</time>
                  <span>{event.event_type ?? 'event'}</span>
                  <code>{compactJson(event.payload)}</code>
                </div>
              ))}
            </div>
          </div>

          <div className="side-stack">
            <div className="artifact-list">
              <div className="panel-title">
                <FileText size={17} /> 产物
              </div>
              {latestArtifacts.slice(-18).map((item) => (
                <button
                  type="button"
                  className="artifact-row"
                  key={`${item.artifact_id}@${item.version}`}
                  onClick={() => openArtifact(item)}
                >
                  <span>{item.artifact_id}</span>
                  <strong>@{item.version}</strong>
                  <em>{item.is_approved ? 'approved' : item.is_draft ? 'draft' : 'saved'}</em>
                </button>
              ))}
            </div>
            <div className="usage-list">
              <div className="panel-title">
                <History size={17} /> 用量
              </div>
              {(snapshot?.usage ?? []).slice(0, 8).map((row, index) => (
                <div className="usage-row" key={`${row.profile ?? index}-${row.task_type ?? index}`}>
                  <span>{formatUsageLabel(row)}</span>
                  <strong>{formatUsageTokens(row)}</strong>
                </div>
              ))}
              {(snapshot?.usage ?? []).length === 0 ? <p className="muted">暂无用量记录。</p> : null}
            </div>
          </div>
        </section>
      </section>

      <aside className="inspector">
        <section className="approval-pane">
          <div className="panel-title">
            <MessageSquare size={17} /> 当前审批
          </div>
          {waiting ? (
            <>
              <dl className="kv">
                <div><dt>审批</dt><dd>{waiting.approval_id}</dd></div>
                <div><dt>类型</dt><dd>{waiting.approval_type}</dd></div>
                <div><dt>产物</dt><dd>{waitingArtifactId}@{waitingArtifactVersion ?? '?'}</dd></div>
              </dl>
              <textarea
                className="approval-message"
                placeholder="输入修改意见，或记录你对当前产物的判断"
                value={message}
                onChange={(event) => setMessage(event.target.value)}
              />
              <div className="button-column">
                <button type="button" onClick={recordCurrentMessage} disabled={busy || !message.trim()}>
                  <Send size={16} /> 记录消息
                </button>
                <button type="button" onClick={updateCurrentArtifact} disabled={busy || !message.trim()}>
                  <RefreshCw size={16} /> AI 修改产物
                </button>
                <button type="button" className="primary" onClick={approveCurrent} disabled={busy || !waitingArtifactId}>
                  <Check size={16} /> 批准当前版本
                </button>
              </div>
            </>
          ) : (
            <p className="muted">当前没有等待审批项。</p>
          )}
        </section>

        <section className="control-pane">
          <div className="panel-title">
            <RotateCcw size={17} /> 运行控制
          </div>
          <dl className="kv">
            <div><dt>节点</dt><dd>{currentNodeId || '无'}</dd></div>
            <div><dt>状态</dt><dd>{currentNodeStatus || '无'}</dd></div>
          </dl>
          <button
            type="button"
            className="wide-button"
            onClick={retryCurrentNode}
            disabled={busy || !runtimeId || !currentNodeId}
          >
            <RefreshCw size={16} /> 重试当前节点
          </button>
          <label>
            章节号
            <input
              value={chapterActionValue}
              onChange={(event) => setChapterAction(event.target.value)}
              placeholder="用于放弃或重写章节"
            />
          </label>
          <div className="button-row">
            <button
              type="button"
              onClick={abandonSelectedChapter}
              disabled={busy || !runtimeId || !isPositiveInteger(chapterActionValue)}
            >
              放弃
            </button>
            <button
              type="button"
              onClick={rewriteSelectedChapter}
              disabled={busy || !runtimeId || !isPositiveInteger(chapterActionValue)}
            >
              重写
            </button>
          </div>
        </section>

        <section className="artifact-viewer">
          <div className="panel-title">
            <FileText size={17} /> 内容预览
          </div>
          {artifact ? (
            <>
              <p className="artifact-heading">
                {artifact.artifact_id}@{artifact.version} · {artifact.artifact_type}
              </p>
              <div className="diff-controls">
                <input
                  aria-label="左侧版本"
                  value={diffLeft}
                  onChange={(event) => setDiffLeft(event.target.value)}
                  placeholder="左版本"
                />
                <input
                  aria-label="右侧版本"
                  value={diffRight}
                  onChange={(event) => setDiffRight(event.target.value)}
                  placeholder="右版本"
                />
                <button
                  type="button"
                  onClick={showArtifactDiff}
                  disabled={
                    busy ||
                    !isPositiveInteger(diffLeft) ||
                    !isPositiveInteger(diffRight)
                  }
                >
                  Diff
                </button>
              </div>
              {diffText ? <pre className="diff-view">{diffText}</pre> : null}
              <pre>{formatArtifact(artifact.payload)}</pre>
            </>
          ) : (
            <p className="muted">选择产物或进入审批后自动显示内容。</p>
          )}
        </section>
      </aside>
    </main>
  );

  function update<K extends keyof RunForm>(key: K, value: RunForm[K]) {
    setForm((current) => ({ ...current, [key]: value }));
  }

  function updateRuntimeId(value: string) {
    setForm((current) => ({ ...current, runtime_id: value }));
    if (activeRuntimeId && value.trim() !== activeRuntimeId) {
      setActiveRuntimeId('');
      setSnapshot(null);
      setArtifact(null);
      setDiffText('');
      setStatus('运行 ID 已修改，点击续接后加载该运行。');
    }
  }
}

function loadStoredForm(): RunForm {
  const stored = localStorage.getItem(STORAGE_KEY);
  if (!stored) return defaultForm;
  try {
    return { ...defaultForm, ...(JSON.parse(stored) as Partial<RunForm>), runtime_id: '' };
  } catch {
    return defaultForm;
  }
}

function latestArtifactVersions(items: RuntimeArtifact[]): RuntimeArtifact[] {
  const latest = new Map<string, RuntimeArtifact>();
  for (const item of items) {
    const current = latest.get(item.artifact_id);
    if (!current || item.version > current.version) latest.set(item.artifact_id, item);
  }
  return [...latest.values()].sort((left, right) => left.artifact_id.localeCompare(right.artifact_id));
}

function validateForm(form: RunForm): string[] {
  const errors: string[] = [];
  if (!form.input_dir.trim()) errors.push('输入目录不能为空。');
  if (!form.config_path.trim()) errors.push('配置文件不能为空。');
  if (!form.log_root.trim()) errors.push('日志目录不能为空。');
  if (!Number.isInteger(form.min_chars) || form.min_chars < 0) errors.push('最少字数必须是非负整数。');
  if (!Number.isInteger(form.max_chars) || form.max_chars < 0) errors.push('最多字数必须是非负整数。');
  if (form.min_chars > form.max_chars) errors.push('最少字数不能大于最多字数。');
  if (form.continuation_mode === 'fixed') {
    if (!Number.isInteger(form.chapter_count) || form.chapter_count <= 0) {
      errors.push('续写章数必须是正整数。');
    }
  } else {
    if (!Number.isInteger(form.ending_min_chapters) || form.ending_min_chapters <= 0) {
      errors.push('最少章节必须是正整数。');
    }
    if (!Number.isInteger(form.ending_max_chapters) || form.ending_max_chapters <= 0) {
      errors.push('最多章节必须是正整数。');
    }
    if (form.ending_min_chapters > form.ending_max_chapters) {
      errors.push('续写到结局时，最少章节不能大于最多章节。');
    }
  }
  if (form.start_chapter && !isPositiveInteger(form.start_chapter)) {
    errors.push('起始章节必须是正整数，或留空。');
  }
  if (form.max_revision_rounds && !isNonNegativeInteger(form.max_revision_rounds)) {
    errors.push('修订轮数必须是非负整数，或留空。');
  }
  return errors;
}

function stringValue(value: unknown): string {
  return typeof value === 'string' ? value : '';
}

function inferChapterNumber(value: string): number | null {
  const match = value.match(
    /(?:chapter|章节|analyze_chapter|plan_scenes|scene_plan|draft_scene|check_chapter|review_chapter|review_failure|write_output)[:_-]?(\d+)/i,
  );
  if (!match) return null;
  const parsed = Number(match[1]);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : null;
}

function isPositiveInteger(value: string): boolean {
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed > 0;
}

function isNonNegativeInteger(value: string): boolean {
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed >= 0;
}

function formatUsageLabel(row: Record<string, unknown>): string {
  const profile = stringValue(row.profile) || 'profile';
  const task = stringValue(row.task_type) || 'task';
  return `${profile}/${task}`;
}

function formatUsageTokens(row: Record<string, unknown>): string {
  const calls = numericValue(row.calls);
  const total = numericValue(row.total_tokens);
  if (calls === null && total === null) return '--';
  return `${calls ?? 0} calls · ${total ?? 0} tokens`;
}

function numericValue(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function compactJson(value: unknown): string {
  if (value === undefined || value === null) return '';
  return JSON.stringify(value).slice(0, 160);
}

function formatArtifact(value: unknown): string {
  if (Array.isArray(value)) {
    return value.map((item, index) => formatObjectBlock(item, index + 1)).join('\n\n');
  }
  if (typeof value === 'object' && value !== null) {
    const record = value as Record<string, unknown>;
    if (typeof record.outline === 'string') return record.outline;
    return formatObjectBlock(record, 0);
  }
  return String(value ?? '');
}

function formatObjectBlock(value: unknown, index: number): string {
  if (typeof value !== 'object' || value === null) return String(value ?? '');
  const record = value as Record<string, unknown>;
  const title = typeof record.title === 'string' ? record.title : index ? `#${index}` : '内容';
  const lines = [title];
  for (const key of ['summary', 'core_conflict', 'emotional_peak', 'ending_hook', 'goal']) {
    if (typeof record[key] === 'string' && record[key]) lines.push(`${labelFor(key)}：${record[key]}`);
  }
  if (Array.isArray(record.scenes)) {
    lines.push('场景：');
    for (const scene of record.scenes) lines.push(`- ${formatObjectBlock(scene, 0).replace(/\n/g, '；')}`);
  }
  if (lines.length > 1) return lines.join('\n');
  return JSON.stringify(value, null, 2);
}

function labelFor(key: string): string {
  return {
    summary: '摘要',
    core_conflict: '核心冲突',
    emotional_peak: '情绪峰值',
    ending_hook: '收束/钩子',
    goal: '目标',
  }[key] ?? key;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
