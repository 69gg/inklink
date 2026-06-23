import json
import os
from collections.abc import Callable
from pathlib import Path

from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Button, Footer, Header

from inklink.config import api_key_for_profile, load_config
from inklink.tui.screens import (
    DashboardScreen,
    RuntimeApprovalsScreen,
    RuntimeArtifactsScreen,
    RuntimeLogScreen,
    SetupWorkspace,
    StatsScreen,
)
from inklink.workflow.pipeline import (
    GenerationOptions,
    InklinkPipeline,
    OpenAIToolLLM,
    PipelineProgress,
)


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
        self._pipeline_running = False
        self._latest_progress = "待启动"

    def compose(self) -> ComposeResult:
        yield Header()
        yield SetupWorkspace(input_dir=self.input_dir, config=self.config, log_root=self.log_root)
        yield Footer()

    def action_show_dashboard(self) -> None:
        self._sync_from_setup_if_available()
        self._show_runtime_screen(
            DashboardScreen,
            lambda: DashboardScreen(
                input_dir=self.input_dir,
                config=self.config,
                runtime_id=self.latest_runtime_id,
                log_root=self.log_root,
            ),
        )

    def action_show_stats(self) -> None:
        self._sync_from_setup_if_available()
        self._show_runtime_screen(
            StatsScreen,
            lambda: StatsScreen(runtime_id=self.latest_runtime_id, log_root=self.log_root),
        )

    def action_show_artifacts(self) -> None:
        self._sync_from_setup_if_available()
        self._show_runtime_screen(
            RuntimeArtifactsScreen,
            lambda: RuntimeArtifactsScreen(
                runtime_id=self.latest_runtime_id,
                log_root=self.log_root,
            ),
        )

    def action_show_approvals(self) -> None:
        self._sync_from_setup_if_available()
        self._show_runtime_screen(
            RuntimeApprovalsScreen,
            lambda: RuntimeApprovalsScreen(
                runtime_id=self.latest_runtime_id,
                log_root=self.log_root,
                config=self.config or Path("config.toml"),
            ),
        )

    def action_show_events(self) -> None:
        self._sync_from_setup_if_available()
        self._show_runtime_screen(
            RuntimeLogScreen,
            lambda: RuntimeLogScreen(runtime_id=self.latest_runtime_id, log_root=self.log_root),
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
        if self._pipeline_running:
            setup.set_status(f"已有运行正在执行: {self._latest_progress}")
            return
        try:
            options = setup.build_generation_options(resume=resume)
        except Exception as exc:
            setup.set_status(f"参数错误: {exc}")
            return

        self.input_dir = options.input_dir
        self.config = options.config_path
        self.log_root = options.log_root
        self.latest_runtime_id = options.runtime_id or self.latest_runtime_id
        self._pipeline_running = True
        self._latest_progress = "后台任务已提交，正在读取配置"
        setup.set_run_buttons_enabled(False)
        setup.set_status(self._latest_progress)
        setup.set_run_summary(_starting_summary(options, resume=resume))
        self.run_worker(
            self._run_pipeline_worker(options),
            name="inklink-pipeline",
            group="pipeline",
            description="Run Inklink continuation workflow",
            exit_on_error=False,
            exclusive=True,
        )

    async def _run_pipeline_worker(self, options: GenerationOptions) -> None:
        setup = self._setup_or_none()
        try:
            self._handle_pipeline_progress(
                PipelineProgress(
                    message="读取配置文件",
                    runtime_id=options.runtime_id,
                )
            )
            app_config = load_config(options.config_path)
            api_keys = {
                name: api_key_for_profile(profile, os.environ)
                for name, profile in app_config.models.items()
            }
            summary = await InklinkPipeline(
                OpenAIToolLLM(app_config, api_keys),
                progress_callback=self._handle_pipeline_progress,
            ).run(options)
        except Exception as exc:
            self._pipeline_running = False
            self._set_setup_run_buttons_enabled(True)
            if setup is not None:
                setup.set_status(f"运行失败: {exc}")
                setup.set_run_summary(f"运行失败\n当前阶段: {self._latest_progress}\n错误: {exc}")
            return
        self._pipeline_running = False
        self._set_setup_run_buttons_enabled(True)
        self.latest_runtime_id = summary.runtime_id
        setup = self._setup_or_none()
        if setup is not None:
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

    def _show_runtime_screen(
        self,
        screen_type: type[Screen[None]],
        factory: Callable[[], Screen[None]],
    ) -> None:
        if isinstance(self.screen, screen_type):
            return
        existing_screen = next(
            (screen for screen in reversed(self.screen_stack) if isinstance(screen, screen_type)),
            None,
        )
        if existing_screen is not None:
            while self.screen is not existing_screen:
                self.pop_screen()
            return
        self.push_screen(factory())

    def _handle_pipeline_progress(self, progress: PipelineProgress) -> None:
        if progress.runtime_id is not None:
            self.latest_runtime_id = progress.runtime_id
        details = [progress.message]
        if progress.node_id is not None:
            details.append(f"节点: {progress.node_id}")
        if progress.chapter_number is not None:
            details.append(f"章节: {progress.chapter_number}")
        self._latest_progress = "；".join(details)
        setup = self._setup_or_none()
        if setup is None:
            return
        setup.set_status(f"运行中: {self._latest_progress}")
        setup.set_run_summary(
            _progress_summary(
                progress=progress,
                fallback_runtime_id=self.latest_runtime_id,
                log_root=self.log_root,
            )
        )

    def _set_setup_run_buttons_enabled(self, enabled: bool) -> None:
        setup = self._setup_or_none()
        if setup is not None:
            setup.set_run_buttons_enabled(enabled)

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


def _starting_summary(options: GenerationOptions, *, resume: bool) -> str:
    mode = "恢复运行" if resume else "开始运行"
    return (
        f"{mode}: 后台任务已提交\n"
        f"输入目录: {options.input_dir or '沿用已保存运行设置'}\n"
        f"配置文件: {options.config_path}\n"
        f"日志根目录: {options.log_root}\n"
        f"运行 ID: {options.runtime_id or '启动后生成'}\n"
        "当前阶段: 正在读取配置文件"
    )


def _progress_summary(
    *,
    progress: PipelineProgress,
    fallback_runtime_id: str | None,
    log_root: Path,
) -> str:
    runtime_id = progress.runtime_id or fallback_runtime_id
    lines = [
        "运行中",
        f"当前阶段: {progress.message}",
        f"运行 ID: {runtime_id or '启动后生成'}",
    ]
    if progress.node_id is not None:
        lines.append(f"节点: {progress.node_id}")
    if progress.chapter_number is not None:
        lines.append(f"章节: {progress.chapter_number}")
    event_lines = _recent_event_lines(log_root, runtime_id)
    if event_lines:
        lines.append("最近事件:")
        lines.extend(event_lines)
    return "\n".join(lines)


def _recent_event_lines(log_root: Path, runtime_id: str | None, *, limit: int = 5) -> list[str]:
    if runtime_id is None:
        return []
    path = log_root / runtime_id / "events.jsonl"
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    events: list[str] = []
    for line in lines[-limit:]:
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(parsed, dict):
            continue
        event_type = str(parsed.get("event_type") or "event")
        payload = parsed.get("payload")
        suffix = _event_suffix(payload)
        events.append(f"- {event_type}{suffix}")
    return events


def _event_suffix(payload: object) -> str:
    if not isinstance(payload, dict):
        return ""
    parts: list[str] = []
    for key in ("task_type", "node_id", "chapter_number", "approval_id", "tool_name"):
        value = payload.get(key)
        if isinstance(value, str | int):
            parts.append(f"{key}={value}")
    return f" ({', '.join(parts)})" if parts else ""
