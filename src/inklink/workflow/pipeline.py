from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, model_validator

from inklink.atomic import atomic_move_text, atomic_write_text
from inklink.chapters import Chapter, load_chapters
from inklink.config import AppConfig, ModelProfile, load_config
from inklink.domain.checks import run_chapter_checks
from inklink.domain.index import (
    EntityMention,
    StoryIndex,
    StructuredFact,
    facts_from_chapter_analysis,
)
from inklink.domain.models import (
    ChapterAnalysis,
    ChapterContract,
    ChapterPlan,
    ChapterPlanUpdate,
    ChapterReview,
    DraftChapter,
    OutlineProposal,
    OutlineUpdate,
    PlotThread,
    RangeSummary,
    SceneDraft,
    ScenePlan,
    ScenePlanUpdate,
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
from inklink.workflow.service import WorkflowRun, WorkflowService


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


class ReviewFailureApprovalRequired(RuntimeError):
    def __init__(self, *, chapter_number: int, issues: Sequence[object]) -> None:
        super().__init__(f"review failure approval required for chapter {chapter_number}")
        self.chapter_number = chapter_number
        self.issues = list(issues)


class WriteOutputApprovalRequired(RuntimeError):
    def __init__(self, *, chapter_number: int, target: Path, pending_file: Path) -> None:
        super().__init__(f"write output approval required for chapter {chapter_number}")
        self.chapter_number = chapter_number
        self.target = target
        self.pending_file = pending_file


class ToolPayloadParseError(ValueError):
    pass


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

    input_dir: Path | None = None
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
    notes: str = ""
    notes_path: Path | None = None


class RunSettings(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    input_dir: Path
    config_path: Path
    output_mode: str
    chapter_count: int = Field(gt=0)
    start_chapter: int | None = Field(default=None, gt=0)
    min_chars: int = Field(ge=0)
    max_chars: int = Field(ge=0)
    max_revision_rounds: int = Field(ge=0)
    auto_approve: bool = False
    notes: str = ""
    notes_path: Path | None = None


class UsageBucket(BaseModel):
    model_config = ConfigDict(extra="forbid")

    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    reasoning_tokens: int | None = None
    cached_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None

    def add(self, usage: NormalizedUsage) -> None:
        self.calls += 1
        self.input_tokens += usage.input_tokens or 0
        self.output_tokens += usage.output_tokens or 0
        self.total_tokens += usage.total_tokens or 0
        self.reasoning_tokens = _add_optional_tokens(
            self.reasoning_tokens,
            usage.reasoning_tokens,
        )
        self.cached_tokens = _add_optional_tokens(self.cached_tokens, usage.cached_tokens)
        self.cache_read_tokens = _add_optional_tokens(
            self.cache_read_tokens,
            usage.cache_read_tokens,
        )
        self.cache_write_tokens = _add_optional_tokens(
            self.cache_write_tokens,
            usage.cache_write_tokens,
        )


class RunStats(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total_calls: int = 0
    total: UsageBucket = Field(default_factory=UsageBucket)
    by_profile: dict[str, UsageBucket] = Field(default_factory=dict)
    by_model: dict[str, UsageBucket] = Field(default_factory=dict)
    by_task: dict[str, UsageBucket] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_optional_zeros(cls, data: object) -> object:
        if not isinstance(data, dict) or "total" in data:
            return data
        normalized = dict(data)
        for key in ("by_profile", "by_model", "by_task"):
            buckets = normalized.get(key)
            if isinstance(buckets, dict):
                normalized[key] = {
                    bucket_key: _drop_legacy_optional_zero_usage(bucket)
                    for bucket_key, bucket in buckets.items()
                }
        return normalized

    def add(self, result: ToolCallResult) -> None:
        self.total_calls += 1
        self.total.add(result.usage)
        _bucket(self.by_profile, result.profile_name).add(result.usage)
        _bucket(self.by_model, result.model).add(result.usage)
        _bucket(self.by_task, result.task_type).add(result.usage)

    @model_validator(mode="after")
    def fill_total_from_existing_buckets(self) -> RunStats:
        if self.total.calls == 0 and self.total_calls > 0:
            for buckets in (self.by_model, self.by_profile, self.by_task):
                if buckets:
                    self.total = _sum_usage_buckets(buckets.values())
                    break
            if self.total.calls == 0:
                self.total.calls = self.total_calls
        if self.total_calls == 0 and self.total.calls > 0:
            self.total_calls = self.total.calls
        return self


class PipelineSummary(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    runtime_id: str
    log_dir: Path
    generated_chapters: list[int]
    output_files: list[Path]
    stats: RunStats
    status: str = "completed"
    waiting_approval_id: str | None = None
    waiting_node_id: str | None = None
    error_summary: str | None = None


@dataclass(frozen=True)
class PipelineProgress:
    """Human-readable progress update emitted by the workflow runner."""

    message: str
    runtime_id: str | None = None
    node_id: str | None = None
    chapter_number: int | None = None
    phase: str | None = None
    status: str | None = None
    step_index: int | None = None
    step_total: int | None = None
    chapter_done: int | None = None
    chapter_total: int | None = None
    llm_task_type: str | None = None
    llm_profile: str | None = None
    waiting_approval_id: str | None = None
    severity: str = "info"


PipelineProgressCallback = Callable[[PipelineProgress], None]


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
    def __init__(
        self,
        llm: ToolLLM,
        progress_callback: PipelineProgressCallback | None = None,
    ) -> None:
        self._llm = llm
        self._progress_callback = progress_callback

    def _progress(
        self,
        message: str,
        *,
        runtime_id: str | None = None,
        node_id: str | None = None,
        chapter_number: int | None = None,
        phase: str | None = None,
        status: str | None = None,
        step_index: int | None = None,
        step_total: int | None = None,
        chapter_done: int | None = None,
        chapter_total: int | None = None,
        llm_task_type: str | None = None,
        llm_profile: str | None = None,
        waiting_approval_id: str | None = None,
        severity: str = "info",
    ) -> None:
        if self._progress_callback is None:
            return
        progress = PipelineProgress(
            message=message,
            runtime_id=runtime_id,
            node_id=node_id,
            chapter_number=chapter_number,
            phase=phase,
            status=status,
            step_index=step_index,
            step_total=step_total,
            chapter_done=chapter_done,
            chapter_total=chapter_total,
            llm_task_type=llm_task_type,
            llm_profile=llm_profile,
            waiting_approval_id=waiting_approval_id,
            severity=severity,
        )
        try:
            self._progress_callback(progress)
        except Exception:
            return

    async def update_artifact_with_chat(
        self,
        *,
        runtime_id: str,
        log_root: Path,
        config_path: Path,
        approval_id: str,
        artifact_id: str,
        artifact_type: str,
        user_message: str,
    ) -> int:
        config = load_config(config_path)
        with WorkflowService(log_root=log_root) as service:
            run = service.resume_run(runtime_id)
            event_log = JsonlEventLog(run.log_dir / "events.jsonl")
            store = StateStore.open(run.log_dir / "state.sqlite")
            try:
                latest = store.get_latest_artifact(artifact_id)
                if latest is None:
                    raise KeyError(f"artifact not found: {artifact_id}")
                service.record_approval_message(
                    approval_id=approval_id,
                    role="user",
                    content=user_message,
                )
                messages = store.list_messages(approval_id)
                tool_spec = _tool_spec_for_artifact_update(artifact_type)
                updated = await self._call_model(
                    config=config,
                    runtime_id=runtime_id,
                    store=store,
                    stats=RunStats(),
                    event_log=event_log,
                    task_type=_chat_task_for_artifact(artifact_type),
                    tool_spec=tool_spec,
                    approval_messages_hash=store.approval_messages_hash(approval_id),
                    input_payload={
                        "approval_id": approval_id,
                        "artifact_id": artifact_id,
                        "artifact_type": artifact_type,
                        "current_artifact": latest["payload"],
                        "user_message": user_message,
                        "conversation": [
                            {
                                "role": str(message["role"]),
                                "content": str(message["content"]),
                            }
                            for message in messages
                        ],
                    },
                )
                version = store.upsert_artifact(
                    artifact_id=artifact_id,
                    artifact_type=artifact_type,
                    payload=updated.model_dump(mode="json"),
                    is_draft=True,
                    is_approved=False,
                    approval_id=approval_id,
                )
                store.create_or_update_approval(
                    approval_id=approval_id,
                    approval_type=artifact_type,
                    status="waiting",
                    auto_approve=False,
                    artifact_id=artifact_id,
                    artifact_version=version,
                )
                event_log.write(
                    "approval_artifact_updated",
                    {
                        "runtime_id": runtime_id,
                        "approval_id": approval_id,
                        "artifact_id": artifact_id,
                        "artifact_type": artifact_type,
                        "version": version,
                    },
                )
                service.record_approval_message(
                    approval_id=approval_id,
                    role="assistant",
                    content=_artifact_update_change_summary(updated),
                )
                return version
            finally:
                store.close()

    async def run(self, options: GenerationOptions) -> PipelineSummary:
        self._progress(
            "读取配置与运行参数",
            runtime_id=options.runtime_id,
            phase="load",
            status="running",
        )
        initial_config = load_config(options.config_path) if options.runtime_id is None else None
        stats = RunStats()
        output_files: list[Path] = []
        generated_chapters: list[int] = []

        with WorkflowService(log_root=options.log_root) as service:
            self._progress(
                "创建或恢复运行",
                runtime_id=options.runtime_id,
                phase="load",
                status="running",
            )
            run = (
                service.resume_run(options.runtime_id)
                if options.runtime_id is not None
                else service.start_run(_required_input_dir(options))
            )
            self._progress(
                "运行已启动，正在准备状态库",
                runtime_id=run.runtime_id,
                phase="load",
                status="running",
            )
            event_log = JsonlEventLog(run.log_dir / "events.jsonl")
            store = StateStore.open(run.log_dir / "state.sqlite")
            try:
                settings = _settings_for_run(
                    options=options,
                    config=initial_config,
                    run=run,
                    store=store,
                )
                config = load_config(settings.config_path)
                output_mode = settings.output_mode
                max_revision_rounds = settings.max_revision_rounds
                effective_auto_approve = settings.auto_approve or options.auto_approve
                if options.runtime_id is not None:
                    self._progress(
                        "检查可续接的未完成节点",
                        runtime_id=run.runtime_id,
                        phase="load",
                        status="running",
                    )
                    pending_summary = _resume_pending_write_output_summary(
                        store=store,
                        runtime_id=run.runtime_id,
                        log_dir=run.log_dir,
                        input_dir=run.input_dir,
                        event_log=event_log,
                    )
                    if pending_summary is not None:
                        return pending_summary
                    completed_summary = _resume_completed_summary(store)
                    if completed_summary is not None:
                        self._progress(
                            "已复用完成的运行摘要",
                            runtime_id=run.runtime_id,
                            phase="output",
                            status="reused",
                        )
                        event_log.write(
                            "run_completed_reused",
                            {
                                "runtime_id": run.runtime_id,
                                "generated_chapters": completed_summary.generated_chapters,
                            },
                        )
                        return completed_summary
                self._progress(
                    "读取章节文件",
                    runtime_id=run.runtime_id,
                    node_id="load_project",
                    phase="load",
                    status="running",
                )
                chapters = load_chapters(run.input_dir)
                artifacts_dir = run.log_dir / "artifacts"
                artifacts_dir.mkdir(parents=True, exist_ok=True)
                store.upsert_node(
                    node_id="load_project",
                    node_type="load_project",
                    status="completed",
                    input_version=_hash_json(
                        [
                            {
                                "number": chapter.number,
                                "title": chapter.title,
                                "body_hash": _hash_json(chapter.body),
                            }
                            for chapter in chapters
                        ]
                        + [{"run_settings": settings.model_dump(mode="json")}]
                    ),
                    output_version=f"chapters:{len(chapters)}",
                )

                self._progress(
                    f"分析已有章节，共 {len(chapters)} 章",
                    runtime_id=run.runtime_id,
                    phase="analysis",
                    status="running",
                    chapter_done=0,
                    chapter_total=len(chapters),
                )
                analyses = await self._analyze_chapters(
                    chapters=chapters,
                    config=config,
                    runtime_id=run.runtime_id,
                    store=store,
                    stats=stats,
                    event_log=event_log,
                )
                self._progress(
                    "检查是否需要升级浅层分析",
                    runtime_id=run.runtime_id,
                    phase="analysis",
                    status="running",
                )
                analyses = await self._upgrade_shallow_analyses_if_needed(
                    chapters=chapters,
                    analyses=analyses,
                    config=config,
                    runtime_id=run.runtime_id,
                    store=store,
                    stats=stats,
                    event_log=event_log,
                )
                self._progress(
                    "写入结构化检索索引",
                    runtime_id=run.runtime_id,
                    phase="analysis",
                    status="running",
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

                self._progress(
                    "生成区间摘要",
                    runtime_id=run.runtime_id,
                    phase="range_summary",
                    status="running",
                )
                range_summaries = await self._summarize_ranges(
                    chapters=chapters,
                    analyses=analyses,
                    config=config,
                    runtime_id=run.runtime_id,
                    store=store,
                    stats=stats,
                    event_log=event_log,
                )
                self._progress(
                    "合并全书状态",
                    runtime_id=run.runtime_id,
                    node_id="merge_story_state",
                    phase="story_state",
                    status="running",
                )
                store.upsert_node(
                    node_id="merge_story_state",
                    node_type="merge_story_state",
                    status="running",
                    depends_on=[
                        f"summarize_range:{summary.start_chapter}-{summary.end_chapter}"
                        for summary in range_summaries
                    ],
                    input_version=_hash_json(
                        [summary.model_dump(mode="json") for summary in range_summaries]
                    ),
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
                        "range_summaries": [
                            summary.model_dump(mode="json") for summary in range_summaries
                        ],
                        "recent_analyses": [
                            analysis.model_dump(mode="json")
                            for analysis in analyses[-config.writing.story_merge_recent_chapters :]
                        ],
                        "story_index_projection": store.load_story_index().retrieval_items(
                            max_items=50
                        ),
                        "user_notes": settings.notes,
                    },
                )
                _write_json(artifacts_dir / "story_state.json", story_state.model_dump(mode="json"))
                store.upsert_artifact(
                    artifact_id="story_state",
                    artifact_type="story_state",
                    payload=story_state.model_dump(mode="json"),
                    is_approved=True,
                )
                store.upsert_node(
                    node_id="merge_story_state",
                    node_type="merge_story_state",
                    status="completed",
                    depends_on=[
                        f"summarize_range:{summary.start_chapter}-{summary.end_chapter}"
                        for summary in range_summaries
                    ],
                    output_version="story_state",
                )
                self._progress(
                    "组装检索上下文",
                    runtime_id=run.runtime_id,
                    phase="story_state",
                    status="running",
                )
                retrieval_context = _retrieval_context(
                    analyses=analyses,
                    story_state=story_state,
                    story_index=store.load_story_index(),
                    chapters=chapters,
                    budget=config.writing.retrieval_token_budget,
                )

                outline_artifact = _approved_artifact_if_accepted(store, "outline", "outline")
                if outline_artifact is None:
                    self._progress(
                        "生成可讨论大纲",
                        runtime_id=run.runtime_id,
                        node_id="plan_outline",
                        phase="outline",
                        status="running",
                    )
                    store.upsert_node(
                        node_id="plan_outline",
                        node_type="plan_outline",
                        status="running",
                        depends_on=["merge_story_state"],
                        input_version=_hash_json(
                            {
                                "story_state": story_state.model_dump(mode="json"),
                                "chapter_count": settings.chapter_count,
                                "user_notes": settings.notes,
                            }
                        ),
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
                            "chapter_count": settings.chapter_count,
                            "user_notes": settings.notes,
                        },
                    )
                    _write_json(artifacts_dir / "outline.json", outline.model_dump(mode="json"))
                    outline_auto = _should_auto_approve(
                        effective_auto_approve,
                        config.approvals.auto_approve_outline,
                    )
                    outline_version = store.upsert_artifact(
                        artifact_id="outline",
                        artifact_type="outline",
                        payload=outline.model_dump(mode="json"),
                        is_draft=not outline_auto,
                        is_approved=outline_auto,
                        approval_id="outline",
                        source_node_id="plan_outline",
                    )
                    store.upsert_node(
                        node_id="plan_outline",
                        node_type="plan_outline",
                        status="completed",
                        depends_on=["merge_story_state"],
                        output_version=f"outline@{outline_version}",
                    )
                    _record_approval(
                        event_log,
                        store,
                        "outline",
                        outline_auto,
                        artifact_id="outline",
                        artifact_version=outline_version,
                    )
                    store.upsert_node(
                        node_id="approve_outline",
                        node_type="approve_outline",
                        status="completed" if outline_auto else "waiting",
                        depends_on=["plan_outline"],
                        waiting_reason=None if outline_auto else "waiting for outline approval",
                        output_version=f"outline@{outline_version}",
                    )
                    if not outline_auto:
                        self._progress(
                            "等待用户审批大纲",
                            runtime_id=run.runtime_id,
                            node_id="approve_outline",
                            phase="outline",
                            status="waiting",
                            waiting_approval_id="outline",
                        )
                        return _pause_for_approval(
                            runtime_id=run.runtime_id,
                            log_dir=run.log_dir,
                            store=store,
                            event_log=event_log,
                            stats=stats,
                            approval_id="outline",
                        )
                else:
                    outline = _outline_from_artifact(outline_artifact)
                    store.upsert_node(
                        node_id="approve_outline",
                        node_type="approve_outline",
                        status="completed",
                        depends_on=["plan_outline"],
                        output_version=f"outline@{outline_artifact['version']}",
                    )
                    event_log.write(
                        "approval_artifact_reused",
                        {
                            "runtime_id": run.runtime_id,
                            "approval_id": "outline",
                            "artifact_id": "outline",
                            "version": outline_artifact["version"],
                        },
                    )

                chapter_plan_artifact = _approved_artifact_if_accepted(
                    store,
                    "chapter_plan",
                    "chapter_plan",
                )
                if chapter_plan_artifact is None:
                    self._progress(
                        "生成章节计划",
                        runtime_id=run.runtime_id,
                        node_id="plan_chapters",
                        phase="chapter_plan",
                        status="running",
                    )
                    store.upsert_node(
                        node_id="plan_chapters",
                        node_type="plan_chapters",
                        status="running",
                        depends_on=["approve_outline"],
                        input_version=_hash_json(
                            {
                                "outline": outline.model_dump(mode="json"),
                                "start_chapter": settings.start_chapter or len(chapters) + 1,
                                "chapter_count": settings.chapter_count,
                                "min_chars": settings.min_chars,
                                "max_chars": settings.max_chars,
                                "user_notes": settings.notes,
                            }
                        ),
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
                            "start_chapter": settings.start_chapter or len(chapters) + 1,
                            "chapter_count": settings.chapter_count,
                            "min_chars": settings.min_chars,
                            "max_chars": settings.max_chars,
                            "user_notes": settings.notes,
                        },
                    )
                    chapter_contracts = _normalize_chapter_contracts(
                        plan=chapter_plan,
                        start_chapter=settings.start_chapter or len(chapters) + 1,
                        chapter_count=settings.chapter_count,
                        min_chars=settings.min_chars,
                        max_chars=settings.max_chars,
                    )
                    _write_json(
                        artifacts_dir / "chapter_plan.json",
                        [contract.model_dump(mode="json") for contract in chapter_contracts],
                    )
                    chapter_plan_auto = _should_auto_approve(
                        effective_auto_approve,
                        config.approvals.auto_approve_chapter_plan,
                    )
                    chapter_plan_version = store.upsert_artifact(
                        artifact_id="chapter_plan",
                        artifact_type="chapter_plan",
                        payload=[
                            contract.model_dump(mode="json") for contract in chapter_contracts
                        ],
                        is_draft=not chapter_plan_auto,
                        is_approved=chapter_plan_auto,
                        approval_id="chapter_plan",
                        source_node_id="plan_chapters",
                    )
                    store.upsert_node(
                        node_id="plan_chapters",
                        node_type="plan_chapters",
                        status="completed",
                        depends_on=["approve_outline"],
                        output_version=f"chapter_plan@{chapter_plan_version}",
                    )
                    _record_approval(
                        event_log,
                        store,
                        "chapter_plan",
                        chapter_plan_auto,
                        artifact_id="chapter_plan",
                        artifact_version=chapter_plan_version,
                    )
                    store.upsert_node(
                        node_id="approve_chapter_plan",
                        node_type="approve_chapter_plan",
                        status="completed" if chapter_plan_auto else "waiting",
                        depends_on=["plan_chapters"],
                        waiting_reason=(
                            None if chapter_plan_auto else "waiting for chapter plan approval"
                        ),
                        output_version=f"chapter_plan@{chapter_plan_version}",
                    )
                    if not chapter_plan_auto:
                        self._progress(
                            "等待用户审批章节计划",
                            runtime_id=run.runtime_id,
                            node_id="approve_chapter_plan",
                            phase="chapter_plan",
                            status="waiting",
                            waiting_approval_id="chapter_plan",
                        )
                        return _pause_for_approval(
                            runtime_id=run.runtime_id,
                            log_dir=run.log_dir,
                            store=store,
                            event_log=event_log,
                            stats=stats,
                            approval_id="chapter_plan",
                        )
                else:
                    chapter_contracts = _chapter_contracts_from_artifact(chapter_plan_artifact)
                    store.upsert_node(
                        node_id="approve_chapter_plan",
                        node_type="approve_chapter_plan",
                        status="completed",
                        depends_on=["plan_chapters"],
                        output_version=f"chapter_plan@{chapter_plan_artifact['version']}",
                    )
                    event_log.write(
                        "approval_artifact_reused",
                        {
                            "runtime_id": run.runtime_id,
                            "approval_id": "chapter_plan",
                            "artifact_id": "chapter_plan",
                            "version": chapter_plan_artifact["version"],
                        },
                    )

                previous_generated_body = ""
                for contract in chapter_contracts:
                    self._progress(
                        f"准备生成第 {contract.chapter_number} 章",
                        runtime_id=run.runtime_id,
                        node_id=f"chapter-{contract.chapter_number}",
                        chapter_number=contract.chapter_number,
                        phase="scene_plan",
                        status="running",
                    )
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
                    scene_plan_id = f"scene_plan:{contract.chapter_number}"
                    plan_scenes_node_id = f"plan_scenes:{contract.chapter_number}"
                    scene_plan_artifact = _approved_artifact_if_accepted(
                        store,
                        scene_plan_id,
                        scene_plan_id,
                    )
                    if scene_plan_artifact is None:
                        self._progress(
                            f"第 {contract.chapter_number} 章：生成场景计划",
                            runtime_id=run.runtime_id,
                            node_id=plan_scenes_node_id,
                            chapter_number=contract.chapter_number,
                            phase="scene_plan",
                            status="running",
                        )
                        store.upsert_node(
                            node_id=plan_scenes_node_id,
                            node_type="plan_scenes",
                            status="running",
                            depends_on=["approve_chapter_plan"],
                            input_version=_hash_json(contract.model_dump(mode="json")),
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
                                "user_notes": settings.notes,
                            },
                        )
                        scene_plan_auto = _should_auto_approve(
                            effective_auto_approve,
                            config.approvals.auto_approve_scene_plan,
                        )
                        scene_plan_version = store.upsert_artifact(
                            artifact_id=scene_plan_id,
                            artifact_type="scene_plan",
                            payload=scene_plan.model_dump(mode="json"),
                            is_draft=not scene_plan_auto,
                            is_approved=scene_plan_auto,
                            approval_id=scene_plan_id,
                            source_node_id=plan_scenes_node_id,
                        )
                        store.upsert_node(
                            node_id=plan_scenes_node_id,
                            node_type="plan_scenes",
                            status="completed",
                            depends_on=["approve_chapter_plan"],
                            output_version=f"{scene_plan_id}@{scene_plan_version}",
                        )
                        _record_approval(
                            event_log,
                            store,
                            scene_plan_id,
                            scene_plan_auto,
                            artifact_id=scene_plan_id,
                            artifact_version=scene_plan_version,
                        )
                        store.upsert_node(
                            node_id=f"approve_scene_plan:{contract.chapter_number}",
                            node_type="approve_scene_plan",
                            status="completed" if scene_plan_auto else "waiting",
                            depends_on=[plan_scenes_node_id],
                            waiting_reason=(
                                None if scene_plan_auto else "waiting for scene plan approval"
                            ),
                            output_version=f"{scene_plan_id}@{scene_plan_version}",
                        )
                        if not scene_plan_auto:
                            self._progress(
                                f"第 {contract.chapter_number} 章：等待用户审批场景计划",
                                runtime_id=run.runtime_id,
                                node_id=f"approve_scene_plan:{contract.chapter_number}",
                                chapter_number=contract.chapter_number,
                                phase="scene_plan",
                                status="waiting",
                                waiting_approval_id=scene_plan_id,
                            )
                            store.create_or_update_approval(
                                approval_id=scene_plan_id,
                                approval_type="scene_plan",
                                status="waiting",
                                auto_approve=False,
                                artifact_id=scene_plan_id,
                                artifact_version=scene_plan_version,
                            )
                            return _pause_for_approval(
                                runtime_id=run.runtime_id,
                                log_dir=run.log_dir,
                                store=store,
                                event_log=event_log,
                                stats=stats,
                                approval_id=scene_plan_id,
                            )
                    else:
                        scene_plan = ScenePlan.model_validate(scene_plan_artifact["payload"])
                        store.upsert_node(
                            node_id=f"approve_scene_plan:{contract.chapter_number}",
                            node_type="approve_scene_plan",
                            status="completed",
                            depends_on=[plan_scenes_node_id],
                            output_version=f"{scene_plan_id}@{scene_plan_artifact['version']}",
                        )
                        event_log.write(
                            "approval_artifact_reused",
                            {
                                "runtime_id": run.runtime_id,
                                "approval_id": scene_plan_id,
                                "artifact_id": scene_plan_id,
                                "version": scene_plan_artifact["version"],
                            },
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
                        user_notes=settings.notes,
                        generation=generation,
                    )
                    try:
                        self._progress(
                            f"第 {contract.chapter_number} 章：自审与修订",
                            runtime_id=run.runtime_id,
                            node_id=f"check_chapter:{contract.chapter_number}",
                            chapter_number=contract.chapter_number,
                            phase="review",
                            status="running",
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
                            user_notes=settings.notes,
                            auto_approve_review_failure=_should_auto_approve(
                                effective_auto_approve,
                                config.approvals.auto_approve_review_failure,
                            ),
                        )
                    except ReviewFailureApprovalRequired as exc:
                        self._progress(
                            f"第 {contract.chapter_number} 章：自审失败，等待用户处理",
                            runtime_id=run.runtime_id,
                            node_id=f"review_failure:{exc.chapter_number}",
                            chapter_number=contract.chapter_number,
                            phase="review",
                            status="waiting",
                            waiting_approval_id=f"review_failure:{exc.chapter_number}",
                            severity="warning",
                        )
                        return _pause_for_approval(
                            runtime_id=run.runtime_id,
                            log_dir=run.log_dir,
                            store=store,
                            event_log=event_log,
                            stats=stats,
                            approval_id=f"review_failure:{exc.chapter_number}",
                        )
                    integrate_node_id = f"integrate_generated_chapter:{draft.chapter_number}"
                    self._progress(
                        f"第 {draft.chapter_number} 章：反哺故事状态",
                        runtime_id=run.runtime_id,
                        node_id=integrate_node_id,
                        chapter_number=draft.chapter_number,
                        phase="output",
                        status="running",
                    )
                    store.upsert_node(
                        node_id=integrate_node_id,
                        node_type="integrate_generated_chapter",
                        status="running",
                        depends_on=[f"chapter-{contract.chapter_number}"],
                        input_version=_hash_json(draft.model_dump(mode="json")),
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
                        source_node_id=integrate_node_id,
                    )
                    analyses.append(generated_analysis)
                    store.upsert_node(
                        node_id=integrate_node_id,
                        node_type="integrate_generated_chapter",
                        status="completed",
                        depends_on=[f"chapter-{contract.chapter_number}"],
                        output_version=f"chapter_analysis:{draft.chapter_number}",
                    )
                    if config.writing.refresh_range_summary_after_generation:
                        await self._summarize_generated_window(
                            draft=draft,
                            analysis=generated_analysis,
                            analyses=analyses,
                            chapters=chapters,
                            config=config,
                            runtime_id=run.runtime_id,
                            store=store,
                            stats=stats,
                            event_log=event_log,
                        )
                    try:
                        self._progress(
                            f"第 {draft.chapter_number} 章：写入输出",
                            runtime_id=run.runtime_id,
                            node_id=f"write_output:{draft.chapter_number}",
                            chapter_number=draft.chapter_number,
                            phase="output",
                            status="running",
                        )
                        output_file = _write_chapter_output(
                            store=store,
                            event_log=event_log,
                            run_log_dir=run.log_dir,
                            input_dir=run.input_dir,
                            output_mode=output_mode,
                            draft=draft,
                            depends_on=[f"integrate_generated_chapter:{draft.chapter_number}"],
                        )
                    except WriteOutputApprovalRequired as exc:
                        self._progress(
                            f"第 {draft.chapter_number} 章：写回冲突，等待用户处理",
                            runtime_id=run.runtime_id,
                            node_id=f"write_output:{draft.chapter_number}",
                            chapter_number=draft.chapter_number,
                            phase="output",
                            status="waiting",
                            waiting_approval_id=f"write_output:{draft.chapter_number}",
                            severity="warning",
                        )
                        return _pause_for_write_output(
                            runtime_id=run.runtime_id,
                            log_dir=run.log_dir,
                            store=store,
                            event_log=event_log,
                            stats=stats,
                            chapter_number=exc.chapter_number,
                            target=exc.target,
                            pending_file=exc.pending_file,
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

                self._progress(
                    "整理运行摘要",
                    runtime_id=run.runtime_id,
                    phase="output",
                    status="running",
                )
                summary = PipelineSummary(
                    runtime_id=run.runtime_id,
                    log_dir=run.log_dir,
                    generated_chapters=generated_chapters,
                    output_files=output_files,
                    stats=_run_stats_from_store(store) or stats,
                    status="completed",
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
                self._progress(
                    "运行完成",
                    runtime_id=run.runtime_id,
                    phase="output",
                    status="completed",
                )
                return summary
            except Exception as exc:
                _record_failed_run(
                    runtime_id=run.runtime_id,
                    log_dir=run.log_dir,
                    store=store,
                    event_log=event_log,
                    stats=_run_stats_from_store(store) or stats,
                    error_summary=str(exc),
                )
                self._progress(
                    f"运行失败: {exc}",
                    runtime_id=run.runtime_id,
                    phase="failed",
                    status="failed",
                    severity="error",
                )
                raise
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
        total = len(chapters)
        completed = 0

        async def analyze_one(chapter: Chapter) -> ChapterAnalysis:
            depth = "deep" if chapter.number >= deep_start else "shallow"
            node_id = f"analyze_chapter:{chapter.number}"
            self._progress(
                f"第 {chapter.number} 章：开始{depth}分析",
                runtime_id=runtime_id,
                node_id=node_id,
                chapter_number=chapter.number,
                phase="analysis",
                status="running",
                chapter_done=completed,
                chapter_total=total,
            )
            cached_artifact = store.get_latest_artifact(
                f"chapter_analysis:{chapter.number}",
                approved_only=True,
            )
            if cached_artifact is not None:
                cached_output_version = (
                    f"chapter_analysis:{chapter.number}@{cached_artifact['version']}|depth:{depth}"
                )
                try:
                    existing_node = store.get_node(node_id)
                    existing_output_version = existing_node.get("output_version")
                    if (
                        isinstance(existing_output_version, str)
                        and "|depth:deep" in existing_output_version
                    ):
                        cached_output_version = existing_output_version
                except KeyError:
                    pass
                store.upsert_node(
                    node_id=node_id,
                    node_type="analyze_chapter",
                    status="completed",
                    depends_on=["load_project"],
                    output_version=cached_output_version,
                )
                return ChapterAnalysis.model_validate(cached_artifact["payload"])
            store.upsert_node(
                node_id=node_id,
                node_type="analyze_chapter",
                status="running",
                depends_on=["load_project"],
                input_version=_hash_json(_chapter_payload(chapter) | {"depth": depth}),
            )
            try:
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
            except Exception as exc:
                store.upsert_node(
                    node_id=node_id,
                    node_type="analyze_chapter",
                    status="failed",
                    depends_on=["load_project"],
                    error_summary=str(exc),
                )
                raise
            version = store.upsert_artifact(
                artifact_id=f"chapter_analysis:{chapter.number}",
                artifact_type="chapter_analysis",
                payload=analysis.model_dump(mode="json"),
                is_approved=True,
                source_node_id=node_id,
            )
            store.upsert_node(
                node_id=node_id,
                node_type="analyze_chapter",
                status="completed",
                depends_on=["load_project"],
                output_version=f"chapter_analysis:{chapter.number}@{version}|depth:{depth}",
            )
            return cast(ChapterAnalysis, analysis)

        async def analyze_and_report(chapter: Chapter) -> ChapterAnalysis:
            nonlocal completed
            analysis = await analyze_one(chapter)
            completed += 1
            self._progress(
                f"已有章节分析进度: {completed}/{total}",
                runtime_id=runtime_id,
                node_id=f"analyze_chapter:{chapter.number}",
                chapter_number=chapter.number,
                phase="analysis",
                status="running" if completed < total else "completed",
                step_index=completed,
                step_total=total,
                chapter_done=completed,
                chapter_total=total,
            )
            return analysis

        analyses = await asyncio.gather(*(analyze_and_report(chapter) for chapter in chapters))
        return sorted(analyses, key=lambda analysis: analysis.chapter_number)

    async def _upgrade_shallow_analyses_if_needed(
        self,
        *,
        chapters: list[Chapter],
        analyses: list[ChapterAnalysis],
        config: AppConfig,
        runtime_id: str,
        store: StateStore,
        stats: RunStats,
        event_log: JsonlEventLog,
    ) -> list[ChapterAnalysis]:
        if not config.cold_start.enabled:
            return analyses
        deep_start = _deep_analysis_start(chapters, config)
        analyses_by_number = {analysis.chapter_number: analysis for analysis in analyses}
        chapters_by_number = {chapter.number: chapter for chapter in chapters}
        for analysis in list(analyses):
            if analysis.chapter_number >= deep_start:
                continue
            if _analysis_node_is_deep(store, analysis.chapter_number):
                continue
            if not _analysis_needs_deep_upgrade(analysis):
                continue
            chapter = chapters_by_number.get(analysis.chapter_number)
            if chapter is None:
                continue
            node_id = f"analyze_chapter:{chapter.number}"
            event_log.write(
                "analysis_deep_upgrade_requested",
                {
                    "chapter_number": chapter.number,
                    "reason": "shallow structured fields need richer payload",
                },
            )
            upgraded = await self._call_model(
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
                    "depth": "deep",
                    "generation": 1,
                    "upgrade_from": "shallow",
                },
            )
            version = store.upsert_artifact(
                artifact_id=f"chapter_analysis:{chapter.number}",
                artifact_type="chapter_analysis",
                payload=upgraded.model_dump(mode="json"),
                is_approved=True,
                source_node_id=node_id,
            )
            store.upsert_node(
                node_id=node_id,
                node_type="analyze_chapter",
                status="completed",
                depends_on=["load_project"],
                output_version=f"chapter_analysis:{chapter.number}@{version}|depth:deep",
            )
            analyses_by_number[chapter.number] = cast(ChapterAnalysis, upgraded)
        return [analyses_by_number[number] for number in sorted(analyses_by_number)]

    async def _summarize_ranges(
        self,
        *,
        chapters: list[Chapter],
        analyses: list[ChapterAnalysis],
        config: AppConfig,
        runtime_id: str,
        store: StateStore,
        stats: RunStats,
        event_log: JsonlEventLog,
    ) -> list[RangeSummary]:
        analyses_by_number = {analysis.chapter_number: analysis for analysis in analyses}
        summaries: list[RangeSummary] = []
        chapter_groups = _chunk_chapters(chapters, config.writing.range_summary_chapter_span)
        total = len(chapter_groups)
        for index, chapter_group in enumerate(chapter_groups, start=1):
            start = chapter_group[0].number
            end = chapter_group[-1].number
            node_id = f"summarize_range:{start}-{end}"
            artifact_id = f"range_summary:{start}-{end}"
            self._progress(
                f"区间摘要进度: {index}/{total}（第 {start}-{end} 章）",
                runtime_id=runtime_id,
                node_id=node_id,
                phase="range_summary",
                status="running",
                step_index=index,
                step_total=total,
            )
            cached_artifact = store.get_latest_artifact(artifact_id, approved_only=True)
            if cached_artifact is not None:
                summaries.append(RangeSummary.model_validate(cached_artifact["payload"]))
                store.upsert_node(
                    node_id=node_id,
                    node_type="summarize_range",
                    status="completed",
                    depends_on=[f"analyze_chapter:{chapter.number}" for chapter in chapter_group],
                    output_version=f"{artifact_id}@{cached_artifact['version']}",
                )
                continue
            store.upsert_node(
                node_id=node_id,
                node_type="summarize_range",
                status="running",
                depends_on=[f"analyze_chapter:{chapter.number}" for chapter in chapter_group],
                input_version=_hash_json([chapter.number for chapter in chapter_group]),
            )
            summary = await self._call_model(
                config=config,
                runtime_id=runtime_id,
                store=store,
                stats=stats,
                event_log=event_log,
                task_type="range_summary",
                tool_spec=_TOOL_SPECS["record_range_summary"],
                input_payload={
                    "chapters": [_chapter_payload(chapter) for chapter in chapter_group],
                    "analyses": [
                        analyses_by_number[chapter.number].model_dump(mode="json")
                        for chapter in chapter_group
                        if chapter.number in analyses_by_number
                    ],
                    "range": {"start_chapter": start, "end_chapter": end},
                },
            )
            summary = RangeSummary(
                **{
                    **summary.model_dump(mode="python"),
                    "start_chapter": start,
                    "end_chapter": end,
                }
            )
            version = store.upsert_artifact(
                artifact_id=artifact_id,
                artifact_type="range_summary",
                payload=summary.model_dump(mode="json"),
                is_approved=True,
                source_node_id=node_id,
            )
            store.record_node_artifact(
                node_id=node_id,
                artifact_id=artifact_id,
                artifact_version=version,
                direction="output",
            )
            store.upsert_node(
                node_id=node_id,
                node_type="summarize_range",
                status="completed",
                depends_on=[f"analyze_chapter:{chapter.number}" for chapter in chapter_group],
                output_version=f"{artifact_id}@{version}",
            )
            summaries.append(summary)
        return summaries

    async def _summarize_generated_window(
        self,
        *,
        draft: DraftChapter,
        analysis: ChapterAnalysis,
        analyses: list[ChapterAnalysis],
        chapters: list[Chapter],
        config: AppConfig,
        runtime_id: str,
        store: StateStore,
        stats: RunStats,
        event_log: JsonlEventLog,
    ) -> None:
        span = config.writing.range_summary_chapter_span
        start = ((draft.chapter_number - 1) // span) * span + 1
        end = start + span - 1
        node_id = f"summarize_range:{start}-{end}"
        artifact_id = f"range_summary:{start}-{end}"
        store.upsert_node(
            node_id=node_id,
            node_type="summarize_range",
            status="running",
            depends_on=[f"integrate_generated_chapter:{draft.chapter_number}"],
            input_version=_hash_json(
                {
                    "chapter_number": draft.chapter_number,
                    "generation": store.get_chapter_generation(draft.chapter_number),
                    "body_hash": _hash_json(draft.body),
                }
            ),
        )
        window_chapters = _window_chapter_payloads(
            store=store,
            chapters=chapters,
            generated_draft=draft,
            start=start,
            end=end,
        )
        window_analyses = [item for item in analyses if start <= item.chapter_number <= end]
        if not any(item.chapter_number == analysis.chapter_number for item in window_analyses):
            window_analyses.append(analysis)
        window_analyses.sort(key=lambda item: item.chapter_number)
        summary = await self._call_model(
            config=config,
            runtime_id=runtime_id,
            store=store,
            stats=stats,
            event_log=event_log,
            task_type="range_summary",
            tool_spec=_TOOL_SPECS["record_range_summary"],
            input_payload={
                "chapters": window_chapters,
                "analyses": [item.model_dump(mode="json") for item in window_analyses],
                "range": {"start_chapter": start, "end_chapter": end},
            },
            generation=store.get_chapter_generation(draft.chapter_number),
        )
        summary = RangeSummary(
            **{
                **summary.model_dump(mode="python"),
                "start_chapter": start,
                "end_chapter": max(
                    [draft.chapter_number, *[item.chapter_number for item in window_analyses]]
                ),
            }
        )
        version = store.upsert_artifact(
            artifact_id=artifact_id,
            artifact_type="range_summary",
            payload=summary.model_dump(mode="json"),
            is_approved=True,
            source_node_id=node_id,
        )
        store.record_node_artifact(
            node_id=node_id,
            artifact_id=artifact_id,
            artifact_version=version,
            direction="output",
        )
        store.upsert_node(
            node_id=node_id,
            node_type="summarize_range",
            status="completed",
            depends_on=[f"integrate_generated_chapter:{draft.chapter_number}"],
            output_version=f"{artifact_id}@{version}",
        )

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
        user_notes: str,
        generation: int,
    ) -> _DraftPackage:
        scene_drafts: list[SceneDraft] = []
        prior_scene_text = ""
        total_scenes = len(scene_plan.scenes)
        for scene_index, scene in enumerate(scene_plan.scenes, start=1):
            node_id = f"draft_scene:{contract.chapter_number}:{scene.scene_id}"
            depends_on = (
                [f"draft_scene:{contract.chapter_number}:{scene_drafts[-1].scene_id}"]
                if scene_drafts
                else [f"approve_scene_plan:{contract.chapter_number}"]
            )
            self._progress(
                f"第 {contract.chapter_number} 章：生成场景 {scene_index}/{total_scenes}",
                runtime_id=runtime_id,
                node_id=node_id,
                chapter_number=contract.chapter_number,
                phase="draft",
                status="running",
                step_index=scene_index,
                step_total=total_scenes,
            )
            store.upsert_node(
                node_id=node_id,
                node_type="draft_scene",
                status="running",
                depends_on=depends_on,
                input_version=_hash_json(
                    {
                        "chapter_contract": contract.model_dump(mode="json"),
                        "scene_contract": scene.model_dump(mode="json"),
                        "prior_scene_hash": _hash_json(prior_scene_text),
                    }
                ),
            )
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
                        chapters=[],
                        budget=config.writing.retrieval_token_budget,
                    ),
                    "previous_generated_body": previous_generated_body,
                    "prior_scene_text": prior_scene_text,
                    "user_notes": user_notes,
                },
            )
            scene_drafts.append(scene_draft)
            store.upsert_artifact(
                artifact_id=f"scene_draft:{contract.chapter_number}:{scene.scene_id}",
                artifact_type="scene_draft",
                payload=scene_draft.model_dump(mode="json"),
                is_approved=True,
                source_node_id=node_id,
            )
            store.upsert_node(
                node_id=node_id,
                node_type="draft_scene",
                status="completed",
                depends_on=depends_on,
                output_version=f"scene_draft:{contract.chapter_number}:{scene.scene_id}",
            )
            prior_scene_text = scene_draft.text
        assemble_node_id = f"assemble_chapter:{contract.chapter_number}"
        store.upsert_node(
            node_id=assemble_node_id,
            node_type="assemble_chapter",
            status="running",
            depends_on=[
                f"draft_scene:{contract.chapter_number}:{scene.scene_id}"
                for scene in scene_plan.scenes
            ],
        )
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
            source_node_id=assemble_node_id,
        )
        store.upsert_node(
            node_id=assemble_node_id,
            node_type="assemble_chapter",
            status="completed",
            depends_on=[
                f"draft_scene:{contract.chapter_number}:{scene.scene_id}"
                for scene in scene_plan.scenes
            ],
            output_version=f"chapter_draft:{contract.chapter_number}",
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
        user_notes: str,
        auto_approve_review_failure: bool,
    ) -> DraftChapter:
        current = draft
        for attempt in range(max_revision_rounds + 1):
            store.upsert_node(
                node_id=f"check_chapter:{contract.chapter_number}",
                node_type="check_chapter",
                status="running",
                depends_on=[f"assemble_chapter:{contract.chapter_number}"],
                attempt=attempt,
                input_version=_hash_json(current.model_dump(mode="json")),
            )
            check_report = run_chapter_checks(
                contract=contract,
                draft=current,
                plot_threads=_plot_threads_from_index(store.load_story_index()),
                scene_contracts=scene_plan.scenes,
                scene_drafts=scene_drafts,
                tolerance_ratio=config.writing.word_count_tolerance_ratio,
            )
            if not check_report.passed:
                store.upsert_node(
                    node_id=f"check_chapter:{contract.chapter_number}",
                    node_type="check_chapter",
                    status="failed",
                    depends_on=[f"assemble_chapter:{contract.chapter_number}"],
                    attempt=attempt,
                    error_summary=json.dumps(
                        [issue.model_dump(mode="json") for issue in check_report.issues],
                        ensure_ascii=False,
                    ),
                )
                deterministic_issues = [
                    issue.model_dump(mode="json") for issue in check_report.issues
                ]
                if attempt >= max_revision_rounds:
                    approval_id = f"review_failure:{contract.chapter_number}"
                    if _approval_is_accepted(store, approval_id):
                        event_log.write(
                            "review_failure_approval_reused",
                            {
                                "chapter_number": contract.chapter_number,
                                "approval_id": approval_id,
                            },
                        )
                        return current
                    _handle_review_failure_limit(
                        event_log=event_log,
                        store=store,
                        chapter_number=contract.chapter_number,
                        issues=deterministic_issues,
                        auto_approve=auto_approve_review_failure,
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
                    user_notes=user_notes,
                )
                scene_drafts = []
                continue
            store.upsert_node(
                node_id=f"check_chapter:{contract.chapter_number}",
                node_type="check_chapter",
                status="completed",
                depends_on=[f"assemble_chapter:{contract.chapter_number}"],
                attempt=attempt,
            )

            store.upsert_node(
                node_id=f"review_chapter:{contract.chapter_number}",
                node_type="review_chapter",
                status="running",
                depends_on=[f"check_chapter:{contract.chapter_number}"],
                attempt=attempt,
                input_version=_hash_json(current.model_dump(mode="json")),
            )
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
                    "user_notes": user_notes,
                },
            )
            post_review_report = run_chapter_checks(
                contract=contract,
                draft=current,
                plot_threads=_plot_threads_from_index(store.load_story_index()),
                scene_contracts=scene_plan.scenes if scene_drafts else None,
                scene_drafts=scene_drafts if scene_drafts else None,
                tolerance_ratio=config.writing.word_count_tolerance_ratio,
                resolved_thread_ids=review.resolved_thread_ids,
            )
            if not post_review_report.passed:
                review = ChapterReview(
                    passed=False,
                    issues=[
                        *review.issues,
                        *[issue.message for issue in post_review_report.issues],
                    ],
                    resolved_thread_ids=review.resolved_thread_ids,
                )
            if review.passed:
                store.upsert_node(
                    node_id=f"review_chapter:{contract.chapter_number}",
                    node_type="review_chapter",
                    status="completed",
                    depends_on=[f"check_chapter:{contract.chapter_number}"],
                    attempt=attempt,
                )
                return current
            store.upsert_node(
                node_id=f"review_chapter:{contract.chapter_number}",
                node_type="review_chapter",
                status="failed",
                depends_on=[f"check_chapter:{contract.chapter_number}"],
                attempt=attempt,
                error_summary=json.dumps(review.issues, ensure_ascii=False),
            )
            if attempt >= max_revision_rounds:
                approval_id = f"review_failure:{contract.chapter_number}"
                if _approval_is_accepted(store, approval_id):
                    event_log.write(
                        "review_failure_approval_reused",
                        {
                            "chapter_number": contract.chapter_number,
                            "approval_id": approval_id,
                        },
                    )
                    return current
                _handle_review_failure_limit(
                    event_log=event_log,
                    store=store,
                    chapter_number=contract.chapter_number,
                    issues=review.issues,
                    auto_approve=auto_approve_review_failure,
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
                user_notes=user_notes,
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
        user_notes: str,
    ) -> DraftChapter:
        node_id = f"revise_chapter:{contract.chapter_number}"
        store.upsert_node(
            node_id=node_id,
            node_type="revise_chapter",
            status="running",
            depends_on=[
                f"review_chapter:{contract.chapter_number}",
                f"check_chapter:{contract.chapter_number}",
            ],
            attempt=attempt,
            input_version=_hash_json(
                {
                    "draft": draft.model_dump(mode="json"),
                    "reason": reason,
                    "generation": generation,
                    "user_notes": user_notes,
                }
            ),
        )
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
                "user_notes": user_notes,
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
            source_node_id=node_id,
        )
        store.upsert_node(
            node_id=node_id,
            node_type="revise_chapter",
            status="completed",
            depends_on=[
                f"review_chapter:{contract.chapter_number}",
                f"check_chapter:{contract.chapter_number}",
            ],
            attempt=attempt,
            output_version=f"chapter_draft:{contract.chapter_number}",
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
        phase = _phase_for_task_type(task_type)
        chapter_number = _chapter_number_from_call_payload(input_payload)
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
            self._progress(
                f"复用模型结果: {task_type}",
                runtime_id=runtime_id,
                chapter_number=chapter_number,
                phase=phase,
                status="reused",
                llm_task_type=task_type,
                llm_profile=profile_name,
            )
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

        max_attempts = profile.max_retries + 1
        last_error: Exception | None = None
        for local_attempt_index in range(max_attempts):
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
                        input_payload
                        if config.runtime.save_full_prompts
                        else _hash_json(input_payload)
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
            self._progress(
                f"请求模型: {task_type}（{profile_name}/{profile.model}，attempt {attempt}）",
                runtime_id=runtime_id,
                chapter_number=chapter_number,
                phase=phase,
                status="running",
                llm_task_type=task_type,
                llm_profile=profile_name,
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
                validated = tool_spec.model.model_validate(result.payload)
            except Exception as exc:
                last_error = exc
                store.fail_llm_call(call_id=llm_call_id, error=str(exc))
                should_retry = local_attempt_index + 1 < max_attempts and _is_retryable_llm_error(
                    exc
                )
                event_log.write(
                    "llm_response_failed",
                    {
                        "task_type": task_type,
                        "profile": profile_name,
                        "tool_name": tool_spec.name,
                        "idempotency_key": key,
                        "attempt": attempt,
                        "generation": generation,
                        "error": str(exc),
                        "retrying": should_retry,
                    },
                )
                self._progress(
                    (
                        f"模型请求失败，准备重试: {task_type}: {exc}"
                        if should_retry
                        else f"模型请求失败: {task_type}: {exc}"
                    ),
                    runtime_id=runtime_id,
                    chapter_number=chapter_number,
                    phase=phase,
                    status="running" if should_retry else "failed",
                    llm_task_type=task_type,
                    llm_profile=profile_name,
                    severity="warning" if should_retry else "error",
                )
                if should_retry:
                    continue
                raise
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
                    "attempt": attempt,
                    "usage": result.usage.model_dump(mode="json", exclude_none=True),
                },
            )
            self._progress(
                f"模型完成: {task_type}",
                runtime_id=runtime_id,
                chapter_number=chapter_number,
                phase=phase,
                status="completed",
                llm_task_type=task_type,
                llm_profile=profile_name,
            )
            event_log.write(
                "tool_result",
                {
                    "tool_name": tool_spec.name,
                    "payload": result.payload,
                },
            )
            return validated
        if last_error is not None:
            raise last_error
        raise RuntimeError(f"model call failed without error: {task_type}")


