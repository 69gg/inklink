from __future__ import annotations

import json
from pathlib import Path

import pytest

from inklink.llm.types import NormalizedUsage
from inklink.workflow.pipeline import (
    GenerationOptions,
    InklinkPipeline,
    OpenAIToolLLM,
    ToolCallResult,
    ToolLLM,
    trim_retrieval_items,
)


class FakeToolLLM(ToolLLM):
    def __init__(self, draft_body: str | None = None) -> None:
        self.calls: list[tuple[str, str]] = []
        self.inputs: list[tuple[str, str, str]] = []
        self._draft_body = draft_body or "林秋推开门，看见青灯亮起，青灯映出旧钥匙。"

    async def call_tool(
        self,
        *,
        task_type: str,
        profile_name: str,
        tool_name: str,
        instructions: str,
        input_text: str,
        schema: dict[str, object],
    ) -> ToolCallResult:
        self.calls.append((task_type, tool_name))
        self.inputs.append((task_type, tool_name, input_text))
        payload = self._payload_for(tool_name, input_text)
        return ToolCallResult(
            payload=payload,
            usage=NormalizedUsage(input_tokens=10, output_tokens=5, total_tokens=15),
            request_id=f"req-{len(self.calls)}",
            profile_name=profile_name,
            model="fake-model",
            task_type=task_type,
        )

    def _payload_for(self, tool_name: str, input_text: str) -> dict[str, object]:
        if tool_name == "record_chapter_analysis":
            request = json.loads(input_text)
            chapter_number = int(request.get("chapter_number", 1))
            is_deep = request.get("depth") == "deep"
            return {
                "chapter_number": chapter_number,
                "summary": f"第{chapter_number}章分析",
                "characters": ["林秋"],
                "character_facts": [
                    {
                        "entity_id": "林秋",
                        "aliases": ["秋"],
                        "status": "active",
                        "traits": ["谨慎"],
                        "relationships": [],
                    }
                ],
                "worldbuilding": ["青灯会回应钥匙"],
                "worldbuilding_facts": [
                    {
                        "rule_id": f"world:{chapter_number}:lamp",
                        "description": "青灯会回应钥匙",
                        "related_entities": ["林秋"],
                        "keywords": ["青灯", "旧钥匙"],
                        "importance": 4,
                    }
                ],
                "plot_threads": ["旧钥匙的来历"],
                "plot_thread_facts": (
                    [
                        {
                            "thread_id": "thread:key-origin",
                            "description": "旧钥匙的来历",
                            "status": "seeded",
                            "source_chapter": 1,
                            "due_chapter": 5,
                            "resolved_chapter": None,
                            "reinforced_chapters": [chapter_number],
                            "related_entities": ["林秋"],
                            "keywords": ["旧钥匙"],
                            "importance": 2,
                        }
                    ]
                    if is_deep
                    else []
                ),
                "style_notes": ["克制、悬疑"],
                "suspense": ["门后是谁"],
                "event_facts": [
                    {
                        "event_id": f"event:{chapter_number}:door",
                        "description": "门后是谁",
                        "related_entities": ["林秋"],
                        "keywords": ["门后"],
                        "importance": 3,
                    }
                ],
            }
        if tool_name == "record_range_summary":
            return {
                "start_chapter": 1,
                "end_chapter": 2,
                "summary": "前两章建立青灯与旧钥匙。",
                "key_events": ["林秋得到旧钥匙"],
                "active_characters": ["林秋"],
                "open_threads": ["旧钥匙的来历"],
            }
        if tool_name == "merge_story_state":
            return {
                "outline": "林秋追查青灯与旧钥匙。",
                "characters": ["林秋"],
                "worldbuilding": ["青灯会回应钥匙"],
                "plot_threads": ["旧钥匙的来历"],
                "style": "克制、悬疑",
            }
        if tool_name == "propose_outline":
            return {
                "outline": "下一章林秋进入密室，确认旧钥匙能唤醒青灯。",
                "notes": ["保留门后悬念"],
            }
        if tool_name == "update_outline":
            return {
                "outline": "修改后的大纲",
                "change_summary": "强化冲突",
                "notes": ["保留门后悬念"],
            }
        if tool_name == "propose_chapter_plan":
            return {
                "chapters": [
                    {
                        "chapter_number": 3,
                        "title": "第三章 青灯",
                        "summary": "林秋进入密室并发现青灯。",
                        "min_chars": 8,
                        "max_chars": 80,
                        "required_characters": ["林秋"],
                        "required_keywords": ["青灯"],
                        "scene_ids": ["3-1", "3-2"],
                    }
                ]
            }
        if tool_name == "propose_scene_plan":
            return {
                "chapter_number": 3,
                "scenes": [
                    {
                        "scene_id": "3-1",
                        "goal": "进入密室",
                        "characters": ["林秋"],
                        "required_keywords": ["青灯"],
                        "min_chars": 4,
                        "max_chars": 40,
                    },
                    {
                        "scene_id": "3-2",
                        "goal": "发现钥匙反应",
                        "characters": ["林秋"],
                        "required_keywords": ["旧钥匙"],
                        "min_chars": 4,
                        "max_chars": 40,
                    },
                ],
            }
        if tool_name == "submit_scene_draft":
            scene_id = "3-2" if '"scene_id": "3-2"' in input_text else "3-1"
            text = "短" if self._draft_body == "短" else self._draft_body
            if scene_id == "3-2" and self._draft_body != "短":
                text = "林秋握紧旧钥匙，青灯忽然一亮。"
            return {"scene_id": scene_id, "text": text}
        if tool_name == "submit_chapter_review":
            return {"passed": True, "issues": [], "resolved_thread_ids": []}
        if tool_name == "submit_revision":
            return {
                "chapter_number": 3,
                "title": "第三章 青灯",
                "body": "林秋推开门，看见青灯亮起，旧钥匙也随之发烫。",
            }
        raise AssertionError(f"unexpected tool: {tool_name}")


