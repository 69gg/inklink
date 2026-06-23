# Inklink

墨连（Inklink）是一个面向中文长篇小说续写的 AI 驱动 TUI 工具。

## 当前状态

当前仓库已经实现墨连规格中的核心工作流、TUI 操作面和长篇上下文机制：

- 项目 scaffold、`uv` 工具链、Typer CLI 和 Textual TUI shell。
- TOML 配置加载、严格字段校验、多模型 profile 与任务映射。
- 章节目录读取、UTF-8/BOM 处理、LF/CRLF 换行规范化和章节格式校验。
- 原子写入辅助、输入目录运行锁、SQLite 状态库和 JSONL 审计事件。
- 领域模型、确定性检查、typed 结构化索引、伏笔生命周期检查与 generation 撤回逻辑。
- OpenAI Python SDK `AsyncOpenAI` 适配层，区分 Responses API 与 Chat Completions API。
- 可执行端到端 pipeline：章节分析、cold-start shallow/deep 与按需 deep 升级、区间摘要、结构化检索原文片段、故事状态合并、大纲、章节计划、场景计划、场景顺序续写、确定性检查、LLM review、自动修订、输出写入和用量统计。
- workflow primitive，包括 DAG 执行器、幂等键、run 启动、只读 inspect、resume、retry/rewrite/abandon、调用缓存、artifact 版本、审批消息和 generation 撤回。
- 审批门：大纲、章节计划、场景计划和自审失败可暂停 run；审批聊天会把完整消息历史交给 AI 更新工具，`approve` 会把 artifact 版本标为定稿，resume 后继续。
- 章节级放弃/重写会递增 generation、撤回旧 generation 的索引事实，并失效相关章节产物、run summary 和下游章节节点。
- usage 统计会按总计、profile、model、task 汇总 LLM calls、input、output 和 total tokens；当服务端返回 cache 或 reasoning 细分时，CLI 只在有值时追加显示。

当前 `uv run inklink run --execute ...` 可以直接执行续写 pipeline。TUI 首页提供输入目录、配置文件、运行 ID、章节数、起始章节、字数范围、修订轮数、输出模式、自动批准和日志根目录输入；`Ctrl+R` 或“开始运行”会按当前参数启动，“恢复运行”会按运行 ID resume。F1 展示当前 runtime 的状态、文本 DAG 树、节点、产物、审批、用量和事件；F3 可查看产物并输入 artifact ID 与两个版本号生成 JSON/unified diff；F4 可记录审批消息、通过 AI 工具修改当前产物、批准绑定或指定产物版本、重试节点、放弃章节和重写章节；F5 查看事件日志。首版是同进程 TUI/workflow service 边界，不包含后台 daemon。

## 安装

```bash
uv sync
uv run inklink version
```

复制示例配置后填写自己的模型和环境变量名：

```bash
cp config.toml.example config.toml
```

真实 API key 不应写入 `config.toml`；配置中的 `api_key_env` 指向运行时环境变量。

## 快速开始

准备章节目录：

```text
novel/
  1.txt
  2.txt
  3.txt
```

启动 TUI：

```bash
uv run inklink run ./novel --config config.toml
```

在 TUI 首页填写或调整参数后，可以直接运行或恢复已有 runtime；恢复时填写运行 ID，输入目录、配置文件和日志根目录需要与原运行匹配。

直接执行续写 pipeline：

```bash
uv run inklink run ./novel --config config.toml --execute \
  --chapter-count 1 --min-chars 800 --max-chars 1800 \
  --max-revision-rounds 3 --auto-approve
```

默认 `output` 模式会写入 `logs/<runtime_id>/outputs/chapters/<N>.txt`。`--output-mode writeback` 会写回输入目录的目标章节号；若目标文件已存在，正文会先写入 `logs/<runtime_id>/outputs/pending_writeback/<N>.txt`，run 进入 `waiting_write_output`，用户处理目标文件后用同一 runtime resume 即可通过目标目录内临时文件原子落盘。

从已有运行恢复：

```bash
uv run inklink run ./novel --config config.toml --execute \
  --resume-runtime-id <runtime_id>
```

恢复时会复用 SQLite 中已成功的 LLM tool result，并跳过已完成且输出文件仍存在的章节。

查看或操作已有运行：

```bash
uv run inklink workflow info <runtime_id>
uv run inklink workflow stats <runtime_id>
uv run inklink workflow nodes <runtime_id>
uv run inklink workflow artifacts <runtime_id>
uv run inklink workflow artifact <runtime_id> outline --version 1
uv run inklink workflow approvals <runtime_id>
uv run inklink workflow messages <runtime_id> --approval-id outline
uv run inklink workflow events <runtime_id> --limit 20
uv run inklink workflow message <runtime_id> outline "请强化冲突"
uv run inklink workflow chat-update <runtime_id> outline outline outline "请强化冲突"
uv run inklink workflow approve <runtime_id> outline outline 1
uv run inklink workflow retry <runtime_id> draft-1
uv run inklink workflow abandon <runtime_id> 1
uv run inklink workflow rewrite <runtime_id> 1
```

`run --execute` 结束时会打印本次运行的 `usage_total`、`usage_by_profile`、`usage_by_model` 和 `usage_by_task`。`workflow stats` 会从运行目录的 SQLite 状态库读取已持久化的调用记录，并按 `profile/model/task` 汇总。基础字段始终包含 `calls`、`input`、`output`、`total`；只有 Responses 或 Chat Completions usage 中实际存在对应值时，才会额外显示 `cached`、`cache_read`、`cache_write`、`reasoning`。

## 输入格式

输入目录中的章节文件必须使用连续 ASCII 正整数命名：

```text
1.txt
2.txt
3.txt
```

不接受 `01.txt`、非 ASCII 数字、负数、`0.txt` 或章节号缺口。每个章节文件使用 UTF-8 编码，允许 UTF-8 BOM；读取时会把 CRLF 和 CR 换行规范化为 LF。

每个章节文件必须严格符合：

```text
title: 章节标题
---
正文
```

第一行必须以 `title:` 开头且标题不能为空；第二行必须是独立的 `---`；正文从第三行开始。

## 文档

- [配置说明](docs/configuration.md)
- [工作流设计](docs/workflow.md)
- [LLM 兼容性](docs/llm-compatibility.md)
- [长上下文策略](docs/long-context.md)
- [开发指南](docs/development.md)

## 开发验证

```bash
uv run ruff format
uv run ruff check
uv run mypy
uv run pytest
```
