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
| `runtime.output_mode` | `"output"` 或 `"writeback"` | `"output"` | `output` 表示输出到 `logs/<runtime_id>/outputs/chapters/`，不修改原章节目录；`writeback` 写回输入目录的目标章节号，若目标文件已存在会拒绝覆盖。 |
| `runtime.save_full_prompts` | bool | `true` | 是否保存完整 prompt。开启后便于断点续接和排查，但日志可能包含小说正文、设定和审批聊天内容。 |

## writing

当前 pipeline 已使用字数区间、自动修订轮数和轻量检索预算裁剪。检索预算当前以字符数近似 token 预算，用确定性优先级裁剪注入上下文；后续可替换为真实 tokenizer 与更完整检索层。

| key | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `writing.word_count_tolerance_ratio` | float，`0..1` | `0.1` | 中文字数容差比例。当前中文字数统计只计入 Python `unicodedata` 识别到的 CJK 统一表意文字及扩展区；标点、空格、换行、阿拉伯数字、拉丁字母和未分配码位不计入。 |
| `writing.retrieval_token_budget` | 正整数或空字符串 | `None` | 留空不裁剪；填写正整数后，pipeline 会按确定性优先级裁剪故事状态、伏笔、人物、世界观和近期章节摘要。当前预算单位是字符近似值。 |
| `writing.max_revision_rounds` | 非负整数 | `3` | 当前 pipeline 使用该值控制单章最大自动修订轮数。确定性检查失败会直接进入 revision，检查通过后才运行 LLM review。 |

## approvals

当前 CLI/TUI pipeline 支持运行时 `--auto-approve` / `Ctrl+R` 自动接受规划节点并记录审批事件。以下配置字段已支持解析和校验；更细粒度的按审批类型自动批准、审批 UI 和审批聊天仍属于后续 workflow/TUI 集成目标。

| key | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `approvals.auto_approve_outline` | bool | `false` | 配置字段已支持；后续审批执行接入后用于自动批准大纲。 |
| `approvals.auto_approve_chapter_plan` | bool | `false` | 配置字段已支持；后续审批执行接入后用于自动批准章节计划。 |
| `approvals.auto_approve_scene_plan` | bool | `false` | 配置字段已支持；后续审批执行接入后用于自动批准场景计划。 |
| `approvals.auto_approve_review_failure` | bool | `false` | 配置字段已支持，但谨慎开启：后续审批执行接入后，若确定性检查或自审修订达到上限仍未通过，自动批准可能让质量不足或不满足合同的章节进入输出流程。 |

## cold_start

| key | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `cold_start.enabled` | bool | `false` | 是否启用历史章节冷启动分析。当前完整冷启动工作流属于后续集成目标。 |
| `cold_start.recent_chapters_to_deep_analyze` | 非负整数 | `50` | 冷启动开启时，最近多少章按 deep 分析；更早章节可按 shallow 分析。 |

## models

`models` 是 profile 映射，必须包含 `models.default`。每个任务可以映射到任意 profile；未映射任务回退到 `default`。

| key | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `models.<profile>.api` | `"responses"` 或 `"chat_completions"` | `"responses"` | 选择 OpenAI Responses API 或 Chat Completions API 适配器。 |
| `models.<profile>.model` | string | 必填 | 模型名由用户配置，不在代码中硬编码。 |
| `models.<profile>.api_key_env` | string | `"OPENAI_API_KEY"` | 读取 API key 的环境变量名。 |
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
- `scene_planning`
- `scene_chat`
- `drafting`
- `review`
- `revision`

示例：

```toml
[models.default]
api = "responses"
model = "gpt-5.5"
api_key_env = "OPENAI_API_KEY"

[models.draft]
api = "chat_completions"
model = "gpt-compatible-model"
api_key_env = "COMPAT_API_KEY"
base_url = "https://example.test/v1"

[tasks]
drafting = "draft"
review = "default"
```

在这个例子中，`drafting` 使用 `models.draft`，`review` 使用 `models.default`，未写出的任务也回退到 `models.default`。