class FailingReviewLLM(FakeToolLLM):
    async def call_tool(
        self,
        *,
        task_type: str,
        profile_name: str,
        tool_name: str,
        instructions: str,
        input_text: str,
        schema: dict[str, object],
    ) -> ToolCallResult:
        if tool_name == "submit_chapter_review":
            self.calls.append((task_type, tool_name))
            return ToolCallResult(
                payload={"passed": False, "issues": ["节奏太弱"], "resolved_thread_ids": []},
                usage=NormalizedUsage(input_tokens=10, output_tokens=5, total_tokens=15),
                request_id=f"req-{len(self.calls)}",
                profile_name=profile_name,
                model="fake-model",
                task_type=task_type,
            )
        return await super().call_tool(
            task_type=task_type,
            profile_name=profile_name,
            tool_name=tool_name,
            instructions=instructions,
            input_text=input_text,
            schema=schema,
        )


class DetailedUsageLLM(FakeToolLLM):
    async def call_tool(
        self,
        *,
        task_type: str,
        profile_name: str,
        tool_name: str,
        instructions: str,
        input_text: str,
        schema: dict[str, object],
    ) -> ToolCallResult:
        result = await super().call_tool(
            task_type=task_type,
            profile_name=profile_name,
            tool_name=tool_name,
            instructions=instructions,
            input_text=input_text,
            schema=schema,
        )
        return result.model_copy(
            update={
                "usage": NormalizedUsage(
                    input_tokens=10,
                    output_tokens=5,
                    total_tokens=15,
                    cached_tokens=4,
                    reasoning_tokens=2,
                    cache_read_tokens=3,
                    cache_write_tokens=1,
                )
            }
        )


def write_chapter(path: Path, title: str, body: str) -> None:
    path.write_text(f"title: {title}\n---\n{body}", encoding="utf-8")


def write_config(path: Path) -> None:
    path.write_text(
        """
[models.default]
api = "responses"
model = "fake-model"
api_key_env = "INKLINK_FAKE_KEY"

[tasks]
drafting = "default"
review = "default"
""",
        encoding="utf-8",
    )


