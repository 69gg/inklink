from __future__ import annotations

import asyncio
import contextlib
import difflib
import json
import os
import time
from dataclasses import asdict
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from inklink.config import api_key_for_profile, load_config
from inklink.workflow.pipeline import (
    ContinuationMode,
    GenerationOptions,
    InklinkPipeline,
    OpenAIToolLLM,
    PipelineProgress,
    PipelineSummary,
)
from inklink.workflow.service import WorkflowService

from .snapshot import load_run_snapshot


class RunOptionsPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_dir: str | None = None
    config_path: str = "config.toml"
    log_root: str = "logs"
    output_mode: Literal["output", "writeback"] | None = None
    continuation_mode: ContinuationMode = "fixed"
    chapter_count: int = Field(default=1, gt=0)
    ending_min_chapters: int | None = Field(default=None, gt=0)
    ending_max_chapters: int | None = Field(default=None, gt=0)
    start_chapter: int | None = Field(default=None, gt=0)
    min_chars: int = Field(default=800, ge=0)
    max_chars: int = Field(default=1800, ge=0)
    max_revision_rounds: int | None = Field(default=None, ge=0)
    auto_approve: bool = False
    notes: str = ""
    notes_file: str | None = None


class ResumeOptionsPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    config_path: str = "config.toml"
    log_root: str = "logs"
    auto_approve: bool = False


class ApprovalMessagePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approval_id: str = Field(min_length=1)
    content: str = Field(min_length=1)
    role: str = "user"


class ChatUpdatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approval_id: str = Field(min_length=1)
    artifact_id: str = Field(min_length=1)
    artifact_type: Literal["outline", "chapter_plan", "scene_plan"]
    content: str = Field(min_length=1)
    config_path: str = "config.toml"


class ApprovePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approval_id: str = Field(min_length=1)
    artifact_id: str = Field(min_length=1)
    artifact_version: int = Field(gt=0)
    approval_type: str | None = None


class RetryPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: str = Field(min_length=1)


