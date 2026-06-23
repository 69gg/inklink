from __future__ import annotations

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
            chapter_number = 1
            if '"chapter_number": 2' in input_text or "第二章" in input_text:
                chapter_number = 2
            if '"chapter_number": 3' in input_text or "第三章" in input_text:
                chapter_number = 3
            return {
                "chapter_number": chapter_number,
                "summary": f"第{chapter_number}章分析",
                "characters": ["林秋"],
                "worldbuilding": ["青灯会回应钥匙"],
                "plot_threads": ["旧钥匙的来历"],
                "style_notes": ["克制、悬疑"],
                "suspense": ["门后是谁"],
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
    assert (summary.log_dir / "artifacts" / "story_index.json").is_file()
    assert ("drafting", "submit_scene_draft") in llm.calls
    scene_call_indices = [
        index for index, call in enumerate(llm.calls) if call == ("drafting", "submit_scene_draft")
    ]
    assert scene_call_indices == sorted(scene_call_indices)


@pytest.mark.asyncio
async def test_pipeline_resume_reuses_successful_calls_and_completed_output(
    tmp_path: Path,
) -> None:
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
    assert second.stats.total_calls == 0


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
async def test_pipeline_writeback_refuses_existing_target(tmp_path: Path) -> None:
    novel = tmp_path / "novel"
    novel.mkdir()
    write_chapter(novel / "1.txt", "第一章", "林秋得到旧钥匙。")
    write_chapter(novel / "2.txt", "第二章", "青灯在雨夜亮起。")
    write_chapter(novel / "3.txt", "第三章", "已经存在。")
    config = tmp_path / "config.toml"
    write_config(config)

    with pytest.raises(FileExistsError, match="writeback target already exists"):
        await InklinkPipeline(llm=FakeToolLLM()).run(
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
