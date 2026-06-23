import asyncio

from rich.console import Console
from textual.widgets import Button, Input, Static, TextArea

from inklink.storage.sqlite import StateStore
from inklink.tui import screens as tui_screens
from inklink.tui.app import InklinkApp
from inklink.tui.screens import (
    DashboardScreen,
    RuntimeApprovalsScreen,
    RuntimeArtifactsScreen,
    RuntimeLogScreen,
    SetupWorkspace,
    StatsScreen,
    _format_approval_workspace,
    _format_next_action,
    _format_node_tree,
)
from inklink.tui.snapshot import RunSnapshot
from inklink.workflow.pipeline import PipelineProgress, PipelineSummary, RunStats
from inklink.workflow.service import WorkflowService


async def test_tui_app_starts_with_expected_title() -> None:
    app = InklinkApp()

    async with app.run_test() as pilot:
        assert pilot.app.title == "墨连 Inklink"


async def test_tui_initial_interface_contains_workspace_text() -> None:
    app = InklinkApp()

    async with app.run_test() as pilot:
        body_text = pilot.app.screen.query_one("#setup-workspace", Static).render()

    assert "输入目录" in str(body_text)


def test_tui_static_renders_dynamic_text_without_markup_parsing() -> None:
    text = (
        "1 validation error\n"
        "field\n"
        "  Input should be a valid string "
        "[type=string_type, input_value={'a': 1}, input_type=dict]"
    )
    console = Console(record=True, width=240)

    console.print(tui_screens.Static(text).render())

    rendered = console.export_text()
    assert "[type=string_type" in rendered
    assert "input_value={'a': 1}" in rendered


async def test_tui_initial_interface_contains_generation_controls() -> None:
    app = InklinkApp()

    async with app.run_test() as pilot:
        screen = pilot.app.screen

        assert screen.query_one("#tui-input-dir", Input)
        assert screen.query_one("#tui-config-path", Input)
        assert screen.query_one("#tui-runtime-id", Input)
        assert screen.query_one("#tui-chapter-count", Input)
        assert screen.query_one("#tui-start-chapter", Input)
        assert screen.query_one("#tui-min-chars", Input)
        assert screen.query_one("#tui-max-chars", Input)
        assert screen.query_one("#tui-max-revision-rounds", Input)
        assert screen.query_one("#tui-output-mode", Input)
        assert screen.query_one("#tui-auto-approve", Input)
        assert screen.query_one("#tui-notes", TextArea)
        assert screen.query_one("#tui-log-root", Input)
        assert screen.query_one("#run-pipeline", Button)
        assert screen.query_one("#resume-pipeline", Button)


async def test_tui_setup_workspace_scrolls_to_bottom_controls_on_small_terminal() -> None:
    app = InklinkApp()

    async with app.run_test(size=(100, 18)) as pilot:
        setup = pilot.app.screen.query_one(SetupWorkspace)
        run_button = pilot.app.screen.query_one("#run-pipeline", Button)

        assert setup.max_scroll_y > 0

        setup.scroll_end(animate=False, immediate=True)
        await pilot.pause()

        assert setup.scroll_y == setup.max_scroll_y
        assert run_button.region.y >= setup.region.y
        assert run_button.region.y < setup.region.y + setup.region.height


async def test_tui_f1_shows_dashboard_screen() -> None:
    app = InklinkApp()

    async with app.run_test() as pilot:
        await pilot.press("f1")

        assert isinstance(pilot.app.screen, DashboardScreen)
        assert pilot.app.screen.id == "dashboard"
        assert pilot.app.screen.title == "工作台"


async def test_tui_f1_does_not_push_duplicate_dashboard_screen() -> None:
    app = InklinkApp()

    async with app.run_test() as pilot:
        await pilot.press("f1")
        dashboard_screen = pilot.app.screen
        dashboard_stack_size = len(pilot.app.screen_stack)

        await pilot.press("f1")

        assert pilot.app.screen is dashboard_screen
        assert len(pilot.app.screen_stack) == dashboard_stack_size


