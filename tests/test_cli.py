import json
from pathlib import Path

from pytest import MonkeyPatch
from typer.testing import CliRunner

from inklink.cli import app
from inklink.llm.types import NormalizedUsage
from inklink.workflow.pipeline import GenerationOptions, PipelineSummary, RunStats, ToolCallResult
from inklink.workflow.service import WorkflowService

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
    assert "--to-ending" in result.output


def test_cli_run_launches_webui(monkeypatch: MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_serve_web(
        *,
        host: str,
        port: int,
        log_root: Path,
        input_dir: Path | None,
        config: Path,
    ) -> None:
        captured.update(
            {
                "host": host,
                "port": port,
                "log_root": log_root,
                "input_dir": input_dir,
                "config": config,
            }
        )

    monkeypatch.setattr("inklink.cli.serve_web", fake_serve_web)

    result = runner.invoke(
        app,
        [
            "run",
            "chapters",
            "--config",
            "config.toml",
            "--log-root",
            "logs",
            "--host",
            "0.0.0.0",
            "--port",
            "9000",
        ],
    )

    assert result.exit_code == 0
    assert captured == {
        "host": "0.0.0.0",
        "port": 9000,
        "log_root": Path("logs"),
        "input_dir": Path("chapters"),
        "config": Path("config.toml"),
    }


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
                        "by_profile": {
                            "default": {
                                "calls": 2,
                                "input_tokens": 10,
                                "output_tokens": 6,
                                "total_tokens": 16,
                            }
                        },
                        "by_model": {
                            "fake-model": {
                                "calls": 2,
                                "input_tokens": 10,
                                "output_tokens": 6,
                                "total_tokens": 16,
                            }
                        },
                        "by_task": {
                            "drafting": {
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
api_key = "sk-from-config"
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
            "--max-revision-rounds",
            "4",
            "--to-ending",
            "--ending-min-chapters",
            "3",
            "--ending-max-chapters",
            "6",
            "--auto-approve",
            "--notes",
            "保留悬念",
        ],
    )

    assert result.exit_code == 0
    assert "runtime" in result.output
    options = captured["options"]
    assert isinstance(options, GenerationOptions)
    assert options.input_dir == novel
    assert options.chapter_count == 1
    assert options.continuation_mode == "to_ending"
    assert options.ending_min_chapters == 3
    assert options.ending_max_chapters == 6
    assert options.auto_approve is True
    assert options.runtime_id is None
    assert options.max_revision_rounds == 4
    assert options.notes == "保留悬念"
    assert captured["api_keys"] == {"default": "sk-from-config"}
    assert "usage_total" in result.output
    assert "usage_by_profile" in result.output
    assert "usage_by_model" in result.output
    assert "usage_by_task" in result.output
    assert "fake-model" in result.output
    assert "cached" not in result.output
    assert "cache_read" not in result.output
    assert "cache_write" not in result.output
    assert "reasoning" not in result.output


def test_cli_resume_uses_persisted_config_path(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}

    class FakePipeline:
        def __init__(self, llm: object) -> None:
            captured["llm"] = llm

        async def run(self, options: object) -> PipelineSummary:
            captured["options"] = options
            return PipelineSummary(
                runtime_id="runtime",
                log_dir=tmp_path / "logs" / "runtime",
                generated_chapters=[],
                output_files=[],
                stats=RunStats(),
            )

    class FakeLLM:
        def __init__(self, config: object, api_keys: object) -> None:
            captured["config"] = config
            captured["api_keys"] = api_keys

    config_path = tmp_path / "saved-config.toml"
    config_path.write_text(
        """
[models.default]
api = "responses"
model = "fake-model"
api_key_env = "MISSING_FAKE_KEY"
""",
        encoding="utf-8",
    )
    settings_dir = tmp_path / "logs" / "runtime" / "artifacts"
    settings_dir.mkdir(parents=True)
    (settings_dir / "run_settings.json").write_text(
        json.dumps({"config_path": str(config_path)}),
        encoding="utf-8",
    )

    monkeypatch.setattr("inklink.cli.InklinkPipeline", FakePipeline)
    monkeypatch.setattr("inklink.cli.OpenAIToolLLM", FakeLLM)

    result = runner.invoke(
        app,
        [
            "run",
            "--execute",
            "--resume-runtime-id",
            "runtime",
            "--log-root",
            str(tmp_path / "logs"),
        ],
    )

    assert result.exit_code == 0
    options = captured["options"]
    assert isinstance(options, GenerationOptions)
    assert options.input_dir is None
    assert options.config_path == config_path
    assert options.runtime_id == "runtime"
    assert captured["config"].models["default"].model == "fake-model"


def test_cli_run_execute_prints_optional_usage_fields(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    class FakePipeline:
        def __init__(self, llm: object) -> None:
            pass

        async def run(self, options: object) -> PipelineSummary:
            stats = RunStats()
            stats.add(
                ToolCallResult(
                    payload={},
                    usage=NormalizedUsage(
                        input_tokens=10,
                        output_tokens=6,
                        total_tokens=16,
                        cached_tokens=5,
                        cache_read_tokens=3,
                        cache_write_tokens=2,
                        reasoning_tokens=4,
                    ),
                    profile_name="default",
                    model="fake-model",
                    task_type="drafting",
                )
            )
            return PipelineSummary(
                runtime_id="runtime",
                log_dir=tmp_path / "logs" / "runtime",
                generated_chapters=[],
                output_files=[],
                stats=stats,
            )

    class FakeLLM:
        def __init__(self, config: object, api_keys: object) -> None:
            pass

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
        ["run", str(novel), "--config", str(config_path), "--execute"],
    )

    assert result.exit_code == 0
    assert "usage_total" in result.output
    expected = "calls=1 input=10 output=6 total=16 cached=5 cache_read=3 cache_write=2 reasoning=4"
    assert f"total: {expected}" in result.output
    assert f"default: {expected}" in result.output
    assert f"fake-model: {expected}" in result.output
    assert f"drafting: {expected}" in result.output


def test_cli_workflow_commands_operate_existing_runtime(tmp_path: Path) -> None:
    novel = tmp_path / "novel"
    novel.mkdir()
    (novel / "1.txt").write_text("title: 第一章\n---\n正文", encoding="utf-8")
    log_root = tmp_path / "logs"
    service = WorkflowService(log_root=log_root)
    run = service.start_run(novel)
    version = service.update_artifact(
        artifact_id="outline",
        artifact_type="outline",
        payload={"outline": "初稿"},
        approval_id="outline",
    )
    service.close()

    info = runner.invoke(app, ["workflow", "info", run.runtime_id, "--log-root", str(log_root)])
    message = runner.invoke(
        app,
        [
            "workflow",
            "message",
            run.runtime_id,
            "outline",
            "请强化冲突",
            "--log-root",
            str(log_root),
        ],
    )
    approve = runner.invoke(
        app,
        [
            "workflow",
            "approve",
            run.runtime_id,
            "outline",
            "outline",
            str(version),
            "--log-root",
            str(log_root),
        ],
    )
    retry = runner.invoke(
        app,
        ["workflow", "retry", run.runtime_id, "draft-1", "--log-root", str(log_root)],
    )
    abandon = runner.invoke(
        app,
        ["workflow", "abandon", run.runtime_id, "1", "--log-root", str(log_root)],
    )
    rewrite = runner.invoke(
        app,
        ["workflow", "rewrite", run.runtime_id, "1", "--log-root", str(log_root)],
    )
    stats = runner.invoke(app, ["workflow", "stats", run.runtime_id, "--log-root", str(log_root)])
    artifacts = runner.invoke(
        app,
        ["workflow", "artifacts", run.runtime_id, "--log-root", str(log_root)],
    )
    artifact = runner.invoke(
        app,
        [
            "workflow",
            "artifact",
            run.runtime_id,
            "outline",
            "--version",
            "1",
            "--log-root",
            str(log_root),
        ],
    )
    approvals = runner.invoke(
        app,
        ["workflow", "approvals", run.runtime_id, "--log-root", str(log_root)],
    )
    messages = runner.invoke(
        app,
        [
            "workflow",
            "messages",
            run.runtime_id,
            "--approval-id",
            "outline",
            "--log-root",
            str(log_root),
        ],
    )
    nodes = runner.invoke(app, ["workflow", "nodes", run.runtime_id, "--log-root", str(log_root)])
    events = runner.invoke(
        app,
        ["workflow", "events", run.runtime_id, "--limit", "2", "--log-root", str(log_root)],
    )

    assert info.exit_code == 0
    assert str(novel.resolve()) in info.output
    assert message.exit_code == 0
    assert "recorded approval message" in message.output
    assert approve.exit_code == 0
    assert "approved outline@1" in approve.output
    assert retry.exit_code == 0
    assert "accepted retry_node" in retry.output
    assert abandon.exit_code == 0
    assert "generation=2" in abandon.output
    assert rewrite.exit_code == 0
    assert "generation=3" in rewrite.output
    assert stats.exit_code == 0
    assert "no usage rows" in stats.output
    assert artifacts.exit_code == 0
    assert '"artifact_id": "outline"' in artifacts.output
    assert artifact.exit_code == 0
    assert '"outline": "初稿"' in artifact.output
    assert approvals.exit_code == 0
    assert '"approval_id": "outline"' in approvals.output
    assert messages.exit_code == 0
    assert "请强化冲突" in messages.output
    assert nodes.exit_code == 0
    assert nodes.output.strip() == "[]"
    assert events.exit_code == 0
    assert "run_resumed" in events.output


def test_cli_workflow_stats_prints_optional_usage_fields(tmp_path: Path) -> None:
    novel = tmp_path / "novel"
    novel.mkdir()
    (novel / "1.txt").write_text("title: 第一章\n---\n正文", encoding="utf-8")
    log_root = tmp_path / "logs"
    service = WorkflowService(log_root=log_root)
    run = service.start_run(novel)
    first_call_id = service._current_run().store.create_llm_call(
        runtime_id=run.runtime_id,
        idempotency_key="key-1",
        task_type="review",
        profile="default",
        api_type="responses",
        model="fake",
        attempt=1,
        request={},
    )
    service._current_run().store.complete_llm_call(
        call_id=first_call_id,
        request_id="req-1",
        response={},
        usage=NormalizedUsage(
            input_tokens=2,
            output_tokens=3,
            total_tokens=5,
            cached_tokens=8,
            reasoning_tokens=4,
            cache_read_tokens=6,
            cache_write_tokens=1,
        ),
    )
    second_call_id = service._current_run().store.create_llm_call(
        runtime_id=run.runtime_id,
        idempotency_key="key-2",
        task_type="review",
        profile="default",
        api_type="responses",
        model="fake",
        attempt=1,
        request={},
    )
    service._current_run().store.complete_llm_call(
        call_id=second_call_id,
        request_id="req-2",
        response={},
        usage=NormalizedUsage(
            input_tokens=1,
            output_tokens=2,
            total_tokens=3,
            cached_tokens=2,
            reasoning_tokens=5,
            cache_read_tokens=7,
        ),
    )
    service.close()

    stats = runner.invoke(app, ["workflow", "stats", run.runtime_id, "--log-root", str(log_root)])

    assert stats.exit_code == 0
    assert (
        "default/fake/review: calls=2 input=3 output=5 total=8 "
        "cached=10 cache_read=13 cache_write=1 reasoning=9"
    ) in stats.output


def test_cli_workflow_chat_update_invokes_runner(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}

    async def fake_chat_update_artifact(**kwargs: object) -> int:
        captured.update(kwargs)
        return 7

    monkeypatch.setattr("inklink.cli._chat_update_artifact", fake_chat_update_artifact)
    result = runner.invoke(
        app,
        [
            "workflow",
            "chat-update",
            "runtime",
            "outline",
            "outline",
            "outline",
            "强化冲突",
            "--config",
            str(tmp_path / "config.toml"),
            "--log-root",
            str(tmp_path / "logs"),
        ],
    )

    assert result.exit_code == 0
    assert "updated outline@7" in result.output
    assert captured["runtime_id"] == "runtime"
    assert captured["artifact_type"] == "outline"
    assert captured["content"] == "强化冲突"
