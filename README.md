# Inklink

墨连（Inklink）是一个面向中文长篇小说续写的 AI 驱动 TUI 工具。

## 当前状态

Inklink 仍处于分阶段实现中。当前仓库已经实现：

- 项目 scaffold、`uv` 工具链、Typer CLI 和 Textual TUI shell。
- TOML 配置加载、严格字段校验、多模型 profile 与任务映射。
- 章节目录读取、UTF-8/BOM 处理、LF/CRLF 换行规范化和章节格式校验。
- 原子写入辅助、输入目录运行锁、SQLite 状态库和 JSONL 审计事件。
- 领域模型、确定性检查、结构化人物索引与 generation 撤回逻辑。
- OpenAI Python SDK `AsyncOpenAI` 适配层，区分 Responses API 与 Chat Completions API。
- 可执行端到端 pipeline：章节分析、区间摘要、故事状态合并、大纲、章节计划、场景计划、场景顺序续写、确定性检查、LLM review、自动修订、输出写入和用量统计。
- 基础 workflow primitive，包括 DAG 执行器、幂等键、run 启动、resume、retry/rewrite/abandon、调用缓存、artifact 版本和 generation 撤回。

当前 `uv run inklink run --execute ...` 可以直接执行续写 pipeline。TUI 已能通过 `Ctrl+R` 触发同一 pipeline。多轮自由审批聊天、可视化节点树、完整结构化检索和更细的 abandon/rewrite 下游失效仍在后续阶段完善。

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

直接执行续写 pipeline：

```bash
uv run inklink run ./novel --config config.toml --execute \
  --chapter-count 1 --min-chars 800 --max-chars 1800 --auto-approve
```

默认 `output` 模式会写入 `logs/<runtime_id>/outputs/chapters/<N>.txt`。`--output-mode writeback` 会写回输入目录的目标章节号；若目标文件已存在，会拒绝覆盖。

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
uv run inklink workflow message <runtime_id> outline "请强化冲突"
uv run inklink workflow chat-update <runtime_id> outline outline outline "请强化冲突"
uv run inklink workflow approve <runtime_id> outline outline 1
uv run inklink workflow retry <runtime_id> draft-1
uv run inklink workflow abandon <runtime_id> 1
uv run inklink workflow rewrite <runtime_id> 1
```

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