async def test_tui_runtime_navigation_reuses_existing_screen_ids() -> None:
    app = InklinkApp()

    async with app.run_test() as pilot:
        await pilot.press("f5")
        await pilot.press("f1")
        await pilot.press("f2")
        await pilot.press("f1")

        screen_ids = [screen.id for screen in pilot.app.screen_stack if screen.id is not None]

        assert isinstance(pilot.app.screen, DashboardScreen)
        assert screen_ids.count("dashboard") == 1
        assert len(screen_ids) == len(set(screen_ids))


async def test_tui_f2_shows_stats_screen() -> None:
    app = InklinkApp()

    async with app.run_test() as pilot:
        await pilot.press("f2")

        assert isinstance(pilot.app.screen, StatsScreen)
        assert pilot.app.screen.id == "stats"
        assert pilot.app.screen.title == "统计"


async def test_tui_runtime_screens_open_from_function_keys() -> None:
    app = InklinkApp()

    async with app.run_test() as pilot:
        await pilot.press("f3")
        assert isinstance(pilot.app.screen, RuntimeArtifactsScreen)
        await pilot.press("escape")
        await pilot.press("f4")
        assert isinstance(pilot.app.screen, RuntimeApprovalsScreen)
        assert pilot.app.screen.query_one("#approval-id", Input)
        assert pilot.app.screen.query_one("#approval-message", Input)
        assert pilot.app.screen.query_one("#record-approval-message", Button)
        assert pilot.app.screen.query_one("#approval-artifact-type", Input)
        assert pilot.app.screen.query_one("#chat-update-artifact", Button)
        assert pilot.app.screen.query_one("#approve-artifact", Button)
        await pilot.press("escape")
        await pilot.press("f5")
        assert isinstance(pilot.app.screen, RuntimeLogScreen)


async def test_tui_artifacts_screen_contains_diff_controls() -> None:
    app = InklinkApp()

    async with app.run_test() as pilot:
        await pilot.press("f3")

        assert isinstance(pilot.app.screen, RuntimeArtifactsScreen)
        assert pilot.app.screen.query_one("#diff-artifact-id", Input)
        assert pilot.app.screen.query_one("#diff-left-version", Input)
        assert pilot.app.screen.query_one("#diff-right-version", Input)
        assert pilot.app.screen.query_one("#show-artifact-diff", Button)
        assert pilot.app.screen.query_one("#artifact-diff-output", Static)


def test_tui_formats_node_tree_from_dependencies() -> None:
    tree = _format_node_tree(
        [
            {
                "node_id": "load_project",
                "node_type": "load_project",
                "status": "completed",
                "depends_on": [],
            },
            {
                "node_id": "analyze_chapter:1",
                "node_type": "analyze_chapter",
                "status": "completed",
                "depends_on": ["load_project"],
            },
            {
                "node_id": "write_output:2",
                "node_type": "write_output",
                "status": "waiting",
                "depends_on": ["analyze_chapter:1"],
                "waiting_reason": "目标文件已存在",
            },
        ]
    )

    assert "load_project [completed]" in tree
    assert "  analyze_chapter:1 [completed]" in tree
    assert "目标文件已存在" in tree


async def test_tui_approval_message_button_records_message(tmp_path) -> None:
    novel = tmp_path / "novel"
    novel.mkdir()
    (novel / "1.txt").write_text("title: 第一章\n---\n正文", encoding="utf-8")
    log_root = tmp_path / "logs"

    with WorkflowService(log_root=log_root) as service:
        run = service.start_run(novel)

    app = InklinkApp(input_dir=novel, log_root=log_root)
    app.latest_runtime_id = run.runtime_id

    async with app.run_test() as pilot:
        await pilot.press("f4")
        pilot.app.screen.query_one("#approval-id", Input).value = "outline"
        pilot.app.screen.query_one("#approval-message", Input).value = "请强化冲突"

        await pilot.click("#record-approval-message")
        await pilot.pause()

        status = pilot.app.screen.query_one("#approval-command-status", Static).render()

    assert "记录消息" in str(status)
    with WorkflowService(log_root=log_root) as service:
        service.inspect_run(run.runtime_id)
        messages = service.list_messages("outline")

    assert messages[0]["content"] == "请强化冲突"


