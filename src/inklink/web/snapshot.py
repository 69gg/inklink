from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from inklink.storage.sqlite import StateStore
from inklink.workflow.pipeline import PipelineProgress
from inklink.workflow.service import WorkflowService

DEFAULT_EVENT_LIMIT = 80


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
    def waiting_approval(self) -> dict[str, object] | None:
        progress_id = self.latest_progress.waiting_approval_id if self.latest_progress else None
        if progress_id:
            for approval in self.approvals:
                if approval.get("approval_id") == progress_id:
                    return approval
            return {"approval_id": progress_id, "status": "waiting"}
        for approval in self.approvals:
            if approval.get("status") == "waiting":
                return approval
        summary_id = self.run_summary.get("waiting_approval_id")
        if isinstance(summary_id, str) and summary_id:
            return {"approval_id": summary_id, "status": "waiting"}
        return None

    @property
    def current_node(self) -> dict[str, object] | None:
        progress_node_id = self.latest_progress.node_id if self.latest_progress else None
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
    def waiting_approval_id(self) -> str | None:
        approval = self.waiting_approval
        if approval is None:
            return None
        value = approval.get("approval_id")
        return value if isinstance(value, str) and value else None

    @property
    def progress_status(self) -> str | None:
        return self.latest_progress.status if self.latest_progress else None

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
            return "未开始" if self.runtime_id is None else "状态读取中"
        return PHASE_LABELS.get(phase_key, phase_key)

    @property
    def latest_message(self) -> str:
        if self.error:
            return f"运行失败: {self.error}"
        summary_error = self.run_summary.get("error_summary")
        if isinstance(summary_error, str) and summary_error:
            return f"运行失败: {summary_error}"
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
    def stale_hint(self) -> str | None:
        if not self.pipeline_running or self.last_progress_age_seconds is None:
            return None
        age = int(self.last_progress_age_seconds)
        if age < 10:
            return None
        latest_event = self.events[-1].get("timestamp") if self.events else "暂无事件"
        if age >= 60:
            return (
                f"已 {age} 秒没有新进度，可能正在等待模型响应、限流或磁盘 IO。"
                f"最近事件: {latest_event}"
            )
        return f"已 {age} 秒没有新进度，后台仍在运行。最近事件: {latest_event}"

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
            statuses.append(
                PhaseStatus(
                    key=spec.key,
                    label=spec.label,
                    total=len(phase_nodes),
                    completed=sum(1 for node in phase_nodes if node.get("status") == "completed"),
                    running=sum(1 for node in phase_nodes if node.get("status") == "running"),
                    waiting=sum(1 for node in phase_nodes if node.get("status") == "waiting"),
                    failed=sum(1 for node in phase_nodes if node.get("status") == "failed"),
                    current=spec.key == current_key,
                )
            )
        return statuses

    def to_payload(self) -> dict[str, object]:
        latest_progress = asdict(self.latest_progress) if self.latest_progress is not None else None
        waiting = self.waiting_approval
        current_node = self.current_node
        return {
            "runtime_id": self.runtime_id,
            "log_root": str(self.log_root),
            "status": self.status,
            "input_dir": self.input_dir,
            "log_dir": self.log_dir,
            "nodes": self.nodes,
            "artifacts": self.artifacts,
            "approvals": self.approvals,
            "messages": self.messages,
            "usage": self.usage,
            "events": self.events,
            "run_summary": self.run_summary,
            "latest_progress": latest_progress,
            "pipeline_running": self.pipeline_running,
            "last_progress_age_seconds": self.last_progress_age_seconds,
            "error": self.error,
            "state_error": self.state_error,
            "waiting_approval": waiting,
            "waiting_approval_id": self.waiting_approval_id,
            "current_node": current_node,
            "current_phase_key": self.current_phase_key,
            "current_phase_label": self.current_phase_label,
            "latest_message": self.latest_message,
            "stale_hint": self.stale_hint,
            "phase_statuses": [asdict(item) for item in self.phase_statuses()],
        }


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


def _read_run_status(log_root: Path, runtime_id: str) -> str:
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
