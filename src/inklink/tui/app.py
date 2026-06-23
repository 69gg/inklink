import os
from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, Header

from inklink.config import load_config
from inklink.tui.screens import DashboardScreen, SetupWorkspace
from inklink.workflow.pipeline import GenerationOptions, InklinkPipeline, OpenAIToolLLM


class InklinkApp(App[None]):
    """Textual shell for the Inklink workspace."""

    TITLE = "墨连 Inklink"
    BINDINGS = [
        Binding("f1", "show_dashboard", "工作台"),
        Binding("ctrl+r", "run_pipeline", "开始续写"),
    ]

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

    async def action_run_pipeline(self) -> None:
        setup = self.query_one(SetupWorkspace)
        if self.input_dir is None:
            setup.set_status("缺少输入目录")
            return
        config_path = self.config or Path("config.toml")
        setup.set_status("运行中")
        try:
            app_config = load_config(config_path)
            api_keys = {
                name: os.environ.get(profile.api_key_env)
                for name, profile in app_config.models.items()
            }
            summary = await InklinkPipeline(OpenAIToolLLM(app_config, api_keys)).run(
                GenerationOptions(
                    input_dir=self.input_dir,
                    config_path=config_path,
                    chapter_count=1,
                    auto_approve=True,
                )
            )
        except Exception as exc:
            setup.set_status(f"运行失败: {exc}")
            return
        setup.set_status(
            f"运行完成: {summary.runtime_id}，生成 {len(summary.generated_chapters)} 章"
        )
