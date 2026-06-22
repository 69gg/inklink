# 墨连（Inklink）设计规格

## 目标

墨连是一个 Python 小说续写工具，使用 `uv` 管理项目，以中文长篇小说续写为首要目标。输入是一章一个文件的章节目录，系统读取已写内容，自动提取剧情、人物、世界观、伏笔、文风和未解决事件，生成可讨论的大纲、章节计划和场景计划，在用户批准后按场景续写正文，并进行自动审查和修订。

项目要做成完整产品形态，而不是一次性脚本。核心要求是：

- 使用 Textual 构建 TUI，多轮讨论、审批和恢复都在 TUI 内完成。
- 使用 OpenAI Python SDK，支持 Responses API 和 Chat Completions API，也支持 OpenAI-compatible `base_url`。
- 使用全 tool call 架构表达 AI 的结构化结果和领域动作。
- 支持调用级断点续接，细粒度记录节点、调用、工具、产物版本和审批消息。
- 支持超长项目，例如 1000+ 章，不能把全文直接塞进上下文。
- 支持多模型 profile 和任务映射，一个模型或多个模型都能工作。
- 使用 SQLite + JSONL 保存状态和审计日志。
- 使用 `ruff format`、`ruff check`、`mypy`、`pytest` 保证质量。
- 同步编写 README、配置文档、工作流文档、兼容性文档、开发文档和测试。

## 非目标

- 首版不做 Web UI。
- 首版不做多语言优化，中文小说优先。
- 首版不默认把中间产物导出为可人工编辑文件；恢复来源以 SQLite 为准。
- 首版不抽象到 Anthropic、Gemini 等非 OpenAI-compatible SDK。
- 首版不把工具做成外部 MCP server；工具是应用内部领域动作。

## 输入格式

输入是一个目录，章节文件命名为连续数字：

```text
1.txt
2.txt
3.txt
...
```

每个章节文件必须严格符合：

```text
title: 章节标题
---
正文
```

解析规则：

- 文件名必须是连续正整数章节号。
- 第一行必须以 `title:` 开头。
- 标题和正文之间必须有独立的 `---` 分隔行。
- 格式错误时在 TUI 和日志中给出清晰错误，不做宽松猜测。
- 输入目录可包含 `notes.md`，也可在任务启动时指定 notes 文件。

`notes.md` 只作为额外约束。世界观、设定、人物关系、文风、伏笔和悬念必须主要从已有章节推断，并记录来源章节。

## 总体架构

采用“分层 TUI 编排 + 轻量 DAG 工作流”的融合方案。

### TUI 层

Textual 应用负责：

- 项目选择。
- 配置检查。
- 任务参数输入。
- DAG 进度展示。
- 大纲、章节计划、场景计划和自审失败审批。
- 审批点聊天。
- 输出模式选择。
- 统计展示。

TUI 不直接拼 prompt，不直接调用 OpenAI，不直接执行领域工具。它通过 workflow service 查询状态和提交命令。

### Workflow/DAG 层

Workflow 层是流程真相。它把续写拆成可恢复 DAG 节点：

- `load_project`
- `analyze_chapter[*]`
- `merge_story_state`
- `plan_outline`
- `approve_outline`
- `plan_chapters`
- `approve_chapter_plan`
- `plan_scenes[*]`
- `approve_scene_plan[*]`
- `draft_scene[*]`
- `assemble_chapter[*]`
- `review_chapter[*]`
- `revise_chapter[*]`
- `write_output[*]`
- `summarize_run`

每个节点都有：

- 节点 ID。
- 节点类型。
- 依赖节点。
- 输入 artifact 版本。
- 输出 artifact 版本。
- 状态。
- 幂等键。
- 重试次数。
- 开始/结束时间。
- 错误摘要。

节点完成后可跳过，失败后可从失败节点或未完成节点继续。

### Domain/Tool 层

Domain 层定义小说领域对象和 AI 可调用工具。结构化结果不靠“自由 JSON 文本解析”，而通过 tool call 写入领域对象。

首版工具包括：

- `record_chapter_analysis`
- `record_worldbuilding_facts`
- `record_character_updates`
- `record_plot_threads`
- `propose_outline`
- `update_outline`
- `propose_chapter_plan`
- `update_chapter_plan`
- `propose_scene_plan`
- `update_scene_plan`
- `submit_scene_draft`
- `submit_chapter_review`
- `submit_revision`

工具必须做参数校验、产物版本化和幂等保护。工具执行失败要记录到 `tool_calls` 和 JSONL 日志。

### Infrastructure 层

Infrastructure 层负责：

