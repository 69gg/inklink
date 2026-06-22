from __future__ import annotations

import pytest

from inklink.tools.registry import ToolRegistry


def test_default_registry_exposes_openai_function_schema() -> None:
    registry = ToolRegistry.default()

    schemas = registry.openai_tool_schemas()

    schema = next(item for item in schemas if item["function"]["name"] == "record_chapter_analysis")
    function = schema["function"]
    parameters = function["parameters"]
    properties = parameters["properties"]
    assert schema["type"] == "function"
    assert function["description"]
    assert properties["chapter_number"]["type"] == "integer"
    assert properties["summary"]["type"] == "string"
    assert set(parameters["required"]) == {"chapter_number", "summary"}


def test_dispatch_calls_known_tool_handler() -> None:
    registry = ToolRegistry.default()

    result = registry.dispatch(
        "record_chapter_analysis",
        {"chapter_number": 1, "summary": "opening"},
    )

    assert result == {"ok": True}


@pytest.mark.parametrize(
    "arguments",
    [
        {"summary": "missing chapter number"},
        {"chapter_number": 1},
        {"chapter_number": "1", "summary": "wrong type"},
        {"chapter_number": True, "summary": "bool is not an integer"},
        {"chapter_number": 1, "summary": 123},
    ],
)
def test_record_chapter_analysis_validates_required_arguments(
    arguments: dict[str, object],
) -> None:
    registry = ToolRegistry.default()

    result = registry.dispatch("record_chapter_analysis", arguments)

    assert result["ok"] is False
    assert isinstance(result["error"], str)


def test_dispatch_unknown_tool_raises_clear_error() -> None:
    registry = ToolRegistry.default()

    with pytest.raises(KeyError, match="unknown tool: missing_tool"):
        registry.dispatch("missing_tool", {})
