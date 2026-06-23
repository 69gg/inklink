# 长上下文策略

Inklink 的长篇上下文设计以结构化索引为唯一事实来源。全书状态摘要、区间摘要和 prompt 中的自然语言说明都是读时投影或缓存产物，不应成为新的事实源。

## 结构化索引

当前已实现的人物索引以提及事实为底层数据。人物或实体提及事实必须携带：

- `entity_id`
- `chapter_number`
- `generation`
- `strength`

事实身份可理解为 `(entity_id, chapter_number, generation)`。重复合并同一身份是 upsert/集合语义，会替换旧值，不会追加重复事实。

`active_score` 从当前有效 generation 的事实重算，不是运行态累加。首次出现章节、最后出现章节和相关章节同样从有效事实重算。

放弃某个 generation 时，该 generation 的事实贡献会被撤回，然后重新计算人物索引。这样可以避免旧草稿中的人物活跃度、出场章节和相关章节残留到新结果。

## 活跃与相关实体

1000+ 章项目不能把全书所有实体都注入每次 prompt。设计目标是只注入活跃/相关实体；`inactive`、`dead`、`resolved` 等低优先级实体继续保存在 SQLite 中，按需检索。

当前代码已实现人物提及事实、有效 generation 过滤、active score 重算，并把提及事实持久化到 SQLite。更完整的实体状态分类、伏笔/事件/世界观索引查询和 prompt 注入策略属于后续集成。

## 检索预算

`writing.retrieval_token_budget` 默认关闭。当前 pipeline 已实现轻量确定性裁剪：把故事状态、伏笔、人物、世界观和近期章节摘要转换为带优先级的检索项，再按预算裁剪。当前预算单位用字符数近似 token，后续可替换为真实 tokenizer。

开启后，pipeline 会按确定性优先级裁剪上下文，包括：

- 正文生成。
- 章节计划。
- 场景计划。
- 审批聊天。
- 自审修订。

裁剪优先保留当前故事大纲、未解决伏笔、活跃人物、世界观规则和近期章节摘要。预算裁剪不能随机丢弃事实；同一输入和同一索引状态应得到稳定结果。

## cold start

`cold_start.enabled` 默认关闭。完整冷启动设计通过 `analyze_chapter[*]` 的 `depth = shallow|deep` 实现：

- `shallow`：较低成本读取历史章节，提取结构化事实，并生成较短叙事摘要。
- `deep`：更完整分析叙事因果、角色状态、伏笔和语气，生成更丰富摘要。

shallow 与 deep 的差异主要在叙事摘要丰富程度；结构化字段仍应尽量完整。缺关键字段时，应按需把该章节升级为 deep 分析。

`cold_start.recent_chapters_to_deep_analyze` 控制最近多少章使用 deep。当前 pipeline 已把 `depth = shallow|deep` 纳入 `chapter_extraction` 输入；按需自动升级 shallow 来源章节仍在后续集成。

## 区间摘要

`summarize_range[*]` 用于把连续章节折叠成区间摘要。初次导入和运行中持续生成应共用同一套阈值逻辑，避免冷启动摘要与后续摘要语义不一致。

区间摘要是结构化事实的自然语言投影，不是事实源。需要恢复精确信息时，应回到 SQLite 中的结构化事实和 artifact。

已知限制：如果某个 generation 已经吸收到区间摘要后才被放弃，结构化事实可以撤回，但旧区间摘要文本不会自动遗忘该 generation 的内容。对应区间摘要应标记为过期并重生成；v1 不保证所有边界都会自动重写摘要。

## 读时投影

全书状态摘要应在读取时从结构化索引、当前有效 artifact 和区间摘要组合生成。自然语言摘要可以缓存，但缓存必须带有来源版本、范围和 generation 信息；当来源过期时，缓存应被视为可丢弃产物。