def _bucket(target: dict[str, UsageBucket], key: str) -> UsageBucket:
    if key not in target:
        target[key] = UsageBucket()
    return target[key]


def _add_optional_tokens(current: int | None, value: int | None) -> int | None:
    if value is None:
        return current
    return (current or 0) + value


def _phase_for_task_type(task_type: str) -> str | None:
    return {
        "chapter_extraction": "analysis",
        "range_summary": "range_summary",
        "story_merge": "story_state",
        "outline_planning": "outline",
        "outline_chat": "outline",
        "chapter_planning": "chapter_plan",
        "chapter_plan_chat": "chapter_plan",
        "scene_planning": "scene_plan",
        "scene_plan_chat": "scene_plan",
        "drafting": "draft",
        "review": "review",
        "revision": "review",
    }.get(task_type)


def _chapter_number_from_call_payload(payload: Mapping[str, object]) -> int | None:
    direct = payload.get("chapter_number")
    if isinstance(direct, int) and not isinstance(direct, bool):
        return direct
    for key in ("chapter_contract", "draft", "scene_contract"):
        nested = payload.get(key)
        if not isinstance(nested, dict):
            continue
        value = nested.get("chapter_number")
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


def _is_retryable_llm_error(exc: Exception) -> bool:
    if isinstance(exc, ToolPayloadParseError | json.JSONDecodeError | ValidationError):
        return True
    return isinstance(exc, ValueError) and str(exc).startswith("model did not call expected tool")


