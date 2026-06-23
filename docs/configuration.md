# 配置说明

Inklink 从 `config.toml` 读取配置，示例见仓库根目录的 `config.toml.example`。配置模型使用严格字段校验，未知字段会报错；未配置的可选项使用代码默认值。

## 基本规则

通过 `load_config()` 读取 TOML 时，空字符串 `""` 会递归归一为 `None`。归一为空的可选项不会传给 SDK 或限流逻辑，避免对 OpenAI-compatible 服务传入不支持的空参数。

当前明确支持留空的键包括：

- `writing.retrieval_token_budget`
- `models.<profile>.base_url`
- `models.<profile>.temperature`
- `models.<profile>.top_p`
- `models.<profile>.reasoning_effort`
- `models.<profile>.max_completion_tokens`
- `models.<profile>.timeout_seconds`
- `models.<profile>.rpm`

`max_retries`、`max_concurrency`、`word_count_tolerance_ratio`、`max_revision_rounds` 等键应填写合法数字或省略使用默认值。

## runtime

| key | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `runtime.output_mode` | `"output"` 或 `"writeback"` | `"output"` | `output` 表示输出到 `logs/<runtime_id>/outputs/chapters/`，不修改原章节目录；`writeback` 写回输入目录的目标章节号，若目标文件已存在会写入 `pending_writeback` 并等待用户处理后 resume。 |
| `runtime.save_full_prompts` | bool | `true` | 是否保存完整 prompt。开启后便于断点续接和排查，但日志可能包含小说正文、设定和审批聊天内容。 |

## 运行参数与 notes

WebUI 首页和 CLI `inklink run --execute` 支持在每次续写任务开始时设置输入目录、输出模式、章节范围、字数范围、修订轮数、自动批准和额外 notes。notes 是本次续写的补充约束，会和 notes 文件内容合并后写入模型输入；世界观、设定、人物关系、伏笔和文风仍会主要从已有章节推断。

首次运行会把解析后的运行参数保存到 `logs/<runtime_id>/artifacts/run_settings.json`，并同步写入 SQLite `runs.settings_json`。恢复同一 runtime 时，pipeline 优先复用这份持久化设置，因此不需要重新输入 notes、字数范围或结局续写区间；CLI 也可以只传 `--resume-runtime-id`。本次恢复命令里的 `--auto-approve` 可以作为继续通过后续审批点的临时开关，不会改写已保存的创作参数。

运行参数支持两种续写模式：

- `fixed`：固定生成 `chapter_count` 章。
- `to_ending`：用户提供 `ending_min_chapters` 和 `ending_max_chapters`，模型必须在区间内规划到完整结局，最后一章标记为最终章。章节计划数量不足、超出范围或标题重复会被拒绝。

## writing

当前 pipeline 已使用字数区间、自动修订轮数、分段区间摘要和轻量检索预算裁剪。检索预算当前以字符数近似 token 预算，用确定性优先级裁剪故事状态、结构化索引和原文片段；后续可替换为真实 tokenizer。

| key | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `writing.word_count_tolerance_ratio` | float，`0..1` | `0.1` | 中文字数容差比例。当前中文字数统计只计入 Python `unicodedata` 识别到的 CJK 统一表意文字及扩展区；标点、空格、换行、阿拉伯数字、拉丁字母和未分配码位不计入。 |
| `writing.retrieval_token_budget` | 正整数或空字符串 | `None` | 留空不裁剪；填写正整数后，pipeline 会按确定性优先级裁剪故事状态、伏笔、人物、世界观、近期章节摘要和结构化索引命中的原文片段。当前预算单位是字符近似值。 |
| `writing.max_revision_rounds` | 非负整数 | `3` | 当前 pipeline 使用该值控制单章最大自动修订轮数。确定性检查失败会直接进入 revision，检查通过后才运行 LLM review。 |
| `writing.range_summary_chapter_span` | 正整数 | `50` | 每多少章生成一个 `summarize_range[*]` 区间摘要；初次导入和运行中生成章节共用该阈值逻辑。 |
| `writing.story_merge_recent_chapters` | 非负整数 | `20` | `merge_story_state` 额外注入最近多少章单章分析；较早内容通过结构化索引和区间摘要进入上下文。 |
| `writing.refresh_range_summary_after_generation` | bool | `true` | 连续生成多章时，生成章节集成后刷新所在区间摘要。 |
| `writing.banned_generation_terms` | string 数组 | `["墨连", "Inklink", "水印"]` | 大纲、章节计划、场景计划、正文草稿和修订产物中禁止出现的词。默认避免把工具名或水印写进小说内容。 |

## approvals

当前 CLI/WebUI pipeline 支持运行时 `--auto-approve` 自动接受全部审批点；也支持按配置字段自动接受某类审批。未自动接受的大纲、章节计划、场景计划和自审失败会暂停 run，并等待 `workflow approve` 或 WebUI 审批区处理。

