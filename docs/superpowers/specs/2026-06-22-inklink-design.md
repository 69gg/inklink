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
- 使用确定性代码检查补充 LLM 自审。
- 使用 `ruff format`、`ruff check`、`mypy`、`pytest` 保证质量。
- 同步编写 README、配置文档、工作流文档、兼容性文档、开发文档和测试。

## 非目标

- 首版不做 Web UI。
- 首版不做多语言优化，中文小说优先。
- 首版不默认把中间产物导出为可人工编辑文件；恢复来源以 SQLite 为准。
- 首版不抽象到 Anthropic、Gemini 等非 OpenAI-compatible SDK。
- 首版不把工具做成外部 MCP server；工具是应用内部领域动作。
- 首版不做额外内容过滤。内容边界由用户配置的模型及其服务策略决定；工具只执行用户配置、叙事一致性和确定性质量检查。

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
- 文件使用 UTF-8 编码。允许 UTF-8 BOM，解析时去除。
- 允许 LF 或 CRLF 换行，解析时归一化为 `\n`。
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

TUI 必须提供章节级控制操作，包括暂停、恢复、批准、重试、放弃章节和重写章节。`abandon_chapter` 用于放弃已生成或已集成但未落盘的章节，并触发相关 artifact、索引更新和下游节点失效。`rewrite_chapter` 用于从章节计划或场景计划重新生成该章节。放弃或重写章节时必须递增章节级 `generation` 或 `regeneration_count`，该值纳入后续 LLM 调用幂等键，确保不会复用刚被放弃的旧响应。`retry` 用于从瞬时技术错误恢复，不递增 generation；`rewrite_chapter` 用于丢弃当前创作结果重新生成，必须递增 generation。

### Workflow/DAG 层

Workflow 层是流程真相。它把续写拆成可恢复 DAG 节点：

- `load_project`
- `analyze_chapter[*]`
- `summarize_range[*]`
- `merge_story_state`
- `plan_outline`
- `approve_outline`
- `plan_chapters`
- `approve_chapter_plan`
- `plan_scenes[*]`
- `approve_scene_plan[*]`
- `draft_scene[*]`
- `assemble_chapter[*]`
- `check_chapter[*]`
- `review_chapter[*]`
- `revise_chapter[*]`
- `integrate_generated_chapter[*]`
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

并发和限流只在 profile 队列这一层执行。DAG executor 只负责依赖触发、节点调度和状态变更，避免 executor 与 LLM adapter 两层同时限流导致行为不可预测。

TUI 与 workflow service 在首版中是同进程边界：TUI 调用同进程 service 对象，service 以 SQLite 为恢复真相。TUI 关闭后，未完成 run 仍可通过 `resume` 重新读取 SQLite 继续执行；首版不要求后台 daemon 在 TUI 退出后继续运行。

`load_project` 必须对输入目录做 run 级互斥。系统应使用文件锁或在状态库中记录规范化输入目录的活跃 run；检测到同一输入目录已有未完成 run 时，拒绝启动新 run 或提示用户恢复/结束旧 run。该规则尤其保护 writeback 模式，避免两个 run 同时基于相同最大章节号写入同一个 `N+1.txt`。

## 核心数据流

启动任务时，用户在 TUI 中选择输入目录、配置文件、notes、输出模式、续写范围、字数区间、修订轮数、模型任务映射和限流设置。系统生成 `runtime_id`，创建日志目录和 SQLite 状态。

流程如下：

1. `load_project` 严格读取章节和 notes。
2. `analyze_chapter[*]` 按章节提取剧情脉络、人物、关系、地点、世界观、伏笔、悬念、风格、禁忌和未解决事件。
3. `summarize_range[*]` 为章节区间或分卷生成区间摘要，降低长篇合并成本。
4. `merge_story_state` 增量更新结构化索引，并从索引渲染全书状态摘要，包括已写大纲、人物表、关系网、世界观规则、事件时间线、伏笔表、文风摘要、冲突和悬念清单。
5. `plan_outline` 生成后续总体大纲，并进入审批点。
6. 用户在 TUI 中与 AI 讨论大纲，AI 通过工具更新大纲。用户批准后进入章节计划。
7. `plan_chapters` 生成接下来 N 章的章节合同。
8. 用户讨论并批准章节计划。
9. `plan_scenes[*]` 为每章生成场景卡。
10. 用户讨论并批准场景计划。
11. `draft_scene[*]` 按场景生成正文。
12. `assemble_chapter[*]` 合并场景为章节草稿。
13. `check_chapter[*]` 执行确定性代码检查。
14. `review_chapter[*]` 用 LLM 自审确定性检查无法覆盖的问题。
15. `revise_chapter[*]` 自动修订到通过或达到上限。
16. `integrate_generated_chapter[*]` 把新生成章节的实际内容反哺到单章分析、区间摘要和全书状态。
17. `write_output[*]` 写入输出目录或写回输入目录。
18. `summarize_run` 汇总调用次数和 token 统计。

