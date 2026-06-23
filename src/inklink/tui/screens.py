from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widget import Widget
from textual.widgets import Footer, Header, Static


class SetupWorkspace(Widget):
    """Initial workspace setup placeholder."""

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
                yield Static("规划、章节计划、场景计划会记录审批事件；完整聊天审批仍在开发中。")

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
    """Dashboard placeholder for future workflow integration."""

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

    def __init__(self, input_dir: Path | None = None, config: Path | None = None) -> None:
        super().__init__(id="dashboard", name="dashboard")
        self._input_dir = input_dir
        self._config = config
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
                yield Static(
                    "同进程 workflow service；运行状态保存在 logs/<runtime_id>/state.sqlite。"
                )
            with Vertical(classes="dashboard-panel"):
                yield Static("运行摘要", classes="dashboard-title")
                yield Static("Ctrl+R 从主屏启动；CLI 可用 --execute 或 --resume-runtime-id。")
        with Horizontal(classes="dashboard-row"):
            with Vertical(classes="dashboard-panel"):
                yield Static("输入目录", classes="dashboard-title")
                yield Static(input_dir_text)
            with Vertical(classes="dashboard-panel"):
                yield Static("审批区", classes="dashboard-title")
                yield Static("当前版本记录审批事件；完整 artifact diff 和聊天面板后续接入。")
        yield Footer()


class StatsScreen(Screen[None]):
    """Usage statistics placeholder backed by runtime summaries."""

    DEFAULT_CSS = """
    StatsScreen {
        padding: 1 2;
    }

    #stats-workspace {
        height: auto;
        margin-bottom: 1;
    }
    """

    def __init__(self, runtime_id: str | None = None) -> None:
        super().__init__(id="stats", name="stats")
        self._runtime_id = runtime_id
        self.title = "统计"

    def compose(self) -> ComposeResult:
        runtime_text = self._runtime_id or "暂无运行"
        yield Header()
        yield Static(
            "统计\n"
            f"runtime_id: {runtime_text}\n"
            "CLI 执行会输出 usage_by_model 与 usage_by_task；"
            "详细 JSON 保存在 logs/<runtime_id>/artifacts/run_summary.json。",
            id="stats-workspace",
        )
        yield Footer()
