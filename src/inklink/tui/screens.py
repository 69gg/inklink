import json
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widget import Widget
from textual.widgets import Button, Footer, Header, Input, Static

if TYPE_CHECKING:
    from inklink.workflow.service import WorkflowService


class SetupWorkspace(Widget):
    """Initial workspace setup and run launcher."""

    DEFAULT_CSS = """
    SetupWorkspace {
        padding: 1 2;
    }

    #setup-workspace {
        height: auto;
        margin-bottom: 1;
    }

    .workspace-row {
        height: auto;
        margin-bottom: 1;
    }

    .workspace-panel {
        width: 1fr;
        height: auto;
        padding: 1 2;
        border: solid $surface-lighten-1;
    }

    .workspace-title {
        text-style: bold;
        margin-bottom: 1;
    }
    """

    def __init__(self, input_dir: Path | None = None, config: Path | None = None) -> None:
        super().__init__(id="setup-workspace-container")
        self._input_dir = input_dir
        self._config = config
        self._status = "待启动"

    def compose(self) -> ComposeResult:
        config_text = str(self._config) if self._config is not None else "config.toml"

        yield Static(
            self.workspace_text,
            id="setup-workspace",
        )
        with Horizontal(classes="workspace-row"):
            with Vertical(classes="workspace-panel"):
                yield Static("状态", classes="workspace-title")
                yield Static(self.status_text, id="setup-status")
            with Vertical(classes="workspace-panel"):
                yield Static("运行摘要", classes="workspace-title")
                yield Static("未开始。Ctrl+R 可按当前参数执行一次续写。")
        with Horizontal(classes="workspace-row"):
            with Vertical(classes="workspace-panel"):
                yield Static("配置", classes="workspace-title")
                yield Static(f"配置文件: {config_text}")
            with Vertical(classes="workspace-panel"):
                yield Static("审批区", classes="workspace-title")
                yield Static("规划、章节计划、场景计划和自审失败会进入审批点，可用 F4 查看。")

    @property
    def workspace_text(self) -> str:
        input_dir_text = str(self._input_dir) if self._input_dir is not None else "未选择"
        config_text = str(self._config) if self._config is not None else "config.toml"
        return f"设置工作台\n输入目录: {input_dir_text}\n配置: {config_text}\n状态: {self._status}"

    @property
    def status_text(self) -> str:
        return f"当前状态: {self._status}\n快捷键: F1 工作台，Ctrl+R 开始续写"

    def set_status(self, status: str) -> None:
        self._status = status
        workspace = self.query_one("#setup-workspace", Static)
        workspace.update(self.workspace_text)
        status_panel = self.query_one("#setup-status", Static)
        status_panel.update(self.status_text)


class DashboardScreen(Screen[None]):
    """Runtime dashboard with workflow inspection data."""

    DEFAULT_CSS = """
    DashboardScreen {
        padding: 1 2;
    }

    #dashboard-workspace {
        height: auto;
        margin-bottom: 1;
    }

    .dashboard-row {
        height: auto;
        margin-bottom: 1;
    }

    .dashboard-panel {
        width: 1fr;
        height: auto;
        padding: 1 2;
        border: solid $surface-lighten-1;
    }

    .dashboard-title {
        text-style: bold;
        margin-bottom: 1;
    }
    """

    def __init__(
        self,
        input_dir: Path | None = None,
        config: Path | None = None,
        runtime_id: str | None = None,
        log_root: Path = Path("logs"),
    ) -> None:
        super().__init__(id="dashboard", name="dashboard")
        self._input_dir = input_dir
        self._config = config
        self._runtime_id = runtime_id
        self._log_root = log_root
        self.title = "工作台"

    def compose(self) -> ComposeResult:
        input_dir_text = str(self._input_dir) if self._input_dir is not None else "未选择"
        config_text = str(self._config) if self._config is not None else "config.toml"

        yield Header()
        yield Static(
            f"工作台\n状态: 空闲\n输入目录: {input_dir_text}\n配置: {config_text}",
            id="dashboard-workspace",
        )
        with Horizontal(classes="dashboard-row"):
            with Vertical(classes="dashboard-panel"):
                yield Static("状态", classes="dashboard-title")
                yield Static("运行状态保存在 logs/<runtime_id>/state.sqlite。F3/F4/F5 可审计。")
            with Vertical(classes="dashboard-panel"):
                yield Static("运行摘要", classes="dashboard-title")
                yield Static("Ctrl+R 从主屏启动；CLI 可用 --execute 或 --resume-runtime-id。")
        with Horizontal(classes="dashboard-row"):
            with Vertical(classes="dashboard-panel"):
                yield Static("输入目录", classes="dashboard-title")
                yield Static(input_dir_text)
            with Vertical(classes="dashboard-panel"):
                yield Static("审批区", classes="dashboard-title")
                yield Static(_read_state_section(self._log_root, self._runtime_id, "nodes"))
        yield Footer()