| key | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `approvals.auto_approve_outline` | bool | `false` | 自动批准大纲 artifact。 |
| `approvals.auto_approve_chapter_plan` | bool | `false` | 自动批准章节计划 artifact。 |
| `approvals.auto_approve_scene_plan` | bool | `false` | 自动批准场景计划 artifact。 |
| `approvals.auto_approve_review_failure` | bool | `false` | 谨慎开启：若确定性检查或自审修订达到上限仍未通过，自动批准会让质量不足或不满足合同的章节继续进入输出流程。 |

## cold_start

| key | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `cold_start.enabled` | bool | `false` | 是否启用历史章节冷启动分析。开启后 pipeline 会把较早章节标记为 `shallow` 分析，最近章节标记为 `deep`。 |
| `cold_start.recent_chapters_to_deep_analyze` | 非负整数 | `50` | 冷启动开启时，最近多少章按 deep 分析；更早章节按 shallow 分析。shallow/deep 差异通过 prompt 输入传给模型，结构化字段仍要求尽量完整；若 shallow 伏笔缺少回收窗口等关键字段，pipeline 会自动 deep 重析来源章。 |

## models

`models` 是 profile 映射，必须包含 `models.default`。每个任务可以映射到任意 profile；未映射任务回退到 `default`。

| key | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `models.<profile>.api` | `"responses"` 或 `"chat_completions"` | `"responses"` | 选择 OpenAI Responses API 或 Chat Completions API 适配器。 |
| `models.<profile>.model` | string | 必填 | 模型名由用户配置，不在代码中硬编码。 |
| `models.<profile>.api_key` | string 或空字符串 | `None` | 可直接写明文 API key；非空时优先于 `api_key_env`。请确认本地 `config.toml` 不会提交。 |
| `models.<profile>.api_key_env` | string | `"OPENAI_API_KEY"` | `api_key` 留空时读取 API key 的环境变量名。 |
| `models.<profile>.base_url` | string 或空字符串 | `None` | 留空使用 OpenAI SDK 默认地址；OpenAI-compatible 服务可填写自定义 `/v1` base URL。 |
| `models.<profile>.temperature` | float，`0..2`，或空字符串 | `None` | 非空时传给请求。 |
| `models.<profile>.top_p` | float，`0..1`，或空字符串 | `None` | 非空时传给请求。 |
| `models.<profile>.reasoning_effort` | `none/minimal/low/medium/high/xhigh` 或空字符串 | `None` | Responses 适配器会转换为 `reasoning={"effort": ...}`；Chat Completions 适配器使用 `reasoning_effort`。具体模型是否支持由服务端决定。 |
| `models.<profile>.max_completion_tokens` | 正整数或空字符串 | `None` | Responses 适配器使用 `max_output_tokens`；Chat Completions 适配器使用 `max_completion_tokens`。 |
| `models.<profile>.timeout_seconds` | 正数或空字符串 | `None` | 非空时作为 SDK client 的 `timeout`。 |
| `models.<profile>.max_retries` | 非负整数 | `2` | SDK 请求失败后的最大重试次数。 |
| `models.<profile>.rpm` | 正整数或空字符串 | `None` | 每分钟请求数限制；留空不启用 rpm 限制。 |
| `models.<profile>.max_concurrency` | 正整数 | `1` | 同一 profile 最大并发调用数。 |

## tasks

`tasks` 把逻辑任务映射到模型 profile。映射值必须指向已配置的 `models.<profile>`；未映射任务使用 `default`。

当前设计任务名包括：

- `chapter_extraction`
- `worldbuilding_extraction`
- `range_summary`
- `story_merge`
- `outline_planning`
- `outline_chat`
- `chapter_planning`
- `chapter_plan_chat`
- `scene_planning`
- `scene_plan_chat`
- `drafting`
- `review`
- `revision`

示例：

```toml
[models.default]
api = "responses"
model = "gpt-5.5"
api_key = ""
api_key_env = "OPENAI_API_KEY"

[models.draft]
api = "chat_completions"
model = "gpt-compatible-model"
api_key = "sk-local-compatible-key"
api_key_env = "COMPAT_API_KEY"
base_url = "https://example.test/v1"

[tasks]
drafting = "draft"
review = "default"
```

在这个例子中，`drafting` 使用 `models.draft`，`review` 使用 `models.default`，未写出的任务也回退到 `models.default`。

## usage 统计

每次真实 LLM 调用完成后，Inklink 会把归一化后的 usage 写入运行目录的 SQLite 状态库，并纳入本次 `PipelineSummary`。`uv run inklink run --execute ...` 结束时会展示：

- `usage_total`
- `usage_by_profile`
- `usage_by_model`
- `usage_by_task`

`uv run inklink workflow stats <runtime_id>` 会读取已持久化记录，并按 `profile/model/task` 输出同一组 token 指标。基础字段始终包含 `calls`、`input`、`output`、`total`。

Responses 和 Chat Completions 的 usage 字段会统一归一为 `input_tokens`、`output_tokens`、`total_tokens`、`cached_tokens`、`cache_read_tokens`、`cache_write_tokens`、`reasoning_tokens`。其中 `cached`、`cache_read`、`cache_write`、`reasoning` 只有在服务端 usage 中实际存在对应值时才显示；缺失字段不会被臆造为 0，服务端明确返回 0 时会保留并显示。
