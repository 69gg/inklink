from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class PlotThreadStatus(StrEnum):
    SEEDED = "seeded"
    REINFORCED = "reinforced"
    DUE = "due"
    RESOLVED = "resolved"
    ABANDONED = "abandoned"


class ChapterContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chapter_number: int
    title: str
    min_chars: int
    max_chars: int
    required_characters: list[str] = Field(default_factory=list)
    required_keywords: list[str] = Field(default_factory=list)
    scene_ids: list[str] = Field(default_factory=list)


class DraftChapter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chapter_number: int
    title: str
    body: str


class PlotThread(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_id: str
    description: str
    status: PlotThreadStatus
    source_chapter: int
    due_chapter: int | None = None
    related_keywords: list[str] = Field(default_factory=list)


class CheckIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    severity: str = "error"


class CheckReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    passed: bool
    issues: list[CheckIssue] = Field(default_factory=list)