class RuntimeManager:
    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task[PipelineSummary]] = {}
        self._progress: dict[str, PipelineProgress] = {}
        self._last_progress_at: dict[str, float] = {}
        self._errors: dict[str, str] = {}
        self._subscribers: dict[str, set[asyncio.Queue[dict[str, object]]]] = {}
        self._pending_start_future: asyncio.Future[str] | None = None

    async def start(self, options: GenerationOptions) -> str:
        runtime_id = options.runtime_id
        if runtime_id is not None and self.is_running(runtime_id):
            raise ValueError(f"runtime is already running: {runtime_id}")
        loop = asyncio.get_running_loop()
        start_future: asyncio.Future[str] | None = None
        if runtime_id is None:
            start_future = loop.create_future()
            self._pending_start_future = start_future
        task = loop.create_task(self._run(options), name="inklink-web-pipeline")
        if start_future is not None:
            task.add_done_callback(lambda item: _propagate_start_failure(item, start_future))
        if runtime_id is not None:
            self._tasks[runtime_id] = task
            return runtime_id
        if start_future is None:
            raise RuntimeError("start future was not initialized")
        try:
            created_runtime_id = await asyncio.wait_for(start_future, timeout=5.0)
        except Exception:
            task.cancel()
            raise
        self._tasks[created_runtime_id] = task
        return created_runtime_id

    def is_running(self, runtime_id: str) -> bool:
        task = self._tasks.get(runtime_id)
        return task is not None and not task.done()

    def snapshot(self, *, log_root: Path, runtime_id: str | None) -> dict[str, object]:
        progress = self._progress.get(runtime_id or "")
        last_at = self._last_progress_at.get(runtime_id or "")
        age = time.monotonic() - last_at if last_at is not None else None
        return load_run_snapshot(
            log_root=log_root,
            runtime_id=runtime_id,
            latest_progress=progress,
            pipeline_running=self.is_running(runtime_id) if runtime_id else False,
            last_progress_age_seconds=age,
            error=self._errors.get(runtime_id or ""),
        ).to_payload()

    async def subscribe(self, runtime_id: str) -> asyncio.Queue[dict[str, object]]:
        queue: asyncio.Queue[dict[str, object]] = asyncio.Queue(maxsize=20)
        self._subscribers.setdefault(runtime_id, set()).add(queue)
        return queue

    def unsubscribe(self, runtime_id: str, queue: asyncio.Queue[dict[str, object]]) -> None:
        subscribers = self._subscribers.get(runtime_id)
        if subscribers is None:
            return
        subscribers.discard(queue)
        if not subscribers:
            self._subscribers.pop(runtime_id, None)

    async def _run(self, options: GenerationOptions) -> PipelineSummary:
        try:
            config_path = _config_path_for_pipeline(
                config=options.config_path,
                log_root=options.log_root,
                runtime_id=options.runtime_id,
            )
            app_config = load_config(config_path)
            api_keys = {
                name: api_key_for_profile(profile, os.environ)
                for name, profile in app_config.models.items()
            }
            run_options = options.model_copy(update={"config_path": config_path})
            summary = await InklinkPipeline(
                llm=OpenAIToolLLM(app_config, api_keys),
                progress_callback=self._handle_progress,
            ).run(run_options)
            self._notify(
                summary.runtime_id, {"type": "summary", "summary": summary.model_dump(mode="json")}
            )
            return summary
        except Exception as exc:
            runtime_id = options.runtime_id or self._latest_runtime_id()
            if runtime_id is not None:
                self._errors[runtime_id] = str(exc)
                self._notify(runtime_id, {"type": "error", "error": str(exc)})
            raise
        finally:
            self._pending_start_future = None

    def _handle_progress(self, progress: PipelineProgress) -> None:
        runtime_id = progress.runtime_id
        if runtime_id is None:
            return
        self._progress[runtime_id] = progress
        self._last_progress_at[runtime_id] = time.monotonic()
        if self._pending_start_future is not None and not self._pending_start_future.done():
            self._pending_start_future.set_result(runtime_id)
        self._notify(runtime_id, {"type": "progress", "progress": asdict(progress)})

    def _notify(self, runtime_id: str, payload: dict[str, object]) -> None:
        for queue in tuple(self._subscribers.get(runtime_id, ())):
            if queue.full():
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
            queue.put_nowait(payload)

    def _latest_runtime_id(self) -> str | None:
        for runtime_id in reversed(self._progress):
            return runtime_id
        return None