def _sum_usage_buckets(buckets: Iterable[UsageBucket]) -> UsageBucket:
    total = UsageBucket()
    for bucket in buckets:
        total.calls += bucket.calls
        total.input_tokens += bucket.input_tokens
        total.output_tokens += bucket.output_tokens
        total.total_tokens += bucket.total_tokens
        total.reasoning_tokens = _add_optional_tokens(
            total.reasoning_tokens,
            bucket.reasoning_tokens,
        )
        total.cached_tokens = _add_optional_tokens(total.cached_tokens, bucket.cached_tokens)
        total.cache_read_tokens = _add_optional_tokens(
            total.cache_read_tokens,
            bucket.cache_read_tokens,
        )
        total.cache_write_tokens = _add_optional_tokens(
            total.cache_write_tokens,
            bucket.cache_write_tokens,
        )
    return total


def _run_stats_from_store(store: StateStore) -> RunStats | None:
    rows = store.usage_summary()
    if not rows:
        return None
    stats = RunStats()
    for row in rows:
        usage = _normalized_usage_from_row(row.get("normalized_usage_json"))
        if usage is None:
            continue
        stats.add(
            ToolCallResult(
                payload={},
                usage=usage,
                profile_name=str(row["profile"]),
                model=str(row["model"]),
                task_type=str(row["task_type"]),
            )
        )
    return stats if stats.total_calls > 0 else None