- OpenAI-compatible LLM 适配器。
- SQLite 状态库。
- JSONL 事件日志。
- TOML 配置加载。
- 输入/输出文件读写。
- 限流。
- 重试。
- token usage 归一化。
- 错误分类。

## 核心数据流

启动任务时，用户在 TUI 中选择输入目录、配置文件、notes、输出模式、续写范围、字数区间、修订轮数、模型任务映射和限流设置。系统生成 `runtime_id`，创建日志目录和 SQLite 状态。

流程如下：

1. `load_project` 严格读取章节和 notes。
2. `analyze_chapter[*]` 按章节提取剧情脉络、人物、关系、地点、世界观、伏笔、悬念、风格、禁忌和未解决事件。
3. `merge_story_state` 合并为全书状态，包括已写大纲、人物表、关系网、世界观规则、事件时间线、伏笔表、文风摘要、冲突和悬念清单。
4. `plan_outline` 生成后续总体大纲，并进入审批点。
5. 用户在 TUI 中与 AI 讨论大纲，AI 通过工具更新大纲。用户批准后进入章节计划。
6. `plan_chapters` 生成接下来 N 章的章节合同。
7. 用户讨论并批准章节计划。
8. `plan_scenes[*]` 为每章生成场景卡。
9. 用户讨论并批准场景计划。
10. `draft_scene[*]` 按场景生成正文。
11. `assemble_chapter[*]` 合并场景为章节草稿。
12. `review_chapter[*]` 自审章节质量。
13. `revise_chapter[*]` 自动修订到通过或达到上限。
14. `write_output[*]` 写入输出目录或写回输入目录。
15. `summarize_run` 汇总调用次数和 token 统计。

大纲、章节计划、场景计划和自审失败是强审批点。审批点必须保留聊天面板，允许用户自由讨论。AI 只能通过工具更新当前 artifact，用户批准后才能进入下一阶段。

## 长篇规模设计

系统必须支持 1000+ 章项目。核心策略是不把全文直接放入模型上下文，而是建立分层摘要和检索上下文。

数据分层：

- 原始章节正文。
- 单章分析 artifact。
- 分卷或区间摘要 artifact。
- 全书状态 artifact。
- 当前续写上下文。
- 当前章节合同。
- 当前场景合同。

生成某一章时，上下文只包含：

- 全书状态摘要。
- 近期章节摘要和必要原文片段。
- 与当前章节相关的人物、关系、伏笔、世界观规则。
- 当前章节合同。
- 当前场景合同。
- notes 约束。

每章都必须有 `chapter_contract`，用于保证“不多不少”和足够信息点：

- 章节标题候选。
- 目标中文字数区间。
- 核心冲突。
- 必须推进的信息点。
- 爽点或情绪峰值。
- 人物变化。
- 伏笔推进。
- 结尾钩子。
- 禁止事项。

每个场景有 `scene_contract`：

- 场景目标。
- 地点。
- 出场人物。
- 情绪变化。
- 信息揭示。
- 冲突或爽点。
- 目标中文字数区间。
- 与章节合同的对应关系。

自审必须检查章节合同和场景合同是否被满足。未满足时进入自动修订。

## LLM/API 设计

使用 OpenAI Python SDK。LLM 层暴露统一 `LLMClient` 接口，下面有两个独立适配器：

### ResponsesAdapter

使用 Responses API：

- `client.responses.create(...)`
- `instructions`
- `input`
- `tools`
- `tool_choice`
- `previous_response_id`（可用时）

Responses 的结构化输出请求形状是 `text.format`，但本项目首版采用全 tool call 架构，因此领域产物通过 function tool 写入。工具调用从 Responses 输出 item 中解析，并使用 `call_id` 关联工具结果。

### ChatCompletionsAdapter

使用 Chat Completions API：

- `client.chat.completions.create(...)`
- `messages`
- `tools`
- `tool_choice`

Chat Completions 的结构化输出请求形状是 `response_format`，工具调用从 `message.tool_calls` 解析。它不能复用 Responses 的请求/响应结构。

官方文档明确 Responses 与 Chat Completions 在结构化输出、函数调用、会话状态方面存在差异。设计和实现必须保持两个适配器独立，只共享上层领域请求模型和归一化响应模型。

参考：

- https://developers.openai.com/api/docs/guides/migrate-to-responses
- https://developers.openai.com/api/docs/guides/function-calling
- https://developers.openai.com/api/docs/guides/structured-outputs

## 模型 profile 与任务映射

用户可以只配置一个模型，也可以配置多个 profile，并把任务映射到任意 profile。

示例任务：

