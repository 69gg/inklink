from pytest import MonkeyPatch
from typer.testing import CliRunner

from inklink.cli import app
from inklink.tui.app import InklinkApp

runner = CliRunner()


def test_cli_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "inklink" in result.output


def test_cli_has_run_command() -> None:
    result = runner.invoke(app, ["run", "--help"])
    assert result.exit_code == 0
    assert "--config" in result.output


def test_cli_run_launches_tui(monkeypatch: MonkeyPatch) -> None:
    run_calls = 0

    def fake_run(self: InklinkApp) -> None:
        nonlocal run_calls
        run_calls += 1

    monkeypatch.setattr(InklinkApp, "run", fake_run)

    result = runner.invoke(app, ["run", "chapters", "--config", "config.toml"])

    assert result.exit_code == 0
    assert run_calls == 1