def write_config_with_planning_auto_approval(path: Path) -> None:
    path.write_text(
        """
[approvals]
auto_approve_outline = true
auto_approve_chapter_plan = true
auto_approve_scene_plan = true
auto_approve_review_failure = false

[writing]
range_summary_chapter_span = 2

[models.default]
api = "responses"
model = "fake-model"
api_key_env = "INKLINK_FAKE_KEY"

[tasks]
drafting = "default"
review = "default"
""",
        encoding="utf-8",
    )


def write_config_with_cold_start(path: Path) -> None:
    path.write_text(
        """
[cold_start]
enabled = true
recent_chapters_to_deep_analyze = 1

[approvals]
auto_approve_outline = true
auto_approve_chapter_plan = true
auto_approve_scene_plan = true

[writing]
range_summary_chapter_span = 2
story_merge_recent_chapters = 1

[models.default]
api = "responses"
model = "fake-model"
api_key_env = "INKLINK_FAKE_KEY"

[tasks]
drafting = "default"
review = "default"
""",
        encoding="utf-8",
    )


def test_trim_retrieval_items_keeps_deterministic_priority_order() -> None:
    items = [
        {"priority": 5, "text": "一般人物信息"},
        {"priority": 1, "text": "当前章节合同"},
        {"priority": 2, "text": "即将回收的伏笔"},
        {"priority": 3, "text": "必须出场人物"},
    ]

    trimmed = trim_retrieval_items(items, budget_chars=18)

    assert [item["text"] for item in trimmed] == ["当前章节合同", "即将回收的伏笔"]


@pytest.mark.asyncio
async def test_openai_tool_llm_calls_expected_tool_with_chat_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeAdapter:
        def __init__(self, client: object, profile: object) -> None:
            captured["client"] = client
            captured["profile"] = profile

        async def create(self, request: object) -> object:
            captured["request"] = request
            return type(
                "FakeResponse",
                (),
                {
                    "tool_calls": [
                        type(
                            "FakeToolCall",
                            (),
                            {
                                "name": "record_chapter_analysis",
                                "arguments_json": (
                                    '{"chapter_number":1,"summary":"开篇",'
                                    '"characters":[],"worldbuilding":[],"plot_threads":[],'
                                    '"style_notes":[],"suspense":[]}'
                                ),
                            },
                        )()
                    ],
                    "usage": NormalizedUsage(total_tokens=7),
                    "request_id": "req-chat",
                },
            )()

    def fake_make_async_openai(profile: object, api_key: str | None) -> object:
        captured["api_key"] = api_key
        return object()

    monkeypatch.setattr("inklink.workflow.pipeline.ChatCompletionsAdapter", FakeAdapter)
    monkeypatch.setattr("inklink.workflow.pipeline.make_async_openai", fake_make_async_openai)
    from inklink.config import AppConfig, ModelProfile

    config = AppConfig(models={"default": ModelProfile(api="chat_completions", model="fake-chat")})
    llm = OpenAIToolLLM(config, {"default": "sk-test"})

    result = await llm.call_tool(
        task_type="chapter_extraction",
        profile_name="default",
        tool_name="record_chapter_analysis",
        instructions="Use the tool.",
        input_text="chapter",
        schema={
            "type": "function",
            "name": "record_chapter_analysis",
            "description": "desc",
            "parameters": {"type": "object", "additionalProperties": False},
            "strict": True,
        },
    )

    request = captured["request"]
    tool = request.tools[0]
    assert tool["type"] == "function"
    assert tool["function"]["name"] == "record_chapter_analysis"
    assert tool["function"]["strict"] is True
    assert request.tool_choice == {
        "type": "function",
        "function": {"name": "record_chapter_analysis"},
    }
    assert result.payload["summary"] == "开篇"


