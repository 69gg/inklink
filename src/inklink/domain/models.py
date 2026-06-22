from __future__ import annotations

from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class PlotThreadStatus(StrEnum):
    SEEDED = "seeded"
    REINFORCED = "reinforced"
    DUE = "due"
    RESOLVED = "resolved"
    ABANDONED = "abandoned"


class ChapterContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chapter_number: int = Field(gt=0)
    title: str = Field(min_length=1)
    min_chars: int = Field(ge=0)
    max_chars: int = Field(ge=0)
    required_characters: list[str] = Field(default_factory=list)
    required_keywords: list[str] = Field(default_factory=list)
    scene_ids: list[str] = Field(default_factory=list)

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        return _validate_non_blank_string(value)

    @field_validator("required_characters", "required_keywords", "scene_ids")
    @classmethod
    def validate_non_blank_items(cls, values: list[str]) -> list[str]:
        return _validate_non_blank_list(values)

    @model_validator(mode="after")
    def validate_char_range(self) -> Self:
        if self.min_chars > self.max_chars:
            raise ValueError("min_chars must be less than or equal to max_chars")
        return self


class DraftChapter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chapter_number: int = Field(gt=0)
    title: str = Field(min_length=1)
    body: str

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        return _validate_non_blank_string(value)


class PlotThread(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    status: PlotThreadStatus
    source_chapter: int = Field(gt=0)
    due_chapter: int | None = Field(default=None, gt=0)
    related_keywords: list[str] = Field(default_factory=list)

    @field_validator("thread_id", "description")
    @classmethod
    def validate_identity_text(cls, value: str) -> str:
        return _validate_non_blank_string(value)

    @field_validator("related_keywords")
    @classmethod
    def validate_related_keywords(cls, values: list[str]) -> list[str]:
        return _validate_non_blank_list(values)


CheckIssueCode = Literal[
    "chapter_number_mismatch",
    "word_count_out_of_range",
    "required_character_missing",
    "required_keyword_missing",
    "plot_thread_repeated_resolution",
]
CheckSeverity = Literal["error", "warning"]


class CheckIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: CheckIssueCode
    message: str
    severity: CheckSeverity = "error"


class CheckReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    passed: bool
    issues: list[CheckIssue] = Field(default_factory=list)


def _validate_non_blank_string(value: str) -> str:
    if not value.strip():
        raise ValueError("value must not be blank")
    return value


def _validate_non_blank_list(values: list[str]) -> list[str]:
    for value in values:
        _validate_non_blank_string(value)
    return values