class StatsScreen(Screen[None]):
    """Usage statistics backed by runtime summaries."""

    DEFAULT_CSS = """
    StatsScreen {
        padding: 1 2;
    }

    #stats-workspace {
        height: auto;
        margin-bottom: 1;
    }
    """

    def __init__(self, runtime_id: str | None = None, log_root: Path = Path("logs")) -> None:
        super().__init__(id="stats", name="stats")
        self._runtime_id = runtime_id
        self._log_root = log_root
        self.title = "统计"

    def compose(self) -> ComposeResult:
        runtime_text = self._runtime_id or "暂无运行"
        summary = _read_run_summary(self._log_root, self._runtime_id)
        yield Header()
        yield Static(
            f"统计\nruntime_id: {runtime_text}\n{summary}",
            id="stats-workspace",
        )
        yield Footer()


class RuntimeArtifactsScreen(Screen[None]):
    """Artifact list for the latest runtime."""

    def __init__(self, runtime_id: str | None = None, log_root: Path = Path("logs")) -> None:
        super().__init__(id="artifacts", name="artifacts")
        self._runtime_id = runtime_id
        self._log_root = log_root
        self.title = "产物"

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(
            "产物\n" + _read_state_section(self._log_root, self._runtime_id, "artifacts"),
            id="artifacts-workspace",
        )
        yield Footer()