def _normalized_usage_from_row(value: object) -> NormalizedUsage | None:
    if not isinstance(value, str):
        return None
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        return None
    return NormalizedUsage.model_validate(parsed)


def _required_input_dir(options: GenerationOptions) -> Path:
    if options.input_dir is None:
        raise ValueError("input_dir is required when starting a new run")
    return options.input_dir


def _settings_for_run(
    *,
    options: GenerationOptions,
    config: AppConfig | None,
    run: WorkflowRun,
    store: StateStore,
) -> RunSettings:
    if options.runtime_id is not None:
        stored = store.get_run_settings(options.runtime_id)
        if stored:
            return RunSettings.model_validate(stored)
        if config is None:
            config = load_config(options.config_path)

    if config is None:
        raise ValueError("config is required when creating run settings")
    settings = RunSettings(
        input_dir=_required_input_dir(options).resolve(),
        config_path=options.config_path,
        output_mode=options.output_mode or config.runtime.output_mode,
        chapter_count=options.chapter_count,
        start_chapter=options.start_chapter,
        min_chars=options.min_chars,
        max_chars=options.max_chars,
        max_revision_rounds=(
            options.max_revision_rounds
            if options.max_revision_rounds is not None
            else config.writing.max_revision_rounds
        ),
        auto_approve=options.auto_approve,
        notes=_combined_notes(options.notes, options.notes_path),
        notes_path=options.notes_path,
    )
    settings_payload = settings.model_dump(mode="json")
    store.update_run_settings(run.runtime_id, settings_payload)
    artifacts_dir = run.log_dir / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    _write_json(artifacts_dir / "run_settings.json", settings_payload)
    store.upsert_artifact(
        artifact_id="run_settings",
        artifact_type="run_settings",
        payload=settings_payload,
        is_approved=True,
    )
    return settings


