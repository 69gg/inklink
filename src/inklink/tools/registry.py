from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

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
                    parameters={
                        "type": "object",
                        "properties": {
                            "chapter_number": {"type": "integer"},
                            "summary": {"type": "string"},
                        },
                        "required": ["chapter_number", "summary"],
                        "additionalProperties": False,
                    },
                    handler=_record_chapter_analysis,
                )
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
    chapter_number = arguments.get("chapter_number")
    summary = arguments.get("summary")
    if not isinstance(chapter_number, int) or isinstance(chapter_number, bool):
        return {"ok": False, "error": "chapter_number must be an integer"}
    if not isinstance(summary, str):
        return {"ok": False, "error": "summary must be a string"}
    return {"ok": True}