大纲、章节计划、场景计划和自审失败是默认强审批点。审批点必须保留聊天面板，允许用户自由讨论。AI 只能通过工具更新当前 artifact，用户批准后才能进入下一阶段。

为了避免审批疲劳，运行参数预留 `auto_approve` 模式。该模式可按审批类型配置，例如自动通过章节计划和场景计划，但仍在大纲或自审失败处暂停。默认关闭。

虽然工作流称为 DAG，`check -> review -> revise` 的修订过程不在顶层 DAG 中建回边。它是 `revise_chapter[*]` 节点内部的修订子循环，由同一个章节节点维护 attempt 计数和当前草稿版本：

- `check_chapter[*]` 失败时，直接进入 `revise_chapter[*]`，不消耗 token 询问 LLM 是否通过。
- `review_chapter[*]` 只在确定性检查通过后执行。
- `revise_chapter[*]` 每轮产生新的草稿 artifact 版本，然后重新运行 `check_chapter[*]`，必要时再运行 `review_chapter[*]`。
- 达到 `max_revision_rounds` 后仍不通过，进入自审失败审批点。
- `nodes` 表记录章节级节点状态和修订 attempt；每次 LLM 修订调用仍以 `llm_calls` 和 `tool_calls` 细粒度记录。

同一章节内的 `draft_scene[*]` 不按场景并行执行。场景必须按顺序生成，后续场景的 prompt 必须包含前序场景的实际生成正文片段或短摘要、前序场景结尾状态和人物状态变化，不能只依赖 `scene_contract`。这样从源头保证同章内情绪、动作、悬念和人物状态连续，而不是把场景衔接问题推给 `review_chapter[*]` 或 `revise_chapter[*]` 补救。

## 长篇规模设计

系统必须支持 1000+ 章项目。核心策略是不把全文直接放入模型上下文，而是建立分层摘要和检索上下文。

数据分层：

- 原始章节正文。
- 单章分析 artifact。
- 分卷或区间摘要 artifact。
- 全书状态摘要 artifact。
- 当前续写上下文。
- 当前章节合同。
- 当前场景合同。
- 结构化检索索引。

结构化检索索引是故事状态的唯一事实来源。人物表、关系网、伏笔表、事件时间线、世界观规则本质上都是索引表或索引视图。全书状态摘要 artifact 只是从这些索引和区间摘要动态渲染出的自然语言投影，用于 prompt 展示和模型输入，不作为另一份独立持久化事实。`merge_story_state` 的职责是更新索引表和区间摘要引用；摘要渲染在读时完成，避免同一个人物或伏笔在两份存储中冲突。

索引更新必须与章节分析完成顺序无关。并行执行 `analyze_chapter[*]` 时，较晚完成的低章节号不能覆盖较早完成的高章节号事实。字段合并规则必须按语义计算，例如：

- `last_mentioned_chapter` 取最大章节号。
- `first_mentioned_chapter` 取最小章节号。
- `active_score` 由章节号、提及强度和衰减规则重新计算，而不是后写覆盖。
- 伏笔状态按生命周期状态机和章节号迁移，不能由完成顺序决定。
- 列表型字段使用集合语义 upsert，例如强化章节列表、相关章节列表不能无条件追加重复值。

`active_score` 必须是底层提及事实的纯函数，而不是原地累加的运行态计数器。索引层需要保留每章节、每 generation、每实体的提及事实记录，例如“人物 X 在第 N 章 generation G 被提及，强度 Y”。分数由当前有效 generation 的事实和衰减规则计算得出，保证并行、重试、恢复或重复合并同一章节分析时结果一致。