def _combined_notes(inline_notes: str, notes_path: Path | None) -> str:
    parts: list[str] = []
    if notes_path is not None:
        note_file_text = notes_path.read_text(encoding="utf-8-sig")
        if note_file_text.strip():
            parts.append(note_file_text.strip())
    if inline_notes.strip():
        parts.append(inline_notes.strip())
    return "\n\n".join(parts)


def _drop_legacy_optional_zero_usage(bucket: object) -> object:
    if not isinstance(bucket, dict):
        return bucket
    normalized = dict(bucket)
    for key in (
        "reasoning_tokens",
        "cached_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
    ):
        if normalized.get(key) == 0:
            normalized.pop(key)
    return normalized


def _chapter_payload(chapter: Chapter) -> dict[str, object]:
    return {
        "number": chapter.number,
        "title": chapter.title,
        "body": chapter.body,
    }


def _chunk_chapters(chapters: list[Chapter], span: int) -> list[list[Chapter]]:
    if span <= 0:
        raise ValueError("span must be positive")
    return [chapters[index : index + span] for index in range(0, len(chapters), span)]


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
    chapters: list[Chapter],
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
        items.extend(story_index.retrieval_items(max_items=30))
        items.extend(_source_excerpt_items(story_index=story_index, chapters=chapters))
    return trim_retrieval_items(items, budget)


