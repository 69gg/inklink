import type { ArtifactDiff, ArtifactPayload, RunForm, RuntimeSnapshot } from './types';

export interface CommandResult {
  accepted: boolean;
  message: string;
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
    ...init,
  });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const payload = (await response.json()) as { detail?: string };
      detail = payload.detail ?? detail;
    } catch {
      // Keep the HTTP status text when the response is not JSON.
    }
    throw new Error(detail);
  }
  return (await response.json()) as T;
}

export function startRun(form: RunForm): Promise<RuntimeSnapshot> {
  return requestJson<RuntimeSnapshot>('/api/runs/start', {
    method: 'POST',
    body: JSON.stringify(formToPayload(form)),
  });
}

export function loadDefaults(): Promise<Partial<RunForm>> {
  return requestJson<Partial<RunForm>>('/api/defaults');
}

export function resumeRun(form: RunForm): Promise<RuntimeSnapshot> {
  if (!form.runtime_id.trim()) {
    throw new Error('运行 ID 不能为空');
  }
  return requestJson<RuntimeSnapshot>(`/api/runs/${encodeURIComponent(form.runtime_id)}/resume`, {
    method: 'POST',
    body: JSON.stringify({
      config_path: form.config_path,
      log_root: form.log_root,
      auto_approve: form.auto_approve,
    }),
  });
}

export function loadSnapshot(runtimeId: string, logRoot: string): Promise<RuntimeSnapshot> {
  const query = new URLSearchParams({ log_root_query: logRoot });
  return requestJson<RuntimeSnapshot>(`/api/runs/${encodeURIComponent(runtimeId)}/snapshot?${query}`);
}

export function loadArtifact(
  runtimeId: string,
  logRoot: string,
  artifactId: string,
  version?: number | null,
): Promise<ArtifactPayload> {
  const query = new URLSearchParams({ log_root_query: logRoot });
  if (version) query.set('version', String(version));
  return requestJson<ArtifactPayload>(
    `/api/runs/${encodeURIComponent(runtimeId)}/artifacts/${encodeURIComponent(artifactId)}?${query}`,
  );
}

export function loadArtifactDiff(
  runtimeId: string,
  logRoot: string,
  artifactId: string,
  leftVersion: number,
  rightVersion: number,
): Promise<ArtifactDiff> {
  const query = new URLSearchParams({
    log_root_query: logRoot,
    left_version: String(leftVersion),
    right_version: String(rightVersion),
  });
  return requestJson<ArtifactDiff>(
    `/api/runs/${encodeURIComponent(runtimeId)}/artifacts/${encodeURIComponent(artifactId)}/diff?${query}`,
  );
}

export function recordMessage(
  runtimeId: string,
  logRoot: string,
  approvalId: string,
  content: string,
): Promise<CommandResult> {
  const query = new URLSearchParams({ log_root_query: logRoot });
  return requestJson<CommandResult>(
    `/api/runs/${encodeURIComponent(runtimeId)}/approvals/message?${query}`,
    {
      method: 'POST',
      body: JSON.stringify({ approval_id: approvalId, content, role: 'user' }),
    },
  );
}

export function chatUpdateArtifact(
  runtimeId: string,
  logRoot: string,
  configPath: string,
  approvalId: string,
  artifactId: string,
  artifactType: string,
  content: string,
): Promise<{ artifact_id: string; version: number }> {
  const query = new URLSearchParams({ log_root_query: logRoot });
  return requestJson<{ artifact_id: string; version: number }>(
    `/api/runs/${encodeURIComponent(runtimeId)}/approvals/chat-update?${query}`,
    {
      method: 'POST',
      body: JSON.stringify({
        approval_id: approvalId,
        artifact_id: artifactId,
        artifact_type: artifactType,
        content,
        config_path: configPath,
      }),
    },
  );
}

export function approveArtifact(
  runtimeId: string,
  logRoot: string,
  approvalId: string,
  artifactId: string,
  artifactVersion: number,
): Promise<CommandResult> {
  const query = new URLSearchParams({ log_root_query: logRoot });
  return requestJson<CommandResult>(
    `/api/runs/${encodeURIComponent(runtimeId)}/approvals/approve?${query}`,
    {
      method: 'POST',
      body: JSON.stringify({
        approval_id: approvalId,
        artifact_id: artifactId,
        artifact_version: artifactVersion,
        approval_type: approvalId.includes(':') ? approvalId.split(':')[0] : approvalId,
      }),
    },
  );
}

export function retryNode(runtimeId: string, logRoot: string, nodeId: string): Promise<CommandResult> {
  const query = new URLSearchParams({ log_root_query: logRoot });
  return requestJson<CommandResult>(
    `/api/runs/${encodeURIComponent(runtimeId)}/nodes/retry?${query}`,
    {
      method: 'POST',
      body: JSON.stringify({ node_id: nodeId }),
    },
  );
}

export function abandonChapter(
  runtimeId: string,
  logRoot: string,
  chapterNumber: number,
): Promise<CommandResult> {
  const query = new URLSearchParams({ log_root_query: logRoot });
  return requestJson<CommandResult>(
    `/api/runs/${encodeURIComponent(runtimeId)}/chapters/${chapterNumber}/abandon?${query}`,
    { method: 'POST' },
  );
}

export function rewriteChapter(
  runtimeId: string,
  logRoot: string,
  chapterNumber: number,
): Promise<CommandResult> {
  const query = new URLSearchParams({ log_root_query: logRoot });
  return requestJson<CommandResult>(
    `/api/runs/${encodeURIComponent(runtimeId)}/chapters/${chapterNumber}/rewrite?${query}`,
    { method: 'POST' },
  );
}

function formToPayload(form: RunForm): Record<string, unknown> {
  return {
    input_dir: form.input_dir || null,
    config_path: form.config_path,
    log_root: form.log_root,
    output_mode: form.output_mode,
    continuation_mode: form.continuation_mode,
    chapter_count: form.chapter_count,
    ending_min_chapters:
      form.continuation_mode === 'to_ending' ? form.ending_min_chapters : null,
    ending_max_chapters:
      form.continuation_mode === 'to_ending' ? form.ending_max_chapters : null,
    start_chapter: form.start_chapter ? Number(form.start_chapter) : null,
    min_chars: form.min_chars,
    max_chars: form.max_chars,
    max_revision_rounds: form.max_revision_rounds ? Number(form.max_revision_rounds) : null,
    auto_approve: form.auto_approve,
    notes: form.notes,
    notes_file: form.notes_file || null,
  };
}
