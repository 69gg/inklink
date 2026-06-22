from __future__ import annotations

from types import SimpleNamespace

import pytest

from inklink.config import ModelProfile
from inklink.llm.openai_client import (
    ChatCompletionsAdapter,
    LLMMessage,
    LLMRequest,
    ResponsesAdapter,
    make_async_openai,
)
from inklink.llm.types import LLMToolCall, NormalizedUsage

RESPONSES_TOOL_SCHEMA: dict[str, object] = {
    "type": "function",
    "name": "record_chapter_analysis",
    "description": "Record extracted analysis.",
    "parameters": {"type": "object"},
    "strict": True,
}

CHAT_TOOL_SCHEMA: dict[str, object] = {
    "type": "function",
    "function": {
        "name": "record_chapter_analysis",
        "description": "Record extracted analysis.",
        "parameters": {"type": "object"},
    },
}


class FakeCreateEndpoint:
    def __init__(self, response: object) -> None:
        self.response = response
        self.kwargs: dict[str, object] | None = None

    async def create(self, **kwargs: object) -> object:
        self.kwargs = kwargs
        return self.response


class FakeResponsesClient:
    def __init__(self, response: object) -> None:
        self.responses = FakeCreateEndpoint(response)


class FakeChatClient:
    def __init__(self, response: object) -> None:
        self.chat = SimpleNamespace(
            completions=FakeCreateEndpoint(response),
        )


class FunctionObject:
    def __init__(self, name: str, arguments: str) -> None:
        self.name = name
        self.arguments = arguments


class ToolCallObject:
    def __init__(self, call_id: str, name: str, arguments: str) -> None:
        self.id = call_id
        self.function = FunctionObject(name, arguments)


def test_make_async_openai_uses_profile_client_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeAsyncOpenAI:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setattr("inklink.llm.openai_client.AsyncOpenAI", FakeAsyncOpenAI)
    profile = ModelProfile(
        api="responses",
        model="gpt-test",
        base_url="https://example.test/v1",
        timeout_seconds=30,
        max_retries=4,
    )

    client = make_async_openai(profile, api_key="sk-test")

    assert isinstance(client, FakeAsyncOpenAI)
    assert captured == {
        "api_key": "sk-test",
        "base_url": "https://example.test/v1",
        "timeout": 30.0,
        "max_retries": 4,
    }


def test_make_async_openai_omits_none_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeAsyncOpenAI:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setattr("inklink.llm.openai_client.AsyncOpenAI", FakeAsyncOpenAI)
    profile = ModelProfile(api="responses", model="gpt-test")

    make_async_openai(profile, api_key=None)

    assert "api_key" not in captured
    assert captured == {"max_retries": 2}


async def test_responses_adapter_creates_request_and_normalizes_response() -> None:
    response = SimpleNamespace(
        output_text="analysis complete",
        usage=SimpleNamespace(
            input_tokens=11,
            output_tokens=7,
            total_tokens=18,
            output_tokens_details=SimpleNamespace(reasoning_tokens=3),
        ),
        _request_id="req-responses",
        output=[
            {
                "type": "function_call",
                "call_id": "call-dict",
                "name": "record_chapter_analysis",
                "arguments": '{"chapter_number":1,"summary":"opening"}',
            },
            SimpleNamespace(
                type="function_call",
                call_id="call-object",
                name="record_chapter_analysis",
                arguments='{"chapter_number":2,"summary":"middle"}',
            ),
            {"type": "message", "content": "ignored"},
        ],
    )
    client = FakeResponsesClient(response)
    profile = ModelProfile(
        api="responses",
        model="gpt-test",
        temperature=0.2,
        top_p=0.9,
        reasoning_effort="low",
        max_completion_tokens=200,
    )
    request = LLMRequest(
        instructions="Extract chapter information.",
        input_text="Chapter one text.",
        tools=[RESPONSES_TOOL_SCHEMA],
        tool_choice="auto",
        previous_response_id="resp-previous",
    )

    result = await ResponsesAdapter(client, profile).create(request)

    assert client.responses.kwargs == {
        "model": "gpt-test",
        "instructions": "Extract chapter information.",
        "input": "Chapter one text.",
        "tools": request.tools,
        "tool_choice": "auto",
        "previous_response_id": "resp-previous",
        "temperature": 0.2,
        "top_p": 0.9,
        "reasoning": {"effort": "low"},
        "max_output_tokens": 200,
    }
    assert result.text == "analysis complete"
    assert result.usage == NormalizedUsage(
        input_tokens=11,
        output_tokens=7,
        total_tokens=18,
        reasoning_tokens=3,
    )
    assert result.request_id == "req-responses"
    assert result.tool_calls == [
        LLMToolCall(
            call_id="call-dict",
            name="record_chapter_analysis",
            arguments_json='{"chapter_number":1,"summary":"opening"}',
        ),
        LLMToolCall(
            call_id="call-object",
            name="record_chapter_analysis",
            arguments_json='{"chapter_number":2,"summary":"middle"}',
        ),
    ]


