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

    chapter_number: int = Field(strict=True, gt=0)
    title: str = Field(min_length=1)
    summary: str = ""
    core_conflict: str = ""
    emotional_peak: str = ""
    ending_hook: str = ""
    min_chars: int = Field(strict=True, ge=0)
    max_chars: int = Field(strict=True, ge=0)
    required_characters: list[str] = Field(default_factory=list)
    required_keywords: list[str] = Field(default_factory=list)
    scene_ids: list[str] = Field(default_factory=list)
    forbidden: list[str] = Field(default_factory=list)

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        return _validate_non_blank_string(value)

    @field_validator("required_characters", "required_keywords", "scene_ids", "forbidden")
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

    chapter_number: int = Field(strict=True, gt=0)
    title: str = Field(min_length=1)
    body: str

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        return _validate_non_blank_string(value)


class AnalysisCharacterFact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity_id: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
    status: str = "unknown"
    traits: list[str] = Field(default_factory=list)
    relationships: list[str] = Field(default_factory=list)

    @field_validator("entity_id", "status")
    @classmethod
    def validate_identity_text(cls, value: str) -> str:
        return _validate_non_blank_string(value)

    @field_validator("aliases", "traits", "relationships")
    @classmethod
    def validate_items(cls, values: list[str]) -> list[str]:
        return _validate_non_blank_list(values)


class AnalysisWorldRuleFact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule_id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    related_entities: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    importance: int = Field(default=5, ge=1)

    @field_validator("rule_id", "description")
    @classmethod
    def validate_identity_text(cls, value: str) -> str:
        return _validate_non_blank_string(value)

    @field_validator("related_entities", "keywords")
    @classmethod
    def validate_items(cls, values: list[str]) -> list[str]:
        return _validate_non_blank_list(values)


class AnalysisPlotThreadFact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    status: PlotThreadStatus = PlotThreadStatus.SEEDED
    source_chapter: int | None = Field(default=None, strict=True, gt=0)
    due_chapter: int | None = Field(default=None, strict=True, gt=0)
    resolved_chapter: int | None = Field(default=None, strict=True, gt=0)
    reinforced_chapters: list[int] = Field(default_factory=list)
    related_entities: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    importance: int = Field(default=2, ge=1)

    @field_validator("thread_id", "description")
    @classmethod
    def validate_identity_text(cls, value: str) -> str:
        return _validate_non_blank_string(value)

    @field_validator("reinforced_chapters")
    @classmethod
    def validate_reinforced_chapters(cls, values: list[int]) -> list[int]:
        for value in values:
            if value <= 0:
                raise ValueError("reinforced_chapters must contain positive integers")
        return sorted(set(values))

    @field_validator("related_entities", "keywords")
    @classmethod
    def validate_items(cls, values: list[str]) -> list[str]:
        return _validate_non_blank_list(values)


class AnalysisEventFact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    related_entities: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    importance: int = Field(default=3, ge=1)

    @field_validator("event_id", "description")
    @classmethod
    def validate_identity_text(cls, value: str) -> str:
        return _validate_non_blank_string(value)

    @field_validator("related_entities", "keywords")
    @classmethod
    def validate_items(cls, values: list[str]) -> list[str]:
        return _validate_non_blank_list(values)


class ChapterAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chapter_number: int = Field(strict=True, gt=0)
    summary: str = Field(min_length=1)
    characters: list[str] = Field(default_factory=list)
    character_facts: list[AnalysisCharacterFact] = Field(default_factory=list)
    worldbuilding: list[str] = Field(default_factory=list)
    worldbuilding_facts: list[AnalysisWorldRuleFact] = Field(default_factory=list)
    plot_threads: list[str] = Field(default_factory=list)
    plot_thread_facts: list[AnalysisPlotThreadFact] = Field(default_factory=list)
    style_notes: list[str] = Field(default_factory=list)
    suspense: list[str] = Field(default_factory=list)
    event_facts: list[AnalysisEventFact] = Field(default_factory=list)

    @field_validator("summary")
    @classmethod
    def validate_summary(cls, value: str) -> str:
        return _validate_non_blank_string(value)

    @field_validator("characters", "worldbuilding", "plot_threads", "style_notes", "suspense")
    @classmethod
    def validate_analysis_items(cls, values: list[str]) -> list[str]:
        return _validate_non_blank_list(values)


class RangeSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_chapter: int = Field(strict=True, gt=0)
    end_chapter: int = Field(strict=True, gt=0)
    summary: str = Field(min_length=1)
    key_events: list[str] = Field(default_factory=list)
    active_characters: list[str] = Field(default_factory=list)
    open_threads: list[str] = Field(default_factory=list)

    @field_validator("summary")
    @classmethod
    def validate_summary(cls, value: str) -> str:
        return _validate_non_blank_string(value)

    @field_validator("key_events", "active_characters", "open_threads")
    @classmethod
    def validate_summary_items(cls, values: list[str]) -> list[str]:
        return _validate_non_blank_list(values)

    @model_validator(mode="after")
    def validate_chapter_range(self) -> Self:
        if self.start_chapter > self.end_chapter:
            raise ValueError("start_chapter must be less than or equal to end_chapter")
        return self


class StoryState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outline: str = Field(min_length=1)
    characters: list[str] = Field(default_factory=list)
    worldbuilding: list[str] = Field(default_factory=list)
    plot_threads: list[str] = Field(default_factory=list)
    style: str = ""

    @field_validator("outline")
    @classmethod
    def validate_outline(cls, value: str) -> str:
        return _validate_non_blank_string(value)

    @field_validator("characters", "worldbuilding", "plot_threads")
    @classmethod
    def validate_story_state_items(cls, values: list[str]) -> list[str]:
        return _validate_non_blank_list(values)


class OutlineProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outline: str = Field(min_length=1)
    notes: list[str] = Field(default_factory=list)

    @field_validator("outline")
    @classmethod
    def validate_outline(cls, value: str) -> str:
        return _validate_non_blank_string(value)

    @field_validator("notes")
    @classmethod
    def validate_notes(cls, values: list[str]) -> list[str]:
        return _validate_non_blank_list(values)


class OutlineUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outline: str = Field(min_length=1)
    change_summary: str = Field(min_length=1)
    notes: list[str] = Field(default_factory=list)

    @field_validator("outline", "change_summary")
    @classmethod
    def validate_outline_text(cls, value: str) -> str:
        return _validate_non_blank_string(value)

    @field_validator("notes")
    @classmethod
    def validate_notes(cls, values: list[str]) -> list[str]:
        return _validate_non_blank_list(values)


class ChapterPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chapters: list[ChapterContract] = Field(min_length=1)


class ChapterPlanUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chapters: list[ChapterContract] = Field(min_length=1)
    change_summary: str = Field(min_length=1)

    @field_validator("change_summary")
    @classmethod
    def validate_change_summary(cls, value: str) -> str:
        return _validate_non_blank_string(value)


class SceneContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scene_id: str = Field(min_length=1)
    goal: str = Field(min_length=1)
    characters: list[str] = Field(default_factory=list)
    required_keywords: list[str] = Field(default_factory=list)
    min_chars: int = Field(strict=True, ge=0)
    max_chars: int = Field(strict=True, ge=0)

    @field_validator("scene_id", "goal")
    @classmethod
    def validate_scene_text(cls, value: str) -> str:
        return _validate_non_blank_string(value)

    @field_validator("characters", "required_keywords")
    @classmethod
    def validate_scene_items(cls, values: list[str]) -> list[str]:
        return _validate_non_blank_list(values)

    @model_validator(mode="after")
    def validate_char_range(self) -> Self:
        if self.min_chars > self.max_chars:
            raise ValueError("min_chars must be less than or equal to max_chars")
        return self


class ScenePlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chapter_number: int = Field(strict=True, gt=0)
    scenes: list[SceneContract] = Field(min_length=1)


class ScenePlanUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chapter_number: int = Field(strict=True, gt=0)
    scenes: list[SceneContract] = Field(min_length=1)
    change_summary: str = Field(min_length=1)

    @field_validator("change_summary")
    @classmethod
    def validate_change_summary(cls, value: str) -> str:
        return _validate_non_blank_string(value)


class SceneDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scene_id: str = Field(min_length=1)
    text: str = Field(min_length=1)

    @field_validator("scene_id", "text")
    @classmethod
    def validate_scene_draft_text(cls, value: str) -> str:
        return _validate_non_blank_string(value)


class ChapterReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    passed: bool = Field(strict=True)
    issues: list[str] = Field(default_factory=list)
    resolved_thread_ids: list[str] = Field(default_factory=list)

    @field_validator("issues", "resolved_thread_ids")
    @classmethod
    def validate_review_items(cls, values: list[str]) -> list[str]:
        return _validate_non_blank_list(values)


class PlotThread(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    status: PlotThreadStatus
    source_chapter: int = Field(strict=True, gt=0)
    due_chapter: int | None = Field(default=None, strict=True, gt=0)
    resolved_chapter: int | None = Field(default=None, strict=True, gt=0)
    abandoned_chapter: int | None = Field(default=None, strict=True, gt=0)
    related_keywords: list[str] = Field(default_factory=list)

    @field_validator("thread_id", "description")
    @classmethod
    def validate_identity_text(cls, value: str) -> str:
        return _validate_non_blank_string(value)

    @field_validator("related_keywords")
    @classmethod
    def validate_related_keywords(cls, values: list[str]) -> list[str]:
        return _validate_non_blank_list(values)

    @model_validator(mode="after")
    def validate_lifecycle_chapters(self) -> Self:
        if self.resolved_chapter is not None and self.status != PlotThreadStatus.RESOLVED:
            raise ValueError("resolved_chapter requires resolved status")
        if self.abandoned_chapter is not None and self.status != PlotThreadStatus.ABANDONED:
            raise ValueError("abandoned_chapter requires abandoned status")
        return self


class WorldbuildingFacts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chapter_number: int = Field(strict=True, gt=0)
    facts: list[str] = Field(min_length=1)

    @field_validator("facts")
    @classmethod
    def validate_facts(cls, values: list[str]) -> list[str]:
        return _validate_non_blank_list(values)


class CharacterUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    status: str = Field(min_length=1)
    traits: list[str] = Field(default_factory=list)
    relationships: list[str] = Field(default_factory=list)
    source_chapter: int = Field(strict=True, gt=0)

    @field_validator("name", "status")
    @classmethod
    def validate_identity_text(cls, value: str) -> str:
        return _validate_non_blank_string(value)

    @field_validator("traits", "relationships")
    @classmethod
    def validate_items(cls, values: list[str]) -> list[str]:
        return _validate_non_blank_list(values)


class CharacterUpdates(BaseModel):
    model_config = ConfigDict(extra="forbid")

    updates: list[CharacterUpdate] = Field(min_length=1)


class PlotThreadUpdates(BaseModel):
    model_config = ConfigDict(extra="forbid")

    threads: list[PlotThread] = Field(min_length=1)


CheckIssueCode = Literal[
    "chapter_number_mismatch",
    "word_count_out_of_range",
    "scene_word_count_out_of_range",
    "scene_total_out_of_range",
    "required_character_missing",
    "required_keyword_missing",
    "forbidden_term_present",
    "plot_thread_repeated_resolution",
    "plot_thread_overdue",
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