提及事实必须携带产生它的章节 generation。`abandon_chapter` 必须能精确撤回被放弃 generation 贡献的全部人物、伏笔、事件、世界观和关键词事实，而不是依赖新 generation 的事实自然覆盖旧痕迹。这样多次放弃和重写后，索引不会残留已放弃草稿的事实。

生成某一章时，上下文只包含：

- 全书状态摘要。
- 近期章节摘要和必要原文片段。
- 与当前章节相关的人物、关系、伏笔、世界观规则。
- 当前章节合同。
- 当前场景合同。
- notes 约束。

“必要原文片段”由检索层决定，不能只靠全书摘要。首版使用轻量结构化检索，不要求向量库：

- 人物索引：人物名、别名、状态、首次/最后出场章节、近期活跃度、相关章节。
- 伏笔索引：伏笔状态、来源章节、强化章节、建议回收窗口、相关人物和关键词。
- 事件索引：事件类型、章节范围、参与人物、影响、未解决事项。
- 世界观索引：规则、例外、来源章节、适用范围。
- 关键词索引：地点、物品、组织、功法、道具等领域名词。

组装 prompt 时，根据当前章节合同、场景合同、出场人物、待推进伏笔、用户讨论内容和近期章节，检索相关摘要和必要原文片段。第 50 章埋下并计划在第 800 章回收的伏笔，应通过伏笔索引和回收窗口被定向拉取，而不是依赖“全书状态摘要”碰巧保留细节。

检索结果可配置显式 token 预算。该预算默认关闭；开启后所有检索上下文都会按同一机制裁剪，包括正文生成、章节计划、场景计划、审批点聊天和自审修订。裁剪按确定性优先级执行，不让命中材料无限增长。裁剪优先级：

1. 当前章节合同和场景合同直接相关的材料。
2. 到达或接近回收窗口的高重要伏笔。
3. 必须出场人物的近期状态和关键历史。
4. 本章相关世界观规则和硬约束。
5. 一般活跃人物、地点、组织、物品。
6. 低活跃或弱相关材料。

预算裁剪由代码执行，不让 LLM 自己决定“哪些材料太多可以不看”。

全书状态必须有衰减和活跃度机制：

- 人物记录 `status = active | inactive | dead | unknown`。
- 人物记录 `first_mentioned_chapter`、`last_mentioned_chapter`、`active_score`。
- 伏笔记录生命周期状态，见“伏笔生命周期”。
- 长期不活跃或已解决实体保留在 SQLite，不默认注入 prompt。
- 当前章节相关、近期活跃、用户指定或检索命中的实体才进入上下文。

分卷或区间摘要由 `summarize_range[*]` 节点显式生成。默认每满固定章节数或累计 token 量触发一次，也可由配置调整。`merge_story_state` 不应每次把全部单章分析重新喂给模型，而应优先基于索引表、区间摘要和新增章节分析做增量合并。

`summarize_range[*]` 的阈值逻辑同时适用于初次导入和运行中持续生成。`integrate_generated_chapter[*]` 如果发现新章节跨过相同的章节数或 token 阈值，必须触发同一套区间摘要生成逻辑，避免长 run 退化成旧区间摘要加一串散落单章分析。

在同一次 run 内连续生成多章时，`integrate_generated_chapter[*]` 必须在每章通过检查和修订后执行。它把 AI 刚写完的一章生成轻量章节分析，并更新相关人物、伏笔、事件、区间摘要和索引表，确保第 502 章能看到第 501 章实际发生的内容。

冷启动模式默认关闭。用户首次导入 1000+ 章时，可选择开启冷启动模式：精读最近 N 章，其他历史章节先做粗摘要或区间摘要，后续按需补做细粒度分析。默认模式仍会完整分析已有章节，保证质量优先。

冷启动不绕开 `analyze_chapter[*]` 节点，而是给该节点增加 `depth = "shallow" | "deep"`。默认模式对所有章节使用 `deep`。冷启动时，近期章节使用 `deep`，旧章节使用 `shallow`，`summarize_range[*]` 统一消费章节分析 artifact，不维护两条不同形状的 DAG 链路。