async def test_responses_adapter_uses_empty_text_and_request_id_fallback() -> None:
    response = SimpleNamespace(
        usage=None,
        request_id="req-fallback",
        output=[],
    )
    client = FakeResponsesClient(response)
    profile = ModelProfile(api="responses", model="gpt-test")

    result = await ResponsesAdapter(client, profile).create(
        LLMRequest(input_text="chapter text"),
    )

    assert result.text == ""
    assert result.request_id == "req-fallback"
    assert client.responses.kwargs == {
        "model": "gpt-test",
        "instructions": None,
        "input": "chapter text",
        "tools": [],
    }


async def test_chat_adapter_creates_request_and_normalizes_response() -> None:
    message = SimpleNamespace(
        content="summary complete",
        tool_calls=[
            ToolCallObject(
                "chat-call-object",
                "record_chapter_analysis",
                '{"chapter_number":1,"summary":"opening"}',
            ),
            {
                "call_id": "chat-call-dict",
                "function": {
                    "name": "record_chapter_analysis",
                    "arguments": '{"chapter_number":2,"summary":"middle"}',
                },
            },
        ],
    )
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=message)],
        usage={
            "prompt_tokens": 13,
            "completion_tokens": 5,
            "total_tokens": 18,
            "completion_tokens_details": {"reasoning_tokens": 2},
        },
        _request_id="req-chat",
    )
    client = FakeChatClient(response)
    profile = ModelProfile(
        api="chat_completions",
        model="gpt-chat",
        temperature=0.1,
        top_p=0.8,
        reasoning_effort="medium",
        max_completion_tokens=100,
    )
    request = LLMRequest(
        instructions="You are an analysis assistant.",
        messages=[
            LLMMessage(role="user", content="Chapter one text."),
            LLMMessage(role="assistant", content="Existing summary."),
        ],
        input_text="unused fallback",
        tools=[CHAT_TOOL_SCHEMA],
        tool_choice={"type": "function", "function": {"name": "record_chapter_analysis"}},
    )

    result = await ChatCompletionsAdapter(client, profile).create(request)

    assert client.chat.completions.kwargs == {
        "model": "gpt-chat",
        "messages": [
            {"role": "system", "content": "You are an analysis assistant."},
            {"role": "user", "content": "Chapter one text."},
            {"role": "assistant", "content": "Existing summary."},
        ],
        "tools": request.tools,
        "tool_choice": {"type": "function", "function": {"name": "record_chapter_analysis"}},
        "temperature": 0.1,
        "top_p": 0.8,
        "reasoning_effort": "medium",
        "max_completion_tokens": 100,
    }
    assert result.text == "summary complete"
    assert result.usage == NormalizedUsage(
        input_tokens=13,
        output_tokens=5,
        total_tokens=18,
        reasoning_tokens=2,
    )
    assert result.request_id == "req-chat"
    assert result.tool_calls == [
        LLMToolCall(
            call_id="chat-call-object",
            name="record_chapter_analysis",
            arguments_json='{"chapter_number":1,"summary":"opening"}',
        ),
        LLMToolCall(
            call_id="chat-call-dict",
            name="record_chapter_analysis",
            arguments_json='{"chapter_number":2,"summary":"middle"}',
        ),
    ]


async def test_chat_adapter_uses_input_text_when_messages_are_empty() -> None:
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=None, tool_calls=[]))],
        usage=None,
        request_id="req-chat-fallback",
    )
    client = FakeChatClient(response)
    profile = ModelProfile(api="chat_completions", model="gpt-chat")

    result = await ChatCompletionsAdapter(client, profile).create(
        LLMRequest(input_text="chapter text"),
    )

    assert client.chat.completions.kwargs == {
        "model": "gpt-chat",
        "messages": [{"role": "user", "content": "chapter text"}],
        "tools": [],
    }
    assert result.text == ""
    assert result.request_id == "req-chat-fallback"