class RuntimeApprovalsScreen(Screen[None]):
    """Approval list and basic approval controls for the latest runtime."""

    def __init__(self, runtime_id: str | None = None, log_root: Path = Path("logs")) -> None:
        super().__init__(id="approvals", name="approvals")
        self._runtime_id = runtime_id
        self._log_root = log_root
        self.title = "审批"

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(
            "审批\n" + _read_approval_workspace(self._log_root, self._runtime_id),
            id="approvals-workspace",
        )
        with Vertical(id="approval-controls"):
            yield Input(placeholder="approval_id", id="approval-id")
            yield Input(placeholder="artifact_id", id="approval-artifact-id")
            yield Input(placeholder="artifact_version", id="approval-artifact-version")
            yield Button("批准", id="approve-artifact")
            yield Input(placeholder="node_id", id="retry-node-id")
            yield Button("重试节点", id="retry-node")
            yield Input(placeholder="chapter_number", id="chapter-number")
            with Horizontal():
                yield Button("放弃章节", id="abandon-chapter")
                yield Button("重写章节", id="rewrite-chapter")
            yield Static("", id="approval-command-status")
        yield Footer()

    @on(Button.Pressed, "#approve-artifact")
    def approve_artifact(self) -> None:
        approval_id = self.query_one("#approval-id", Input).value
        artifact_id = self.query_one("#approval-artifact-id", Input).value
        version_text = self.query_one("#approval-artifact-version", Input).value
        self._run_command(
            "approve",
            lambda service: (
                service.approve_artifact(
                    approval_id=approval_id,
                    approval_type=approval_id,
                    artifact_id=artifact_id,
                    artifact_version=int(version_text),
                ).message
            ),
        )

    @on(Button.Pressed, "#retry-node")
    def retry_node(self) -> None:
        node_id = self.query_one("#retry-node-id", Input).value
        self._run_command("retry", lambda service: service.retry_node(node_id).message)

    @on(Button.Pressed, "#abandon-chapter")
    def abandon_chapter(self) -> None:
        chapter_text = self.query_one("#chapter-number", Input).value
        self._run_command(
            "abandon",
            lambda service: service.abandon_chapter(int(chapter_text)).message,
        )

    @on(Button.Pressed, "#rewrite-chapter")
    def rewrite_chapter(self) -> None:
        chapter_text = self.query_one("#chapter-number", Input).value
        self._run_command(
            "rewrite",
            lambda service: service.rewrite_chapter(int(chapter_text)).message,
        )

    def _run_command(
        self,
        action: str,
        handler: Callable[["WorkflowService"], str],
    ) -> None:
        status = self.query_one("#approval-command-status", Static)
        if self._runtime_id is None:
            status.update("暂无运行。")
            return
        try:
            from inklink.workflow.service import WorkflowService

            with WorkflowService(log_root=self._log_root) as service:
                service.resume_run(self._runtime_id)
                message = handler(service)
            status.update(f"{action}: {message}")
            self.query_one("#approvals-workspace", Static).update(
                "审批\n" + _read_approval_workspace(self._log_root, self._runtime_id)
            )
        except Exception as exc:
            status.update(f"{action} 失败: {exc}")


class RuntimeLogScreen(Screen[None]):
    """Recent event log for the latest runtime."""

    def __init__(self, runtime_id: str | None = None, log_root: Path = Path("logs")) -> None:
        super().__init__(id="events", name="events")
        self._runtime_id = runtime_id
        self._log_root = log_root
        self.title = "日志"

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(
            "日志\n" + _read_events(self._log_root, self._runtime_id),
            id="events-workspace",
        )
        yield Footer()


def _read_run_summary(log_root: Path, runtime_id: str | None) -> str:
    if runtime_id is None:
        return "暂无运行。"
    path = log_root / runtime_id / "artifacts" / "run_summary.json"
    if not path.exists():
        return "暂无 run_summary.json。"
    raw = json.loads(path.read_text(encoding="utf-8"))
    return json.dumps(raw, ensure_ascii=False, indent=2, sort_keys=True)


def _read_state_section(log_root: Path, runtime_id: str | None, section: str) -> str:
    if runtime_id is None:
        return "暂无运行。"
    db_path = log_root / runtime_id / "state.sqlite"
    if not db_path.exists():
        return "暂无 state.sqlite。"
    from inklink.storage.sqlite import StateStore

    with StateStore.open(db_path) as store:
        payload: object
        if section == "artifacts":
            payload = store.list_artifacts()
        elif section == "approvals":
            payload = store.list_approvals()
        elif section == "nodes":
            payload = store.list_nodes()
        elif section == "messages":
            payload = store.list_messages()
        else:
            payload = []
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def _read_approval_workspace(log_root: Path, runtime_id: str | None) -> str:
    return "\n\n".join(
        [
            "approvals:",
            _read_state_section(log_root, runtime_id, "approvals"),
            "messages:",
            _read_state_section(log_root, runtime_id, "messages"),
        ]
    )


def _read_events(log_root: Path, runtime_id: str | None, limit: int = 20) -> str:
    if runtime_id is None:
        return "暂无运行。"
    path = log_root / runtime_id / "events.jsonl"
    if not path.exists():
        return "暂无 events.jsonl。"
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    return json.dumps(events[-limit:], ensure_ascii=False, indent=2, sort_keys=True)