async def test_tui_approvals_prefill_waiting_artifact(tmp_path) -> None:
    novel = tmp_path / "novel"
    novel.mkdir()
    (novel / "1.txt").write_text("title: 第一章\n---\n正文", encoding="utf-8")
    log_root = tmp_path / "logs"

    with WorkflowService(log_root=log_root) as service:
        run = service.start_run(novel)

    with StateStore.open(run.log_dir / "state.sqlite") as store:
        version = store.upsert_artifact(
            artifact_id="outline",
            artifact_type="outline",
            payload={"items": []},
            is_draft=True,
            approval_id="outline",
        )
        store.create_or_update_approval(
            approval_id="outline",
            approval_type="outline",
            status="waiting",
            auto_approve=False,
            artifact_id="outline",
            artifact_version=version,
        )
        store.update_run_status(run.runtime_id, "waiting_approval")

    app = InklinkApp(input_dir=novel, log_root=log_root)
    app.latest_runtime_id = run.runtime_id

    async with app.run_test() as pilot:
        await pilot.press("f4")
        await pilot.pause()

        screen = pilot.app.screen
        assert isinstance(screen, RuntimeApprovalsScreen)
        assert screen.query_one("#approval-id", Input).value == "outline"
        assert screen.query_one("#approval-artifact-id", Input).value == "outline"
        assert screen.query_one("#approval-artifact-type", Input).value == "outline"
        assert screen.query_one("#approval-artifact-version", Input).value == str(version)


async def test_tui_waiting_approval_does_not_overwrite_user_input(tmp_path) -> None:
    novel = tmp_path / "novel"
    novel.mkdir()
    (novel / "1.txt").write_text("title: 第一章\n---\n正文", encoding="utf-8")
    log_root = tmp_path / "logs"

    with WorkflowService(log_root=log_root) as service:
        run = service.start_run(novel)

    with StateStore.open(run.log_dir / "state.sqlite") as store:
        outline_version = store.upsert_artifact(
            artifact_id="outline",
            artifact_type="outline",
            payload={"items": []},
            is_draft=True,
            approval_id="outline",
        )
        store.create_or_update_approval(
            approval_id="outline",
            approval_type="outline",
            status="waiting",
            auto_approve=False,
            artifact_id="outline",
            artifact_version=outline_version,
        )
        chapter_version = store.upsert_artifact(
            artifact_id="chapter_plan",
            artifact_type="chapter_plan",
            payload=[],
            is_draft=True,
            approval_id="chapter_plan",
        )
        store.create_or_update_approval(
            approval_id="chapter_plan",
            approval_type="chapter_plan",
            status="waiting",
            auto_approve=False,
            artifact_id="chapter_plan",
            artifact_version=chapter_version,
        )
        store.update_run_status(run.runtime_id, "waiting_approval")

    app = InklinkApp(input_dir=novel, log_root=log_root)
    app.latest_runtime_id = run.runtime_id

    async with app.run_test() as pilot:
        await pilot.press("f4")
        await pilot.pause()
        message_input = pilot.app.screen.query_one("#approval-message", Input)
        message_input.value = "我正在输入，不要覆盖"
        pilot.app.screen.query_one("#approval-id", Input).value = "outline"
        message_input.focus()

        pilot.app._handle_pipeline_progress(
            PipelineProgress(
                message="等待用户审批章节计划",
                runtime_id=run.runtime_id,
                status="waiting",
                waiting_approval_id="chapter_plan",
            )
        )
        await pilot.pause()

        assert (
            pilot.app.screen.query_one("#approval-message", Input).value == "我正在输入，不要覆盖"
        )
        assert pilot.app.screen.query_one("#approval-id", Input).value == "outline"


