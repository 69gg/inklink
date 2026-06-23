from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from uuid import uuid4

from inklink.chapters import load_chapters
from inklink.locks import ProjectLock
from inklink.storage.events import JsonlEventLog
from inklink.storage.sqlite import StateStore


class WorkflowServiceError(RuntimeError):
    pass


@dataclass(frozen=True)
class WorkflowRun:
    runtime_id: str
    input_dir: Path
    log_dir: Path
    chapter_count: int


@dataclass(frozen=True)
class StartRunCheck:
    allowed: bool
    reason: str = ""


@dataclass(frozen=True)
class CommandResult:
    accepted: bool
    message: str


@dataclass(frozen=True)
class UsageStatRow:
    profile: str
    model: str
    task_type: str
    calls: int
    input_tokens: int
    output_tokens: int
    total_tokens: int


@dataclass
class _ActiveRun:
    run: WorkflowRun
    lock: ProjectLock
    store: StateStore
    event_log: JsonlEventLog
    lock_released: bool = False
    store_closed: bool = False


class WorkflowService:
    def __init__(self, log_root: Path) -> None:
        self._log_root = log_root
        self._active_runs: dict[str, _ActiveRun] = {}
        self._runtime_id_by_input_dir: dict[Path, str] = {}
        self._latest_runtime_id: str | None = None

    def __enter__(self) -> WorkflowService:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def start_run(self, input_dir: Path) -> WorkflowRun:
        normalized_input_dir = input_dir.resolve()
        check = self.can_start_run(normalized_input_dir)
        if not check.allowed:
            raise WorkflowServiceError(check.reason)

        runtime_id = uuid4().hex
        log_dir = self._log_root / runtime_id
        lock: ProjectLock | None = None
        store: StateStore | None = None
        try:
            lock = ProjectLock.acquire(normalized_input_dir, runtime_id)
            chapters = load_chapters(normalized_input_dir)
            store = StateStore.open(log_dir / "state.sqlite")
            store.create_run(
                runtime_id=runtime_id,
                input_dir=str(normalized_input_dir),
                status="running",
            )
            event_log = JsonlEventLog(log_dir / "events.jsonl")
            event_log.write(
                "run_started",
                {
                    "runtime_id": runtime_id,
                    "input_dir": str(normalized_input_dir),
                    "chapter_count": len(chapters),
                },
            )
            run = WorkflowRun(
                runtime_id=runtime_id,
                input_dir=normalized_input_dir,
                log_dir=log_dir,
                chapter_count=len(chapters),
            )
            self._active_runs[runtime_id] = _ActiveRun(
                run=run,
                lock=lock,
                store=store,
                event_log=event_log,
            )
            self._runtime_id_by_input_dir[normalized_input_dir] = runtime_id
            self._latest_runtime_id = runtime_id
            return run
        except BaseException:
            if lock is not None:
                lock.release()
            if store is not None:
                store.close()
            raise

    def resume_run(self, runtime_id: str) -> WorkflowRun:
        if not runtime_id.strip() or runtime_id != runtime_id.strip():
            raise ValueError("runtime_id must not be empty or contain leading/trailing whitespace")
        if runtime_id in self._active_runs:
            return self._active_runs[runtime_id].run

        log_dir = self._log_root / runtime_id
        store: StateStore | None = None
        lock: ProjectLock | None = None
        try:
            store = StateStore.open(log_dir / "state.sqlite")
            run_row = store.get_run(runtime_id)
            normalized_input_dir = Path(str(run_row["input_dir"])).resolve()
            check = self.can_start_run(normalized_input_dir)
            if not check.allowed:
                raise WorkflowServiceError(check.reason)
            lock = ProjectLock.acquire(normalized_input_dir, runtime_id)
            chapters = load_chapters(normalized_input_dir)
            event_log = JsonlEventLog(log_dir / "events.jsonl")
            event_log.write(
                "run_resumed",
                {
                    "runtime_id": runtime_id,
                    "input_dir": str(normalized_input_dir),
                    "chapter_count": len(chapters),
                },
            )
            store.update_run_status(runtime_id, "running")
            run = WorkflowRun(
                runtime_id=runtime_id,
                input_dir=normalized_input_dir,
                log_dir=log_dir,
                chapter_count=len(chapters),
            )
            self._active_runs[runtime_id] = _ActiveRun(
                run=run,
                lock=lock,
                store=store,
                event_log=event_log,
            )
            self._runtime_id_by_input_dir[normalized_input_dir] = runtime_id
            self._latest_runtime_id = runtime_id
            return run
        except BaseException:
            if lock is not None:
                lock.release()
            if store is not None:
                store.close()
            raise

    def can_start_run(self, input_dir: Path) -> StartRunCheck:
        normalized_input_dir = input_dir.resolve()
        if normalized_input_dir in self._runtime_id_by_input_dir:
            return StartRunCheck(
                allowed=False,
                reason=f"active run already exists for input_dir: {normalized_input_dir}",
            )
        return StartRunCheck(allowed=True)

    def abandon_chapter(self, chapter_number: int) -> CommandResult:
        active_run = self._current_run()
        self._validate_chapter_number(chapter_number, active_run.run.chapter_count)
        next_generation = active_run.store.increment_chapter_generation(chapter_number)
        active_run.event_log.write(
            "chapter_abandon_requested",
            {
                "runtime_id": active_run.run.runtime_id,
                "chapter_number": chapter_number,
                "next_generation": next_generation,
            },
        )
        return CommandResult(
            accepted=True,
            message=(
                f"accepted abandon_chapter for chapter {chapter_number}; "
                f"generation={next_generation}"
            ),
        )

    def rewrite_chapter(self, chapter_number: int) -> CommandResult:
        active_run = self._current_run()
        self._validate_chapter_number(chapter_number, active_run.run.chapter_count)
        next_generation = active_run.store.increment_chapter_generation(chapter_number)
        active_run.event_log.write(
            "chapter_rewrite_requested",
            {
                "runtime_id": active_run.run.runtime_id,
                "chapter_number": chapter_number,
                "next_generation": next_generation,
            },
        )
        return CommandResult(
            accepted=True,
            message=(
                f"accepted rewrite_chapter for chapter {chapter_number}; "
                f"generation={next_generation}"
            ),
        )

    def retry_node(self, node_id: str) -> CommandResult:
        if not node_id.strip() or node_id != node_id.strip():
            raise ValueError("node_id must not be empty or contain leading/trailing whitespace")
        active_run = self._current_run()
        active_run.event_log.write(
            "node_retry_requested",
            {
                "runtime_id": active_run.run.runtime_id,
                "node_id": node_id,
            },
        )
        return CommandResult(
            accepted=True,
            message=f"accepted retry_node for node {node_id}",
        )

    def record_approval_message(
        self,
        *,
        approval_id: str,
        role: str,
        content: str,
    ) -> CommandResult:
        _validate_non_blank_identifier("approval_id", approval_id)
        _validate_non_blank_identifier("role", role)
        if not content.strip():
            raise ValueError("content must not be empty")
        active_run = self._current_run()
        message_id = uuid4().hex
        active_run.store.create_or_update_approval(
            approval_id=approval_id,
            approval_type=approval_id,
            status="waiting",
            auto_approve=False,
        )
        active_run.store.add_message(
            message_id=message_id,
            approval_id=approval_id,
            role=role,
            content=content,
        )
        messages_hash = active_run.store.approval_messages_hash(approval_id)
        active_run.event_log.write(
            "approval_message_recorded",
            {
                "runtime_id": active_run.run.runtime_id,
                "approval_id": approval_id,
                "message_id": message_id,
                "role": role,
                "messages_hash": messages_hash,
            },
        )
        return CommandResult(
            accepted=True,
            message=f"recorded approval message {message_id}",
        )

    def approve_artifact(
        self,
        *,
        approval_id: str,
        approval_type: str,
        artifact_id: str,
        artifact_version: int,
    ) -> CommandResult:
        _validate_non_blank_identifier("approval_id", approval_id)
        _validate_non_blank_identifier("approval_type", approval_type)
        _validate_non_blank_identifier("artifact_id", artifact_id)
        if artifact_version <= 0:
            raise ValueError("artifact_version must be positive")
        active_run = self._current_run()
        active_run.store.create_or_update_approval(
            approval_id=approval_id,
            approval_type=approval_type,
            status="accepted",
            auto_approve=False,
            artifact_id=artifact_id,
            artifact_version=artifact_version,
        )
        active_run.event_log.write(
            "approval_accepted",
            {
                "runtime_id": active_run.run.runtime_id,
                "approval_id": approval_id,
                "approval_type": approval_type,
                "artifact_id": artifact_id,
                "artifact_version": artifact_version,
                "auto_approve": False,
            },
        )
        return CommandResult(
            accepted=True,
            message=f"approved {artifact_id}@{artifact_version}",
        )

    def update_artifact(
        self,
        *,
        artifact_id: str,
        artifact_type: str,
        payload: object,
        approval_id: str | None = None,
        is_draft: bool = True,
    ) -> int:
        _validate_non_blank_identifier("artifact_id", artifact_id)
        _validate_non_blank_identifier("artifact_type", artifact_type)
        if approval_id is not None:
            _validate_non_blank_identifier("approval_id", approval_id)
        active_run = self._current_run()
        version = active_run.store.upsert_artifact(
            artifact_id=artifact_id,
            artifact_type=artifact_type,
            payload=payload,
            is_draft=is_draft,
            is_approved=not is_draft,
            approval_id=approval_id,
        )
        active_run.event_log.write(
            "artifact_updated",
            {
                "runtime_id": active_run.run.runtime_id,
                "artifact_id": artifact_id,
                "artifact_type": artifact_type,
                "version": version,
                "is_draft": is_draft,
                "approval_id": approval_id,
            },
        )
        return version

    def usage_stats(self) -> list[UsageStatRow]:
        active_run = self._current_run()
        grouped: dict[tuple[str, str, str], UsageStatRow] = {}
        for row in active_run.store.usage_summary():
            usage = row.get("normalized_usage_json")
            parsed = _parse_usage_json(usage)
            key = (str(row["profile"]), str(row["model"]), str(row["task_type"]))
            current = grouped.get(key)
            if current is None:
                current = UsageStatRow(
                    profile=key[0],
                    model=key[1],
                    task_type=key[2],
                    calls=0,
                    input_tokens=0,
                    output_tokens=0,
                    total_tokens=0,
                )
            grouped[key] = UsageStatRow(
                profile=current.profile,
                model=current.model,
                task_type=current.task_type,
                calls=current.calls + 1,
                input_tokens=current.input_tokens + parsed.get("input_tokens", 0),
                output_tokens=current.output_tokens + parsed.get("output_tokens", 0),
                total_tokens=current.total_tokens + parsed.get("total_tokens", 0),
            )
        return [grouped[key] for key in sorted(grouped)]

    def close(self) -> None:
        first_error: BaseException | None = None
        for runtime_id, active_run in list(self._active_runs.items()):
            if not active_run.lock_released:
                try:
                    active_run.lock.release()
                    active_run.lock_released = True
                except BaseException as exc:
                    if first_error is None:
                        first_error = exc
            if not active_run.store_closed:
                try:
                    active_run.store.close()
                    active_run.store_closed = True
                except BaseException as exc:
                    if first_error is None:
                        first_error = exc
            if active_run.lock_released and active_run.store_closed:
                del self._active_runs[runtime_id]
                del self._runtime_id_by_input_dir[active_run.run.input_dir]
        if self._latest_runtime_id not in self._active_runs:
            self._latest_runtime_id = next(reversed(self._active_runs), None)
        if first_error is not None:
            raise first_error

    def _current_run(self) -> _ActiveRun:
        if self._latest_runtime_id is None:
            raise WorkflowServiceError("no active workflow run")
        return self._active_runs[self._latest_runtime_id]

    def _validate_chapter_number(self, chapter_number: int, chapter_count: int) -> None:
        if chapter_number <= 0:
            raise ValueError("chapter_number must be positive")
        if chapter_number > chapter_count:
            raise ValueError(
                f"chapter_number must be between 1 and {chapter_count}: {chapter_number}"
            )


def _validate_non_blank_identifier(field_name: str, value: str) -> None:
    if not value.strip() or value != value.strip():
        raise ValueError(f"{field_name} must not be empty or contain leading/trailing whitespace")


def _parse_usage_json(value: object) -> dict[str, int]:
    if not isinstance(value, str):
        return {}
    raw = json.loads(value)
    if not isinstance(raw, dict):
        return {}
    parsed: dict[str, int] = {}
    for key in (
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "reasoning_tokens",
        "cached_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
    ):
        item = raw.get(key)
        if isinstance(item, int) and not isinstance(item, bool):
            parsed[key] = item
    return parsed


__all__ = [
    "CommandResult",
    "StartRunCheck",
    "UsageStatRow",
    "WorkflowRun",
    "WorkflowService",
    "WorkflowServiceError",
]