- `chapter_extraction`
- `worldbuilding_extraction`
- `story_merge`
- `outline_planning`
- `outline_chat`
- `chapter_planning`
- `scene_planning`
- `scene_chat`
- `drafting`
- `review`
- `revision`

未配置映射的任务回退到 `default` profile。

每个 profile 支持：

- `api = "responses" | "chat_completions"`
- `model`
- `api_key_env`
- `base_url`
- `timeout_seconds`
- `max_retries`
- `rpm`
- `max_concurrency`
- 可选 `temperature`
- 可选 `top_p`
- 可选 `reasoning_effort`
- 可选 `max_completion_tokens`
- 可选兼容参数

空值不传给 SDK，避免对 OpenAI-compatible 服务造成不必要兼容问题。

## 限流和重试

限流按模型 profile 配置，不硬编码。首版支持：

- `rpm`：每分钟请求数。
- `max_concurrency`：并发调用数。

预留：

- `tpm`：每分钟 token 数。
- `daily_budget`：每日预算。

每个 profile 使用独立限流队列。DAG 节点根据任务映射进入对应 profile 队列。重试策略区分：

- 网络超时。
- 429 限流。
- 5xx 服务错误。
- JSON/tool 参数校验失败。
- 内容不满足合同。

网络和服务错误可自动重试。工具参数失败可让模型修复一次或多次。合同未满足进入 review/revision 流程。

## 状态库与日志

每次运行创建目录：

```text
logs/<runtime_id>/
  events.jsonl
  state.sqlite
  artifacts/
  outputs/
```

SQLite 是恢复依据，JSONL 是审计依据。

SQLite 表至少包括：

- `runs`
- `nodes`
- `llm_calls`
- `tool_calls`
- `artifacts`
- `approvals`
- `messages`

`llm_calls` 记录：

- profile。
- 模型名。
- API 类型。
- 任务类型。
- 请求摘要。
- 响应摘要。
- OpenAI request ID（如果有）。
- usage 原始值。
- usage 归一化值。
- attempt。
- 错误。

`tool_calls` 记录：

- 工具名。
- 参数。
- 结果。
- `call_id`。
- 关联 LLM 调用。
- 幂等键。

JSONL 事件包括：

- `run_started`
- `node_started`
- `llm_request`
- `llm_response`
- `tool_call`
- `tool_result`
- `approval_waiting`
- `approval_accepted`
- `node_failed`
- `node_completed`
- `run_completed`

日志不保存 API key。prompt 和 response 是否完整保存可配置，默认完整保存到本地，便于断点续接和排查。

## 调用级续接

每个 LLM call 都有稳定 `idempotency_key`。该 key 由以下内容计算：

- 节点类型。
- 输入 artifact 版本。
- profile。
- 工具集版本。
- prompt 版本。
- 关键任务参数。

恢复时：

- 如果已有成功响应和工具结果，直接复用。
- 如果调用中断但无成功结果，创建新 attempt 并重试。
- 如果工具结果已写入同一 artifact 版本，不重复写入。
- 如果用户在审批点修改 artifact，后续节点输入版本变化，旧的下游节点失效并重新执行。

断点续接粒度必须细到节点、调用、工具、产物版本和审批消息。

## TUI 设计

TUI 使用 Textual，Rich 用于 Markdown、表格、日志和格式化展示。

主要界面：

- `ProjectSetupScreen`
- `ConfigDoctorScreen`
- `RunDashboardScreen`
- `ApprovalWorkspace`
- `StatsScreen`

`RunDashboardScreen` 布局：

- 左侧：DAG 节点树，显示节点状态、重试次数、等待审批点。
- 中间：当前 artifact 视图，大纲、章节计划、场景卡、自审报告用 Markdown 或表格展示。
- 右侧：审批点聊天面板。
- 底部：运行日志、进度、限流状态、当前 profile、token 统计摘要。

审批点聊天规则：

- 大纲、章节计划、场景计划、自审失败都启用聊天面板。
- 用户可自由输入修改意见。
- AI 必须通过对应 tool 更新当前 artifact。
- TUI 展示 diff 或版本摘要。
- 用户批准后节点才完成。

## 输出策略

支持两种输出模式：

### output 模式

默认模式，不修改原始章节目录。输出到：

```text
logs/<runtime_id>/outputs/chapters/<N>.txt
```

### writeback 模式

从输入目录最大章节号后继续写 `N+1.txt`。写入前检查目标文件不存在。若文件已存在，暂停并要求用户处理，不能覆盖。

输出章节格式：

```text
title: 章节标题
---
正文
```

## 统计设计

运行结束时在 TUI 和导出报告中展示统计。

按 profile、模型、任务类型聚合：