def _source_excerpt_items(
    *,
    story_index: StoryIndex,
    chapters: list[Chapter],
) -> list[dict[str, object]]:
    chapters_by_number = {chapter.number: chapter for chapter in chapters}
    source_chapters: dict[int, int] = {}
    for thread in story_index.plot_threads.values():
        priority = 2 if thread.status.value not in {"resolved", "abandoned"} else 6
        source_chapters[thread.source_chapter] = min(
            priority,
            source_chapters.get(thread.source_chapter, priority),
        )
        for chapter_number in thread.reinforced_chapters[-2:]:
            source_chapters[chapter_number] = min(3, source_chapters.get(chapter_number, 3))
    for event in story_index.events.values():
        source_chapters[event.chapter_number] = min(
            event.importance,
            source_chapters.get(event.chapter_number, event.importance),
        )
    for rule in story_index.world_rules.values():
        source_chapters[rule.source_chapter] = min(
            rule.importance,
            source_chapters.get(rule.source_chapter, rule.importance),
        )
    items: list[dict[str, object]] = []
    for chapter_number, priority in sorted(
        source_chapters.items(), key=lambda item: (item[1], item[0])
    ):
        chapter = chapters_by_number.get(chapter_number)
        if chapter is None:
            continue
        items.append(
            {
                "priority": priority,
                "kind": "source_excerpt",
                "chapter_number": chapter_number,
                "text": f"{chapter_number}《{chapter.title}》原文片段: {_excerpt(chapter.body)}",
            }
        )
    return items


