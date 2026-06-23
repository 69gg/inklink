from textual.widgets import Static

from inklink.tui.app import InklinkApp
from inklink.tui.screens import DashboardScreen
from inklink.workflow.pipeline import PipelineSummary, RunStats


async def test_tui_app_starts_with_expected_title() -> None:
    app = InklinkApp()

    async with app.run_test() as pilot:
        assert pilot.app.title == "墨连 Inklink"


async def test_tui_initial_interface_contains_workspace_text() -> None:
    app = InklinkApp()

    async with app.run_test() as pilot:
        body_text = pilot.app.screen.query_one("#setup-workspace", Static).render()

    assert "输入目录" in str(body_text)


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


async def test_tui_ctrl_r_starts_pipeline_when_input_dir_is_set(monkeypatch, tmp_path) -> None:
    captured: dict[str, object] = {}

    class FakePipeline:
        def __init__(self, llm: object) -> None:
            captured["llm"] = llm

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

    assert "运行完成" in str(status)
    assert captured["options"].input_dir == novel
