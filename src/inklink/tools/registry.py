from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from inklink.domain.models import (
    ChapterAnalysis,
    ChapterContract,
    ChapterReview,
    DraftChapter,
    OutlineProposal,
    RangeSummary,
    SceneDraft,
    ScenePlan,
    StoryState,
)

ToolHandler = Callable[[dict[str, object]], dict[str, object]]


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    parameters: dict[str, object]
    handler: ToolHandler
    strict: bool = True


class ToolRegistry:
    def __init__(self, tools: list[ToolDefinition]) -> None:
        self._tools = {tool.name: tool for tool in tools}

    @classmethod
    def default(cls) -> ToolRegistry:
        return cls(
            [
                ToolDefinition(
                    name="record_chapter_analysis",
                    description="Record extracted analysis for one chapter.",
                    parameters=_strict_schema(ChapterAnalysis),
                    handler=_record_chapter_analysis,
                ),
                ToolDefinition(
                    name="record_range_summary",
                    description="Record a range or volume summary.",
                    parameters=_strict_schema(RangeSummary),
                    handler=_accept_validated(RangeSummary),
                ),
                ToolDefinition(
                    name="merge_story_state",
                    description="Merge analyses into the current story state.",
                    parameters=_strict_schema(StoryState),
                    handler=_accept_validated(StoryState),
                ),
                ToolDefinition(
                    name="propose_outline",
                    description="Propose the continuation outline.",
                    parameters=_strict_schema(OutlineProposal),
                    handler=_accept_validated(OutlineProposal),
                ),
                ToolDefinition(
                    name="propose_chapter_plan",
                    description="Propose chapter contracts.",
                    parameters=_strict_schema(_ChapterPlanTool),
                    handler=_accept_validated(_ChapterPlanTool),
                ),
                ToolDefinition(
                    name="propose_scene_plan",
                    description="Propose scene contracts for one chapter.",
                    parameters=_strict_schema(ScenePlan),
                    handler=_accept_validated(ScenePlan),
                ),
                ToolDefinition(
                    name="submit_scene_draft",
                    description="Submit one scene draft.",
                    parameters=_strict_schema(SceneDraft),
                    handler=_accept_validated(SceneDraft),
                ),
                ToolDefinition(
                    name="submit_chapter_review",
                    description="Submit chapter review results.",
                    parameters=_strict_schema(ChapterReview),
                    handler=_accept_validated(ChapterReview),
                ),
                ToolDefinition(
                    name="submit_revision",
                    description="Submit a revised chapter.",
                    parameters=_strict_schema(DraftChapter),
                    handler=_accept_validated(DraftChapter),
                ),
            ]
        )

    def chat_tool_schemas(self) -> list[dict[str, object]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                    "strict": tool.strict,
                },
            }
            for tool in self._tools.values()
        ]

    def responses_tool_schemas(self) -> list[dict[str, object]]:
        return [
            {
                "type": "function",
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
                "strict": tool.strict,
            }
            for tool in self._tools.values()
        ]

    def openai_tool_schemas(self) -> list[dict[str, object]]:
        return self.chat_tool_schemas()

    def dispatch(self, name: str, arguments: dict[str, object]) -> dict[str, object]:
        try:
            tool = self._tools[name]
        except KeyError as exc:
            raise KeyError(f"unknown tool: {name}") from exc
        return tool.handler(arguments)


def _record_chapter_analysis(arguments: dict[str, object]) -> dict[str, object]:
    return _validate_tool_payload(ChapterAnalysis, arguments)


class _ChapterPlanTool(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chapters: list[ChapterContract] = Field(min_length=1)


def _accept_validated(model: type[BaseModel]) -> ToolHandler:
    def handler(arguments: dict[str, object]) -> dict[str, object]:
        return _validate_tool_payload(model, arguments)

    return handler


def _validate_tool_payload(
    model: type[BaseModel], arguments: dict[str, object]
) -> dict[str, object]:
    try:
        model.model_validate(arguments)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True}


def _strict_schema(model: type[BaseModel]) -> dict[str, object]:
    schema = model.model_json_schema()
    _close_object_schemas(schema)
    return schema


def _close_object_schemas(value: Any) -> None:
    if isinstance(value, dict):
        if value.get("type") == "object":
            value["additionalProperties"] = False
        for child in value.values():
            _close_object_schemas(child)
    elif isinstance(value, list):
        for child in value:
            _close_object_schemas(child)