def create_app(
    *,
    log_root: Path = Path("logs"),
    static_dir: Path | None = None,
    manager: RuntimeManager | None = None,
    default_options: dict[str, object] | None = None,
) -> FastAPI:
    app = FastAPI(title="Inklink WebUI", version="0.1.0")
    runtime_manager = manager or RuntimeManager()

    @app.get("/api/health")
    async def health() -> dict[str, object]:
        return {"ok": True}

    @app.get("/api/defaults")
    async def defaults() -> dict[str, object]:
        return default_options or {"log_root": str(log_root)}

    @app.post("/api/runs/start")
    async def start_run(payload: RunOptionsPayload) -> dict[str, object]:
        try:
            options = _generation_options_from_payload(payload)
            runtime_id = await runtime_manager.start(options)
            return runtime_manager.snapshot(log_root=Path(payload.log_root), runtime_id=runtime_id)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/runs/{runtime_id}/resume")
    async def resume_run(runtime_id: str, payload: ResumeOptionsPayload) -> dict[str, object]:
        try:
            options = GenerationOptions(
                runtime_id=runtime_id,
                config_path=Path(payload.config_path),
                log_root=Path(payload.log_root),
                auto_approve=payload.auto_approve,
            )
            started_runtime_id = await runtime_manager.start(options)
            return runtime_manager.snapshot(
                log_root=Path(payload.log_root),
                runtime_id=started_runtime_id,
            )
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/runs/{runtime_id}/snapshot")
    async def snapshot(runtime_id: str, log_root_query: str = "logs") -> dict[str, object]:
        return runtime_manager.snapshot(log_root=Path(log_root_query), runtime_id=runtime_id)

    @app.get("/api/runs/{runtime_id}/artifacts")
    async def artifacts(runtime_id: str, log_root_query: str = "logs") -> list[dict[str, object]]:
        with WorkflowService(log_root=Path(log_root_query)) as service:
            service.inspect_run(runtime_id)
            return service.list_artifacts()

    @app.get("/api/runs/{runtime_id}/artifacts/{artifact_id}")
    async def artifact(
        runtime_id: str,
        artifact_id: str,
        version: int | None = None,
        log_root_query: str = "logs",
    ) -> dict[str, object]:
        try:
            with WorkflowService(log_root=Path(log_root_query)) as service:
                service.inspect_run(runtime_id)
                return service.get_artifact(artifact_id, version)
        except Exception as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/runs/{runtime_id}/artifacts/{artifact_id}/diff")
    async def artifact_diff(
        runtime_id: str,
        artifact_id: str,
        left_version: int,
        right_version: int,
        log_root_query: str = "logs",
    ) -> dict[str, object]:
        with WorkflowService(log_root=Path(log_root_query)) as service:
            service.inspect_run(runtime_id)
            left = service.get_artifact(artifact_id, left_version)
            right = service.get_artifact(artifact_id, right_version)
        left_text = json.dumps(left.get("payload"), ensure_ascii=False, indent=2, sort_keys=True)
        right_text = json.dumps(right.get("payload"), ensure_ascii=False, indent=2, sort_keys=True)
        diff = "".join(
            difflib.unified_diff(
                left_text.splitlines(keepends=True),
                right_text.splitlines(keepends=True),
                fromfile=f"{artifact_id}@{left_version}",
                tofile=f"{artifact_id}@{right_version}",
            )
        )
        return {
            "artifact_id": artifact_id,
            "left_version": left_version,
            "right_version": right_version,
            "diff": diff,
        }

    @app.post("/api/runs/{runtime_id}/approvals/message")
    async def approval_message(
        runtime_id: str,
        payload: ApprovalMessagePayload,
        log_root_query: str = "logs",
    ) -> dict[str, object]:
        with WorkflowService(log_root=Path(log_root_query)) as service:
            service.resume_run(runtime_id)
            result = service.record_approval_message(
                approval_id=payload.approval_id,
                role=payload.role,
                content=payload.content,
            )
            return {"accepted": result.accepted, "message": result.message}

    @app.post("/api/runs/{runtime_id}/approvals/chat-update")
    async def chat_update(
        runtime_id: str,
        payload: ChatUpdatePayload,
        log_root_query: str = "logs",
    ) -> dict[str, object]:
        try:
            config_path = _config_path_for_pipeline(
                config=Path(payload.config_path),
                log_root=Path(log_root_query),
                runtime_id=runtime_id,
            )
            app_config = load_config(config_path)
            api_keys = {
                name: api_key_for_profile(profile, os.environ)
                for name, profile in app_config.models.items()
            }
            version = await InklinkPipeline(
                OpenAIToolLLM(app_config, api_keys)
            ).update_artifact_with_chat(
                runtime_id=runtime_id,
                log_root=Path(log_root_query),
                config_path=config_path,
                approval_id=payload.approval_id,
                artifact_id=payload.artifact_id,
                artifact_type=payload.artifact_type,
                user_message=payload.content,
            )
            return {"artifact_id": payload.artifact_id, "version": version}
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/runs/{runtime_id}/approvals/approve")
    async def approve(
        runtime_id: str,
        payload: ApprovePayload,
        log_root_query: str = "logs",
    ) -> dict[str, object]:
        with WorkflowService(log_root=Path(log_root_query)) as service:
            service.resume_run(runtime_id)
            result = service.approve_artifact(
                approval_id=payload.approval_id,
                approval_type=payload.approval_type or payload.approval_id,
                artifact_id=payload.artifact_id,
                artifact_version=payload.artifact_version,
            )
            return {"accepted": result.accepted, "message": result.message}

    @app.post("/api/runs/{runtime_id}/nodes/retry")
    async def retry_node(
        runtime_id: str,
        payload: RetryPayload,
        log_root_query: str = "logs",
    ) -> dict[str, object]:
        with WorkflowService(log_root=Path(log_root_query)) as service:
            service.resume_run(runtime_id)
            result = service.retry_node(payload.node_id)
            return {"accepted": result.accepted, "message": result.message}

    @app.post("/api/runs/{runtime_id}/chapters/{chapter_number}/abandon")
    async def abandon_chapter(
        runtime_id: str,
        chapter_number: int,
        log_root_query: str = "logs",
    ) -> dict[str, object]:
        with WorkflowService(log_root=Path(log_root_query)) as service:
            service.resume_run(runtime_id)
            result = service.abandon_chapter(chapter_number)
            return {"accepted": result.accepted, "message": result.message}

    @app.post("/api/runs/{runtime_id}/chapters/{chapter_number}/rewrite")
    async def rewrite_chapter(
        runtime_id: str,
        chapter_number: int,
        log_root_query: str = "logs",
    ) -> dict[str, object]:
        with WorkflowService(log_root=Path(log_root_query)) as service:
            service.resume_run(runtime_id)
            result = service.rewrite_chapter(chapter_number)
            return {"accepted": result.accepted, "message": result.message}

    @app.websocket("/api/runs/{runtime_id}/ws")
    async def run_ws(websocket: WebSocket, runtime_id: str) -> None:
        await websocket.accept()
        queue = await runtime_manager.subscribe(runtime_id)
        try:
            await websocket.send_json({"type": "connected", "runtime_id": runtime_id})
            while True:
                payload = await queue.get()
                await websocket.send_json(payload)
        except WebSocketDisconnect:
            pass
        finally:
            runtime_manager.unsubscribe(runtime_id, queue)

    resolved_static_dir = static_dir or _default_static_dir()
    if resolved_static_dir.is_dir():
        app.mount(
            "/assets",
            StaticFiles(directory=resolved_static_dir / "assets"),
            name="assets",
        )

        @app.get("/{path:path}", include_in_schema=False)
        async def web_index(path: str) -> FileResponse:
            candidate = resolved_static_dir / path
            if path and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(resolved_static_dir / "index.html")

    return app