`shallow` 和 `deep` 的差异限定在叙事细节摘要丰富程度。人物、伏笔、世界观、事件、关键词等结构化索引字段在两种深度下都必须尽量完整提取。若某条伏笔或事件到达回收窗口，但来源章节只有 `shallow` 分析且缺少必要结构化字段，系统应自动将来源章节升级为 `deep` 重新分析，再组装续写上下文。

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
- 允许误差比例，默认参考配置中的字数容差。

每个场景有 `scene_contract`：

- 场景目标。
- 地点。
- 出场人物。
- 情绪变化。
- 信息揭示。
- 冲突或爽点。
- 目标中文字数区间。
- 与章节合同的对应关系。
- 允许误差比例，默认参考配置中的字数容差。

自审必须检查章节合同和场景合同是否被满足。未满足时进入自动修订。

字数归约规则：

- 用户配置每章和每场景的目标中文字数区间。
- 场景计划的目标字数总和必须落在章节目标区间内，或落在配置容差内。
- 默认容差由 `writing.word_count_tolerance_ratio` 控制，建议默认值为 `0.1`。
- 确定性检查使用代码统计中文字数，不依赖 LLM 判断。

中文字数统计口径：默认只统计 Unicode CJK 统一表意文字及其扩展区字符，不统计标点、空格、换行、阿拉伯数字、拉丁字母和英文单词。混排文本如 `Lv.10` 不计入中文字数。该口径用于章节和场景字数确定性检查，并应在配置文档中说明。

## 伏笔生命周期

伏笔不是简单列表，必须有生命周期状态机。

状态：

- `seeded`：已埋下。
- `reinforced`：已强化或多次提醒。
- `due`：到达建议回收窗口但尚未回收。
- `resolved`：已回收。
- `abandoned`：用户或计划明确废弃。

伏笔记录字段至少包括：

- ID。
- 描述。
- 状态。
- 来源章节。
- 强化章节列表。
- 建议回收窗口。
- 实际回收章节。
- 相关人物、地点、物品和关键词。
- 重要性。

确定性层检查：

- 伏笔 ID 已是 `resolved` 或 `abandoned` 时，不能被再次标记为本章回收。
- 伏笔当前章节超过建议回收窗口时，标记为 `due` 或产生硬性告警。

LLM review 只判断叙事层问题，例如高重要伏笔长期未推进时是否应该在本章埋一笔、强化一次或延后回收。

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
- `range_summary`
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

自动重试和用户手动重试使用独立计数。`max_retries` 只限制系统为网络、限流、5xx 等瞬时错误做的自动重试；用户在 TUI 中手动触发的重试会创建新的 manual attempt，并记录原因，不因自动重试次数耗尽而被拒绝。手动重试仍受 profile 限流和预算控制。

## 确定性检查

LLM 自审不能单独作为质量门。`check_chapter[*]` 在 `review_chapter[*]` 前运行，用 Python 代码执行硬性检查。

首版确定性检查包括：

- 章节中文字数是否落在目标区间和容差内。
- 场景中文字数总和是否匹配章节目标。
- 必须出场人物名或别名是否出现在正文中。
- 必须出现的信息点关键词是否出现。
- 标题和正文输出格式是否符合 `title: ...\n---\n正文`。
- 禁止覆盖目标文件。
- 伏笔 ID 是否被重复回收。
- 伏笔是否超过建议回收窗口。

确定性检查失败时，不询问 LLM “是否通过”，直接进入重写或修订流程。LLM review 只负责一致性、文风、人物行为、伏笔推进、节奏、爽点等代码难以判断的问题。

修订默认采用定向重写优先。系统根据失败检查项和场景合同定位问题场景，只重写受影响场景并重新装配章节；无法定位到具体场景、全章结构性失败或 LLM review 判定整体节奏/一致性失败时，才整章重写。这样降低 token 成本，同时保留必要时整章重写的能力。

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

`artifacts` 记录：

- artifact 类型。
- 当前版本。
- 父版本。
- 是否为讨论稿。
- 是否已批准。
- 关联审批点。
- 创建来源节点或工具调用。

