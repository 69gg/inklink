# 工作流设计

本文区分当前已实现 pipeline 与完整交互式产品目标。当前代码已有 DAG 执行器、幂等键、SQLite/JSONL 状态、run 启动与 resume、retry/rewrite/abandon 命令、调用缓存、artifact 版本、generation 撤回，以及可执行端到端续写 pipeline。pipeline 会读取章节、调用 LLM tool、生成规划和场景、执行确定性检查与 LLM review、自动修订、写入输出并汇总 usage。

## 设计 DAG

完整产品目标 DAG 节点如下：

```text
load_project
  -> analyze_chapter[*]
  -> summarize_range[*]
  -> merge_story_state
  -> plan_outline
  -> approve_outline
  -> plan_chapters
  -> approve_chapter_plan
  -> plan_scenes[*]
  -> approve_scene_plan[*]
  -> draft_scene[*]
  -> assemble_chapter[*]
  -> check_chapter[*]
  -> review_chapter[*]
  -> revise_chapter[*]
  -> integrate_generated_chapter[*]
  -> write_output[*]
  -> summarize_run
```

`[*]` 表示按章节、区间或场景展开的节点族。不同章节之间可以在依赖满足时并行；同一章内的 scene drafting 应按顺序生成，后续场景必须承接前序实际正文或摘要，而不是只依赖原始计划。

## 审批点

设计审批点包括：

- `approve_outline`：批准或通过聊天修改大纲。
- `approve_chapter_plan`：批准章节计划。
- `approve_scene_plan[*]`：逐章或逐场景批准场景计划。
- 自审失败审批：当确定性检查或 LLM review 修订达到上限后，要求人工决定继续修订、放弃或接受风险。

审批聊天每轮消息都会入库、参与幂等键，并在 `chat-update` 时作为完整会话历史交给对应 `update_*` 工具。AI 更新产物后会把 change summary 写回审批消息，避免后续轮次只看到最后一句用户意见。

当前 pipeline 会在未自动批准的大纲、章节计划、场景计划和自审失败处暂停，并把 run 状态写为 `waiting_approval`。用户可用 `inklink workflow message` 记录讨论，用 `chat-update` 通过 LLM 工具生成新的讨论稿 artifact，用 `approve` 将某个 artifact 版本标为定稿，再用 `--resume-runtime-id` 继续。TUI 首页可填写运行参数并启动或恢复 runtime；F1 展示文本 DAG 树和 runtime 状态；F4 屏幕可记录审批消息、调用 AI 工具修改产物、批准绑定或指定产物版本、重试、放弃章节和重写章节；F3 屏幕可输入 artifact ID 与两个版本号查看 JSON/unified diff。

启动任务时的输入目录、配置文件、输出模式、章节数、起始章节、字数区间、修订轮数、自动批准默认值和 notes 会保存为 `run_settings` artifact，并同步到 SQLite `runs.settings_json`。notes 是额外约束，会进入故事合并、大纲、章节计划、场景计划、正文、review 和 revision 的模型输入；但正文中已有章节仍是世界观和设定推断的主要来源。恢复同一 runtime 时默认复用保存的创作参数，避免用户重启 TUI 后丢失 notes 或误改字数范围；本次 resume 的 `--auto-approve` 可临时放行后续审批点。

## auto-approve

自动批准默认关闭，可在 `config.toml` 中按类型开启：

- `approvals.auto_approve_outline`
- `approvals.auto_approve_chapter_plan`
- `approvals.auto_approve_scene_plan`
- `approvals.auto_approve_review_failure`

`auto_approve_review_failure` 风险最高。开启后，修订达到上限仍未通过的章节可能进入输出流程，应只在用户明确接受质量风险时使用。

## 幂等、generation 与恢复

SQLite 是恢复依据，JSONL 是审计日志。设计恢复粒度包括：

- workflow node 状态。
- LLM 调用与 usage。
- tool call 入参和结果。
- artifact 版本。
- approval 状态。
- approval message 记录。

幂等键包含节点类型、输入版本、模型 profile、toolset 版本、prompt 版本、任务参数哈希、审批消息哈希和 generation。`generation/regeneration_count` 必须进入 idempotency key；同一创作结果被放弃或重写后，新的 generation 不应复用旧 LLM 结果。

当前代码已实现 `WorkflowNode.idempotency_key(...)` 与 `IdempotencyInputs`，并验证 generation、审批消息、任务参数、prompt 版本、toolset 版本和 profile 变化会改变 key。pipeline 的 LLM 调用会先查 `llm_calls/tool_calls` 中相同 key 的成功结果，命中时复用 tool payload，不再次调用模型。

结构化索引的 typed views 也遵循同一 generation 规则。`StoryIndex.characters`、`plot_threads`、`events`、`world_rules` 和 `keywords` 不作为独立事实写入；它们从当前有效 `entity_mentions`、`structured_facts` 和 `abandoned_generations` 重建。放弃或重写章节后，对应 generation 的伏笔状态、回收窗口、世界观规则、事件和关键词投影会随事实撤回而消失。

## retry 与 rewrite_chapter

`retry` 用于瞬时技术错误，例如网络超时、429、5xx、进程中断或可恢复的 SDK 异常。retry 不递增 generation；当前实现会把已有目标节点标记为 `invalidated` 并记录 `node_retry_requested` 事件，使后续恢复流程不会把该节点视为有效完成态。创作性不满意应使用 `rewrite_chapter`，而不是 retry。