@pytest.mark.asyncio
async def test_pipeline_generates_chapter_outputs_and_stats(tmp_path: Path) -> None:
    novel = tmp_path / "novel"
    novel.mkdir()
    write_chapter(novel / "1.txt", "第一章", "林秋得到旧钥匙。")
    write_chapter(novel / "2.txt", "第二章", "青灯在雨夜亮起。")
    config = tmp_path / "config.toml"
    write_config(config)
    llm = FakeToolLLM()

    summary = await InklinkPipeline(llm=llm).run(
        GenerationOptions(
            input_dir=novel,
            config_path=config,
            log_root=tmp_path / "logs",
            chapter_count=1,
            min_chars=8,
            max_chars=80,
            auto_approve=True,
        )
    )

    output = summary.output_files[0]
    assert output.name == "3.txt"
    assert output.read_text(encoding="utf-8").startswith("title: 第三章 青灯\n---\n")
    assert summary.generated_chapters == [3]
    assert summary.stats.total_calls == len(llm.calls)
    assert summary.stats.by_model["fake-model"].total_tokens == 15 * len(llm.calls)
    assert (summary.log_dir / "artifacts" / "story_state.json").is_file()
    story_index = json.loads(
        (summary.log_dir / "artifacts" / "story_index.json").read_text(encoding="utf-8")
    )
    assert story_index["facts"]
    assert ("drafting", "submit_scene_draft") in llm.calls
    scene_call_indices = [
        index for index, call in enumerate(llm.calls) if call == ("drafting", "submit_scene_draft")
    ]
    assert scene_call_indices == sorted(scene_call_indices)
    planning_inputs = [
        input_text
        for task_type, tool_name, input_text in llm.inputs
        if task_type == "chapter_planning" and tool_name == "propose_chapter_plan"
    ]
    assert planning_inputs
    assert "原文片段" in planning_inputs[0]


@pytest.mark.asyncio
async def test_pipeline_persists_notes_and_reuses_settings_on_resume(tmp_path: Path) -> None:
    novel = tmp_path / "novel"
    novel.mkdir()
    write_chapter(novel / "1.txt", "第一章", "林秋得到旧钥匙。")
    write_chapter(novel / "2.txt", "第二章", "青灯在雨夜亮起。")
    config = tmp_path / "config.toml"
    write_config(config)
    notes_file = tmp_path / "notes.md"
    notes_file.write_text("文件 notes：青灯不能被直接解释。", encoding="utf-8")
    log_root = tmp_path / "logs"
    first_llm = FakeToolLLM()

    paused = await InklinkPipeline(llm=first_llm).run(
        GenerationOptions(
            input_dir=novel,
            config_path=config,
            log_root=log_root,
            chapter_count=1,
            min_chars=8,
            max_chars=80,
            auto_approve=False,
            notes="界面 notes：保持克制。",
            notes_path=notes_file,
        )
    )

    settings = json.loads(
        (paused.log_dir / "artifacts" / "run_settings.json").read_text(encoding="utf-8")
    )
    assert "文件 notes" in settings["notes"]
    assert "界面 notes" in settings["notes"]
    assert any("文件 notes" in input_text for _, _, input_text in first_llm.inputs)

    from inklink.workflow.service import WorkflowService

    with WorkflowService(log_root=log_root) as service:
        service.resume_run(paused.runtime_id)
        service.approve_artifact(
            approval_id="outline",
            approval_type="outline",
            artifact_id="outline",
            artifact_version=1,
        )

    second_llm = FakeToolLLM()
    resumed = await InklinkPipeline(llm=second_llm).run(
        GenerationOptions(
            log_root=log_root,
            runtime_id=paused.runtime_id,
            auto_approve=True,
        )
    )

    assert resumed.status == "completed"
    assert resumed.generated_chapters == [3]
    assert any("界面 notes" in input_text for _, _, input_text in second_llm.inputs)


@pytest.mark.asyncio
async def test_pipeline_cold_start_upgrades_shallow_thread_sources(tmp_path: Path) -> None:
    novel = tmp_path / "novel"
    novel.mkdir()
    write_chapter(novel / "1.txt", "第一章", "林秋得到旧钥匙。")
    write_chapter(novel / "2.txt", "第二章", "青灯在雨夜亮起。")
    config = tmp_path / "config.toml"
    write_config_with_cold_start(config)
    llm = FakeToolLLM()

    summary = await InklinkPipeline(llm=llm).run(
        GenerationOptions(
            input_dir=novel,
            config_path=config,
            log_root=tmp_path / "logs",
            chapter_count=1,
            min_chars=8,
            max_chars=80,
            auto_approve=True,
        )
    )

    extraction_inputs = [
        input_text
        for task_type, tool_name, input_text in llm.inputs
        if task_type == "chapter_extraction" and tool_name == "record_chapter_analysis"
    ]
    assert any(
        '"depth": "shallow"' in item and '"chapter_number": 1' in item for item in extraction_inputs
    )
    assert any(
        '"upgrade_from": "shallow"' in item and '"chapter_number": 1' in item
        for item in extraction_inputs
    )
    assert summary.status == "completed"