审批点讨论过程中允许产生多个 artifact 版本。所有版本保留用于审计，但只有 `is_approved = true` 的版本会触发下游节点继续执行或失效重算。讨论稿版本不会让下游节点提前失效，因为下游节点在审批完成前不应运行。

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
- 本轮审批或聊天消息 hash。
- 章节级 `generation` 或 `regeneration_count`。

恢复时：

- 如果已有成功响应和工具结果，直接复用。
- 如果调用中断但无成功结果，创建新 attempt 并重试。
- 如果工具结果已写入同一 artifact 版本，不重复写入。
- 如果用户在审批点修改 artifact，后续节点输入版本变化，旧的下游节点失效并重新执行。
- 如果用户放弃某个已集成但未落盘的生成章节，必须通过显式操作使该章节的 artifact、该 generation 贡献的索引事实和下游节点失效。
- 如果用户放弃后用相同章节计划和场景计划重新生成，递增后的 `generation` 必须让幂等键变化，不能复用被放弃的成功响应或工具结果。
- 修订子循环恢复时，必须恢复当前修订 attempt、当前草稿 artifact 版本和最近一次检查结果；不能把整个章节节点当作全新未完成节点从第 1 轮重新开始。

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
- 讨论中的每轮消息都会参与本轮 LLM 调用幂等键，避免用户换了修改意见时错误复用旧响应。

`auto_approve` 可按审批类型配置：

- `outline`
- `chapter_plan`
- `scene_plan`
- `review_failure`

默认全部关闭。即使开启自动批准，TUI 仍必须记录审批事件，并允许用户在运行中暂停。

## 输出策略

支持两种输出模式：

### output 模式

默认模式，不修改原始章节目录。输出到：

```text
logs/<runtime_id>/outputs/chapters/<N>.txt
```

### writeback 模式

从输入目录最大章节号后继续写 `N+1.txt`。写入前检查目标文件不存在。若文件已存在，暂停并要求用户处理，不能覆盖。

写入必须使用原子写入：

1. 先写入 `logs/<runtime_id>/outputs/tmp_<N>.txt` 或同文件系统临时文件。
2. 写入完成后 flush 并关闭文件。
3. 校验文件内容和格式。
4. 使用 `os.replace` 移动到最终路径。

writeback 模式不得直接打开最终章节文件写入，避免崩溃时损坏用户原始目录。

SQLite 中的故事状态以已批准并集成的生成内容为准，与是否已成功落盘到 writeback 目标路径解耦。若 `write_output[*]` 因目标文件冲突暂停，此时后续章节仍可基于已集成内容继续，前提是用户没有放弃该章节。若用户最终选择放弃或重写该章节，必须通过显式操作使该章节的集成更新和下游节点失效并重新计算。

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
- `[approvals]`
- `[cold_start]`

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

[writing]
# 中文字数容差比例。0.1 表示允许目标区间上下浮动 10%。
word_count_tolerance_ratio = 0.1

# 组装 prompt 时用于检索材料的 token 预算。
# 留空表示关闭预算裁剪；填写后会按确定性优先级裁剪检索结果。
retrieval_token_budget = ""

[approvals]
# 默认关闭自动批准，避免用户错过关键规划节点。
auto_approve_outline = false
auto_approve_chapter_plan = false
auto_approve_scene_plan = false

# 谨慎开启：如果修订达到上限仍未通过，开启后可能让低质量章节继续进入输出流程。
auto_approve_review_failure = false

[cold_start]
# 默认关闭冷启动模式。开启后可先粗读历史章节、精读最近章节。
enabled = false

