import json
import os
from collections.abc import Callable, Sized
from dataclasses import asdict
from difflib import unified_diff
from pathlib import Path
from typing import TYPE_CHECKING

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widget import Widget
from textual.widgets import Button, Footer, Header, Input, Static

from inklink.config import load_config
from inklink.workflow.pipeline import GenerationOptions, InklinkPipeline, OpenAIToolLLM

if TYPE_CHECKING:
    from inklink.workflow.service import WorkflowService

DEFAULT_EVENT_LIMIT = 20
OUTPUT_MODES = {"output", "writeback"}


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

    .workspace-input {
        margin-bottom: 1;
    }

    .workspace-button {
        margin-right: 1;
    }
    """

    def __init__(
        self,
        input_dir: Path | None = None,
        config: Path | None = None,
        log_root: Path = Path("logs"),
    ) -> None:
        super().__init__(id="setup-workspace-container")
        self._input_dir = input_dir
        self._config = config
        self._log_root = log_root
        self._status = "待启动"

    def compose(self) -> ComposeResult:
        config_text = str(self._config) if self._config is not None else "config.toml"
        input_dir_text = str(self._input_dir) if self._input_dir is not None else ""
        defaults = GenerationOptions(input_dir=Path("."))

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
                yield Static(
                    "未开始。Ctrl+R 按当前参数执行；也可点击开始运行或恢复运行。",
                    id="setup-run-summary",
                )
        with Horizontal(classes="workspace-row"):
            with Vertical(classes="workspace-panel"):
                yield Static("基础参数", classes="workspace-title")
                yield Static("输入目录")
                yield Input(
                    value=input_dir_text,
                    placeholder="输入目录路径",
                    id="tui-input-dir",
                    classes="workspace-input",
                )
                yield Static("配置文件")
                yield Input(
                    value=config_text,
                    placeholder="配置文件路径",
                    id="tui-config-path",
                    classes="workspace-input",
                )
                yield Static("运行 ID")
                yield Input(
                    placeholder="恢复运行时填写",
                    id="tui-runtime-id",
                    classes="workspace-input",
                )
                yield Static("日志根目录")
                yield Input(
                    value=str(self._log_root),
                    placeholder="日志根目录",
                    id="tui-log-root",
                    classes="workspace-input",
                )
            with Vertical(classes="workspace-panel"):
                yield Static("生成参数", classes="workspace-title")
                yield Static("生成章节数")
                yield Input(
                    value=str(defaults.chapter_count),
                    placeholder="生成章节数",
                    id="tui-chapter-count",
                    classes="workspace-input",
                )
                yield Static("起始章节")
                yield Input(
                    placeholder="留空则接在输入章节后",
                    id="tui-start-chapter",
                    classes="workspace-input",
                )
                yield Static("最低字数")
                yield Input(
                    value=str(defaults.min_chars),
                    placeholder="最低字数",
                    id="tui-min-chars",
                    classes="workspace-input",
                )
                yield Static("最高字数")
                yield Input(
                    value=str(defaults.max_chars),
                    placeholder="最高字数",
                    id="tui-max-chars",
                    classes="workspace-input",
                )
                yield Static("最大修订轮数")
                yield Input(
                    placeholder="留空使用配置",
                    id="tui-max-revision-rounds",
                    classes="workspace-input",
                )
        with Horizontal(classes="workspace-row"):
            with Vertical(classes="workspace-panel"):
                yield Static("输出与审批", classes="workspace-title")
                yield Static("输出模式")
                yield Input(
                    value=defaults.output_mode or "",
                    placeholder="留空使用配置，可填 output 或 writeback",
                    id="tui-output-mode",
                    classes="workspace-input",
                )
                yield Static("自动批准")
                yield Input(
                    value=_format_bool(defaults.auto_approve),
                    placeholder="是或否",
                    id="tui-auto-approve",
                    classes="workspace-input",
                )
                with Horizontal():
                    yield Button("开始运行", id="run-pipeline", classes="workspace-button")
                    yield Button("恢复运行", id="resume-pipeline", classes="workspace-button")
            with Vertical(classes="workspace-panel"):
                yield Static("审批区", classes="workspace-title")
                yield Static("大纲、章节计划、场景计划和自审失败会进入审批点，可用 F4 查看。")

    @property
    def workspace_text(self) -> str:
        input_dir_text = str(self._input_dir) if self._input_dir is not None else "未选择"
        config_text = str(self._config) if self._config is not None else "config.toml"
        return (
            "设置工作台\n"
            f"输入目录: {input_dir_text}\n"
            f"配置文件: {config_text}\n"
            f"日志根目录: {self._log_root}\n"
            f"运行 ID: {self.current_runtime_id or '暂无'}\n"
            f"状态: {self._status}"
        )

    @property
    def status_text(self) -> str:
        return f"当前状态: {self._status}\n快捷键: F1 工作台，Ctrl+R 按当前参数执行"

    @property
    def current_runtime_id(self) -> str | None:
        return _blank_to_none(self._optional_input_value("#tui-runtime-id"))

    @property
    def configured_input_dir(self) -> Path | None:
        value = self._optional_input_value("#tui-input-dir")
        return Path(value) if value else self._input_dir

    @property
    def configured_config_path(self) -> Path:
        value = self._optional_input_value("#tui-config-path")
        return Path(value) if value else Path("config.toml")

    @property
    def configured_log_root(self) -> Path:
        value = self._optional_input_value("#tui-log-root")
        return Path(value) if value else Path("logs")

    def build_generation_options(self, *, resume: bool | None = None) -> GenerationOptions:
        input_dir = _parse_required_path("输入目录", self._input_value("#tui-input-dir"))
        config_path = _parse_required_path("配置文件", self._input_value("#tui-config-path"))
        log_root = _parse_required_path("日志根目录", self._input_value("#tui-log-root"))
        runtime_id = _blank_to_none(self._input_value("#tui-runtime-id"))
        if resume is True and runtime_id is None:
            raise ValueError("恢复运行需要填写运行 ID")
        if resume is False:
            runtime_id = None

        min_chars = _parse_required_int(
            "最低字数",
            self._input_value("#tui-min-chars"),
            minimum=0,
        )
        max_chars = _parse_required_int(
            "最高字数",
            self._input_value("#tui-max-chars"),
            minimum=0,
        )
        if max_chars < min_chars:
            raise ValueError("最高字数不能小于最低字数")

        output_mode = _blank_to_none(self._input_value("#tui-output-mode"))
        if output_mode is not None and output_mode not in OUTPUT_MODES:
            raise ValueError("输出模式仅支持 output 或 writeback")

        return GenerationOptions(
            input_dir=input_dir,
            config_path=config_path,
            log_root=log_root,
            output_mode=output_mode,
            runtime_id=runtime_id,
            chapter_count=_parse_required_int(
                "生成章节数",
                self._input_value("#tui-chapter-count"),
                minimum=1,
            ),
            start_chapter=_parse_optional_int(
                "起始章节",
                self._input_value("#tui-start-chapter"),
                minimum=1,
            ),
            min_chars=min_chars,
            max_chars=max_chars,
            max_revision_rounds=_parse_optional_int(
                "最大修订轮数",
                self._input_value("#tui-max-revision-rounds"),
                minimum=0,
            ),
            auto_approve=_parse_bool("自动批准", self._input_value("#tui-auto-approve")),
        )

    def set_status(self, status: str) -> None:
        self._status = status
        self._input_dir = self.configured_input_dir
        self._config = self.configured_config_path
        self._log_root = self.configured_log_root
        workspace = self.query_one("#setup-workspace", Static)
        workspace.update(self.workspace_text)
        status_panel = self.query_one("#setup-status", Static)
        status_panel.update(self.status_text)

    def set_runtime_id(self, runtime_id: str) -> None:
        self.query_one("#tui-runtime-id", Input).value = runtime_id
        self.query_one("#setup-workspace", Static).update(self.workspace_text)

    def set_run_summary(self, summary: str) -> None:
        self.query_one("#setup-run-summary", Static).update(summary)

    def _input_value(self, selector: str) -> str:
        return self.query_one(selector, Input).value.strip()

    def _optional_input_value(self, selector: str) -> str:
        try:
            return self.query_one(selector, Input).value.strip()
        except Exception:
            return ""


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
        runtime_overview = _read_runtime_overview(self._log_root, self._runtime_id)

        yield Header()
        yield Static(
            f"工作台\n输入目录: {input_dir_text}\n配置文件: {config_text}\n{runtime_overview}",
            id="dashboard-workspace",
        )
        with Horizontal(classes="dashboard-row"):
            with Vertical(classes="dashboard-panel"):
                yield Static("状态", classes="dashboard-title")
                yield Static(_read_run_status_text(self._log_root, self._runtime_id))
            with Vertical(classes="dashboard-panel"):
                yield Static("运行摘要", classes="dashboard-title")
                yield Static(_read_run_summary(self._log_root, self._runtime_id))
        with Horizontal(classes="dashboard-row"):
            with Vertical(classes="dashboard-panel"):
                yield Static("节点与产物", classes="dashboard-title")
                yield Static(_read_runtime_lists(self._log_root, self._runtime_id))
            with Vertical(classes="dashboard-panel"):
                yield Static("审批、用量与事件", classes="dashboard-title")
                yield Static(_read_runtime_activity(self._log_root, self._runtime_id))
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
        summary = _read_state_section(self._log_root, self._runtime_id, "usage")
        yield Header()
        yield Static(
            f"统计\n运行 ID: {runtime_text}\n{summary}",
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
        with Vertical(id="artifact-diff-controls"):
            yield Static("产物版本对比")
            yield Input(placeholder="产物 ID", id="diff-artifact-id")
            yield Input(placeholder="左侧版本号", id="diff-left-version")
            yield Input(placeholder="右侧版本号", id="diff-right-version")
            yield Button("显示差异", id="show-artifact-diff")
            yield Static("", id="artifact-diff-output")
        yield Footer()

    @on(Button.Pressed, "#show-artifact-diff")
    def show_artifact_diff(self) -> None:
        output = self.query_one("#artifact-diff-output", Static)
        if self._runtime_id is None:
            output.update("暂无运行。")
            return
        artifact_id = self.query_one("#diff-artifact-id", Input).value.strip()
        left_version_text = self.query_one("#diff-left-version", Input).value.strip()
        right_version_text = self.query_one("#diff-right-version", Input).value.strip()
        try:
            if not artifact_id:
                raise ValueError("产物 ID 不能为空")
            left_version = _parse_required_int("左侧版本号", left_version_text, minimum=1)
            right_version = _parse_required_int("右侧版本号", right_version_text, minimum=1)
            from inklink.workflow.service import WorkflowService

            with WorkflowService(log_root=self._log_root) as service:
                service.inspect_run(self._runtime_id)
                left = service.get_artifact(artifact_id, left_version)
                right = service.get_artifact(artifact_id, right_version)
            output.update(_diff_artifacts(artifact_id, left, right))
        except Exception as exc:
            output.update(f"显示差异失败: {exc}")


class RuntimeApprovalsScreen(Screen[None]):
    """Approval list and basic approval controls for the latest runtime."""

    def __init__(
        self,
        runtime_id: str | None = None,
        log_root: Path = Path("logs"),
        config: Path = Path("config.toml"),
    ) -> None:
        super().__init__(id="approvals", name="approvals")
        self._runtime_id = runtime_id
        self._log_root = log_root
        self._config = config
        self.title = "审批"

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(
            "审批\n" + _read_approval_workspace(self._log_root, self._runtime_id),
            id="approvals-workspace",
        )
        with Vertical(id="approval-controls"):
            yield Input(placeholder="审批 ID", id="approval-id")
            yield Input(placeholder="审批消息", id="approval-message")
            yield Button("记录消息", id="record-approval-message")
            yield Input(placeholder="产物 ID，可留空使用审批绑定产物", id="approval-artifact-id")
            yield Input(
                placeholder="产物类型 outline/chapter_plan/scene_plan", id="approval-artifact-type"
            )
            yield Input(
                placeholder="产物版本，可留空使用审批绑定版本",
                id="approval-artifact-version",
            )
            yield Button("AI 修改产物", id="chat-update-artifact")
            yield Button("批准产物", id="approve-artifact")
            yield Input(placeholder="节点 ID", id="retry-node-id")
            yield Button("重试节点", id="retry-node")
            yield Input(placeholder="章节号", id="chapter-number")
            with Horizontal():
                yield Button("放弃章节", id="abandon-chapter")
                yield Button("重写章节", id="rewrite-chapter")
            yield Static("", id="approval-command-status")
        yield Footer()

    @on(Button.Pressed, "#record-approval-message")
    def record_approval_message(self) -> None:
        approval_id = self.query_one("#approval-id", Input).value
        content = self.query_one("#approval-message", Input).value
        self._run_command(
            "记录消息",
            lambda service: (
                service.record_approval_message(
                    approval_id=approval_id,
                    role="user",
                    content=content,
                ).message
            ),
        )

    @on(Button.Pressed, "#approve-artifact")
    def approve_artifact(self) -> None:
        approval_id = self.query_one("#approval-id", Input).value
        artifact_id = self.query_one("#approval-artifact-id", Input).value
        version_text = self.query_one("#approval-artifact-version", Input).value
        self._run_command(
            "批准产物",
            lambda service: _approve_available_artifact(
                service=service,
                approval_id=approval_id,
                artifact_id=artifact_id,
                artifact_version_text=version_text,
            ),
        )

    @on(Button.Pressed, "#chat-update-artifact")
    async def chat_update_artifact(self) -> None:
        status = self.query_one("#approval-command-status", Static)
        if self._runtime_id is None:
            status.update("暂无运行。")
            return
        approval_id = self.query_one("#approval-id", Input).value.strip()
        artifact_id = self.query_one("#approval-artifact-id", Input).value.strip()
        artifact_type = self.query_one("#approval-artifact-type", Input).value.strip()
        message = self.query_one("#approval-message", Input).value.strip()
        try:
            artifact_id, artifact_type = _artifact_update_target(
                approval_id=approval_id,
                artifact_id=artifact_id,
                artifact_type=artifact_type,
                log_root=self._log_root,
                runtime_id=self._runtime_id,
            )
            if not message:
                raise ValueError("审批消息不能为空")
            app_config = load_config(self._config)
            api_keys = {
                name: os.environ.get(profile.api_key_env)
                for name, profile in app_config.models.items()
            }
            version = await InklinkPipeline(
                OpenAIToolLLM(app_config, api_keys)
            ).update_artifact_with_chat(
                runtime_id=self._runtime_id,
                log_root=self._log_root,
                config_path=self._config,
                approval_id=approval_id,
                artifact_id=artifact_id,
                artifact_type=artifact_type,
                user_message=message,
            )
            status.update(f"AI 修改产物: updated {artifact_id}@{version}")
            self.query_one("#approvals-workspace", Static).update(
                "审批\n" + _read_approval_workspace(self._log_root, self._runtime_id)
            )
        except Exception as exc:
            status.update(f"AI 修改产物失败: {exc}")

    @on(Button.Pressed, "#retry-node")
    def retry_node(self) -> None:
        node_id = self.query_one("#retry-node-id", Input).value
        self._run_command("重试节点", lambda service: service.retry_node(node_id).message)

    @on(Button.Pressed, "#abandon-chapter")
    def abandon_chapter(self) -> None:
        chapter_text = self.query_one("#chapter-number", Input).value
        self._run_command(
            "放弃章节",
            lambda service: service.abandon_chapter(int(chapter_text)).message,
        )

    @on(Button.Pressed, "#rewrite-chapter")
    def rewrite_chapter(self) -> None:
        chapter_text = self.query_one("#chapter-number", Input).value
        self._run_command(
            "重写章节",
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
            status.update(f"{action}失败: {exc}")


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


def _blank_to_none(value: str) -> str | None:
    stripped = value.strip()
    return stripped or None


def _format_bool(value: bool) -> str:
    return "是" if value else "否"


def _parse_required_path(field_name: str, value: str) -> Path:
    stripped = value.strip()
    if not stripped:
        raise ValueError(f"{field_name}不能为空")
    return Path(stripped)


def _parse_required_int(field_name: str, value: str, *, minimum: int) -> int:
    stripped = value.strip()
    if not stripped:
        raise ValueError(f"{field_name}不能为空")
    try:
        parsed = int(stripped)
    except ValueError as exc:
        raise ValueError(f"{field_name}必须是整数") from exc
    if parsed < minimum:
        raise ValueError(f"{field_name}必须大于等于 {minimum}")
    return parsed


def _parse_optional_int(field_name: str, value: str, *, minimum: int) -> int | None:
    stripped = value.strip()
    if not stripped:
        return None
    return _parse_required_int(field_name, stripped, minimum=minimum)


def _parse_bool(field_name: str, value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"是", "true", "1", "yes", "y", "on", "开启"}:
        return True
    if normalized in {"", "否", "false", "0", "no", "n", "off", "关闭"}:
        return False
    raise ValueError(f"{field_name}必须填写是或否")


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
    try:
        from inklink.workflow.service import WorkflowService

        with WorkflowService(log_root=log_root) as service:
            service.inspect_run(runtime_id)
            payload: object
            if section == "artifacts":
                payload = service.list_artifacts()
            elif section == "approvals":
                payload = service.list_approvals()
            elif section == "nodes":
                payload = service.list_nodes()
            elif section == "messages":
                payload = service.list_messages()
            elif section == "usage":
                payload = [asdict(row) for row in service.usage_stats()]
            else:
                payload = []
    except Exception as exc:
        return f"读取运行状态失败: {exc}"
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def _read_runtime_overview(log_root: Path, runtime_id: str | None) -> str:
    if runtime_id is None:
        return "运行 ID: 暂无\n状态: 暂无运行"
    payload = _runtime_payload(log_root, runtime_id)
    if "error" in payload:
        return f"运行 ID: {runtime_id}\n状态: 读取失败\n错误: {payload['error']}"
    nodes = _payload_sized(payload, "nodes")
    artifacts = _payload_sized(payload, "artifacts")
    approvals = _payload_sized(payload, "approvals")
    usage = _payload_sized(payload, "usage")
    events = _payload_sized(payload, "events")
    return (
        f"运行 ID: {payload['runtime_id']}\n"
        f"状态: {payload['status']}\n"
        f"日志目录: {payload['log_dir']}\n"
        f"节点数: {len(nodes)}\n"
        f"产物数: {len(artifacts)}\n"
        f"审批数: {len(approvals)}\n"
        f"用量记录: {len(usage)}\n"
        f"事件数: {len(events)}"
    )


def _read_run_status_text(log_root: Path, runtime_id: str | None) -> str:
    if runtime_id is None:
        return "暂无运行。"
    payload = _runtime_payload(log_root, runtime_id)
    if "error" in payload:
        return f"读取状态失败: {payload['error']}"
    return (
        f"运行 ID: {payload['runtime_id']}\n"
        f"状态: {payload['status']}\n"
        f"输入目录: {payload['input_dir']}\n"
        f"日志目录: {payload['log_dir']}"
    )


def _read_runtime_lists(log_root: Path, runtime_id: str | None) -> str:
    if runtime_id is None:
        return "暂无运行。"
    payload = _runtime_payload(log_root, runtime_id)
    if "error" in payload:
        return f"读取节点与产物失败: {payload['error']}"
    return "\n\n".join(
        [
            "DAG:",
            _format_node_tree(_payload_list(payload, "nodes")),
            "节点:",
            json.dumps(payload["nodes"], ensure_ascii=False, indent=2, sort_keys=True),
            "产物:",
            json.dumps(payload["artifacts"], ensure_ascii=False, indent=2, sort_keys=True),
        ]
    )


def _payload_list(payload: dict[str, object], key: str) -> list[dict[str, object]]:
    value = payload[key]
    if not isinstance(value, list):
        raise TypeError(f"{key} 不是列表")
    return [item for item in value if isinstance(item, dict)]


def _format_node_tree(nodes: list[dict[str, object]]) -> str:
    if not nodes:
        return "暂无节点。"
    node_ids = [str(node.get("node_id")) for node in nodes]
    node_by_id = {str(node.get("node_id")): node for node in nodes}
    children: dict[str, list[str]] = {node_id: [] for node_id in node_ids}
    roots: list[str] = []
    for node in nodes:
        node_id = str(node.get("node_id"))
        depends_on = node.get("depends_on")
        dependencies = [str(item) for item in depends_on] if isinstance(depends_on, list) else []
        known_dependencies = [dependency for dependency in dependencies if dependency in children]
        if not known_dependencies:
            roots.append(node_id)
        for dependency in known_dependencies:
            children[dependency].append(node_id)

    lines: list[str] = []
    seen: set[str] = set()

    def visit(node_id: str, prefix: str) -> None:
        if node_id in seen:
            return
        seen.add(node_id)
        node = node_by_id[node_id]
        status = node.get("status")
        node_type = node.get("node_type")
        waiting_reason = node.get("waiting_reason")
        suffix = f" [{status}] {node_type}"
        if waiting_reason:
            suffix += f" - {waiting_reason}"
        lines.append(f"{prefix}{node_id}{suffix}")
        for child_id in sorted(children[node_id]):
            visit(child_id, prefix + "  ")

    for root in sorted(set(roots)):
        visit(root, "")
    for node_id in sorted(node_ids):
        visit(node_id, "")
    return "\n".join(lines)


def _read_runtime_activity(log_root: Path, runtime_id: str | None) -> str:
    if runtime_id is None:
        return "暂无运行。"
    payload = _runtime_payload(log_root, runtime_id)
    if "error" in payload:
        return f"读取活动失败: {payload['error']}"
    return "\n\n".join(
        [
            "审批:",
            json.dumps(payload["approvals"], ensure_ascii=False, indent=2, sort_keys=True),
            "用量:",
            json.dumps(payload["usage"], ensure_ascii=False, indent=2, sort_keys=True),
            "事件:",
            json.dumps(payload["events"], ensure_ascii=False, indent=2, sort_keys=True),
        ]
    )


def _runtime_payload(log_root: Path, runtime_id: str) -> dict[str, object]:
    try:
        from inklink.workflow.service import WorkflowService

        with WorkflowService(log_root=log_root) as service:
            run = service.inspect_run(runtime_id)
            return {
                "runtime_id": run.runtime_id,
                "input_dir": str(run.input_dir),
                "log_dir": str(run.log_dir),
                "status": _read_run_status(log_root, runtime_id),
                "nodes": service.list_nodes(),
                "artifacts": service.list_artifacts(),
                "approvals": service.list_approvals(),
                "usage": [asdict(row) for row in service.usage_stats()],
                "events": service.recent_events(limit=DEFAULT_EVENT_LIMIT),
            }
    except Exception as exc:
        return {"error": str(exc)}


def _read_run_status(log_root: Path, runtime_id: str) -> str:
    db_path = log_root / runtime_id / "state.sqlite"
    if not db_path.exists():
        return "暂无状态库"
    from inklink.storage.sqlite import StateStore

    with StateStore.open(db_path) as store:
        row = store.get_run(runtime_id)
    return str(row["status"])


def _payload_sized(payload: dict[str, object], key: str) -> Sized:
    value = payload[key]
    if not isinstance(value, Sized):
        raise TypeError(f"{key} 不是可计数数据")
    return value


def _approve_available_artifact(
    *,
    service: "WorkflowService",
    approval_id: str,
    artifact_id: str,
    artifact_version_text: str,
) -> str:
    normalized_approval_id = approval_id.strip()
    if not normalized_approval_id:
        raise ValueError("审批 ID 不能为空")
    normalized_artifact_id = artifact_id.strip()
    normalized_version_text = artifact_version_text.strip()
    approval_type = normalized_approval_id
    if not normalized_artifact_id or not normalized_version_text:
        for approval in service.list_approvals():
            if approval.get("approval_id") == normalized_approval_id:
                approval_type = str(approval.get("approval_type") or normalized_approval_id)
                if not normalized_artifact_id:
                    normalized_artifact_id = str(approval.get("artifact_id") or "")
                if not normalized_version_text:
                    normalized_version_text = str(approval.get("artifact_version") or "")
                break
    if not normalized_artifact_id or not normalized_version_text:
        raise ValueError("审批未绑定可批准产物，请填写产物 ID 和版本")
    artifact_version = _parse_required_int("产物版本", normalized_version_text, minimum=1)
    return service.approve_artifact(
        approval_id=normalized_approval_id,
        approval_type=approval_type,
        artifact_id=normalized_artifact_id,
        artifact_version=artifact_version,
    ).message


def _artifact_update_target(
    *,
    approval_id: str,
    artifact_id: str,
    artifact_type: str,
    log_root: Path,
    runtime_id: str,
) -> tuple[str, str]:
    normalized_approval_id = approval_id.strip()
    normalized_artifact_id = artifact_id.strip()
    normalized_artifact_type = artifact_type.strip()
    if not normalized_approval_id:
        raise ValueError("审批 ID 不能为空")
    if normalized_artifact_id and normalized_artifact_type:
        return normalized_artifact_id, normalized_artifact_type

    from inklink.workflow.service import WorkflowService

    with WorkflowService(log_root=log_root) as service:
        service.inspect_run(runtime_id)
        for approval in service.list_approvals():
            if approval.get("approval_id") != normalized_approval_id:
                continue
            if not normalized_artifact_id:
                normalized_artifact_id = str(approval.get("artifact_id") or "")
            if not normalized_artifact_type:
                normalized_artifact_type = str(approval.get("approval_type") or "")
            break
    if not normalized_artifact_id or not normalized_artifact_type:
        raise ValueError("审批未绑定可修改产物，请填写产物 ID 和类型")
    return normalized_artifact_id, normalized_artifact_type


def _diff_artifacts(
    artifact_id: str,
    left: dict[str, object],
    right: dict[str, object],
) -> str:
    left_version = _object_to_int(left["version"])
    right_version = _object_to_int(right["version"])
    diff = "".join(
        unified_diff(
            _artifact_payload_text(left).splitlines(keepends=True),
            _artifact_payload_text(right).splitlines(keepends=True),
            fromfile=f"{artifact_id}@{left_version}",
            tofile=f"{artifact_id}@{right_version}",
        )
    )
    return diff or "两个版本没有差异。"


def _artifact_payload_text(artifact: dict[str, object]) -> str:
    return json.dumps(artifact.get("payload"), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _object_to_int(value: object) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str):
        return int(value)
    raise TypeError("版本号不是整数")


def _legacy_read_state_section(log_root: Path, runtime_id: str | None, section: str) -> str:
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
            "审批:",
            _read_state_section(log_root, runtime_id, "approvals"),
            "消息:",
            _read_state_section(log_root, runtime_id, "messages"),
        ]
    )


def _read_events(log_root: Path, runtime_id: str | None, limit: int = DEFAULT_EVENT_LIMIT) -> str:
    if runtime_id is None:
        return "暂无运行。"
    try:
        from inklink.workflow.service import WorkflowService

        with WorkflowService(log_root=log_root) as service:
            service.inspect_run(runtime_id)
            events = service.recent_events(limit=limit)
    except Exception as exc:
        return f"读取事件失败: {exc}"
    return json.dumps(events, ensure_ascii=False, indent=2, sort_keys=True)
