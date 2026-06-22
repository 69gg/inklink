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
- 基础 workflow primitive，包括 DAG 执行器、幂等键、run 启动、retry/rewrite/abandon 请求事件。

完整自动续写产品仍在后续阶段集成。当前 `uv run inklink run ...` 会启动 TUI shell，但端到端自动分析、规划、生成、审批、修订和写回流程尚未全部接通。

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
