from __future__ import annotations

import asyncio
import hashlib
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from inklink.atomic import atomic_write_text
from inklink.chapters import Chapter, load_chapters
from inklink.config import AppConfig, ModelProfile, load_config
from inklink.domain.checks import run_chapter_checks
from inklink.domain.index import EntityMention, StoryIndex, StructuredFact
from inklink.domain.models import (
    ChapterAnalysis,
    ChapterContract,
    ChapterPlan,
    ChapterReview,
    DraftChapter,
    OutlineProposal,
    RangeSummary,
    SceneDraft,
    ScenePlan,
    StoryState,
)
from inklink.llm.limiter import ProfileLimiter
from inklink.llm.openai_client import (
    ChatCompletionsAdapter,
    LLMRequest,
    ResponsesAdapter,
    _ChatClient,
    _ResponsesClient,
    make_async_openai,
)
from inklink.llm.types import LLMToolCall, NormalizedUsage
from inklink.storage.events import JsonlEventLog
from inklink.storage.sqlite import StateStore
from inklink.workflow.service import WorkflowService


class ToolLLM(Protocol):
    async def call_tool(
        self,
        *,
        task_type: str,
        profile_name: str,
        tool_name: str,
        instructions: str,
        input_text: str,
        schema: dict[str, object],
    ) -> ToolCallResult: ...


class ToolCallResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    payload: dict[str, object]
    usage: NormalizedUsage = Field(default_factory=NormalizedUsage)
    request_id: str | None = None
    profile_name: str
    model: str
    task_type: str
    tool_call_id: str | None = None


class OpenAIToolLLM:
    def __init__(self, config: AppConfig, api_keys: Mapping[str, str | None]) -> None:
        self._config = config
        self._api_keys = dict(api_keys)
        self._clients: dict[str, object] = {}
        self._limiters = {
            name: ProfileLimiter(
                max_concurrency=profile.max_concurrency,
                rpm=profile.rpm,
            )
            for name, profile in config.models.items()
        }

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
        profile = self._config.models[profile_name]
        async with self._limiters[profile_name]:
            client = self._client_for(profile_name, profile)
            request = LLMRequest(
                instructions=instructions,
                input_text=input_text,
                tools=[_schema_for_api(schema, profile)],
                tool_choice=_tool_choice_for(profile, tool_name),
            )
            response = (
                await ResponsesAdapter(cast(_ResponsesClient, client), profile).create(request)
                if profile.api == "responses"
                else await ChatCompletionsAdapter(cast(_ChatClient, client), profile).create(
                    request
                )
            )
        tool_call = _tool_call_from_response(response.tool_calls, expected_tool_name=tool_name)
        payload = _payload_from_tool_call(tool_call)
        return ToolCallResult(
            payload=payload,
            usage=response.usage,
            request_id=response.request_id,
            profile_name=profile_name,
            model=profile.model,
            task_type=task_type,
            tool_call_id=tool_call.call_id,
        )

    def _client_for(self, profile_name: str, profile: ModelProfile) -> object:
        if profile_name not in self._clients:
            self._clients[profile_name] = make_async_openai(
                profile,
                api_key=self._api_keys.get(profile_name),
            )
        return self._clients[profile_name]


class GenerationOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_dir: Path
    config_path: Path = Path("config.toml")
    log_root: Path = Path("logs")
    output_mode: str | None = None
    runtime_id: str | None = None
    chapter_count: int = Field(default=1, gt=0)
    start_chapter: int | None = Field(default=None, gt=0)
    min_chars: int = Field(default=800, ge=0)
    max_chars: int = Field(default=1800, ge=0)
    max_revision_rounds: int | None = Field(default=None, ge=0)
    auto_approve: bool = False


class UsageBucket(BaseModel):
    model_config = ConfigDict(extra="forbid")

    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    reasoning_tokens: int = 0
    cached_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def add(self, usage: NormalizedUsage) -> None:
        self.calls += 1
        self.input_tokens += usage.input_tokens or 0
        self.output_tokens += usage.output_tokens or 0
        self.total_tokens += usage.total_tokens or 0
        self.reasoning_tokens += usage.reasoning_tokens or 0
        self.cached_tokens += usage.cached_tokens or 0
        self.cache_read_tokens += usage.cache_read_tokens or 0
        self.cache_write_tokens += usage.cache_write_tokens or 0


