from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from inklink.workflow.pipeline import PipelineProgress

DEFAULT_EVENT_LIMIT = 40


@dataclass(frozen=True)
class PhaseSpec:
    key: str
    label: str
    node_types: tuple[str, ...]


@dataclass(frozen=True)
class PhaseStatus:
    key: str
    label: str
    total: int
    completed: int
    running: int
    waiting: int
    failed: int
    current: bool


@dataclass(frozen=True)
class RunSnapshot:
    runtime_id: str | None
    log_root: Path
    status: str = "暂无运行"
    input_dir: str | None = None
    log_dir: str | None = None
    nodes: list[dict[str, object]] = field(default_factory=list)
    artifacts: list[dict[str, object]] = field(default_factory=list)
    approvals: list[dict[str, object]] = field(default_factory=list)
    messages: list[dict[str, object]] = field(default_factory=list)
    usage: list[dict[str, object]] = field(default_factory=list)
    events: list[dict[str, object]] = field(default_factory=list)
    run_summary: dict[str, object] = field(default_factory=dict)
    latest_progress: PipelineProgress | None = None
    pipeline_running: bool = False
    last_progress_age_seconds: float | None = None
    error: str | None = None
    state_error: str | None = None

    @property
    def failure_error(self) -> str | None:
        if self.error:
            return self.error
        summary_error = self.run_summary.get("error_summary")
        return summary_error if isinstance(summary_error, str) and summary_error else None

    @property
    def waiting_approval(self) -> dict[str, object] | None:
        progress_approval_id = (
            self.latest_progress.waiting_approval_id if self.latest_progress is not None else None
        )
        if progress_approval_id:
            for approval in self.approvals:
                if approval.get("approval_id") == progress_approval_id:
                    return approval
            return {"approval_id": progress_approval_id, "status": "waiting"}
        for approval in self.approvals:
            if approval.get("status") == "waiting":
                return approval
        summary_approval_id = self.run_summary.get("waiting_approval_id")
        if isinstance(summary_approval_id, str) and summary_approval_id:
            return {"approval_id": summary_approval_id, "status": "waiting"}
        return None

    @property
    def waiting_approval_id(self) -> str | None:
        approval = self.waiting_approval
        if approval is None:
            return None
        value = approval.get("approval_id")
        return value if isinstance(value, str) and value else None

    @property
    def current_node(self) -> dict[str, object] | None:
        progress_node_id = (
            self.latest_progress.node_id if self.latest_progress is not None else None
        )
        if progress_node_id:
            for node in self.nodes:
                if node.get("node_id") == progress_node_id:
                    return node
            return {"node_id": progress_node_id, "status": self.progress_status or "running"}
        for status in ("running", "waiting", "failed"):
            for node in self.nodes:
                if node.get("status") == status:
                    return node
        return None

    @property
    def progress_status(self) -> str | None:
        if self.latest_progress is None:
            return None
        return self.latest_progress.status

    @property
    def current_phase_key(self) -> str | None:
        if self.latest_progress is not None and self.latest_progress.phase:
            return _normalize_phase_key(self.latest_progress.phase)
        node = self.current_node
        if node is not None:
            return phase_key_for_node(node)
        if self.status == "completed":
            return "output"
        return None

    @property
    def current_phase_label(self) -> str:
        phase_key = self.current_phase_key
        if phase_key is None:
            return "待启动" if self.runtime_id is None else "运行状态读取中"
        return PHASE_LABELS.get(phase_key, phase_key)

    @property
    def latest_message(self) -> str:
        if self.failure_error:
            return f"运行失败: {self.failure_error}"
        if self.latest_progress is not None:
            return self.latest_progress.message
        if self.waiting_approval_id:
            return f"等待审批: {self.waiting_approval_id}"
        if self.status == "completed":
            return "运行完成"
        if self.runtime_id is None:
            return "未开始"
        return self.status

    @property
    def current_chapter_number(self) -> int | None:
        if self.latest_progress is not None and self.latest_progress.chapter_number is not None:
            return self.latest_progress.chapter_number
        node = self.current_node
        if node is None:
            return None
        node_id = node.get("node_id")
        if not isinstance(node_id, str):
            return None
        return _last_number_in_text(node_id)

    @property
    def total_calls(self) -> int:
        usage_calls = sum(_int_value(row.get("calls")) for row in self.usage)
        request_events = sum(1 for event in self.events if event.get("event_type") == "llm_request")
        return max(usage_calls, request_events)

    @property
    def latest_llm_task(self) -> str | None:
        if self.latest_progress is not None and self.latest_progress.llm_task_type:
            return self.latest_progress.llm_task_type
        for event in reversed(self.events):
            if event.get("event_type") not in {
                "llm_request",
                "llm_response",
                "llm_response_reused",
            }:
                continue
            payload = event.get("payload")
            if not isinstance(payload, dict):
                continue
            task_type = payload.get("task_type")
            if isinstance(task_type, str) and task_type:
                return task_type
        return None

    @property
    def latest_event_timestamp(self) -> str | None:
        for event in reversed(self.events):
            timestamp = event.get("timestamp")
            if isinstance(timestamp, str) and timestamp:
                return timestamp
        return None

    @property
    def stale_hint(self) -> str | None:
        if not self.pipeline_running or self.last_progress_age_seconds is None:
            return None
        age = int(self.last_progress_age_seconds)
        latest_event = self.latest_event_timestamp or "暂无事件"
        llm_task = self.latest_llm_task or "暂无模型任务"
        if age >= 60:
            return (
                f"已 {age} 秒没有新的进度更新。可能正在等待模型响应、限流队列或磁盘 IO；"
                f"当前任务: {llm_task}；最近事件: {latest_event}"
            )
        if age >= 10:
            return f"已 {age} 秒没有新的进度更新，仍在等待后台任务；最近事件: {latest_event}"
        return None

    def phase_statuses(self) -> list[PhaseStatus]:
        current_key = self.current_phase_key
        statuses: list[PhaseStatus] = []
        for spec in PHASES:
            phase_nodes = [
                node
                for node in self.nodes
                if str(node.get("node_type") or "") in spec.node_types
                or _node_id_matches_phase(node, spec.key)
            ]
            completed = sum(1 for node in phase_nodes if node.get("status") == "completed")
            running = sum(1 for node in phase_nodes if node.get("status") == "running")
            waiting = sum(1 for node in phase_nodes if node.get("status") == "waiting")
            failed = sum(1 for node in phase_nodes if node.get("status") == "failed")
            statuses.append(
                PhaseStatus(
                    key=spec.key,
                    label=spec.label,
                    total=len(phase_nodes),
                    completed=completed,
                    running=running,
                    waiting=waiting,
                    failed=failed,
                    current=spec.key == current_key,
                )
            )
        return statuses


