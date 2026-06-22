from __future__ import annotations

from typing import Any, Protocol, cast

from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict, Field

from inklink.config import (
    ModelProfile,
    client_options_for_profile,
    request_options_for_profile,
)
from inklink.llm.types import LLMResponse, LLMToolCall
from inklink.llm.usage import normalize_usage


class LLMMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str
    content: str


class LLMRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instructions: str | None = None
    messages: list[LLMMessage] = Field(default_factory=list)
    input_text: str
    tools: list[dict[str, object]] = Field(default_factory=list)
    tool_choice: object | None = None
    previous_response_id: str | None = None


class _CreateEndpoint(Protocol):
    async def create(self, **kwargs: object) -> object: ...


class _ResponsesClient(Protocol):
    @property
    def responses(self) -> object: ...


class _ChatNamespace(Protocol):
    @property
    def completions(self) -> object: ...


class _ChatClient(Protocol):
    @property
    def chat(self) -> _ChatNamespace: ...


def make_async_openai(profile: ModelProfile, api_key: str | None) -> AsyncOpenAI:
    kwargs: dict[str, Any] = dict(client_options_for_profile(profile))
    if api_key is not None:
        kwargs["api_key"] = api_key
    return AsyncOpenAI(**kwargs)


class ResponsesAdapter:
    def __init__(self, client: _ResponsesClient, profile: ModelProfile) -> None:
        self._client = client
        self._profile = profile

    async def create(self, request: LLMRequest) -> LLMResponse:
        kwargs: dict[str, object] = {
            "model": self._profile.model,
            "instructions": request.instructions,
            "input": request.input_text,
            "tools": request.tools,
            **request_options_for_profile(self._profile),
        }
        if request.tool_choice is not None:
            kwargs["tool_choice"] = request.tool_choice
        if request.previous_response_id is not None:
            kwargs["previous_response_id"] = request.previous_response_id

        endpoint = cast(_CreateEndpoint, self._client.responses)
        response = await endpoint.create(**kwargs)
        return LLMResponse(
            text=_string_or_empty(_read(response, "output_text")),
            tool_calls=_parse_responses_tool_calls(_read(response, "output")),
            usage=normalize_usage(_read(response, "usage")),
            request_id=_request_id(response),
        )


class ChatCompletionsAdapter:
    def __init__(self, client: _ChatClient, profile: ModelProfile) -> None:
        self._client = client
        self._profile = profile

    async def create(self, request: LLMRequest) -> LLMResponse:
        kwargs: dict[str, object] = {
            "model": self._profile.model,
            "messages": _chat_messages(request),
            "tools": request.tools,
            **request_options_for_profile(self._profile),
        }
        if request.tool_choice is not None:
            kwargs["tool_choice"] = request.tool_choice
        endpoint = cast(_CreateEndpoint, self._client.chat.completions)
        response = await endpoint.create(**kwargs)
        message = _first_choice_message(response)
        return LLMResponse(
            text=_string_or_empty(_read(message, "content")),
            tool_calls=_parse_chat_tool_calls(_read(message, "tool_calls")),
            usage=normalize_usage(_read(response, "usage")),
            request_id=_request_id(response),
        )


def _chat_messages(request: LLMRequest) -> list[dict[str, str]]:
    messages = [{"role": message.role, "content": message.content} for message in request.messages]
    if not messages:
        messages = [{"role": "user", "content": request.input_text}]
    if request.instructions:
        return [{"role": "system", "content": request.instructions}, *messages]
    return messages


def _first_choice_message(response: object) -> object:
    choices = _read(response, "choices")
    if not isinstance(choices, list) or not choices:
        return None
    return _read(choices[0], "message")


def _parse_responses_tool_calls(output: object) -> list[LLMToolCall]:
    if not isinstance(output, list):
        return []
    tool_calls: list[LLMToolCall] = []
    for item in output:
        if _read(item, "type") != "function_call":
            continue
        call_id = _read(item, "call_id")
        name = _read(item, "name")
        arguments = _read(item, "arguments")
        tool_call = _make_tool_call(call_id=call_id, name=name, arguments=arguments)
        if tool_call is not None:
            tool_calls.append(tool_call)
    return tool_calls


def _parse_chat_tool_calls(raw_tool_calls: object) -> list[LLMToolCall]:
    if not isinstance(raw_tool_calls, list):
        return []
    tool_calls: list[LLMToolCall] = []
    for item in raw_tool_calls:
        function = _read(item, "function")
        call_id = _read(item, "id")
        if not isinstance(call_id, str):
            call_id = _read(item, "call_id")
        tool_call = _make_tool_call(
            call_id=call_id,
            name=_read(function, "name"),
            arguments=_read(function, "arguments"),
        )
        if tool_call is not None:
            tool_calls.append(tool_call)
    return tool_calls


def _make_tool_call(call_id: object, name: object, arguments: object) -> LLMToolCall | None:
    if not isinstance(call_id, str):
        return None
    if not isinstance(name, str):
        return None
    if not isinstance(arguments, str):
        return None
    return LLMToolCall(call_id=call_id, name=name, arguments_json=arguments)


def _request_id(response: object) -> str | None:
    request_id = _read(response, "_request_id")
    if isinstance(request_id, str):
        return request_id
    request_id = _read(response, "request_id")
    if isinstance(request_id, str):
        return request_id
    return None


def _string_or_empty(value: object) -> str:
    return value if isinstance(value, str) else ""


def _read(source: object, name: str) -> object:
    if source is None:
        return None
    if isinstance(source, dict):
        return source.get(name)
    return getattr(source, name, None)


__all__ = [
    "ChatCompletionsAdapter",
    "LLMMessage",
    "LLMRequest",
    "ResponsesAdapter",
    "make_async_openai",
]