@pytest.mark.asyncio
async def test_pipeline_summary_aggregates_optional_usage_fields(tmp_path: Path) -> None:
    novel = tmp_path / "novel"
    novel.mkdir()
    write_chapter(novel / "1.txt", "第一章", "林秋得到旧钥匙。")
    write_chapter(novel / "2.txt", "第二章", "青灯在雨夜亮起。")
    config = tmp_path / "config.toml"
    write_config(config)
    llm = DetailedUsageLLM()

    summary = await InklinkPipeline(llm=llm).run(
        GenerationOptions(
            input_dir=novel,
            config_path=config,
            log_root=tmp_path / "logs",
            chapter_count=1,
            min_chars=8,
            max_chars=80,
            auto_approve=True,
        )
    )

    assert summary.stats.total.calls == len(llm.calls)
    assert summary.stats.total.cached_tokens == 4 * len(llm.calls)
    assert summary.stats.total.reasoning_tokens == 2 * len(llm.calls)
    assert summary.stats.total.cache_read_tokens == 3 * len(llm.calls)
    assert summary.stats.total.cache_write_tokens == len(llm.calls)
    assert summary.stats.by_profile["default"].cache_read_tokens == 3 * len(llm.calls)
    assert summary.stats.by_model["fake-model"].reasoning_tokens == 2 * len(llm.calls)
    assert summary.stats.by_task["drafting"].cache_write_tokens is not None
    run_summary = json.loads(
        (summary.log_dir / "artifacts" / "run_summary.json").read_text(encoding="utf-8")
    )
    assert run_summary["stats"]["total"]["cached_tokens"] == 4 * len(llm.calls)
    assert run_summary["stats"]["total"]["cache_read_tokens"] == 3 * len(llm.calls)
    assert run_summary["stats"]["total"]["reasoning_tokens"] == 2 * len(llm.calls)


@pytest.mark.asyncio
async def test_generated_range_summary_receives_all_window_generated_chapters(
    tmp_path: Path,
) -> None:
    novel = tmp_path / "novel"
    novel.mkdir()
    write_chapter(novel / "1.txt", "第一章", "林秋得到旧钥匙。")
    write_chapter(novel / "2.txt", "第二章", "青灯在雨夜亮起。")
    config = tmp_path / "config.toml"
    write_config_with_planning_auto_approval(config)
    llm = FakeToolLLM()

    summary = await InklinkPipeline(llm=llm).run(
        GenerationOptions(
            input_dir=novel,
            config_path=config,
            log_root=tmp_path / "logs",
            chapter_count=2,
            start_chapter=3,
            min_chars=8,
            max_chars=80,
            auto_approve=True,
        )
    )

    assert summary.generated_chapters == [3, 4]
    range_inputs = [
        json.loads(input_text)
        for task_type, tool_name, input_text in llm.inputs
        if task_type == "range_summary" and tool_name == "record_range_summary"
    ]
    generated_window_inputs = [
        payload
        for payload in range_inputs
        if payload["range"] == {"start_chapter": 3, "end_chapter": 4}
    ]

    assert generated_window_inputs
    last_payload = generated_window_inputs[-1]
    assert [chapter["number"] for chapter in last_payload["chapters"]] == [3, 4]
    assert [analysis["chapter_number"] for analysis in last_payload["analyses"]] == [3, 4]


