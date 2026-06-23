import asyncio

from textual.widgets import Button, Input, Static, TextArea

from inklink.tui.app import InklinkApp
from inklink.tui.screens import (
    DashboardScreen,
    RuntimeApprovalsScreen,
    RuntimeArtifactsScreen,
    RuntimeLogScreen,
    SetupWorkspace,
    StatsScreen,
    _format_node_tree,
)
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

        status = pilot.app.screen.query_one("#setup-workspace", Static).render()
        latest_runtime_id = pilot.app.latest_runtime_id

    assert "运行完成" in str(status)
    assert captured["options"].input_dir == novel
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

        immediate_summary = pilot.app.screen.query_one("#setup-run-summary", Static).render()
        run_button = pilot.app.screen.query_one("#run-pipeline", Button)

        assert "未开始" not in str(immediate_summary)
        assert "运行中" in str(immediate_summary) or "后台任务已提交" in str(immediate_summary)
        assert run_button.disabled is True

        await asyncio.wait_for(started.wait(), timeout=1)
        await pilot.pause()

        live_status = pilot.app.screen.query_one("#setup-workspace", Static).render()
        live_summary = pilot.app.screen.query_one("#setup-run-summary", Static).render()

        assert "测试阶段" in str(live_status)
        assert "test-node" in str(live_summary)

        release.set()
        await pilot.pause()

        final_status = pilot.app.screen.query_one("#setup-workspace", Static).render()

    assert "运行完成" in str(final_status)


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

        status = pilot.app.screen.query_one("#setup-workspace", Static).render()
        latest_runtime_id = pilot.app.latest_runtime_id

    options = captured["options"]
    assert "runtime-from-controls" in str(status)
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
