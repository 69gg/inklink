from pathlib import Path

from pytest import MonkeyPatch
from typer.testing import CliRunner

from inklink.cli import app
from inklink.tui.app import InklinkApp
from inklink.workflow.pipeline import GenerationOptions, PipelineSummary, RunStats

runner = CliRunner()


def test_cli_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "inklink" in result.output


def test_cli_has_run_command() -> None:
    result = runner.invoke(app, ["run", "--help"])
    assert result.exit_code == 0
    assert "--config" in result.output
    assert "--execute" in result.output


def test_cli_run_launches_tui(monkeypatch: MonkeyPatch) -> None:
    run_calls = 0
    app_inputs: list[tuple[Path | None, Path | None]] = []

    original_init = InklinkApp.__init__

    def fake_init(
        self: InklinkApp,
        input_dir: Path | None = None,
        config: Path | None = None,
    ) -> None:
        app_inputs.append((input_dir, config))
        original_init(self, input_dir=input_dir, config=config)

    def fake_run(self: InklinkApp) -> None:
        nonlocal run_calls
        run_calls += 1

    monkeypatch.setattr(InklinkApp, "__init__", fake_init)
    monkeypatch.setattr(InklinkApp, "run", fake_run)

    result = runner.invoke(app, ["run", "chapters", "--config", "config.toml"])

    assert result.exit_code == 0
    assert run_calls == 1
    assert app_inputs == [(Path("chapters"), Path("config.toml"))]


def test_cli_run_execute_invokes_pipeline(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    class FakePipeline:
        def __init__(self, llm: object) -> None:
            captured["llm"] = llm

        async def run(self, options: object) -> PipelineSummary:
            captured["options"] = options
            return PipelineSummary(
                runtime_id="runtime",
                log_dir=tmp_path / "logs" / "runtime",
                generated_chapters=[3],
                output_files=[tmp_path / "logs" / "runtime" / "outputs" / "chapters" / "3.txt"],
                stats=RunStats.model_validate(
                    {
                        "total_calls": 2,
                        "by_model": {
                            "fake-model": {
                                "calls": 2,
                                "input_tokens": 10,
                                "output_tokens": 6,
                                "total_tokens": 16,
                            }
                        },
                    }
                ),
            )

    class FakeLLM:
        def __init__(self, config: object, api_keys: object) -> None:
            captured["config"] = config
            captured["api_keys"] = api_keys

    config_path = tmp_path / "config.toml"
    config_path.write_text(
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

    monkeypatch.setattr("inklink.cli.InklinkPipeline", FakePipeline)
    monkeypatch.setattr("inklink.cli.OpenAIToolLLM", FakeLLM)

    result = runner.invoke(
        app,
        [
            "run",
            str(novel),
            "--config",
            str(config_path),
            "--execute",
            "--chapter-count",
            "1",
            "--min-chars",
            "8",
            "--max-chars",
            "80",
            "--auto-approve",
            "--resume-runtime-id",
            "runtime",
        ],
    )

    assert result.exit_code == 0
    assert "runtime" in result.output
    options = captured["options"]
    assert isinstance(options, GenerationOptions)
    assert options.input_dir == novel
    assert options.chapter_count == 1
    assert options.auto_approve is True
    assert options.runtime_id == "runtime"
    assert "usage_by_model" in result.output
    assert "fake-model" in result.output