@pytest.mark.asyncio
async def test_pipeline_pauses_at_outline_when_not_auto_approved(tmp_path: Path) -> None:
    novel = tmp_path / "novel"
    novel.mkdir()
    write_chapter(novel / "1.txt", "第一章", "林秋得到旧钥匙。")
    write_chapter(novel / "2.txt", "第二章", "青灯在雨夜亮起。")
    config = tmp_path / "config.toml"
    write_config(config)
    llm = FakeToolLLM()

    summary = await InklinkPipeline(llm=llm).run(
        GenerationOptions(
            input_dir=novel,
            config_path=config,
            log_root=tmp_path / "logs",
            chapter_count=1,
            min_chars=8,
            max_chars=80,
            auto_approve=False,
        )
    )

    assert summary.status == "waiting_approval"
    assert summary.waiting_approval_id == "outline"
    assert summary.generated_chapters == []
    assert ("chapter_planning", "propose_chapter_plan") not in llm.calls
    assert (
        "waiting_approval"
        in json.loads(
            (summary.log_dir / "artifacts" / "run_summary.json").read_text(encoding="utf-8")
        )["status"]
    )


@pytest.mark.asyncio
async def test_pipeline_resumes_after_outline_approval(tmp_path: Path) -> None:
    novel = tmp_path / "novel"
    novel.mkdir()
    write_chapter(novel / "1.txt", "第一章", "林秋得到旧钥匙。")
    write_chapter(novel / "2.txt", "第二章", "青灯在雨夜亮起。")
    config = tmp_path / "config.toml"
    write_config(config)
    log_root = tmp_path / "logs"
    first_llm = FakeToolLLM()

    paused = await InklinkPipeline(llm=first_llm).run(
        GenerationOptions(
            input_dir=novel,
            config_path=config,
            log_root=log_root,
            chapter_count=1,
            min_chars=8,
            max_chars=80,
            auto_approve=False,
        )
    )
    from inklink.workflow.service import WorkflowService

    with WorkflowService(log_root=log_root) as service:
        service.resume_run(paused.runtime_id)
        service.approve_artifact(
            approval_id="outline",
            approval_type="outline",
            artifact_id="outline",
            artifact_version=1,
        )

    second_llm = FakeToolLLM()
    resumed = await InklinkPipeline(llm=second_llm).run(
        GenerationOptions(
            input_dir=novel,
            config_path=config,
            log_root=log_root,
            runtime_id=paused.runtime_id,
            chapter_count=1,
            min_chars=8,
            max_chars=80,
            auto_approve=True,
        )
    )

    assert resumed.status == "completed"
    assert resumed.generated_chapters == [3]
    assert ("outline_planning", "propose_outline") not in second_llm.calls
    assert resumed.stats.total_calls == len(first_llm.calls) + len(second_llm.calls)
    assert resumed.stats.total.calls == resumed.stats.total_calls


@pytest.mark.asyncio
async def test_pipeline_resume_reuses_successful_calls_and_completed_output(
    tmp_path: Path,
) -> None:
    novel = tmp_path / "novel"
    novel.mkdir()
    write_chapter(novel / "1.txt", "第一章", "林秋得到旧钥匙。")
    write_chapter(novel / "2.txt", "第二章", "青灯在雨夜亮起。")
    config = tmp_path / "config.toml"
    write_config_with_planning_auto_approval(config)
    first_llm = FakeToolLLM()

    first = await InklinkPipeline(llm=first_llm).run(
        GenerationOptions(
            input_dir=novel,
            config_path=config,
            log_root=tmp_path / "logs",
            chapter_count=1,
            min_chars=8,
            max_chars=80,
            auto_approve=True,
        )
    )
    second_llm = FakeToolLLM()

    second = await InklinkPipeline(llm=second_llm).run(
        GenerationOptions(
            input_dir=novel,
            config_path=config,
            log_root=tmp_path / "logs",
            runtime_id=first.runtime_id,
            chapter_count=1,
            min_chars=8,
            max_chars=80,
            auto_approve=True,
        )
    )

    assert second.runtime_id == first.runtime_id
    assert second.output_files == first.output_files
    assert second_llm.calls == []
    assert second.stats.total_calls == first.stats.total_calls


