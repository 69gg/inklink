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

    def compose(self) -> ComposeResult:
        input_dir_text = str(self._input_dir) if self._input_dir is not None else "未选择"
        config_text = str(self._config) if self._config is not None else "config.toml"

        yield Static(
            f"设置工作台\n输入目录: {input_dir_text}\n配置: {config_text}\n状态: 待启动",
            id="setup-workspace",
        )
        with Horizontal(classes="workspace-row"):
            with Vertical(classes="workspace-panel"):
                yield Static("状态", classes="workspace-title")
                yield Static("当前仅展示 TUI shell。")
            with Vertical(classes="workspace-panel"):
                yield Static("运行摘要", classes="workspace-title")
                yield Static("暂无运行记录。")
        with Horizontal(classes="workspace-row"):
            with Vertical(classes="workspace-panel"):
                yield Static("配置", classes="workspace-title")
                yield Static(f"配置文件: {config_text}")
            with Vertical(classes="workspace-panel"):
                yield Static("审批区", classes="workspace-title")
                yield Static("暂无待审批事项。")


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
                yield Static("服务未连接。")
            with Vertical(classes="dashboard-panel"):
                yield Static("运行摘要", classes="dashboard-title")
                yield Static("等待后续 workflow 集成。")
        with Horizontal(classes="dashboard-row"):
            with Vertical(classes="dashboard-panel"):
                yield Static("输入目录", classes="dashboard-title")
                yield Static(input_dir_text)
            with Vertical(classes="dashboard-panel"):
                yield Static("审批区", classes="dashboard-title")
                yield Static("暂无待审批事项。")
        yield Footer()
