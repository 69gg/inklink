# 开发指南

Inklink 使用 Python 3.12+、`uv`、Typer、Textual、Pydantic v2、OpenAI Python SDK、SQLite、pytest、ruff 和 mypy。

## 环境

```bash
uv sync
uv run inklink version
```

启动当前 TUI shell：

```bash
uv run inklink run ./novel --config config.toml
```

直接执行端到端 pipeline：

```bash
uv run inklink run ./novel --config config.toml --execute \
  --chapter-count 1 --min-chars 800 --max-chars 1800 --auto-approve
```

恢复已有 runtime：

```bash
uv run inklink run --execute --resume-runtime-id <runtime_id>
```

操作已有 runtime：

```bash
uv run inklink workflow info <runtime_id>
uv run inklink workflow stats <runtime_id>
uv run inklink workflow nodes <runtime_id>
uv run inklink workflow artifacts <runtime_id>
uv run inklink workflow artifact <runtime_id> outline --version 1
uv run inklink workflow approvals <runtime_id>
uv run inklink workflow messages <runtime_id> --approval-id outline
uv run inklink workflow events <runtime_id>
uv run inklink workflow message <runtime_id> outline "请强化冲突"
uv run inklink workflow chat-update <runtime_id> outline outline outline "请强化冲突"
uv run inklink workflow approve <runtime_id> outline outline 1
uv run inklink workflow retry <runtime_id> draft-1
uv run inklink workflow abandon <runtime_id> 3
uv run inklink workflow rewrite <runtime_id> 3
```

`info/stats/nodes/artifacts/artifact/approvals/messages/events` 使用只读 inspect，不会修改 run 状态，也不会写 `run_resumed` 事件。`message/chat-update/approve/retry/abandon/rewrite` 是写命令，会获取输入目录锁。

## 验证命令

提交前运行完整验证：

```bash
uv run ruff format
uv run ruff check
uv run mypy
uv run pytest
```

单测示例：

```bash
uv run pytest tests/test_config.py -q
```

## 项目结构

| 路径 | 职责 |
| --- | --- |
| `src/inklink/config.py` | TOML 读取、空字符串归一、Pydantic 配置校验、SDK 参数映射。 |
| `src/inklink/chapters.py` | 严格章节文件发现、UTF-8/BOM 读取、换行规范化和 `title:`/`---` 格式校验。 |
| `src/inklink/atomic.py` | 同目录临时文件、`fsync`、`os.replace` 原子写入，以及跨目录来源到目标目录的原子文本移动。 |
| `src/inklink/locks.py` | 输入目录级运行锁和 `.inklink.lock` 标记。 |
| `src/inklink/domain/models.py` | 章节合同、草稿、伏笔、检查报告等领域模型。 |
| `src/inklink/domain/checks.py` | 中文字数、章节合同、必需人物/关键词和伏笔状态确定性检查。 |
| `src/inklink/domain/index.py` | 结构化人物索引、generation 撤回和 active score 重算。 |
| `src/inklink/storage/*` | SQLite schema/store、调用缓存、artifact 版本、generation 事实和 JSONL 审计事件。 |
| `src/inklink/llm/*` | provider-independent 类型、OpenAI Responses/Chat 适配器、usage 归一化、profile 限流。 |
| `src/inklink/tools/registry.py` | 内部 function tool schema 与 dispatch 注册。 |
| `src/inklink/workflow/*` | DAG 节点模型、幂等键、执行器、workflow service 和端到端 pipeline。 |
| `src/inklink/tui/*` | Textual TUI、运行摘要、产物/审批/事件审计屏和基础审批控件。 |
| `tests/*` | 与上述模块对应的 focused tests。 |

## 代码约定

- 写 Python 代码必须使用类型注释。
- 不要硬编码模型名、路径、章节号范围或服务参数；优先走配置、输入数据和已有 helper。
- 不要过度复杂化；先复用已有模块和测试模式，再新增抽象。
- 改代码前先阅读相关实现和测试。
- 新增或修改行为时同步补测试；用户可见行为或配置变化必须同步更新文档。

## 提交流程

- 小步提交，提交信息描述单一目的。
- 提交前运行完整验证命令。
- 本仓库按用户要求 git 命令需要提权执行。
- 如果 `git commit` 遇到 GPG 密钥或签名问题，停止并提醒用户刷新 GPG，不要用关闭签名的方式绕过。
- 不要回滚他人改动；开始任务前检查 `git status --short`，提交前再次核对 diff。

推荐文档或代码变更收尾顺序：

```bash
uv run ruff format
uv run ruff check
uv run mypy
uv run pytest
git status --short
git diff
git add README.md docs
git commit -m "docs: add inklink project documentation"
```