async def test_tui_approval_prefill_clears_stale_fields(tmp_path) -> None:
    novel = tmp_path / "novel"
    novel.mkdir()
    (novel / "1.txt").write_text("title: 第一章\n---\n正文", encoding="utf-8")
    log_root = tmp_path / "logs"

    with WorkflowService(log_root=log_root) as service:
        run = service.start_run(novel)

    with StateStore.open(run.log_dir / "state.sqlite") as store:
        version = store.upsert_artifact(
            artifact_id="outline",
            artifact_type="outline",
            payload={"items": []},
            is_draft=True,
            approval_id="outline",
        )
        store.create_or_update_approval(
            approval_id="outline",
            approval_type="outline",
            status="waiting",
            auto_approve=False,
            artifact_id="outline",
            artifact_version=version,
        )
        store.update_run_status(run.runtime_id, "waiting_approval")

    app = InklinkApp(input_dir=novel, log_root=log_root)
    app.latest_runtime_id = run.runtime_id

    async with app.run_test() as pilot:
        await pilot.press("f4")
        await pilot.pause()
        screen = pilot.app.screen
        assert isinstance(screen, RuntimeApprovalsScreen)
        assert screen.query_one("#approval-artifact-version", Input).value == str(version)

        screen.refresh_from_snapshot(
            RunSnapshot(
                runtime_id=run.runtime_id,
                log_root=log_root,
                status="waiting_approval",
                approvals=[
                    {
                        "approval_id": "review_failure:3",
                        "approval_type": "review_failure",
                        "status": "waiting",
                    }
                ],
            ),
            prefill=True,
        )

        assert screen.query_one("#approval-id", Input).value == "review_failure:3"
        assert screen.query_one("#approval-artifact-id", Input).value == ""
        assert screen.query_one("#approval-artifact-type", Input).value == "review_failure"
        assert screen.query_one("#approval-artifact-version", Input).value == ""
        assert screen.query_one("#chapter-number", Input).value == "3"


async def test_tui_chat_update_syncs_new_artifact_version(monkeypatch, tmp_path) -> None:
    novel = tmp_path / "novel"
    novel.mkdir()
    (novel / "1.txt").write_text("title: 第一章\n---\n正文", encoding="utf-8")
    log_root = tmp_path / "logs"
    config = tmp_path / "config.toml"
    config.write_text(
        """
[models.default]
api = "responses"
model = "fake-model"
api_key = "sk-test"
""",
        encoding="utf-8",
    )

    with WorkflowService(log_root=log_root) as service:
        run = service.start_run(novel)

    with StateStore.open(run.log_dir / "state.sqlite") as store:
        version = store.upsert_artifact(
            artifact_id="outline",
            artifact_type="outline",
            payload={"items": []},
            is_draft=True,
            approval_id="outline",
        )
        store.create_or_update_approval(
            approval_id="outline",
            approval_type="outline",
            status="waiting",
            auto_approve=False,
            artifact_id="outline",
            artifact_version=version,
        )
        store.update_run_status(run.runtime_id, "waiting_approval")

    class FakePipeline:
        def __init__(self, llm: object) -> None:
            self.llm = llm

        async def update_artifact_with_chat(self, **kwargs: object) -> int:
            return 2

    class FakeLLM:
        def __init__(self, config: object, api_keys: object) -> None:
            self.config = config
            self.api_keys = api_keys

    monkeypatch.setattr("inklink.tui.screens.InklinkPipeline", FakePipeline)
    monkeypatch.setattr("inklink.tui.screens.OpenAIToolLLM", FakeLLM)

    app = InklinkApp(input_dir=novel, config=config, log_root=log_root)
    app.latest_runtime_id = run.runtime_id

    async with app.run_test() as pilot:
        await pilot.press("f4")
        await pilot.pause()
        screen = pilot.app.screen
        assert isinstance(screen, RuntimeApprovalsScreen)
        screen.query_one("#approval-message", Input).value = "请调整冲突"

        await screen.chat_update_artifact()

        assert screen.query_one("#approval-artifact-version", Input).value == "2"
        assert screen.query_one("#approval-message", Input).value == ""
        status = screen.query_one("#approval-command-status", Static).render()
        assert "outline@2" in str(status)


def test_tui_snapshot_reports_stale_progress(tmp_path) -> None:
    snapshot = RunSnapshot(
        runtime_id="runtime",
        log_root=tmp_path / "logs",
        latest_progress=PipelineProgress(
            message="请求模型",
            llm_task_type="drafting",
        ),
        pipeline_running=True,
        last_progress_age_seconds=65,
        events=[
            {
                "timestamp": "2026-06-23T00:00:00+00:00",
                "event_type": "llm_request",
                "payload": {"task_type": "drafting"},
            }
        ],
    )

    assert snapshot.stale_hint is not None
    assert "65 秒" in snapshot.stale_hint
    assert "drafting" in snapshot.stale_hint