def _excerpt(text: str, limit: int = 360) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[:limit].rstrip()


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
    try:
        parsed = json.loads(tool_call.arguments_json)
    except json.JSONDecodeError as exc:
        raise ToolPayloadParseError(
            f"tool {tool_call.name} returned invalid JSON arguments: "
            f"{exc.msg} at line {exc.lineno} column {exc.colno}"
        ) from exc
    if not isinstance(parsed, dict):
        raise ToolPayloadParseError(f"tool {tool_call.name} returned non-object arguments")
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
    "update_outline": _ToolSpec(
        name="update_outline",
        model=OutlineUpdate,
        schema=_tool_schema("update_outline", OutlineUpdate),
    ),
    "propose_chapter_plan": _ToolSpec(
        name="propose_chapter_plan",
        model=_ChapterPlanTool,
        schema=_tool_schema("propose_chapter_plan", _ChapterPlanTool),
    ),
    "update_chapter_plan": _ToolSpec(
        name="update_chapter_plan",
        model=ChapterPlanUpdate,
        schema=_tool_schema("update_chapter_plan", ChapterPlanUpdate),
    ),
    "propose_scene_plan": _ToolSpec(
        name="propose_scene_plan",
        model=ScenePlan,
        schema=_tool_schema("propose_scene_plan", ScenePlan),
    ),
    "update_scene_plan": _ToolSpec(
        name="update_scene_plan",
        model=ScenePlanUpdate,
        schema=_tool_schema("update_scene_plan", ScenePlanUpdate),
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


def _tool_spec_for_artifact_update(artifact_type: str) -> _ToolSpec:
    if artifact_type == "outline":
        return _TOOL_SPECS["update_outline"]
    if artifact_type == "chapter_plan":
        return _TOOL_SPECS["update_chapter_plan"]
    if artifact_type == "scene_plan":
        return _TOOL_SPECS["update_scene_plan"]
    raise ValueError(f"artifact_type does not support chat updates: {artifact_type}")


def _chat_task_for_artifact(artifact_type: str) -> str:
    if artifact_type == "outline":
        return "outline_chat"
    if artifact_type == "chapter_plan":
        return "chapter_planning"
    if artifact_type == "scene_plan":
        return "scene_chat"
    raise ValueError(f"artifact_type does not support chat updates: {artifact_type}")


def _artifact_update_change_summary(updated: BaseModel) -> str:
    value = getattr(updated, "change_summary", None)
    if isinstance(value, str) and value.strip():
        return value
    return "已根据本轮审批聊天更新产物。"


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


def _analysis_needs_deep_upgrade(analysis: ChapterAnalysis) -> bool:
    if analysis.plot_threads and not analysis.plot_thread_facts:
        return True
    if analysis.plot_thread_facts:
        return False
    if not analysis.plot_threads and not analysis.suspense:
        return False
    facts = _facts_from_analysis(analysis, generation=1)
    for fact in facts:
        if fact.kind != "plot_thread":
            continue
        payload = fact.payload
        has_source = isinstance(payload.get("source_chapter"), int)
        has_thread_id = isinstance(payload.get("thread_id"), str)
        has_status = isinstance(payload.get("status"), str)
        has_resolution_window = isinstance(payload.get("due_chapter"), int) or isinstance(
            payload.get("resolution_end_chapter"),
            int,
        )
        if not (has_source and has_thread_id and has_status and has_resolution_window):
            return True
    return False


def _window_chapter_payloads(
    *,
    store: StateStore,
    chapters: list[Chapter],
    generated_draft: DraftChapter,
    start: int,
    end: int,
) -> list[dict[str, object]]:
    payloads: list[dict[str, object]] = [
        _chapter_payload(chapter) for chapter in chapters if start <= chapter.number <= end
    ]
    if start <= generated_draft.chapter_number <= end:
        payloads.append(
            {
                "number": generated_draft.chapter_number,
                "title": generated_draft.title,
                "body": generated_draft.body,
                "source": "generated",
            }
        )
    existing_numbers = {
        number for payload in payloads if (number := _payload_number(payload)) is not None
    }
    for chapter_number in range(start, end + 1):
        if chapter_number in existing_numbers:
            continue
        artifact = store.get_latest_artifact(f"chapter_draft:{chapter_number}")
        if artifact is None:
            continue
        try:
            draft = DraftChapter.model_validate(artifact["payload"])
        except ValueError:
            continue
        payloads.append(
            {
                "number": draft.chapter_number,
                "title": draft.title,
                "body": draft.body,
                "source": "generated",
            }
        )
    return sorted(payloads, key=lambda item: _payload_number(item) or 0)


def _payload_number(payload: dict[str, object]) -> int | None:
    value = payload.get("number")
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def _analysis_node_is_deep(store: StateStore, chapter_number: int) -> bool:
    try:
        node = store.get_node(f"analyze_chapter:{chapter_number}")
    except KeyError:
        return False
    output_version = node.get("output_version")
    return isinstance(output_version, str) and "|depth:deep" in output_version


def _plot_threads_from_index(index: StoryIndex) -> list[PlotThread]:
    threads: list[PlotThread] = []
    for entry in index.plot_threads.values():
        due_chapter = (
            entry.resolution_window.end_chapter if entry.resolution_window is not None else None
        )
        abandoned_chapter = entry.resolved_chapter if entry.status.value == "abandoned" else None
        threads.append(
            PlotThread(
                thread_id=entry.thread_id,
                description=entry.description,
                status=entry.status,
                source_chapter=entry.source_chapter,
                due_chapter=due_chapter,
                resolved_chapter=entry.resolved_chapter
                if entry.status.value == "resolved"
                else None,
                abandoned_chapter=abandoned_chapter,
                related_keywords=entry.keywords,
            )
        )
    return threads


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
    return facts_from_chapter_analysis(
        chapter_number=analysis.chapter_number,
        generation=generation,
        worldbuilding=analysis.worldbuilding,
        plot_threads=analysis.plot_threads,
        suspense=analysis.suspense,
        character_facts=analysis.character_facts,
        worldbuilding_facts=analysis.worldbuilding_facts,
        plot_thread_facts=analysis.plot_thread_facts,
        event_facts=analysis.event_facts,
    )


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
    store: StateStore,
    event_log: JsonlEventLog,
    run_log_dir: Path,
    input_dir: Path,
    output_mode: str,
    draft: DraftChapter,
    depends_on: list[str],
) -> Path:
    node_id = f"write_output:{draft.chapter_number}"
    content = f"title: {draft.title}\n---\n{draft.body}"
    store.upsert_node(
        node_id=node_id,
        node_type="write_output",
        status="running",
        depends_on=depends_on,
        input_version=_hash_json(draft.model_dump(mode="json")),
    )
    if output_mode == "output":
        target = run_log_dir / "outputs" / "chapters" / f"{draft.chapter_number}.txt"
        atomic_write_text(target, content)
        store.upsert_node(
            node_id=node_id,
            node_type="write_output",
            status="completed",
            depends_on=depends_on,
            output_version=str(target),
        )
        return target
    if output_mode == "writeback":
        target = input_dir / f"{draft.chapter_number}.txt"
        pending_target = (
            run_log_dir / "outputs" / "pending_writeback" / f"{draft.chapter_number}.txt"
        )
        if target.exists():
            atomic_write_text(pending_target, content)
            store.upsert_node(
                node_id=node_id,
                node_type="write_output",
                status="waiting",
                depends_on=depends_on,
                waiting_reason=f"writeback target already exists: {target}",
                output_version=str(pending_target),
            )
            event_log.write(
                "write_output_waiting",
                {
                    "chapter_number": draft.chapter_number,
                    "target": str(target),
                    "pending_file": str(pending_target),
                },
            )
            raise WriteOutputApprovalRequired(
                chapter_number=draft.chapter_number,
                target=target,
                pending_file=pending_target,
            )
        source = pending_target if pending_target.exists() else None
        target.parent.mkdir(parents=True, exist_ok=True)
        if source is None:
            atomic_write_text(target, content)
        else:
            atomic_move_text(source, target)
        store.upsert_node(
            node_id=node_id,
            node_type="write_output",
            status="completed",
            depends_on=depends_on,
            output_version=str(target),
            waiting_reason=None,
        )
        return target
    raise ValueError(f"unknown output_mode: {output_mode}")


