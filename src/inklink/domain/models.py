from __future__ import annotations

from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


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


class PlotThread(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    status: PlotThreadStatus
    source_chapter: int = Field(gt=0)
    due_chapter: int | None = Field(default=None, gt=0)
    related_keywords: list[str] = Field(default_factory=list)


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