@pytest.mark.asyncio
async def test_pipeline_resume_after_rewrite_regenerates_chapter(tmp_path: Path) -> None:
    novel = tmp_path / "novel"
    novel.mkdir()
    write_chapter(novel / "1.txt", "第一章", "林秋得到旧钥匙。")
    write_chapter(novel / "2.txt", "第二章", "青灯在雨夜亮起。")
    config = tmp_path / "config.toml"
    write_config(config)
    first_llm = FakeToolLLM()

    first = await InklinkPipeline(llm=first_llm).run(
        GenerationOptions(
            input_dir=novel,
            config_path=config,
            log_root=tmp_path / "logs",
            chapter_count=1,
            min_chars=8,
            max_chars=80,
            auto_approve=True,
        )
    )
    from inklink.workflow.service import WorkflowService

    with WorkflowService(log_root=tmp_path / "logs") as service:
        service.resume_run(first.runtime_id)
        service.rewrite_chapter(3)

    second_llm = FakeToolLLM(draft_body="林秋重新推门，青灯照见第二枚旧钥匙。")
    second = await InklinkPipeline(llm=second_llm).run(
        GenerationOptions(
            input_dir=novel,
            config_path=config,
            log_root=tmp_path / "logs",
            runtime_id=first.runtime_id,
            chapter_count=1,
            min_chars=8,
            max_chars=80,
            auto_approve=True,
        )
    )

    assert second.runtime_id == first.runtime_id
    assert second.generated_chapters == [3]
    assert ("drafting", "submit_scene_draft") in second_llm.calls
    assert "重新推门" in second.output_files[0].read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_pipeline_updates_approval_artifact_with_chat(tmp_path: Path) -> None:
    novel = tmp_path / "novel"
    novel.mkdir()
    write_chapter(novel / "1.txt", "第一章", "林秋得到旧钥匙。")
    config = tmp_path / "config.toml"
    write_config(config)
    log_root = tmp_path / "logs"
    from inklink.workflow.service import WorkflowService

    service = WorkflowService(log_root=log_root)
    run = service.start_run(novel)
    first_version = service.update_artifact(
        artifact_id="outline",
        artifact_type="outline",
        payload={"outline": "初稿", "notes": []},
        approval_id="outline",
    )
    service.close()
    with WorkflowService(log_root=log_root) as service:
        service.resume_run(run.runtime_id)
        service.record_approval_message(
            approval_id="outline",
            role="user",
            content="第一轮：加强压迫感",
        )
    llm = FakeToolLLM()

    second_version = await InklinkPipeline(llm=llm).update_artifact_with_chat(
        runtime_id=run.runtime_id,
        log_root=log_root,
        config_path=config,
        approval_id="outline",
        artifact_id="outline",
        artifact_type="outline",
        user_message="强化冲突",
    )

    assert first_version == 1
    assert second_version == 2
    assert ("outline_chat", "update_outline") in llm.calls
    update_inputs = [
        input_text
        for task_type, tool_name, input_text in llm.inputs
        if task_type == "outline_chat" and tool_name == "update_outline"
    ]
    assert "第一轮：加强压迫感" in update_inputs[0]
    assert "强化冲突" in update_inputs[0]
    with WorkflowService(log_root=log_root) as service:
        service.inspect_run(run.runtime_id)
        messages = service.list_messages("outline")
        approval = service.get_artifact("outline", version=second_version)

    assert messages[-1]["role"] == "assistant"
    assert "强化冲突" in str(messages[-1]["content"])
    assert approval["is_draft"] is True


