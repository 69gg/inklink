from textual.widgets import Static

from inklink.tui.app import InklinkApp
from inklink.tui.screens import DashboardScreen


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