def test_tui_snapshot_uses_persisted_run_summary_error(tmp_path) -> None:
    snapshot = RunSnapshot(
        runtime_id="runtime",
        log_root=tmp_path / "logs",
        status="failed",
        run_summary={"status": "failed", "error_summary": "模型返回了坏 JSON"},
    )

    assert snapshot.failure_error == "模型返回了坏 JSON"
    assert snapshot.latest_message == "运行失败: 模型返回了坏 JSON"
    assert "运行失败" in _format_next_action(snapshot)


def test_tui_approval_workspace_shows_state_error(tmp_path) -> None:
    text = _format_approval_workspace(
        RunSnapshot(
            runtime_id="runtime",
            log_root=tmp_path / "logs",
            status="读取失败",
            state_error="state.sqlite missing",
        )
    )

    assert "状态库暂不可读" in text
    assert "state.sqlite missing" in text


async def test_tui_ctrl_r_starts_pipeline_when_input_dir_is_set(monkeypatch, tmp_path) -> None:
    captured: dict[str, object] = {}

    class FakePipeline:
        def __init__(self, llm: object, progress_callback: object = None) -> None:
            captured["llm"] = llm
            captured["progress_callback"] = progress_callback

        async def run(self, options: object) -> object:
            captured["options"] = options
            return PipelineSummary(
                runtime_id="runtime",
                log_dir=tmp_path / "logs" / "runtime",
                generated_chapters=[3],
                output_files=[tmp_path / "logs" / "runtime" / "outputs" / "chapters" / "3.txt"],
                stats=RunStats(total_calls=1),
            )

    class FakeLLM:
        def __init__(self, config: object, api_keys: object) -> None:
            captured["config"] = config
            captured["api_keys"] = api_keys

    config = tmp_path / "config.toml"
    config.write_text(
        """
[models.default]
api = "responses"
model = "fake-model"
api_key = "sk-from-config"
api_key_env = "MISSING_FAKE_KEY"
""",
        encoding="utf-8",
    )
    novel = tmp_path / "novel"
    novel.mkdir()

    monkeypatch.setattr("inklink.tui.app.InklinkPipeline", FakePipeline)
    monkeypatch.setattr("inklink.tui.app.OpenAIToolLLM", FakeLLM)

    app = InklinkApp(input_dir=novel, config=config)

    async with app.run_test() as pilot:
        await pilot.press("ctrl+r")
        await pilot.pause()

        assert isinstance(pilot.app.screen, DashboardScreen)
        status = pilot.app.screen.query_one("#dashboard-status", Static).render()
        latest_runtime_id = pilot.app.latest_runtime_id

    assert "运行结束: completed" in str(status)
    assert captured["options"].input_dir == novel
    assert captured["api_keys"] == {"default": "sk-from-config"}
    assert latest_runtime_id == "runtime"


async def test_tui_start_run_shows_immediate_and_live_progress(monkeypatch, tmp_path) -> None:
    captured: dict[str, object] = {}
    started = asyncio.Event()
    release = asyncio.Event()

    class FakePipeline:
        def __init__(self, llm: object, progress_callback: object = None) -> None:
            captured["llm"] = llm
            captured["progress_callback"] = progress_callback

        async def run(self, options: object) -> object:
            captured["options"] = options
            progress_callback = captured["progress_callback"]
            if callable(progress_callback):
                progress_callback(
                    PipelineProgress(
                        message="测试阶段",
                        runtime_id="runtime-running",
                        node_id="test-node",
                    )
                )
            started.set()
            await release.wait()
            return PipelineSummary(
                runtime_id="runtime-running",
                log_dir=tmp_path / "logs" / "runtime-running",
                generated_chapters=[],
                output_files=[],
                stats=RunStats(total_calls=0),
            )

    class FakeLLM:
        def __init__(self, config: object, api_keys: object) -> None:
            captured["config"] = config
            captured["api_keys"] = api_keys

    config = tmp_path / "config.toml"
    config.write_text(
        """
[models.default]
api = "responses"
model = "fake-model"
api_key_env = "MISSING_FAKE_KEY"
""",
        encoding="utf-8",
    )
    novel = tmp_path / "novel"
    novel.mkdir()

    monkeypatch.setattr("inklink.tui.app.InklinkPipeline", FakePipeline)
    monkeypatch.setattr("inklink.tui.app.OpenAIToolLLM", FakeLLM)

    app = InklinkApp(input_dir=novel, config=config, log_root=tmp_path / "logs")

    async with app.run_test() as pilot:
        await pilot.press("ctrl+r")
        await pilot.pause()

        assert isinstance(pilot.app.screen, DashboardScreen)
        immediate_summary = pilot.app.screen.query_one("#dashboard-status", Static).render()

        assert "未开始" not in str(immediate_summary)
        assert (
            "运行中" in str(immediate_summary)
            or "后台任务已提交" in str(immediate_summary)
            or "测试阶段" in str(immediate_summary)
        )

        await asyncio.wait_for(started.wait(), timeout=1)
        await pilot.pause()

        live_status = pilot.app.screen.query_one("#dashboard-status", Static).render()
        live_summary = pilot.app.screen.query_one("#dashboard-nodes", Static).render()

        assert "测试阶段" in str(live_status)
        assert "test-node" in str(live_status) or "test-node" in str(live_summary)

        release.set()
        await pilot.pause()

        final_status = pilot.app.screen.query_one("#dashboard-status", Static).render()

    assert "运行结束: completed" in str(final_status)


