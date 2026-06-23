from __future__ import annotations

import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

ApiKind = Literal["responses", "chat_completions"]
ToolSchemaMode = Literal["strict", "compatible"]


class RuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    output_mode: Literal["output", "writeback"] = "output"
    save_full_prompts: bool = True


class WritingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    word_count_tolerance_ratio: float = Field(default=0.1, ge=0, le=1)
    retrieval_token_budget: int | None = Field(default=None, gt=0)
    max_revision_rounds: int = Field(default=3, ge=0)
    range_summary_chapter_span: int = Field(default=50, gt=0)
    story_merge_recent_chapters: int = Field(default=20, ge=0)
    refresh_range_summary_after_generation: bool = True
    banned_generation_terms: list[str] = Field(default_factory=lambda: ["墨连", "Inklink", "水印"])


class ApprovalConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    auto_approve_outline: bool = False
    auto_approve_chapter_plan: bool = False
    auto_approve_scene_plan: bool = False
    auto_approve_review_failure: bool = False


class ColdStartConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    recent_chapters_to_deep_analyze: int = Field(default=50, ge=0)


class ModelProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api: ApiKind = "responses"
    model: str
    api_key: str | None = None
    api_key_env: str = "OPENAI_API_KEY"
    base_url: str | None = None
    timeout_seconds: float | None = Field(default=None, gt=0)
    max_retries: int = Field(default=2, ge=0)
    rpm: int | None = Field(default=None, gt=0)
    max_concurrency: int = Field(default=1, gt=0)
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, ge=0, le=1)
    reasoning_effort: str | None = None
    max_completion_tokens: int | None = Field(default=None, gt=0)
    tool_schema_mode: ToolSchemaMode = "strict"


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    writing: WritingConfig = Field(default_factory=WritingConfig)
    approvals: ApprovalConfig = Field(default_factory=ApprovalConfig)
    cold_start: ColdStartConfig = Field(default_factory=ColdStartConfig)
    models: dict[str, ModelProfile]
    tasks: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_profile_references(self) -> Self:
        if "default" not in self.models:
            raise ValueError("models.default must be configured")
        missing = sorted({profile for profile in self.tasks.values() if profile not in self.models})
        if missing:
            names = ", ".join(missing)
            raise ValueError(f"task profiles are not configured: {names}")
        return self

    def profile_for_task(self, task: str) -> str:
        return self.tasks.get(task, "default")


def _none_if_blank(value: Any) -> Any:
    return None if value == "" else value


def _normalize_blanks(data: Any) -> Any:
    if isinstance(data, dict):
        return {key: _normalize_blanks(value) for key, value in data.items()}
    if isinstance(data, list):
        return [_normalize_blanks(value) for value in data]
    return _none_if_blank(data)


def load_config(path: Path) -> AppConfig:
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    return AppConfig.model_validate(_normalize_blanks(data))


def client_options_for_profile(profile: ModelProfile) -> dict[str, object]:
    optional: dict[str, object | None] = {
        "base_url": profile.base_url,
        "timeout": profile.timeout_seconds,
        "max_retries": profile.max_retries,
    }
    return {key: value for key, value in optional.items() if value is not None}


def api_key_for_profile(
    profile: ModelProfile,
    environment: Mapping[str, str | None],
) -> str | None:
    if profile.api_key:
        return profile.api_key
    return environment.get(profile.api_key_env)


def request_options_for_profile(profile: ModelProfile) -> dict[str, object]:
    optional: dict[str, object | None] = {
        "temperature": profile.temperature,
        "top_p": profile.top_p,
    }
    options = {key: value for key, value in optional.items() if value is not None}
    if profile.api == "responses":
        if profile.reasoning_effort is not None:
            options["reasoning"] = {"effort": profile.reasoning_effort}
        if profile.max_completion_tokens is not None:
            options["max_output_tokens"] = profile.max_completion_tokens
    else:
        if profile.reasoning_effort is not None:
            options["reasoning_effort"] = profile.reasoning_effort
        if profile.max_completion_tokens is not None:
            options["max_completion_tokens"] = profile.max_completion_tokens
    return options