`rewrite_chapter` 用于丢弃已有创作结果并重新生成。它必须递增 generation，并使旧 artifact、旧结构化索引贡献和下游依赖失效。

`abandon_chapter` 也必须使对应 generation 的 artifact、索引贡献和下游依赖失效。当前 `WorkflowService.abandon_chapter()` 与 `WorkflowService.rewrite_chapter()` 已递增章节 generation，并把旧 generation 写入 `abandoned_generations`，结构化索引读取时会撤回该 generation 的提及事实。服务还会失效相关章节产物、`run_summary/story_index/story_state/range_summary` 等投影 artifact，以及该章和后续章节节点；下一次 resume 不会直接复用旧完成摘要或旧章节输出。

## 修订子循环

修订不是顶层 DAG 回边。设计上：

1. `assemble_chapter[*]` 生成章节草稿。
2. `check_chapter[*]` 先跑确定性检查。
3. 确定性检查失败时直接进入 `revise_chapter[*]`，不先调用 LLM review。
4. 只有确定性检查通过后才运行 `review_chapter[*]`。
5. LLM review 发现叙事问题时进入 `revise_chapter[*]`。
6. 修订轮数受 `writing.max_revision_rounds` 限制。

这样可以避免把硬性格式、字数、合同问题交给叙事 review 判断。

## 当前实现边界

当前 workflow service 和 pipeline 的边界如下：

- `start_run()` 会读取章节、获取输入目录锁、创建 SQLite 状态库、写入 `run_started` JSONL 事件。
- `retry_node()` 会记录 `node_retry_requested` 事件；如果节点存在，会把该节点标记为 `invalidated`。
- `inspect_run()` 会只读打开 runtime，不获取项目锁、不修改 run 状态、不写 `run_resumed`，供 info/stats/list 类命令使用。
- `resume_run()` 会重新打开 `logs/<runtime_id>/state.sqlite`，获取项目锁，并记录 `run_resumed`。
- `abandon_chapter()` 会递增 generation、登记废弃 generation、失效相关 artifact/node，并记录 `chapter_abandon_requested` 事件。
- `rewrite_chapter()` 会递增 generation、登记废弃 generation、失效相关 artifact/node，并记录 `chapter_rewrite_requested` 事件。
- `record_approval_message()` 会写入审批聊天消息并更新消息 hash，且不会清空已有审批绑定的 artifact。
- `approve_artifact()` 会批准指定 artifact 版本，并把 artifact 行从讨论稿转为已批准定稿。
- `InklinkPipeline.update_artifact_with_chat()` 会把完整审批会话历史传入 `update_outline`、`update_chapter_plan` 或 `update_scene_plan` 工具，生成新的讨论稿 artifact 版本，并记录 assistant change summary。
- `usage_stats()` 会从持久化 LLM 调用记录聚合 profile/model/task 用量。
- `WorkflowExecutor` 能按依赖运行节点、拒绝重复节点和循环依赖，并在 runner 失败时标记 failed。
- `InklinkPipeline` 会执行当前端到端生成流：`chapter_extraction`、`range_summary`、`story_merge`、`outline_planning`、`chapter_planning`、`scene_planning`、`drafting`、`review`、`revision`、生成章节集成分析和输出写入。
- 同章内场景按顺序生成，后续场景 prompt 包含前序场景正文。
- 确定性检查覆盖章节字数、场景字数、场景总目标区间、必须人物/关键词和重复回收伏笔。伏笔检查会按 `thread_id` 对 `PlotThread` 去重，乱序输入下仍确定性检查已 resolved/abandoned 伏笔的重复回收，以及超过 `due_chapter` 且未终止的伏笔。
- 运行中生成章节会反哺结构化索引，并刷新所在区间摘要；刷新时会把窗口内已存在的原章节和已生成章节一起交给 `summarize_range`，避免同一窗口的摘要只剩最后一章。
- `output` 模式写入 `logs/<runtime_id>/outputs/chapters/<N>.txt`；`writeback` 模式在目标文件已存在时写入 `pending_writeback` 并进入 `waiting_write_output`，目标释放后 resume 会通过目标目录内临时文件原子落盘。
- `--resume-runtime-id` 会恢复同一 runtime，复用已成功 LLM tool result，并跳过已完成且输出文件仍存在的章节。
- `run_settings` 会在恢复时优先作为参数来源；CLI/TUI 不需要重新填写 notes、字数范围或输出模式。
- `inklink workflow ...` 子命令可对已有 runtime 执行 info、stats、nodes、artifacts、artifact、approvals、messages、events、message、chat-update、approve、retry、abandon 和 rewrite。查询类命令使用只读 inspect，不会把已完成 run 改回 running。
- TUI 已提供参数化启动/恢复入口、runtime 状态汇总、文本 DAG 树、审批消息、AI 产物修改、artifact diff、批准、retry、abandon 和 rewrite 控件。

首版保持同进程 TUI/workflow service 边界，不提供后台 daemon。若某章节 generation 已被吸收到区间摘要后才被放弃，结构化索引事实会撤回，但对应区间摘要需要重新生成后才能彻底移除该 generation 的自然语言痕迹。