PHASES: tuple[PhaseSpec, ...] = (
    PhaseSpec("load", "导入章节", ("load_project",)),
    PhaseSpec("analysis", "章节分析", ("analyze_chapter",)),
    PhaseSpec("range_summary", "区间摘要", ("summarize_range",)),
    PhaseSpec("story_state", "故事状态", ("merge_story_state",)),
    PhaseSpec("outline", "大纲", ("plan_outline", "approve_outline")),
    PhaseSpec("chapter_plan", "章节计划", ("plan_chapters", "approve_chapter_plan")),
    PhaseSpec("scene_plan", "场景计划", ("plan_scenes", "approve_scene_plan")),
    PhaseSpec("draft", "正文生成", ("draft_scene", "assemble_chapter")),
    PhaseSpec("review", "审查修订", ("check_chapter", "review_chapter", "revise_chapter")),
    PhaseSpec("output", "输出", ("integrate_generated_chapter", "write_output")),
)

PHASE_LABELS = {phase.key: phase.label for phase in PHASES}

TASK_PHASES: dict[str, str] = {
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
}

NODE_PHASES: dict[str, str] = {
    node_type: phase.key for phase in PHASES for node_type in phase.node_types
}


def load_run_snapshot(
    *,
    log_root: Path,
    runtime_id: str | None,
    latest_progress: PipelineProgress | None = None,
    pipeline_running: bool = False,
    last_progress_age_seconds: float | None = None,
    error: str | None = None,
) -> RunSnapshot:
    if runtime_id is None:
        return RunSnapshot(
            runtime_id=None,
            log_root=log_root,
            latest_progress=latest_progress,
            pipeline_running=pipeline_running,
            last_progress_age_seconds=last_progress_age_seconds,
            error=error,
        )
    try:
        from inklink.workflow.service import WorkflowService

        with WorkflowService(log_root=log_root) as service:
            run = service.inspect_run(runtime_id)
            return RunSnapshot(
                runtime_id=runtime_id,
                log_root=log_root,
                status=_read_run_status(log_root, runtime_id),
                input_dir=str(run.input_dir),
                log_dir=str(run.log_dir),
                nodes=service.list_nodes(),
                artifacts=service.list_artifacts(),
                approvals=service.list_approvals(),
                messages=service.list_messages(),
                usage=[asdict(row) for row in service.usage_stats()],
                events=service.recent_events(limit=DEFAULT_EVENT_LIMIT),
                run_summary=_read_run_summary(log_root, runtime_id),
                latest_progress=latest_progress,
                pipeline_running=pipeline_running,
                last_progress_age_seconds=last_progress_age_seconds,
                error=error,
            )
    except Exception as exc:
        return RunSnapshot(
            runtime_id=runtime_id,
            log_root=log_root,
            status="状态库未就绪" if pipeline_running else "读取失败",
            latest_progress=latest_progress,
            pipeline_running=pipeline_running,
            last_progress_age_seconds=last_progress_age_seconds,
            error=error,
            state_error=str(exc),
        )