- successful calls。
- failed attempts。
- total attempts。
- input tokens。
- output tokens。
- total tokens。
- reasoning tokens。
- cached tokens。
- cache read/write tokens（如果接口返回）。

不同 API 和兼容服务返回字段不一致。系统做归一化，有字段则显示，无字段则隐藏，不能臆造。

## 配置设计

`config.toml` 加入 `.gitignore`。项目提供 `config.toml.example`，其中必须有详细 `#` 注释，解释字段含义、默认行为、留空语义和兼容性注意事项。

配置分区：

- `[runtime]`
- `[storage]`
- `[writing]`
- `[tui]`
- `[models.<profile>]`
- `[tasks]`

示例结构：

```toml
# 真实 config.toml 不应提交到 git。
# 复制本文件为 config.toml 后再填写自己的密钥环境变量和模型配置。

[runtime]
# 默认输出模式：output 不修改原目录；writeback 会写回输入目录后续章节。
output_mode = "output"

# 是否把完整 prompt/response 保存到本地日志。
# 开启后更利于断点续接和排查，但日志会包含小说正文。
save_full_prompts = true

[models.default]
# 可选值：responses 或 chat_completions。
api = "responses"

# 模型名不硬编码，由用户配置。
model = "gpt-5.5"

# 从环境变量读取 API key，避免把密钥写入 config.toml。
api_key_env = "OPENAI_API_KEY"

# 留空时使用 SDK 默认地址；兼容服务可填写自定义 /v1 base_url。
base_url = ""

# 每分钟请求数限制。
rpm = 60

# 同一 profile 最大并发调用数。
max_concurrency = 4

[tasks]
# 未配置的任务都会回退到 models.default。
drafting = "default"
review = "default"
```

## 注释规范

代码必须使用类型注释。

注释原则：

- 配置示例使用充分的 `#` 注释，让用户能直接理解每个字段。
- 复杂状态机、幂等恢复、DAG 失效规则、API 差异适配处写解释性注释。
- 普通代码不写重复代码含义的注释。
- 文档中解释关键设计取舍。

## 测试策略

测试分层：

- 章节解析测试：严格格式、连续章节、错误提示。
- 配置加载测试：空值不传、任务映射回退、限流配置。
- DAG 测试：依赖排序、节点失败/恢复、幂等跳过、下游失效。
- LLM 适配测试：mock Responses 和 Chat Completions，分别验证 tool call 解析、usage 归一化和重试。
- Tool 层测试：工具参数校验、产物版本化、重复调用幂等。
- TUI 测试：关键 screen、审批流和消息提交。
- 集成测试：使用 fake LLM 跑完整流程，不依赖真实 API。

实现阶段必须补测试，不能只写功能。

## 文档计划

首版文档：

- `README.md`：项目定位、安装、快速开始、输入格式、TUI 用法。
- `docs/configuration.md`：配置详解和多模型映射示例。
- `docs/workflow.md`：DAG、审批点、断点续接。
- `docs/llm-compatibility.md`：Responses 与 Chat Completions 的适配差异、兼容接口限制。
- `docs/development.md`：uv、ruff、mypy、pytest、提交流程。
- `docs/superpowers/specs/2026-06-22-inklink-design.md`：本设计规格。

## 开发工作流

项目使用 `uv`。验证命令包括：

```bash
uv run ruff format
uv run ruff check
uv run mypy
uv run pytest
```

实现阶段按可验证里程碑提交。规格文档先提交；后续通过 implementation plan 拆成并行 agent 任务。Git 命令按用户要求提权执行。若 commit 遇到 GPG 问题，提醒用户刷新 GPG，不绕过签名。

## 已确认决策

- TUI 采用 Textual，Rich 用于渲染辅助。
- 首版以场景级控制续写。
- 支持 OpenAI SDK + OpenAI-compatible `base_url`。
- 同时支持 Responses API 和 Chat Completions API。
- 使用全 tool call 架构。
- 输出支持 `output` 和 `writeback` 两种模式。
- 使用 SQLite + JSONL 做调用级续接和审计。
- 输入章节格式严格校验。
- notes 是额外约束，设定必须从正文推断。
- 用户可配置多个模型 profile，也可只配置一个。
- 自审自动修订到通过或达到上限。
- 字数控制以中文字数区间为主。
- 中间产物主要在 TUI 中编辑和审批，不默认做人类可手改文件。
- 运行结束展示每模型/profile 的调用和 token 统计。
- 审批点必须支持用户与 AI 讨论。
- 中文小说优先。
- 面向完整产品开发，但实现必须按子系统和里程碑拆分。
