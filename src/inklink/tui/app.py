import os
from pathlib import Path

from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Button, Footer, Header

from inklink.config import load_config
from inklink.tui.screens import (
    DashboardScreen,
    RuntimeApprovalsScreen,
    RuntimeArtifactsScreen,
    RuntimeLogScreen,
    SetupWorkspace,
    StatsScreen,
)
from inklink.workflow.pipeline import InklinkPipeline, OpenAIToolLLM


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

    def __init__(
        self,
        input_dir: Path | None = None,
        config: Path | None = None,
        log_root: Path = Path("logs"),
    ) -> None:
        super().__init__()
        self.input_dir = input_dir
        self.config = config
        self.log_root = log_root
        self.latest_runtime_id: str | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield SetupWorkspace(input_dir=self.input_dir, config=self.config, log_root=self.log_root)
        yield Footer()

    def action_show_dashboard(self) -> None:
        self._sync_from_setup_if_available()
        if not isinstance(self.screen, DashboardScreen):
            self.push_screen(
                DashboardScreen(
                    input_dir=self.input_dir,
                    config=self.config,
                    runtime_id=self.latest_runtime_id,
                    log_root=self.log_root,
                )
            )

    def action_show_stats(self) -> None:
        self._sync_from_setup_if_available()
        if not isinstance(self.screen, StatsScreen):
            self.push_screen(StatsScreen(runtime_id=self.latest_runtime_id, log_root=self.log_root))

    def action_show_artifacts(self) -> None:
        self._sync_from_setup_if_available()
        if not isinstance(self.screen, RuntimeArtifactsScreen):
            self.push_screen(
                RuntimeArtifactsScreen(runtime_id=self.latest_runtime_id, log_root=self.log_root)
            )

    def action_show_approvals(self) -> None:
        self._sync_from_setup_if_available()
        if not isinstance(self.screen, RuntimeApprovalsScreen):
            self.push_screen(
                RuntimeApprovalsScreen(
                    runtime_id=self.latest_runtime_id,
                    log_root=self.log_root,
                    config=self.config or Path("config.toml"),
                )
            )

    def action_show_events(self) -> None:
        self._sync_from_setup_if_available()
        if not isinstance(self.screen, RuntimeLogScreen):
            self.push_screen(
                RuntimeLogScreen(runtime_id=self.latest_runtime_id, log_root=self.log_root)
            )

    async def action_run_pipeline(self) -> None:
        await self._run_pipeline_from_setup(resume=False)

    @on(Button.Pressed, "#run-pipeline")
    async def run_pipeline_from_button(self) -> None:
        await self._run_pipeline_from_setup(resume=False)

    @on(Button.Pressed, "#resume-pipeline")
    async def resume_pipeline_from_button(self) -> None:
        await self._run_pipeline_from_setup(resume=True)

    async def _run_pipeline_from_setup(self, *, resume: bool) -> None:
        setup = self._setup_or_none()
        if setup is None:
            return
        try:
            options = setup.build_generation_options(resume=resume)
        except Exception as exc:
            setup.set_status(f"参数错误: {exc}")
            return

        self.input_dir = options.input_dir
        self.config = options.config_path
        self.log_root = options.log_root
        setup.set_status("运行中")
        try:
            app_config = load_config(options.config_path)
            api_keys = {
                name: os.environ.get(profile.api_key_env)
                for name, profile in app_config.models.items()
            }
            summary = await InklinkPipeline(OpenAIToolLLM(app_config, api_keys)).run(options)
        except Exception as exc:
            setup.set_status(f"运行失败: {exc}")
            return
        self.latest_runtime_id = summary.runtime_id
        setup.set_runtime_id(summary.runtime_id)
        setup.set_run_summary(
            f"运行状态: {summary.status}\n"
            f"运行 ID: {summary.runtime_id}\n"
            f"生成章节: {', '.join(str(item) for item in summary.generated_chapters) or '无'}\n"
            f"输出文件: {len(summary.output_files)} 个\n"
            f"调用次数: {summary.stats.total_calls}\n"
            f"等待审批: {summary.waiting_approval_id or '无'}"
        )
        setup.set_status(
            f"运行完成/运行结束: {summary.runtime_id}，状态 {summary.status}，"
            f"生成 {len(summary.generated_chapters)} 章，调用 {summary.stats.total_calls} 次"
        )

    def _sync_from_setup_if_available(self) -> None:
        setup = self._setup_or_none()
        if setup is None:
            return
        self.input_dir = setup.configured_input_dir
        self.config = setup.configured_config_path
        self.log_root = setup.configured_log_root
        self.latest_runtime_id = setup.current_runtime_id or self.latest_runtime_id

    def _setup_or_none(self) -> SetupWorkspace | None:
        try:
            return self.query_one(SetupWorkspace)
        except Exception:
            return None
