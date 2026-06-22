from __future__ import annotations

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
        active_run.event_log.write(
            "chapter_abandon_requested",
            {
                "runtime_id": active_run.run.runtime_id,
                "chapter_number": chapter_number,
            },
        )
        return CommandResult(
            accepted=True,
            message=(
                f"accepted abandon_chapter for chapter {chapter_number}; TODO: generation "
                "increment and index invalidation are not implemented in this primitive"
            ),
        )

    def rewrite_chapter(self, chapter_number: int) -> CommandResult:
        active_run = self._current_run()
        self._validate_chapter_number(chapter_number, active_run.run.chapter_count)
        active_run.event_log.write(
            "chapter_rewrite_requested",
            {
                "runtime_id": active_run.run.runtime_id,
                "chapter_number": chapter_number,
            },
        )
        return CommandResult(
            accepted=True,
            message=(
                f"accepted rewrite_chapter for chapter {chapter_number}; TODO: generation "
                "increment and index invalidation are not implemented in this primitive"
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


__all__ = [
    "CommandResult",
    "StartRunCheck",
    "WorkflowRun",
    "WorkflowService",
    "WorkflowServiceError",
]
