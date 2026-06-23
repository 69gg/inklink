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

审批聊天每轮消息都参与幂等键，避免同一节点在审批上下文变化后复用旧结果。

当前 pipeline 支持 `--auto-approve` / TUI `Ctrl+R` 自动接受规划节点并记录审批事件到 SQLite 和 JSONL。自由聊天式审批、artifact diff 展示和人工逐节点批准还没有完整 UI；底层 `messages` 表和审批消息 hash 已就绪。

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

## retry 与 rewrite_chapter

`retry` 用于瞬时技术错误，例如网络超时、429、5xx、进程中断或可恢复的 SDK 异常。retry 不递增 generation，目标是重新执行同一个节点尝试。

`rewrite_chapter` 用于丢弃已有创作结果并重新生成。它必须递增 generation，并使旧 artifact、旧结构化索引贡献和下游依赖失效。

`abandon_chapter` 也必须使对应 generation 的 artifact、索引贡献和下游依赖失效。当前 `WorkflowService.abandon_chapter()` 与 `WorkflowService.rewrite_chapter()` 已递增章节 generation，并把旧 generation 写入 `abandoned_generations`，结构化索引读取时会撤回该 generation 的提及事实。更完整的 artifact/downstream 自动失效仍在后续集成。

## 修订子循环

修订不是顶层 DAG 回边。设计上：

1. `assemble_chapter[*]` 生成章节草稿。
2. `check_chapter[*]` 先跑确定性检查。
3. 确定性检查失败时直接进入 `revise_chapter[*]`，不先调用 LLM review。
4. 只有确定性检查通过后才运行 `review_chapter[*]`。
5. LLM review 发现叙事问题时进入 `revise_chapter[*]`。
6. 修订轮数受 `writing.max_revision_rounds` 限制。

这样可以避免把硬性格式、字数、合同问题交给叙事 review 判断。

## 当前实现限制

当前 workflow service 和 pipeline 的边界如下：

- `start_run()` 会读取章节、获取输入目录锁、创建 SQLite 状态库、写入 `run_started` JSONL 事件。
- `retry_node()` 会记录 `node_retry_requested` 事件。
- `resume_run()` 会重新打开 `logs/<runtime_id>/state.sqlite`，获取项目锁，并记录 `run_resumed`。
- `abandon_chapter()` 会递增 generation、登记废弃 generation，并记录 `chapter_abandon_requested` 事件。
- `rewrite_chapter()` 会递增 generation、登记废弃 generation，并记录 `chapter_rewrite_requested` 事件。
- `WorkflowExecutor` 能按依赖运行节点、拒绝重复节点和循环依赖，并在 runner 失败时标记 failed。
- `InklinkPipeline` 会执行当前端到端生成流：`chapter_extraction`、`range_summary`、`story_merge`、`outline_planning`、`chapter_planning`、`scene_planning`、`drafting`、`review`、`revision`、生成章节集成分析和输出写入。
- 同章内场景按顺序生成，后续场景 prompt 包含前序场景正文。
- 确定性检查覆盖章节字数、场景字数、场景总目标区间、必须人物/关键词和重复回收伏笔。
- `output` 模式写入 `logs/<runtime_id>/outputs/chapters/<N>.txt`；`writeback` 模式拒绝覆盖已存在目标文件。
- `--resume-runtime-id` 会恢复同一 runtime，复用已成功 LLM tool result，并跳过已完成且输出文件仍存在的章节。

仍未完整接通的部分包括：多轮审批聊天、artifact diff、可视化节点树、完整结构化检索查询、artifact/downstream 自动失效和 shallow 分析自动升级。
