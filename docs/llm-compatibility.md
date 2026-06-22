# LLM 兼容性

Inklink 使用 OpenAI Python SDK 的 `AsyncOpenAI`。`models.<profile>.base_url` 可配置 OpenAI-compatible 服务；留空时使用 SDK 默认地址。

LLM 层保留两个独立适配器，避免把 Responses API 与 Chat Completions API 的请求/响应形状混用。

## Responses API

Responses profile 使用：

```python
await client.responses.create(
    model=profile.model,
    instructions=request.instructions,
    input=request.input_text,
    tools=request.tools,
    ...
)
```

支持的当前请求字段包括：

- `instructions`
- `input`
- `tools`
- `tool_choice`
- 可选 `previous_response_id`

`tool_choice=None` 时不传该参数。`previous_response_id` 只在非空时传入。

Responses function tool 是扁平结构：

```json
{
  "type": "function",
  "name": "record_chapter_analysis",
  "description": "Record extracted analysis.",
  "parameters": {"type": "object"},
  "strict": true
}
```

工具调用从 response output item 中解析：item 的 `type` 必须是 `function_call`，并读取 `call_id`、`name` 和 `arguments`。

Responses 的结构化输出请求形状是 `text.format`。OpenAI Python SDK 也提供 `responses.parse(..., text_format=...)` 辅助形式；Inklink 首版领域产物走 function tool 架构，因此结构化领域数据通过 tool call 写入，而不是依赖普通文本 JSON。

## Chat Completions API

Chat profile 使用：

```python
await client.chat.completions.create(
    model=profile.model,
    messages=messages,
    tools=request.tools,
    ...
)
```

支持的当前请求字段包括：

- `messages`
- `tools`
- `tool_choice`
- `response_format` 是 Chat 结构化输出的 API 形状，但当前 Inklink 适配器首版未把它暴露到上层请求模型。

`tool_choice=None` 时不传该参数。`instructions` 会转换成第一条 `system` message；如果请求没有显式 messages，则 `input_text` 会作为一条 `user` message。

Chat Completions function tool 是 wrapped 结构：

```json
{
  "type": "function",
  "function": {
    "name": "record_chapter_analysis",
    "description": "Record extracted analysis.",
    "parameters": {"type": "object"},
    "strict": true
  }
}
```

工具调用从 `message.tool_calls` 解析，读取每个 tool call 的 `id` 或 `call_id`，以及 `function.name` 和 `function.arguments`。

Chat Completions 的结构化输出请求形状是 `response_format`。这不能与 Responses 的 `text.format` 互换。

## 请求参数映射

通用配置会按 API 类型映射成不同请求参数：

| 配置键 | Responses | Chat Completions |
| --- | --- | --- |
| `temperature` | `temperature` | `temperature` |
| `top_p` | `top_p` | `top_p` |
| `reasoning_effort` | `reasoning={"effort": value}` | `reasoning_effort=value` |
| `max_completion_tokens` | `max_output_tokens` | `max_completion_tokens` |

空字符串经 `load_config()` 归一为 `None` 后不会传给 SDK。`base_url`、`timeout_seconds` 和 `max_retries` 是 client 级参数，不属于单次请求 payload。

## usage 归一化

Inklink 把 Responses 与 Chat usage 归一为：

- `input_tokens`
- `output_tokens`
- `total_tokens`
- `reasoning_tokens`
- `cached_tokens`
- `cache_read_tokens`
- `cache_write_tokens`

只有原始 usage 中存在对应字段时才显示或保存这些 token 计数。适配层不会臆造 cache read/write/cached/reasoning tokens。

## 兼容性限制

- OpenAI-compatible 服务可能只实现 Chat Completions，或只支持部分参数；对这类服务应把不支持的可选参数留空。
- `reasoning_effort`、结构化输出、工具调用和 usage details 的支持情况由模型和服务端决定。
- Responses 与 Chat 的工具 schema、结构化输出字段和 tool call 响应形状不同，不能复用同一个原始 payload。
- 当前领域产物优先使用 function tool；后续如果接入 `text.format` 或 `response_format`，需要在两个适配器分别实现。

## 参考依据

Context7 `/openai/openai-python` 文档确认：

- Responses `FunctionToolParam` 使用扁平结构：`type/name/parameters/strict` 位于 tool 顶层。来源：`openai-python/src/openai/types/responses/function_tool_param.py`。
- Chat `chat.completions.create` 支持 `messages`、`tools`、`tool_choice`、`response_format`。来源：`openai-python/src/openai/resources/chat/completions/completions.py` 与 SDK 示例。
- Responses 结构化输出使用 `text.format` 请求形状，SDK parse 辅助使用 `text_format`。来源：`openai-python/examples/responses/structured_outputs.py` 和 SDK README。