# 冷启动开启时，最近多少章按完整单章分析处理。
recent_chapters_to_deep_analyze = 50

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
range_summary = "default"
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
- DAG 测试：依赖排序、节点失败/恢复、幂等跳过、下游失效、自动审批、修订子循环恢复、放弃后 generation 改变幂等键。
- 项目互斥测试：同一输入目录已有未完成 run 时，新 run 被拒绝或提示恢复旧 run。
- LLM 适配测试：mock Responses 和 Chat Completions，分别验证 tool call 解析、usage 归一化和重试。
- Tool 层测试：工具参数校验、产物版本化、重复调用幂等。
- 检索测试：人物、伏笔、事件、世界观索引能定向拉取相关章节片段，乱序完成章节分析后索引仍正确，列表字段重跑合并后不重复。
- 活跃度测试：`active_score` 从携带 generation 的提及事实重算，重复合并同一章节分析不改变结果，放弃 generation 后旧事实不再贡献分数。
- 冷启动测试：`shallow` 分析仍填充结构化索引字段，缺字段时可升级为 `deep`。
- 检索预算测试：超出预算时按确定性优先级裁剪材料。
- 重试测试：自动重试和手动重试计数分离，手动重试不被自动重试上限误挡。
- 确定性检查测试：中文字数口径、混排文本、人名、关键词、格式、目标文件覆盖、伏笔重复回收和伏笔超期检查。
- 修订粒度测试：可定位场景时优先定向重写，结构性失败时整章重写。
- 场景连续性测试：同一章节内场景按顺序生成，后续场景可接收前序场景实际正文或摘要。
- 原子写入测试：writeback 通过临时文件和 `os.replace` 完成，不直接写最终文件。
- 集成失效测试：放弃已集成但未落盘章节时，相关索引更新和下游节点失效。
- TUI 测试：关键 screen、审批流和消息提交。
- 集成测试：使用 fake LLM 跑完整流程，不依赖真实 API。

实现阶段必须补测试，不能只写功能。

## 文档计划

首版文档：

- `README.md`：项目定位、安装、快速开始、输入格式、TUI 用法。
- `docs/configuration.md`：配置详解和多模型映射示例。
- `docs/workflow.md`：DAG、审批点、断点续接。
- `docs/llm-compatibility.md`：Responses 与 Chat Completions 的适配差异、兼容接口限制。
- `docs/long-context.md`：长篇检索、状态衰减、区间摘要和冷启动策略。
- `docs/development.md`：uv、ruff、mypy、pytest、提交流程。
- `docs/superpowers/specs/2026-06-22-inklink-design.md`：本设计规格。

`docs/long-context.md` 需要记录一个已知限制：如果某章节 generation 已被吸收到区间摘要后才被放弃，撤回结构化索引事实不会自动让已有区间摘要文本忘记它。对应区间摘要应被标记为可能过期，并由用户或系统触发重新生成；首版不要求在所有边界情况下自动重写已生成的区间摘要文本。

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
- 同一输入目录同一时间只允许一个未完成 run。
- 输入章节格式严格校验。
- notes 是额外约束，设定必须从正文推断。
- 用户可配置多个模型 profile，也可只配置一个。
- 自审自动修订到通过或达到上限。
- 字数控制以中文字数区间为主。
- 中间产物主要在 TUI 中编辑和审批，不默认做人类可手改文件。
- 运行结束展示每模型/profile 的调用和 token 统计。
- 审批点必须支持用户与 AI 讨论。
- 审批点支持默认关闭的自动批准模式。
- TUI 必须提供放弃章节和重写章节操作。
- 放弃或重写章节必须递增 generation，避免复用旧幂等结果。
- retry 不递增 generation；rewrite 丢弃创作结果并递增 generation。
- 长篇状态使用衰减、活跃度、结构化检索和区间摘要。
- 结构化索引是故事状态事实来源，全书状态摘要是读时渲染投影。
- 索引更新必须与并行章节分析完成顺序无关。
- 列表型索引字段必须用集合语义合并，active_score 必须由携带 generation 的提及事实重算。
- 检索结果支持默认关闭的 token 预算裁剪。
- 检索 token 预算适用于正文生成、计划、自审和审批点聊天。
- 新生成章节必须反哺当前 run 的故事状态。
- 同章内场景必须按顺序生成，后续场景承接前序场景实际正文或摘要。
- 修订循环是章节节点内部子循环，不在顶层 DAG 建回边。
- 修订优先定向重写问题场景，必要时整章重写。
- 自审前必须执行确定性代码检查。
- 伏笔重复回收和超期属于确定性检查。
- 中文字数默认只统计 CJK 表意文字，不统计标点、空格、数字和英文。
- writeback 必须原子写入。
- 冷启动模式预留且默认关闭。
- 冷启动通过 `analyze_chapter` 的 shallow/deep 深度实现。
- 中文小说优先。
- 面向完整产品开发，但实现必须按子系统和里程碑拆分。
