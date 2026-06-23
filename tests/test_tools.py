from __future__ import annotations

import pytest

from inklink.tools.registry import ToolRegistry

EXPECTED_TOOL_NAMES = {
    "record_chapter_analysis",
    "record_range_summary",
    "merge_story_state",
    "propose_outline",
    "propose_chapter_plan",
    "propose_scene_plan",
    "submit_scene_draft",
    "submit_chapter_review",
    "submit_revision",
}


def test_default_registry_exposes_chat_function_schema() -> None:
    registry = ToolRegistry.default()

    schemas = registry.chat_tool_schemas()

    schema = next(item for item in schemas if item["function"]["name"] == "record_chapter_analysis")
    function = schema["function"]
    parameters = function["parameters"]
    properties = parameters["properties"]
    assert schema["type"] == "function"
    assert function["description"]
    assert function["strict"] is True
    assert parameters["additionalProperties"] is False
    assert properties["chapter_number"]["type"] == "integer"
    assert properties["summary"]["type"] == "string"
    assert set(parameters["required"]) == {"chapter_number", "summary"}


def test_default_registry_exposes_all_pipeline_tools_with_strict_schemas() -> None:
    registry = ToolRegistry.default()

    chat_schemas = registry.chat_tool_schemas()
    responses_schemas = registry.responses_tool_schemas()

    assert {schema["function"]["name"] for schema in chat_schemas} == EXPECTED_TOOL_NAMES
    assert {schema["name"] for schema in responses_schemas} == EXPECTED_TOOL_NAMES
    for schema in chat_schemas:
        function = schema["function"]
        parameters = function["parameters"]
        assert function["strict"] is True
        assert parameters["type"] == "object"
        assert parameters["additionalProperties"] is False
    for schema in responses_schemas:
        parameters = schema["parameters"]
        assert schema["strict"] is True
        assert parameters["type"] == "object"
        assert parameters["additionalProperties"] is False


def test_default_registry_exposes_responses_function_schema() -> None:
    registry = ToolRegistry.default()

    schemas = registry.responses_tool_schemas()

    schema = next(item for item in schemas if item["name"] == "record_chapter_analysis")
    parameters = schema["parameters"]
    properties = parameters["properties"]
    assert schema["type"] == "function"
    assert schema["description"]
    assert schema["strict"] is True
    assert "function" not in schema
    assert parameters["additionalProperties"] is False
    assert properties["chapter_number"]["type"] == "integer"
    assert properties["summary"]["type"] == "string"
    assert set(parameters["required"]) == {"chapter_number", "summary"}


def test_openai_tool_schemas_remains_chat_schema() -> None:
    registry = ToolRegistry.default()

    assert registry.openai_tool_schemas() == registry.chat_tool_schemas()


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