@pytest.mark.asyncio
async def test_pipeline_revises_when_deterministic_check_fails(tmp_path: Path) -> None:
    novel = tmp_path / "novel"
    novel.mkdir()
    write_chapter(novel / "1.txt", "第一章", "林秋得到旧钥匙。")
    write_chapter(novel / "2.txt", "第二章", "青灯在雨夜亮起。")
    config = tmp_path / "config.toml"
    write_config(config)
    llm = FakeToolLLM(draft_body="短")

    summary = await InklinkPipeline(llm=llm).run(
        GenerationOptions(
            input_dir=novel,
            config_path=config,
            log_root=tmp_path / "logs",
            chapter_count=1,
            min_chars=8,
            max_chars=80,
            auto_approve=True,
        )
    )

    assert ("revision", "submit_revision") in llm.calls
    assert "旧钥匙也随之发烫" in summary.output_files[0].read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_pipeline_pauses_when_review_failure_needs_approval(tmp_path: Path) -> None:
    novel = tmp_path / "novel"
    novel.mkdir()
    write_chapter(novel / "1.txt", "第一章", "林秋得到旧钥匙。")
    write_chapter(novel / "2.txt", "第二章", "青灯在雨夜亮起。")
    config = tmp_path / "config.toml"
    write_config_with_planning_auto_approval(config)
    llm = FailingReviewLLM()

    summary = await InklinkPipeline(llm=llm).run(
        GenerationOptions(
            input_dir=novel,
            config_path=config,
            log_root=tmp_path / "logs",
            chapter_count=1,
            min_chars=8,
            max_chars=80,
            max_revision_rounds=0,
            auto_approve=False,
        )
    )

    assert summary.status == "waiting_approval"
    assert summary.waiting_approval_id == "review_failure:3"
    assert summary.output_files == []
    assert not (summary.log_dir / "outputs" / "chapters" / "3.txt").exists()


@pytest.mark.asyncio
async def test_pipeline_writeback_pauses_existing_target(tmp_path: Path) -> None:
    novel = tmp_path / "novel"
    novel.mkdir()
    write_chapter(novel / "1.txt", "第一章", "林秋得到旧钥匙。")
    write_chapter(novel / "2.txt", "第二章", "青灯在雨夜亮起。")
    write_chapter(novel / "3.txt", "第三章", "已经存在。")
    config = tmp_path / "config.toml"
    write_config(config)

    summary = await InklinkPipeline(llm=FakeToolLLM()).run(
        GenerationOptions(
            input_dir=novel,
            config_path=config,
            log_root=tmp_path / "logs",
            output_mode="writeback",
            chapter_count=1,
            min_chars=8,
            max_chars=80,
            auto_approve=True,
            start_chapter=3,
        )
    )

    assert summary.status == "waiting_write_output"
    assert summary.waiting_node_id == "write_output:3"
    pending = summary.log_dir / "outputs" / "pending_writeback" / "3.txt"
    assert pending.is_file()
    from inklink.storage.sqlite import StateStore

    with StateStore.open(summary.log_dir / "state.sqlite") as store:
        node = store.get_node("write_output:3")

    assert node["status"] == "waiting"
    assert "writeback target already exists" in str(node["waiting_reason"])


@pytest.mark.asyncio
async def test_pipeline_writeback_resume_flushes_pending_target(tmp_path: Path) -> None:
    novel = tmp_path / "novel"
    novel.mkdir()
    write_chapter(novel / "1.txt", "第一章", "林秋得到旧钥匙。")
    write_chapter(novel / "2.txt", "第二章", "青灯在雨夜亮起。")
    write_chapter(novel / "3.txt", "第三章", "已经存在。")
    config = tmp_path / "config.toml"
    write_config(config)
    log_root = tmp_path / "logs"

    paused = await InklinkPipeline(llm=FakeToolLLM()).run(
        GenerationOptions(
            input_dir=novel,
            config_path=config,
            log_root=log_root,
            output_mode="writeback",
            chapter_count=1,
            min_chars=8,
            max_chars=80,
            auto_approve=True,
            start_chapter=3,
        )
    )
    (novel / "3.txt").unlink()

    resumed_llm = FakeToolLLM(draft_body="这段不应重新生成。")
    resumed = await InklinkPipeline(llm=resumed_llm).run(
        GenerationOptions(
            input_dir=novel,
            config_path=config,
            log_root=log_root,
            runtime_id=paused.runtime_id,
            output_mode="writeback",
            chapter_count=1,
            min_chars=8,
            max_chars=80,
            auto_approve=True,
            start_chapter=3,
        )
    )

    assert resumed.status == "completed"
    assert (novel / "3.txt").read_text(encoding="utf-8").startswith("title: 第三章 青灯")
    assert resumed_llm.calls == []