def _generation_options_from_payload(payload: RunOptionsPayload) -> GenerationOptions:
    return GenerationOptions(
        input_dir=Path(payload.input_dir) if payload.input_dir else None,
        config_path=Path(payload.config_path),
        log_root=Path(payload.log_root),
        output_mode=payload.output_mode,
        continuation_mode=payload.continuation_mode,
        chapter_count=payload.chapter_count,
        ending_min_chapters=payload.ending_min_chapters,
        ending_max_chapters=payload.ending_max_chapters,
        start_chapter=payload.start_chapter,
        min_chars=payload.min_chars,
        max_chars=payload.max_chars,
        max_revision_rounds=payload.max_revision_rounds,
        auto_approve=payload.auto_approve,
        notes=payload.notes,
        notes_path=Path(payload.notes_file) if payload.notes_file else None,
    )


def _propagate_start_failure(
    task: asyncio.Task[PipelineSummary],
    start_future: asyncio.Future[str],
) -> None:
    if start_future.done() or not task.done() or task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        start_future.set_exception(exc)


def _config_path_for_pipeline(*, config: Path, log_root: Path, runtime_id: str | None) -> Path:
    if runtime_id is None:
        return config
    settings_path = log_root / runtime_id / "artifacts" / "run_settings.json"
    if not settings_path.is_file():
        return config
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    if not isinstance(settings, dict):
        return config
    value = settings.get("config_path")
    return Path(value) if isinstance(value, str) and value else config


def _default_static_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "web" / "dist"