async def test_tui_ctrl_r_uses_generation_controls(monkeypatch, tmp_path) -> None:
    captured: dict[str, object] = {}

    class FakePipeline:
        def __init__(self, llm: object, progress_callback: object = None) -> None:
            captured["llm"] = llm
            captured["progress_callback"] = progress_callback

        async def run(self, options: object) -> object:
            captured["options"] = options
            return PipelineSummary(
                runtime_id="runtime-from-controls",
                log_dir=tmp_path / "custom-logs" / "runtime-from-controls",
                generated_chapters=[8, 9],
                output_files=[],
                stats=RunStats(total_calls=2),
                status="waiting_approval",
                waiting_approval_id="outline",
            )

    class FakeLLM:
        def __init__(self, config: object, api_keys: object) -> None:
            captured["config"] = config
            captured["api_keys"] = api_keys

    config = tmp_path / "config.toml"
    config.write_text(
        """
[models.default]
api = "responses"
model = "fake-model"
api_key_env = "MISSING_FAKE_KEY"
""",
        encoding="utf-8",
    )
    novel = tmp_path / "novel"
    novel.mkdir()
    log_root = tmp_path / "custom-logs"

    monkeypatch.setattr("inklink.tui.app.InklinkPipeline", FakePipeline)
    monkeypatch.setattr("inklink.tui.app.OpenAIToolLLM", FakeLLM)

    app = InklinkApp()

    async with app.run_test() as pilot:
        pilot.app.screen.query_one("#tui-input-dir", Input).value = str(novel)
        pilot.app.screen.query_one("#tui-config-path", Input).value = str(config)
        pilot.app.screen.query_one("#tui-log-root", Input).value = str(log_root)
        pilot.app.screen.query_one("#tui-chapter-count", Input).value = "2"
        pilot.app.screen.query_one("#tui-start-chapter", Input).value = "8"
        pilot.app.screen.query_one("#tui-min-chars", Input).value = "1200"
        pilot.app.screen.query_one("#tui-max-chars", Input).value = "2400"
        pilot.app.screen.query_one("#tui-max-revision-rounds", Input).value = "3"
        pilot.app.screen.query_one("#tui-output-mode", Input).value = "writeback"
        pilot.app.screen.query_one("#tui-auto-approve", Input).value = "是"

        await pilot.press("ctrl+r")
        await pilot.pause()

        assert isinstance(pilot.app.screen, RuntimeApprovalsScreen)
        status = pilot.app.screen.query_one("#approvals-workspace", Static).render()
        approval_id = pilot.app.screen.query_one("#approval-id", Input).value
        latest_runtime_id = pilot.app.latest_runtime_id

    options = captured["options"]
    assert "runtime-from-controls" in str(status)
    assert approval_id == "outline"
    assert options.input_dir == novel
    assert options.config_path == config
    assert options.log_root == log_root
    assert options.runtime_id is None
    assert options.chapter_count == 2
    assert options.start_chapter == 8
    assert options.min_chars == 1200
    assert options.max_chars == 2400
    assert options.max_revision_rounds == 3
    assert options.output_mode == "writeback"
    assert options.auto_approve is True
    assert latest_runtime_id == "runtime-from-controls"
