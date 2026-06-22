from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class NormalizedUsage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_tokens: int | None = Field(default=None, strict=True)
    output_tokens: int | None = Field(default=None, strict=True)
    total_tokens: int | None = Field(default=None, strict=True)
    reasoning_tokens: int | None = Field(default=None, strict=True)
    cached_tokens: int | None = Field(default=None, strict=True)
    cache_read_tokens: int | None = Field(default=None, strict=True)
    cache_write_tokens: int | None = Field(default=None, strict=True)


class LLMToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    call_id: str
    name: str
    arguments_json: str


class LLMResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    tool_calls: list[LLMToolCall] = Field(default_factory=list)
    usage: NormalizedUsage = Field(default_factory=NormalizedUsage)
    request_id: str | None = None