def phase_key_for_task(task_type: str) -> str | None:
    return TASK_PHASES.get(task_type)


def phase_key_for_node(node: dict[str, object]) -> str | None:
    node_type = node.get("node_type")
    if isinstance(node_type, str) and node_type in NODE_PHASES:
        return NODE_PHASES[node_type]
    node_id = node.get("node_id")
    if not isinstance(node_id, str):
        return None
    for phase in PHASES:
        if _node_id_matches_phase(node, phase.key):
            return phase.key
    return None


def latest_event_payload(snapshot: RunSnapshot, event_type: str) -> dict[str, object] | None:
    for event in reversed(snapshot.events):
        if event.get("event_type") != event_type:
            continue
        payload = event.get("payload")
        if isinstance(payload, dict):
            return payload
    return None


def _read_run_status(log_root: Path, runtime_id: str) -> str:
    from inklink.storage.sqlite import StateStore

    db_path = log_root / runtime_id / "state.sqlite"
    if not db_path.exists():
        return "暂无状态库"
    with StateStore.open(db_path) as store:
        row = store.get_run(runtime_id)
    return str(row["status"])


def _read_run_summary(log_root: Path, runtime_id: str) -> dict[str, object]:
    path = log_root / runtime_id / "artifacts" / "run_summary.json"
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _normalize_phase_key(value: str) -> str:
    if value in PHASE_LABELS:
        return value
    for key, label in PHASE_LABELS.items():
        if value == label:
            return key
    return value


def _node_id_matches_phase(node: dict[str, object], phase_key: str) -> bool:
    node_id = node.get("node_id")
    if not isinstance(node_id, str):
        return False
    return phase_key == "review" and node_id.startswith("review_failure:")


def _int_value(value: object) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return 0


def _last_number_in_text(value: str) -> int | None:
    parts = [part for part in value.replace("-", ":").split(":") if part.isdigit()]
    if not parts:
        return None
    return int(parts[-1])
