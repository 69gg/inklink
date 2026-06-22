from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, Header

from inklink.tui.screens import DashboardScreen, SetupWorkspace


class InklinkApp(App[None]):
    """Textual shell for the Inklink workspace."""

    TITLE = "墨连 Inklink"
    BINDINGS = [Binding("f1", "show_dashboard", "工作台")]

    def __init__(self, input_dir: Path | None = None, config: Path | None = None) -> None:
        super().__init__()
        self.input_dir = input_dir
        self.config = config

    def compose(self) -> ComposeResult:
        yield Header()
        yield SetupWorkspace(input_dir=self.input_dir, config=self.config)
        yield Footer()

    def action_show_dashboard(self) -> None:
        if not isinstance(self.screen, DashboardScreen):
            self.push_screen(DashboardScreen(input_dir=self.input_dir, config=self.config))
