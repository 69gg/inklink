import os
from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, Header

from inklink.config import load_config
from inklink.tui.screens import (
    DashboardScreen,
    RuntimeApprovalsScreen,
    RuntimeArtifactsScreen,
    RuntimeLogScreen,
    SetupWorkspace,
    StatsScreen,
)
from inklink.workflow.pipeline import GenerationOptions, InklinkPipeline, OpenAIToolLLM


class InklinkApp(App[None]):
    """Textual shell for the Inklink workspace."""

    TITLE = "墨连 Inklink"
    BINDINGS = [
        Binding("f1", "show_dashboard", "工作台"),
        Binding("f2", "show_stats", "统计"),
        Binding("f3", "show_artifacts", "产物"),
        Binding("f4", "show_approvals", "审批"),
        Binding("f5", "show_events", "日志"),
        Binding("ctrl+r", "run_pipeline", "开始续写"),
    ]

    def __init__(self, input_dir: Path | None = None, config: Path | None = None) -> None:
        super().__init__()
        self.input_dir = input_dir
        self.config = config
        self.latest_runtime_id: str | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield SetupWorkspace(input_dir=self.input_dir, config=self.config)
        yield Footer()

    def action_show_dashboard(self) -> None:
        if not isinstance(self.screen, DashboardScreen):
            self.push_screen(
                DashboardScreen(
                    input_dir=self.input_dir,
                    config=self.config,
                    runtime_id=self.latest_runtime_id,
                )
            )

    def action_show_stats(self) -> None:
        if not isinstance(self.screen, StatsScreen):
            self.push_screen(StatsScreen(runtime_id=self.latest_runtime_id))

    def action_show_artifacts(self) -> None:
        if not isinstance(self.screen, RuntimeArtifactsScreen):
            self.push_screen(RuntimeArtifactsScreen(runtime_id=self.latest_runtime_id))

    def action_show_approvals(self) -> None:
        if not isinstance(self.screen, RuntimeApprovalsScreen):
            self.push_screen(RuntimeApprovalsScreen(runtime_id=self.latest_runtime_id))

    def action_show_events(self) -> None:
        if not isinstance(self.screen, RuntimeLogScreen):
            self.push_screen(RuntimeLogScreen(runtime_id=self.latest_runtime_id))

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
        self.latest_runtime_id = summary.runtime_id
        setup.set_status(
            f"运行完成: {summary.runtime_id}，生成 {len(summary.generated_chapters)} 章，"
            f"调用 {summary.stats.total_calls} 次"
        )