def _resume_completed_summary(store: StateStore) -> PipelineSummary | None:
    artifact = store.get_latest_artifact("run_summary", approved_only=True)
    if artifact is None:
        return None
    summary = PipelineSummary.model_validate(artifact["payload"])
    return summary if summary.status == "completed" else None


def _resume_pending_write_output_summary(
    *,
    store: StateStore,
    runtime_id: str,
    log_dir: Path,
    input_dir: Path,
    event_log: JsonlEventLog,
) -> PipelineSummary | None:
    artifact = store.get_latest_artifact("run_summary", approved_only=True)
    if artifact is None:
        return None
    summary = PipelineSummary.model_validate(artifact["payload"])
    if summary.status != "waiting_write_output" or summary.waiting_node_id is None:
        return None
    prefix = "write_output:"
    if not summary.waiting_node_id.startswith(prefix):
        return None
    try:
        chapter_number = int(summary.waiting_node_id.removeprefix(prefix))
    except ValueError:
        return None
    pending_file = log_dir / "outputs" / "pending_writeback" / f"{chapter_number}.txt"
    target = input_dir / f"{chapter_number}.txt"
    if not pending_file.is_file() or target.exists():
        return summary
    target.parent.mkdir(parents=True, exist_ok=True)
    atomic_move_text(pending_file, target)
    store.upsert_node(
        node_id=summary.waiting_node_id,
        node_type="write_output",
        status="completed",
        output_version=str(target),
        waiting_reason=None,
    )
    completed = PipelineSummary(
        runtime_id=summary.runtime_id,
        log_dir=summary.log_dir,
        generated_chapters=[chapter_number],
        output_files=[target],
        stats=summary.stats,
        status="completed",
    )
    _write_json(log_dir / "artifacts" / "run_summary.json", completed.model_dump(mode="json"))
    store.upsert_artifact(
        artifact_id="run_summary",
        artifact_type="run_summary",
        payload=completed.model_dump(mode="json"),
        is_approved=True,
    )
    store.update_run_status(runtime_id, "completed")
    event_log.write(
        "write_output_resumed",
        {
            "runtime_id": runtime_id,
            "chapter_number": chapter_number,
            "target": str(target),
        },
    )
    return completed


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


def _pause_for_approval(
    *,
    runtime_id: str,
    log_dir: Path,
    store: StateStore,
    event_log: JsonlEventLog,
    stats: RunStats,
    approval_id: str,
) -> PipelineSummary:
    summary = PipelineSummary(
        runtime_id=runtime_id,
        log_dir=log_dir,
        generated_chapters=[],
        output_files=[],
        stats=stats,
        status="waiting_approval",
        waiting_approval_id=approval_id,
    )
    artifacts_dir = log_dir / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    _write_json(artifacts_dir / "run_summary.json", summary.model_dump(mode="json"))
    store.upsert_artifact(
        artifact_id="run_summary",
        artifact_type="run_summary",
        payload=summary.model_dump(mode="json"),
        is_approved=True,
    )
    store.update_run_status(runtime_id, "waiting_approval")
    event_log.write(
        "run_waiting_approval",
        {
            "runtime_id": runtime_id,
            "approval_id": approval_id,
        },
    )
    return summary


def _pause_for_write_output(
    *,
    runtime_id: str,
    log_dir: Path,
    store: StateStore,
    event_log: JsonlEventLog,
    stats: RunStats,
    chapter_number: int,
    target: Path,
    pending_file: Path,
) -> PipelineSummary:
    approval_id = f"write_output:{chapter_number}"
    store.create_or_update_approval(
        approval_id=approval_id,
        approval_type="write_output",
        status="waiting",
        auto_approve=False,
    )
    summary = PipelineSummary(
        runtime_id=runtime_id,
        log_dir=log_dir,
        generated_chapters=[],
        output_files=[],
        stats=stats,
        status="waiting_write_output",
        waiting_approval_id=approval_id,
        waiting_node_id=approval_id,
    )
    artifacts_dir = log_dir / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    _write_json(artifacts_dir / "run_summary.json", summary.model_dump(mode="json"))
    store.upsert_artifact(
        artifact_id="run_summary",
        artifact_type="run_summary",
        payload=summary.model_dump(mode="json"),
        is_approved=True,
    )
    store.update_run_status(runtime_id, "waiting_write_output")
    event_log.write(
        "run_waiting_write_output",
        {
            "runtime_id": runtime_id,
            "chapter_number": chapter_number,
            "target": str(target),
            "pending_file": str(pending_file),
        },
    )
    return summary


def _record_failed_run(
    *,
    runtime_id: str,
    log_dir: Path,
    store: StateStore,
    event_log: JsonlEventLog,
    stats: RunStats,
    error_summary: str,
) -> PipelineSummary:
    failed_nodes = store.fail_running_nodes(error_summary=f"run failed: {error_summary}")
    failed_llm_calls = store.fail_running_llm_calls(runtime_id=runtime_id, error=error_summary)
    summary = PipelineSummary(
        runtime_id=runtime_id,
        log_dir=log_dir,
        generated_chapters=[],
        output_files=[],
        stats=stats,
        status="failed",
        error_summary=error_summary,
    )
    artifacts_dir = log_dir / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    _write_json(artifacts_dir / "run_summary.json", summary.model_dump(mode="json"))
    store.upsert_artifact(
        artifact_id="run_summary",
        artifact_type="run_summary",
        payload=summary.model_dump(mode="json"),
        is_approved=True,
    )
    store.update_run_status(runtime_id, "failed")
    event_log.write(
        "run_failed",
        {
            "runtime_id": runtime_id,
            "error": error_summary,
            "failed_running_nodes": failed_nodes,
            "failed_running_llm_calls": failed_llm_calls,
        },
    )
    return summary


def _write_json(path: Path, payload: object) -> None:
    TypeAdapter(object).validate_python(payload)
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def _should_auto_approve(global_auto_approve: bool, scoped_auto_approve: bool) -> bool:
    return global_auto_approve or scoped_auto_approve


def _approval_is_accepted(store: StateStore, approval_id: str) -> bool:
    approval = store.get_approval(approval_id)
    return approval is not None and approval["status"] == "accepted"


def _approved_artifact_if_accepted(
    store: StateStore,
    approval_id: str,
    artifact_id: str,
) -> dict[str, object] | None:
    if not _approval_is_accepted(store, approval_id):
        return None
    return store.get_latest_artifact(artifact_id, approved_only=True)


def _outline_from_artifact(artifact: dict[str, object]) -> OutlineProposal:
    payload = artifact["payload"]
    if not isinstance(payload, dict):
        raise ValueError("outline artifact payload must be an object")
    if "change_summary" in payload:
        update = OutlineUpdate.model_validate(payload)
        return OutlineProposal(outline=update.outline, notes=update.notes)
    return OutlineProposal.model_validate(payload)


def _chapter_contracts_from_artifact(artifact: dict[str, object]) -> list[ChapterContract]:
    payload = artifact["payload"]
    if isinstance(payload, list):
        return [ChapterContract.model_validate(item) for item in payload]
    if isinstance(payload, dict):
        if "change_summary" in payload:
            return ChapterPlanUpdate.model_validate(payload).chapters
        return ChapterPlan.model_validate(payload).chapters
    raise ValueError("chapter plan artifact payload must be a list or object")


def _handle_review_failure_limit(
    *,
    event_log: JsonlEventLog,
    store: StateStore,
    chapter_number: int,
    issues: Sequence[object],
    auto_approve: bool,
) -> None:
    approval_id = f"review_failure:{chapter_number}"
    store.create_or_update_approval(
        approval_id=approval_id,
        approval_type="review_failure",
        status="accepted" if auto_approve else "waiting",
        auto_approve=auto_approve,
    )
    event_log.write(
        "review_failure_auto_accepted" if auto_approve else "review_failure_waiting",
        {
            "chapter_number": chapter_number,
            "issues": issues,
            "approval_id": approval_id,
            "auto_approve": auto_approve,
        },
    )
    if not auto_approve:
        raise ReviewFailureApprovalRequired(chapter_number=chapter_number, issues=issues)


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
    if auto_approve and artifact_id is not None and artifact_version is not None:
        store.approve_artifact_version(artifact_id, artifact_version)
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