class RunStats(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total_calls: int = 0
    by_profile: dict[str, UsageBucket] = Field(default_factory=dict)
    by_model: dict[str, UsageBucket] = Field(default_factory=dict)
    by_task: dict[str, UsageBucket] = Field(default_factory=dict)

    def add(self, result: ToolCallResult) -> None:
        self.total_calls += 1
        _bucket(self.by_profile, result.profile_name).add(result.usage)
        _bucket(self.by_model, result.model).add(result.usage)
        _bucket(self.by_task, result.task_type).add(result.usage)


class PipelineSummary(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    runtime_id: str
    log_dir: Path
    generated_chapters: list[int]
    output_files: list[Path]
    stats: RunStats


class RetrievalItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    priority: int = Field(strict=True, gt=0)
    text: str = Field(min_length=1)
    kind: str = "context"


@dataclass(frozen=True)
class _ToolSpec:
    name: str
    model: type[BaseModel]
    schema: dict[str, object]


@dataclass(frozen=True)
class _DraftPackage:
    draft: DraftChapter
    scenes: list[SceneDraft]


class InklinkPipeline:
    def __init__(self, llm: ToolLLM) -> None:
        self._llm = llm

    async def run(self, options: GenerationOptions) -> PipelineSummary:
        config = load_config(options.config_path)
        output_mode = options.output_mode or config.runtime.output_mode
        max_revision_rounds = (
            options.max_revision_rounds
            if options.max_revision_rounds is not None
            else config.writing.max_revision_rounds
        )
        stats = RunStats()
        output_files: list[Path] = []
        generated_chapters: list[int] = []

        with WorkflowService(log_root=options.log_root) as service:
            run = (
                service.resume_run(options.runtime_id)
                if options.runtime_id is not None
                else service.start_run(options.input_dir)
            )
            event_log = JsonlEventLog(run.log_dir / "events.jsonl")
            store = StateStore.open(run.log_dir / "state.sqlite")
            try:
                if options.runtime_id is not None:
                    completed_summary = _resume_completed_summary(store)
                    if completed_summary is not None:
                        event_log.write(
                            "run_completed_reused",
                            {
                                "runtime_id": run.runtime_id,
                                "generated_chapters": completed_summary.generated_chapters,
                            },
                        )
                        return completed_summary
                chapters = load_chapters(run.input_dir)
                artifacts_dir = run.log_dir / "artifacts"
                artifacts_dir.mkdir(parents=True, exist_ok=True)

                analyses = await self._analyze_chapters(
                    chapters=chapters,
                    config=config,
                    runtime_id=run.runtime_id,
                    store=store,
                    stats=stats,
                    event_log=event_log,
                )
                _write_json(
                    artifacts_dir / "chapter_analyses.json",
                    [analysis.model_dump(mode="json") for analysis in analyses],
                )
                index = _index_from_analyses(analyses)
                store.upsert_entity_mentions(index.mentions, source="chapter_analysis")
                store.upsert_structured_facts(index.facts, source="chapter_analysis")
                _write_json(artifacts_dir / "story_index.json", index.model_dump(mode="json"))
                store.upsert_artifact(
                    artifact_id="story_index",
                    artifact_type="story_index",
                    payload=index.model_dump(mode="json"),
                    is_approved=True,
                )

                range_summary = await self._call_model(
                    config=config,
                    runtime_id=run.runtime_id,
                    store=store,
                    stats=stats,
                    event_log=event_log,
                    task_type="range_summary",
                    tool_spec=_TOOL_SPECS["record_range_summary"],
                    input_payload={
                        "chapters": [_chapter_payload(chapter) for chapter in chapters],
                        "analyses": [analysis.model_dump(mode="json") for analysis in analyses],
                    },
                )
                store.upsert_artifact(
                    artifact_id="range_summary:1",
                    artifact_type="range_summary",
                    payload=range_summary.model_dump(mode="json"),
                    is_approved=True,
                )
                story_state = await self._call_model(
                    config=config,
                    runtime_id=run.runtime_id,
                    store=store,
                    stats=stats,
                    event_log=event_log,
                    task_type="story_merge",
                    tool_spec=_TOOL_SPECS["merge_story_state"],
                    input_payload={
                        "range_summary": range_summary.model_dump(mode="json"),
                        "analyses": [analysis.model_dump(mode="json") for analysis in analyses],
                    },
                )
                _write_json(artifacts_dir / "story_state.json", story_state.model_dump(mode="json"))
                store.upsert_artifact(
                    artifact_id="story_state",
                    artifact_type="story_state",
                    payload=story_state.model_dump(mode="json"),
                    is_approved=True,
                )
                retrieval_context = _retrieval_context(
                    analyses=analyses,
                    story_state=story_state,
                    story_index=store.load_story_index(),
                    budget=config.writing.retrieval_token_budget,
                )

                outline = await self._call_model(
                    config=config,
                    runtime_id=run.runtime_id,
                    store=store,
                    stats=stats,
                    event_log=event_log,
                    task_type="outline_planning",
                    tool_spec=_TOOL_SPECS["propose_outline"],
                    input_payload={
                        "story_state": story_state.model_dump(mode="json"),
                        "retrieval_context": retrieval_context,
                        "chapter_count": options.chapter_count,
                    },
                )
                _write_json(artifacts_dir / "outline.json", outline.model_dump(mode="json"))
                outline_version = store.upsert_artifact(
                    artifact_id="outline",
                    artifact_type="outline",
                    payload=outline.model_dump(mode="json"),
                    is_draft=not options.auto_approve,
                    is_approved=options.auto_approve,
                    approval_id="outline",
                )
                _record_approval(
                    event_log,
                    store,
                    "outline",
                    options.auto_approve,
                    artifact_id="outline",
                    artifact_version=outline_version,
                )

                chapter_plan = await self._call_model(
                    config=config,
                    runtime_id=run.runtime_id,
                    store=store,
                    stats=stats,
                    event_log=event_log,
                    task_type="chapter_planning",
                    tool_spec=_TOOL_SPECS["propose_chapter_plan"],
                    input_payload={
                        "outline": outline.model_dump(mode="json"),
                        "retrieval_context": retrieval_context,
                        "start_chapter": options.start_chapter or len(chapters) + 1,
                        "chapter_count": options.chapter_count,
                        "min_chars": options.min_chars,
                        "max_chars": options.max_chars,
                    },
                )
                chapter_contracts = _normalize_chapter_contracts(
                    plan=chapter_plan,
                    start_chapter=options.start_chapter or len(chapters) + 1,
                    chapter_count=options.chapter_count,
                    min_chars=options.min_chars,
                    max_chars=options.max_chars,
                )
                _write_json(
                    artifacts_dir / "chapter_plan.json",
                    [contract.model_dump(mode="json") for contract in chapter_contracts],
                )
                chapter_plan_version = store.upsert_artifact(
                    artifact_id="chapter_plan",
                    artifact_type="chapter_plan",
                    payload=[contract.model_dump(mode="json") for contract in chapter_contracts],
                    is_draft=not options.auto_approve,
                    is_approved=options.auto_approve,
                    approval_id="chapter_plan",
                )
                _record_approval(
                    event_log,
                    store,
                    "chapter_plan",
                    options.auto_approve,
                    artifact_id="chapter_plan",
                    artifact_version=chapter_plan_version,
                )

                previous_generated_body = ""
                for contract in chapter_contracts:
                    generation = store.get_chapter_generation(contract.chapter_number)
                    skipped_output = _completed_chapter_output(
                        store=store,
                        output_mode=output_mode,
                        run_log_dir=run.log_dir,
                        input_dir=run.input_dir,
                        contract=contract,
                    )
                    if skipped_output is not None:
                        output_files.append(skipped_output)
                        generated_chapters.append(contract.chapter_number)
                        previous_generated_body = _read_chapter_body(skipped_output)
                        event_log.write(
                            "chapter_generation_reused",
                            {
                                "runtime_id": run.runtime_id,
                                "chapter_number": contract.chapter_number,
                                "output_file": str(skipped_output),
                            },
                        )
                        continue
                    store.upsert_node(
                        node_id=f"chapter-{contract.chapter_number}",
                        node_type="chapter_generation",
                        status="running",
                        attempt=generation,
                    )
                    scene_plan = await self._call_model(
                        config=config,
                        runtime_id=run.runtime_id,
                        store=store,
                        stats=stats,
                        event_log=event_log,
                        task_type="scene_planning",
                        tool_spec=_TOOL_SPECS["propose_scene_plan"],
                        generation=generation,
                        input_payload={
                            "chapter_contract": contract.model_dump(mode="json"),
                            "story_state": story_state.model_dump(mode="json"),
                            "retrieval_context": retrieval_context,
                            "previous_generated_body": previous_generated_body,
                        },
                    )
                    _record_approval(
                        event_log,
                        store,
                        f"scene_plan:{contract.chapter_number}",
                        options.auto_approve,
                    )
                    store.upsert_artifact(
                        artifact_id=f"scene_plan:{contract.chapter_number}",
                        artifact_type="scene_plan",
                        payload=scene_plan.model_dump(mode="json"),
                        is_draft=not options.auto_approve,
                        is_approved=options.auto_approve,
                        approval_id=f"scene_plan:{contract.chapter_number}",
                    )
                    draft_package = await self._draft_chapter(
                        config=config,
                        runtime_id=run.runtime_id,
                        store=store,
                        stats=stats,
                        event_log=event_log,
                        contract=contract,
                        scene_plan=scene_plan,
                        story_state=story_state,
                        previous_generated_body=previous_generated_body,
                        generation=generation,
                    )
                    draft = await self._review_and_revise(
                        config=config,
                        runtime_id=run.runtime_id,
                        store=store,
                        stats=stats,
                        event_log=event_log,
                        contract=contract,
                        scene_plan=scene_plan,
                        scene_drafts=draft_package.scenes,
                        draft=draft_package.draft,
                        max_revision_rounds=max_revision_rounds,
                        generation=generation,
                    )
                    generated_analysis = await self._call_model(
                        config=config,
                        runtime_id=run.runtime_id,
                        store=store,
                        stats=stats,
                        event_log=event_log,
                        task_type="chapter_extraction",
                        tool_spec=_TOOL_SPECS["record_chapter_analysis"],
                        generation=generation,
                        input_payload={
                            "chapter_number": draft.chapter_number,
                            "title": draft.title,
                            "body": draft.body,
                            "depth": "deep",
                            "generation": generation,
                            "source": "generated",
                        },
                    )
                    store.upsert_entity_mentions(
                        _mentions_from_analysis(generated_analysis, generation=generation),
                        source="generated_chapter",
                    )
                    store.upsert_structured_facts(
                        _facts_from_analysis(generated_analysis, generation=generation),
                        source="generated_chapter",
                    )
                    store.upsert_artifact(
                        artifact_id=f"chapter_analysis:{draft.chapter_number}",
                        artifact_type="chapter_analysis",
                        payload=generated_analysis.model_dump(mode="json"),
                        is_approved=True,
                        source_node_id=f"chapter-{contract.chapter_number}",
                    )
                    output_file = _write_chapter_output(
                        run_log_dir=run.log_dir,
                        input_dir=run.input_dir,
                        output_mode=output_mode,
                        draft=draft,
                    )
                    output_files.append(output_file)
                    generated_chapters.append(draft.chapter_number)
                    previous_generated_body = draft.body
                    store.upsert_node(
                        node_id=f"chapter-{contract.chapter_number}",
                        node_type="chapter_generation",
                        status="completed",
                        attempt=generation,
                        output_version=f"generation:{generation}",
                    )
                    event_log.write(
                        "chapter_generated",
                        {
                            "runtime_id": run.runtime_id,
                            "chapter_number": draft.chapter_number,
                            "output_file": str(output_file),
                        },
                    )

                summary = PipelineSummary(
                    runtime_id=run.runtime_id,
                    log_dir=run.log_dir,
                    generated_chapters=generated_chapters,
                    output_files=output_files,
                    stats=stats,
                )
                _write_json(artifacts_dir / "run_summary.json", summary.model_dump(mode="json"))
                store.upsert_artifact(
                    artifact_id="run_summary",
                    artifact_type="run_summary",
                    payload=summary.model_dump(mode="json"),
                    is_approved=True,
                )
                event_log.write("run_completed", summary.model_dump(mode="json"))
                store.update_run_status(run.runtime_id, "completed")
                return summary
            finally:
                store.close()

    async def _analyze_chapters(
        self,
        *,
        chapters: list[Chapter],
        config: AppConfig,
        runtime_id: str,
        store: StateStore,
        stats: RunStats,
        event_log: JsonlEventLog,
    ) -> list[ChapterAnalysis]:
        deep_start = _deep_analysis_start(chapters, config)

        async def analyze_one(chapter: Chapter) -> ChapterAnalysis:
            depth = "deep" if chapter.number >= deep_start else "shallow"
            analysis = await self._call_model(
                config=config,
                runtime_id=runtime_id,
                store=store,
                stats=stats,
                event_log=event_log,
                task_type="chapter_extraction",
                tool_spec=_TOOL_SPECS["record_chapter_analysis"],
                input_payload={
                    "chapter_number": chapter.number,
                    "title": chapter.title,
                    "body": chapter.body,
                    "depth": depth,
                    "generation": 1,
                },
            )
            store.upsert_artifact(
                artifact_id=f"chapter_analysis:{chapter.number}",
                artifact_type="chapter_analysis",
                payload=analysis.model_dump(mode="json"),
                is_approved=True,
                source_node_id=f"analyze_chapter:{chapter.number}",
            )
            return cast(ChapterAnalysis, analysis)

        analyses = await asyncio.gather(*(analyze_one(chapter) for chapter in chapters))
        return sorted(analyses, key=lambda analysis: analysis.chapter_number)

    async def _draft_chapter(
        self,
        *,
        config: AppConfig,
        runtime_id: str,
        store: StateStore,
        stats: RunStats,
        event_log: JsonlEventLog,
        contract: ChapterContract,
        scene_plan: ScenePlan,
        story_state: StoryState,
        previous_generated_body: str,
        generation: int,
    ) -> _DraftPackage:
        scene_drafts: list[SceneDraft] = []
        prior_scene_text = ""
        for scene in scene_plan.scenes:
            scene_draft = await self._call_model(
                config=config,
                runtime_id=runtime_id,
                store=store,
                stats=stats,
                event_log=event_log,
                task_type="drafting",
                tool_spec=_TOOL_SPECS["submit_scene_draft"],
                generation=generation,
                input_payload={
                    "chapter_contract": contract.model_dump(mode="json"),
                    "scene_contract": scene.model_dump(mode="json"),
                    "story_state": story_state.model_dump(mode="json"),
                    "retrieval_context": _retrieval_context(
                        analyses=[],
                        story_state=story_state,
                        story_index=store.load_story_index(),
                        budget=config.writing.retrieval_token_budget,
                    ),
                    "previous_generated_body": previous_generated_body,
                    "prior_scene_text": prior_scene_text,
                },
            )
            scene_drafts.append(scene_draft)
            store.upsert_artifact(
                artifact_id=f"scene_draft:{contract.chapter_number}:{scene.scene_id}",
                artifact_type="scene_draft",
                payload=scene_draft.model_dump(mode="json"),
                is_approved=True,
                source_node_id=f"chapter-{contract.chapter_number}",
            )
            prior_scene_text = scene_draft.text
        draft = DraftChapter(
            chapter_number=contract.chapter_number,
            title=contract.title,
            body="\n\n".join(scene.text for scene in scene_drafts),
        )
        store.upsert_artifact(
            artifact_id=f"chapter_draft:{contract.chapter_number}",
            artifact_type="chapter_draft",
            payload=draft.model_dump(mode="json"),
            is_approved=True,
            source_node_id=f"chapter-{contract.chapter_number}",
        )
        return _DraftPackage(draft=draft, scenes=scene_drafts)

    async def _review_and_revise(
        self,
        *,
        config: AppConfig,
        runtime_id: str,
        store: StateStore,
        stats: RunStats,
        event_log: JsonlEventLog,
        contract: ChapterContract,
        scene_plan: ScenePlan,
        scene_drafts: list[SceneDraft],
        draft: DraftChapter,
        max_revision_rounds: int,
        generation: int,
    ) -> DraftChapter:
        current = draft
        for attempt in range(max_revision_rounds + 1):
            check_report = run_chapter_checks(
                contract=contract,
                draft=current,
                plot_threads=[],
                scene_contracts=scene_plan.scenes,
                scene_drafts=scene_drafts,
                tolerance_ratio=config.writing.word_count_tolerance_ratio,
            )
            if not check_report.passed:
                deterministic_issues = [
                    issue.model_dump(mode="json") for issue in check_report.issues
                ]
                if attempt >= max_revision_rounds:
                    event_log.write(
                        "review_failure_waiting",
                        {
                            "chapter_number": contract.chapter_number,
                            "issues": deterministic_issues,
                        },
                    )
                    return current
                current = await self._revise(
                    config=config,
                    runtime_id=runtime_id,
                    store=store,
                    stats=stats,
                    event_log=event_log,
                    contract=contract,
                    draft=current,
                    reason={"deterministic_issues": deterministic_issues},
                    generation=generation,
                    attempt=attempt + 1,
                )
                scene_drafts = []
                continue

            review = await self._call_model(
                config=config,
                runtime_id=runtime_id,
                store=store,
                stats=stats,
                event_log=event_log,
                task_type="review",
                tool_spec=_TOOL_SPECS["submit_chapter_review"],
                generation=generation,
                input_payload={
                    "chapter_contract": contract.model_dump(mode="json"),
                    "draft": current.model_dump(mode="json"),
                },
            )
            if review.passed:
                return current
            if attempt >= max_revision_rounds:
                event_log.write(
                    "review_failure_waiting",
                    {"chapter_number": contract.chapter_number, "issues": review.issues},
                )
                return current
            current = await self._revise(
                config=config,
                runtime_id=runtime_id,
                store=store,
                stats=stats,
                event_log=event_log,
                contract=contract,
                draft=current,
                reason={"review_issues": review.issues},
                generation=generation,
                attempt=attempt + 1,
            )
            scene_drafts = []
        return current

    async def _revise(
        self,
        *,
        config: AppConfig,
        runtime_id: str,
        store: StateStore,
        stats: RunStats,
        event_log: JsonlEventLog,
        contract: ChapterContract,
        draft: DraftChapter,
        reason: dict[str, object],
        generation: int,
        attempt: int,
    ) -> DraftChapter:
        revision = await self._call_model(
            config=config,
            runtime_id=runtime_id,
            store=store,
            stats=stats,
            event_log=event_log,
            task_type="revision",
            tool_spec=_TOOL_SPECS["submit_revision"],
            generation=generation,
            input_payload={
                "chapter_contract": contract.model_dump(mode="json"),
                "draft": draft.model_dump(mode="json"),
                "reason": reason,
                "revision_attempt": attempt,
            },
        )
        revised = DraftChapter(
            chapter_number=contract.chapter_number,
            title=revision.title,
            body=revision.body,
        )
        store.upsert_artifact(
            artifact_id=f"chapter_draft:{contract.chapter_number}",
            artifact_type="chapter_draft",
            payload=revised.model_dump(mode="json"),
            is_approved=True,
            source_node_id=f"chapter-{contract.chapter_number}",
        )
        return revised

    async def _call_model(
        self,
        *,
        config: AppConfig,
        runtime_id: str,
        store: StateStore,
        stats: RunStats,
        event_log: JsonlEventLog,
        task_type: str,
        tool_spec: _ToolSpec,
        input_payload: Mapping[str, object],
        generation: int = 1,
        approval_messages_hash: str = "no-approval-messages",
    ) -> Any:
        profile_name = config.profile_for_task(task_type)
        payload_text = json.dumps(input_payload, ensure_ascii=False, sort_keys=True)
        profile = config.models[profile_name]
        key = _call_idempotency_key(
            task_type=task_type,
            input_payload=input_payload,
            profile_name=profile_name,
            tool_spec=tool_spec,
            approval_messages_hash=approval_messages_hash,
            generation=generation,
        )
        cached_payload = store.get_successful_tool_payload(
            idempotency_key=key,
            tool_name=tool_spec.name,
        )
        if cached_payload is not None:
            event_log.write(
                "llm_response_reused",
                {
                    "task_type": task_type,
                    "profile": profile_name,
                    "tool_name": tool_spec.name,
                    "idempotency_key": key,
                    "generation": generation,
                },
            )
            return tool_spec.model.model_validate(cached_payload)

        attempt = store.next_llm_attempt(key)
        llm_call_id = store.create_llm_call(
            runtime_id=runtime_id,
            idempotency_key=key,
            task_type=task_type,
            profile=profile_name,
            api_type=profile.api,
            model=profile.model,
            attempt=attempt,
            request={
                "tool_name": tool_spec.name,
                "generation": generation,
                "input": (
                    input_payload if config.runtime.save_full_prompts else _hash_json(input_payload)
                ),
            },
        )
        event_log.write(
            "llm_request",
            {
                "task_type": task_type,
                "profile": profile_name,
                "tool_name": tool_spec.name,
                "idempotency_key": key,
                "attempt": attempt,
                "generation": generation,
            },
        )
        try:
            result = await self._llm.call_tool(
                task_type=task_type,
                profile_name=profile_name,
                tool_name=tool_spec.name,
                instructions=_instructions_for(tool_spec.name),
                input_text=payload_text,
                schema=tool_spec.schema,
            )
        except Exception as exc:
            store.fail_llm_call(call_id=llm_call_id, error=str(exc))
            raise
        validated = tool_spec.model.model_validate(result.payload)
        stats.add(result)
        store.complete_llm_call(
            call_id=llm_call_id,
            request_id=result.request_id,
            response={
                "tool_name": tool_spec.name,
                "payload": (
                    result.payload
                    if config.runtime.save_full_prompts
                    else _hash_json(result.payload)
                ),
            },
            usage=result.usage,
        )
        store.record_tool_call(
            llm_call_id=llm_call_id,
            idempotency_key=key,
            name=tool_spec.name,
            call_id=result.tool_call_id,
            arguments=result.payload,
            result={"ok": True},
        )
        event_log.write(
            "llm_response",
            {
                "task_type": task_type,
                "profile": result.profile_name,
                "model": result.model,
                "request_id": result.request_id,
                "idempotency_key": key,
                "usage": result.usage.model_dump(mode="json", exclude_none=True),
            },
        )
        event_log.write(
            "tool_result",
            {
                "tool_name": tool_spec.name,
                "payload": result.payload,
            },
        )
        return validated


def _bucket(target: dict[str, UsageBucket], key: str) -> UsageBucket:
    if key not in target:
        target[key] = UsageBucket()
    return target[key]


def _chapter_payload(chapter: Chapter) -> dict[str, object]:
    return {
        "number": chapter.number,
        "title": chapter.title,
        "body": chapter.body,
    }


def trim_retrieval_items(
    items: list[dict[str, object]],
    budget_chars: int | None,
) -> list[dict[str, object]]:
    if budget_chars is None:
        return sorted(items, key=_retrieval_sort_key)
    remaining = budget_chars
    selected: list[dict[str, object]] = []
    for item in sorted(items, key=_retrieval_sort_key):
        text = item.get("text")
        if not isinstance(text, str):
            continue
        cost = len(text)
        if cost > remaining:
            continue
        selected.append(item)
        remaining -= cost
    return selected


def _retrieval_sort_key(item: dict[str, object]) -> tuple[int, str]:
    priority = item.get("priority")
    text = item.get("text")
    return (
        priority if isinstance(priority, int) and not isinstance(priority, bool) else 999,
        text if isinstance(text, str) else "",
    )


def _retrieval_context(
    *,
    analyses: list[ChapterAnalysis],
    story_state: StoryState,
    story_index: StoryIndex | None = None,
    budget: int | None,
) -> list[dict[str, object]]:
    items: list[dict[str, object]] = [
        {"priority": 1, "kind": "story_outline", "text": story_state.outline},
    ]
    for thread in story_state.plot_threads:
        items.append({"priority": 2, "kind": "plot_thread", "text": thread})
    for character in story_state.characters:
        items.append({"priority": 3, "kind": "character", "text": character})
    for fact in story_state.worldbuilding:
        items.append({"priority": 4, "kind": "worldbuilding", "text": fact})
    for analysis in analyses[-3:]:
        items.append(
            {
                "priority": 5,
                "kind": "recent_chapter",
                "text": f"{analysis.chapter_number}: {analysis.summary}",
            }
        )
    if story_index is not None:
        items.extend(story_index.retrieval_items(max_items=20))
    return trim_retrieval_items(items, budget)


def _instructions_for(tool_name: str) -> str:
    return (
        f"你是墨连的小说续写工作流模型。必须只通过指定工具返回结构化结果，当前工具是 {tool_name}。"
    )


def _json_schema_for(model: type[BaseModel]) -> dict[str, object]:
    schema = model.model_json_schema()
    _close_object_schemas(schema)
    return schema


def _close_object_schemas(value: object) -> None:
    if isinstance(value, dict):
        if value.get("type") == "object":
            value.setdefault("additionalProperties", False)
        for child in value.values():
            _close_object_schemas(child)
    elif isinstance(value, list):
        for child in value:
            _close_object_schemas(child)


def _tool_schema(name: str, model: type[BaseModel]) -> dict[str, object]:
    return {
        "type": "function",
        "name": name,
        "description": _instructions_for(name),
        "parameters": _json_schema_for(model),
        "strict": True,
    }


def _schema_for_api(schema: dict[str, object], profile: ModelProfile) -> dict[str, object]:
    if profile.api == "responses":
        return schema
    return {
        "type": "function",
        "function": {
            "name": schema["name"],
            "description": schema.get("description", ""),
            "parameters": schema["parameters"],
            "strict": schema.get("strict", True),
        },
    }


def _tool_choice_for(profile: ModelProfile, tool_name: str) -> dict[str, object]:
    if profile.api == "responses":
        return {"type": "function", "name": tool_name}
    return {"type": "function", "function": {"name": tool_name}}


def _payload_from_tool_response(
    tool_calls: list[LLMToolCall],
    *,
    expected_tool_name: str,
) -> dict[str, object]:
    return _payload_from_tool_call(
        _tool_call_from_response(tool_calls, expected_tool_name=expected_tool_name)
    )


def _tool_call_from_response(
    tool_calls: list[LLMToolCall],
    *,
    expected_tool_name: str,
) -> LLMToolCall:
    for tool_call in tool_calls:
        if tool_call.name != expected_tool_name:
            continue
        return LLMToolCall(
            call_id=getattr(tool_call, "call_id", None) or "",
            name=tool_call.name,
            arguments_json=tool_call.arguments_json,
        )
    raise ValueError(f"model did not call expected tool: {expected_tool_name}")


def _payload_from_tool_call(tool_call: LLMToolCall) -> dict[str, object]:
    parsed = json.loads(tool_call.arguments_json)
    if not isinstance(parsed, dict):
        raise ValueError(f"tool {tool_call.name} returned non-object arguments")
    return cast(dict[str, object], parsed)


class _ChapterPlanTool(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chapters: list[ChapterContract] = Field(min_length=1)


_TOOL_SPECS: dict[str, _ToolSpec] = {
    "record_chapter_analysis": _ToolSpec(
        name="record_chapter_analysis",
        model=ChapterAnalysis,
        schema=_tool_schema("record_chapter_analysis", ChapterAnalysis),
    ),
    "record_range_summary": _ToolSpec(
        name="record_range_summary",
        model=RangeSummary,
        schema=_tool_schema("record_range_summary", RangeSummary),
    ),
    "merge_story_state": _ToolSpec(
        name="merge_story_state",
        model=StoryState,
        schema=_tool_schema("merge_story_state", StoryState),
    ),
    "propose_outline": _ToolSpec(
        name="propose_outline",
        model=OutlineProposal,
        schema=_tool_schema("propose_outline", OutlineProposal),
    ),
    "propose_chapter_plan": _ToolSpec(
        name="propose_chapter_plan",
        model=_ChapterPlanTool,
        schema=_tool_schema("propose_chapter_plan", _ChapterPlanTool),
    ),
    "propose_scene_plan": _ToolSpec(
        name="propose_scene_plan",
        model=ScenePlan,
        schema=_tool_schema("propose_scene_plan", ScenePlan),
    ),
    "submit_scene_draft": _ToolSpec(
        name="submit_scene_draft",
        model=SceneDraft,
        schema=_tool_schema("submit_scene_draft", SceneDraft),
    ),
    "submit_chapter_review": _ToolSpec(
        name="submit_chapter_review",
        model=ChapterReview,
        schema=_tool_schema("submit_chapter_review", ChapterReview),
    ),
    "submit_revision": _ToolSpec(
        name="submit_revision",
        model=DraftChapter,
        schema=_tool_schema("submit_revision", DraftChapter),
    ),
}


def _normalize_chapter_contracts(
    *,
    plan: ChapterPlan | _ChapterPlanTool,
    start_chapter: int,
    chapter_count: int,
    min_chars: int,
    max_chars: int,
) -> list[ChapterContract]:
    contracts = list(plan.chapters)
    normalized: list[ChapterContract] = []
    for offset in range(chapter_count):
        expected_number = start_chapter + offset
        source = contracts[offset] if offset < len(contracts) else contracts[-1]
        normalized.append(
            ChapterContract(
                **{
                    **source.model_dump(mode="python"),
                    "chapter_number": expected_number,
                    "min_chars": min_chars,
                    "max_chars": max_chars,
                }
            )
        )
    return normalized


def _deep_analysis_start(chapters: list[Chapter], config: AppConfig) -> int:
    if not chapters:
        return 1
    if not config.cold_start.enabled:
        return 1
    recent_count = config.cold_start.recent_chapters_to_deep_analyze
    if recent_count <= 0:
        return max(chapter.number for chapter in chapters) + 1
    last_number = max(chapter.number for chapter in chapters)
    return max(1, last_number - recent_count + 1)


def _index_from_analyses(analyses: list[ChapterAnalysis]) -> StoryIndex:
    index = StoryIndex()
    index.upsert_mentions(
        [
            mention
            for analysis in analyses
            for mention in _mentions_from_analysis(analysis, generation=1)
        ]
    )
    index.upsert_facts(
        [fact for analysis in analyses for fact in _facts_from_analysis(analysis, generation=1)]
    )
    return index


def _mentions_from_analysis(
    analysis: ChapterAnalysis,
    *,
    generation: int,
) -> list[EntityMention]:
    mentions: list[EntityMention] = []
    for character in analysis.characters:
        mentions.append(
            EntityMention(
                entity_id=character,
                chapter_number=analysis.chapter_number,
                generation=generation,
                strength=1,
            )
        )
    return mentions


def _facts_from_analysis(
    analysis: ChapterAnalysis,
    *,
    generation: int,
) -> list[StructuredFact]:
    facts: list[StructuredFact] = []
    for offset, fact in enumerate(analysis.worldbuilding):
        facts.append(
            StructuredFact(
                fact_id=f"worldbuilding:{analysis.chapter_number}:{offset}",
                kind="worldbuilding",
                text=fact,
                chapter_number=analysis.chapter_number,
                generation=generation,
                priority=4,
            )
        )
    for offset, thread in enumerate(analysis.plot_threads):
        facts.append(
            StructuredFact(
                fact_id=f"plot_thread:{analysis.chapter_number}:{offset}",
                kind="plot_thread",
                text=thread,
                chapter_number=analysis.chapter_number,
                generation=generation,
                priority=2,
            )
        )
    for offset, suspense in enumerate(analysis.suspense):
        facts.append(
            StructuredFact(
                fact_id=f"event:{analysis.chapter_number}:{offset}",
                kind="event",
                text=suspense,
                chapter_number=analysis.chapter_number,
                generation=generation,
                priority=3,
            )
        )
    return facts


def _call_idempotency_key(
    *,
    task_type: str,
    input_payload: Mapping[str, object],
    profile_name: str,
    tool_spec: _ToolSpec,
    approval_messages_hash: str,
    generation: int,
) -> str:
    key_payload = {
        "node_type": task_type,
        "input_version": _hash_json(input_payload),
        "profile": profile_name,
        "toolset_version": "inklink-tools-v2",
        "prompt_version": "inklink-prompts-v1",
        "task_parameters_hash": _hash_json(
            {"tool_name": tool_spec.name, "schema": tool_spec.schema}
        ),
        "approval_messages_hash": approval_messages_hash,
        "generation": generation,
    }
    return _hash_json(key_payload)


def _hash_json(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def _write_chapter_output(
    *,
    run_log_dir: Path,
    input_dir: Path,
    output_mode: str,
    draft: DraftChapter,
) -> Path:
    content = f"title: {draft.title}\n---\n{draft.body}"
    if output_mode == "output":
        target = run_log_dir / "outputs" / "chapters" / f"{draft.chapter_number}.txt"
        atomic_write_text(target, content)
        return target
    if output_mode == "writeback":
        target = input_dir / f"{draft.chapter_number}.txt"
        if target.exists():
            raise FileExistsError(f"writeback target already exists: {target}")
        tmp_target = run_log_dir / "outputs" / f"tmp_{draft.chapter_number}.txt"
        atomic_write_text(tmp_target, content)
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(tmp_target, target)
        return target
    raise ValueError(f"unknown output_mode: {output_mode}")


def _resume_completed_summary(store: StateStore) -> PipelineSummary | None:
    artifact = store.get_latest_artifact("run_summary", approved_only=True)
    if artifact is None:
        return None
    return PipelineSummary.model_validate(artifact["payload"])


def _completed_chapter_output(
    *,
    store: StateStore,
    output_mode: str,
    run_log_dir: Path,
    input_dir: Path,
    contract: ChapterContract,
) -> Path | None:
    try:
        node = store.get_node(f"chapter-{contract.chapter_number}")
    except KeyError:
        return None
    if node["status"] != "completed":
        return None
    output_file = (
        input_dir / f"{contract.chapter_number}.txt"
        if output_mode == "writeback"
        else run_log_dir / "outputs" / "chapters" / f"{contract.chapter_number}.txt"
    )
    return output_file if output_file.is_file() else None


def _read_chapter_body(path: Path) -> str:
    text = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    _, separator, body = text.partition("\n---\n")
    return body if separator else text


def _write_json(path: Path, payload: object) -> None:
    TypeAdapter(object).validate_python(payload)
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def _record_approval(
    event_log: JsonlEventLog,
    store: StateStore,
    approval_type: str,
    auto_approve: bool,
    *,
    artifact_id: str | None = None,
    artifact_version: int | None = None,
) -> None:
    approval_id = approval_type
    store.create_or_update_approval(
        approval_id=approval_id,
        approval_type=approval_type,
        status="accepted" if auto_approve else "waiting",
        auto_approve=auto_approve,
        artifact_id=artifact_id,
        artifact_version=artifact_version,
    )
    event_log.write(
        "approval_accepted" if auto_approve else "approval_waiting",
        {
            "approval_type": approval_type,
            "auto_approve": auto_approve,
            "artifact_id": artifact_id,
            "artifact_version": artifact_version,
        },
    )
