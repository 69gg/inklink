import asyncio
import json
import os
from pathlib import Path
from typing import Annotated

import typer

from inklink import __version__
from inklink.config import load_config
from inklink.tui.app import InklinkApp
from inklink.workflow.pipeline import (
    GenerationOptions,
    InklinkPipeline,
    OpenAIToolLLM,
    PipelineSummary,
    UsageBucket,
)
from inklink.workflow.service import UsageStatRow, WorkflowService

app = typer.Typer(help="墨连 Inklink: AI-driven Chinese novel continuation TUI.")
workflow_app = typer.Typer(help="Operate an existing Inklink workflow runtime.")
app.add_typer(workflow_app, name="workflow")


@app.command()
def version() -> None:
    """Print the Inklink version."""
    typer.echo(f"inklink {__version__}")


@app.command()
def run(
    input_dir: Annotated[Path | None, typer.Argument(help="Chapter directory.")] = None,
    config: Annotated[Path, typer.Option(help="Path to config.toml.")] = Path("config.toml"),
    execute: Annotated[
        bool,
        typer.Option(help="Run the full continuation workflow instead of opening the TUI shell."),
    ] = False,
    chapter_count: Annotated[int, typer.Option(help="Number of chapters to generate.")] = 1,
    start_chapter: Annotated[
        int | None,
        typer.Option(help="First generated chapter number. Defaults to max input chapter + 1."),
    ] = None,
    min_chars: Annotated[int, typer.Option(help="Minimum Chinese characters per chapter.")] = 800,
    max_chars: Annotated[int, typer.Option(help="Maximum Chinese characters per chapter.")] = 1800,
    max_revision_rounds: Annotated[
        int | None,
        typer.Option(help="Override maximum automatic revision rounds."),
    ] = None,
    output_mode: Annotated[
        str | None,
        typer.Option(help="Override output mode: output or writeback."),
    ] = None,
    resume_runtime_id: Annotated[
        str | None,
        typer.Option(help="Resume an existing logs/<runtime_id> workflow run."),
    ] = None,
    log_root: Annotated[Path, typer.Option(help="Runtime log root.")] = Path("logs"),
    auto_approve: Annotated[
        bool,
        typer.Option(help="Automatically accept workflow approval gates for this run."),
    ] = False,
    notes: Annotated[
        str,
        typer.Option(help="Extra user notes/constraints for this continuation run."),
    ] = "",
    notes_file: Annotated[
        Path | None,
        typer.Option(help="Path to a UTF-8 notes file for this continuation run."),
    ] = None,
) -> None:
    """Launch the Inklink TUI."""
    if execute:
        if input_dir is None and resume_runtime_id is None:
            raise typer.BadParameter("input_dir is required when --execute is used")
        summary = asyncio.run(
            _run_pipeline(
                input_dir=input_dir,
                config=config,
                log_root=log_root,
                output_mode=output_mode,
                runtime_id=resume_runtime_id,
                chapter_count=chapter_count,
                start_chapter=start_chapter,
                min_chars=min_chars,
                max_chars=max_chars,
                max_revision_rounds=max_revision_rounds,
                auto_approve=auto_approve,
                notes=notes,
                notes_path=notes_file,
            )
        )
        typer.echo(f"runtime_id: {summary.runtime_id}")
        typer.echo(f"log_dir: {summary.log_dir}")
        generated_chapters = ", ".join(str(item) for item in summary.generated_chapters)
        typer.echo(f"generated_chapters: {generated_chapters}")
        for output_file in summary.output_files:
            typer.echo(f"output: {output_file}")
        typer.echo(f"llm_calls: {summary.stats.total_calls}")
        _print_usage_summary(summary)
        return
    InklinkApp(input_dir=input_dir, config=config).run()


async def _run_pipeline(
    *,
    input_dir: Path | None,
    config: Path,
    log_root: Path,
    output_mode: str | None,
    runtime_id: str | None,
    chapter_count: int,
    start_chapter: int | None,
    min_chars: int,
    max_chars: int,
    max_revision_rounds: int | None,
    auto_approve: bool,
    notes: str,
    notes_path: Path | None,
) -> PipelineSummary:
    config_path = _config_path_for_pipeline(config=config, log_root=log_root, runtime_id=runtime_id)
    app_config = load_config(config_path)
    api_keys = {
        name: os.environ.get(profile.api_key_env) for name, profile in app_config.models.items()
    }
    llm = OpenAIToolLLM(app_config, api_keys)
    return await InklinkPipeline(llm=llm).run(
        GenerationOptions(
            input_dir=input_dir,
            config_path=config_path,
            log_root=log_root,
            output_mode=output_mode,
            runtime_id=runtime_id,
            chapter_count=chapter_count,
            start_chapter=start_chapter,
            min_chars=min_chars,
            max_chars=max_chars,
            max_revision_rounds=max_revision_rounds,
            auto_approve=auto_approve,
            notes=notes,
            notes_path=notes_path,
        )
    )


def _config_path_for_pipeline(*, config: Path, log_root: Path, runtime_id: str | None) -> Path:
    if runtime_id is None:
        return config
    settings_path = log_root / runtime_id / "artifacts" / "run_settings.json"
    if not settings_path.is_file():
        return config
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    if not isinstance(settings, dict):
        return config
    value = settings.get("config_path")
    return Path(value) if isinstance(value, str) and value else config


def _print_usage_summary(summary: PipelineSummary) -> None:
    typer.echo("usage_total:")
    typer.echo(f"  {_format_usage_bucket('total', summary.stats.total)}")
    if summary.stats.by_profile:
        typer.echo("usage_by_profile:")
        for profile, bucket in sorted(summary.stats.by_profile.items()):
            typer.echo(f"  {_format_usage_bucket(profile, bucket)}")
    if summary.stats.by_model:
        typer.echo("usage_by_model:")
        for model, bucket in sorted(summary.stats.by_model.items()):
            typer.echo(f"  {_format_usage_bucket(model, bucket)}")
    if summary.stats.by_task:
        typer.echo("usage_by_task:")
        for task, bucket in sorted(summary.stats.by_task.items()):
            typer.echo(f"  {_format_usage_bucket(task, bucket)}")


def _format_usage_bucket(label: str, bucket: UsageBucket) -> str:
    parts = [
        f"{label}: calls={bucket.calls}",
        f"input={bucket.input_tokens}",
        f"output={bucket.output_tokens}",
        f"total={bucket.total_tokens}",
    ]
    _append_optional_usage(parts, "cached", bucket.cached_tokens)
    _append_optional_usage(parts, "cache_read", bucket.cache_read_tokens)
    _append_optional_usage(parts, "cache_write", bucket.cache_write_tokens)
    _append_optional_usage(parts, "reasoning", bucket.reasoning_tokens)
    return " ".join(parts)


def _format_usage_row(row: UsageStatRow) -> str:
    parts = [
        f"{row.profile}/{row.model}/{row.task_type}: calls={row.calls}",
        f"input={row.input_tokens}",
        f"output={row.output_tokens}",
        f"total={row.total_tokens}",
    ]
    _append_optional_usage(parts, "cached", row.cached_tokens)
    _append_optional_usage(parts, "cache_read", row.cache_read_tokens)
    _append_optional_usage(parts, "cache_write", row.cache_write_tokens)
    _append_optional_usage(parts, "reasoning", row.reasoning_tokens)
    return " ".join(parts)


def _append_optional_usage(parts: list[str], label: str, value: int | None) -> None:
    if value is not None:
        parts.append(f"{label}={value}")


@workflow_app.command("info")
def workflow_info(
    runtime_id: Annotated[str, typer.Argument(help="Runtime ID under log root.")],
    log_root: Annotated[Path, typer.Option(help="Runtime log root.")] = Path("logs"),
) -> None:
    """Show basic runtime state without mutating the run."""
    with WorkflowService(log_root=log_root) as service:
        run_state = service.inspect_run(runtime_id)
        typer.echo(f"runtime_id: {run_state.runtime_id}")
        typer.echo(f"input_dir: {run_state.input_dir}")
        typer.echo(f"log_dir: {run_state.log_dir}")
        typer.echo(f"chapter_count: {run_state.chapter_count}")


@workflow_app.command("stats")
def workflow_stats(
    runtime_id: Annotated[str, typer.Argument(help="Runtime ID under log root.")],
    log_root: Annotated[Path, typer.Option(help="Runtime log root.")] = Path("logs"),
) -> None:
    """Show persisted usage stats for an existing runtime."""
    with WorkflowService(log_root=log_root) as service:
        service.inspect_run(runtime_id)
        rows = service.usage_stats()
        if not rows:
            typer.echo("no usage rows")
            return
        for row in rows:
            typer.echo(_format_usage_row(row))


@workflow_app.command("nodes")
def workflow_nodes(
    runtime_id: Annotated[str, typer.Argument(help="Runtime ID under log root.")],
    log_root: Annotated[Path, typer.Option(help="Runtime log root.")] = Path("logs"),
) -> None:
    """List workflow nodes for an existing runtime."""
    with WorkflowService(log_root=log_root) as service:
        service.inspect_run(runtime_id)
        _echo_json(service.list_nodes())


@workflow_app.command("artifacts")
def workflow_artifacts(
    runtime_id: Annotated[str, typer.Argument(help="Runtime ID under log root.")],
    log_root: Annotated[Path, typer.Option(help="Runtime log root.")] = Path("logs"),
) -> None:
    """List artifact versions for an existing runtime."""
    with WorkflowService(log_root=log_root) as service:
        service.inspect_run(runtime_id)
        _echo_json(service.list_artifacts())


@workflow_app.command("artifact")
def workflow_artifact(
    runtime_id: Annotated[str, typer.Argument(help="Runtime ID under log root.")],
    artifact_id: Annotated[str, typer.Argument(help="Artifact ID.")],
    version: Annotated[int | None, typer.Option(help="Artifact version.")] = None,
    log_root: Annotated[Path, typer.Option(help="Runtime log root.")] = Path("logs"),
) -> None:
    """Show one artifact payload."""
    with WorkflowService(log_root=log_root) as service:
        service.inspect_run(runtime_id)
        _echo_json(service.get_artifact(artifact_id, version))


@workflow_app.command("approvals")
def workflow_approvals(
    runtime_id: Annotated[str, typer.Argument(help="Runtime ID under log root.")],
    log_root: Annotated[Path, typer.Option(help="Runtime log root.")] = Path("logs"),
) -> None:
    """List approvals for an existing runtime."""
    with WorkflowService(log_root=log_root) as service:
        service.inspect_run(runtime_id)
        _echo_json(service.list_approvals())


@workflow_app.command("messages")
def workflow_messages(
    runtime_id: Annotated[str, typer.Argument(help="Runtime ID under log root.")],
    approval_id: Annotated[str | None, typer.Option(help="Filter by approval ID.")] = None,
    log_root: Annotated[Path, typer.Option(help="Runtime log root.")] = Path("logs"),
) -> None:
    """List approval messages for an existing runtime."""
    with WorkflowService(log_root=log_root) as service:
        service.inspect_run(runtime_id)
        _echo_json(service.list_messages(approval_id))


@workflow_app.command("events")
def workflow_events(
    runtime_id: Annotated[str, typer.Argument(help="Runtime ID under log root.")],
    limit: Annotated[int, typer.Option(help="Number of recent events.")] = 20,
    log_root: Annotated[Path, typer.Option(help="Runtime log root.")] = Path("logs"),
) -> None:
    """Show recent JSONL audit events."""
    with WorkflowService(log_root=log_root) as service:
        service.inspect_run(runtime_id)
        _echo_json(service.recent_events(limit=limit))


@workflow_app.command("message")
def workflow_message(
    runtime_id: Annotated[str, typer.Argument(help="Runtime ID under log root.")],
    approval_id: Annotated[str, typer.Argument(help="Approval ID, for example outline.")],
    content: Annotated[str, typer.Argument(help="Message content.")],
    role: Annotated[str, typer.Option(help="Message role.")] = "user",
    log_root: Annotated[Path, typer.Option(help="Runtime log root.")] = Path("logs"),
) -> None:
    """Record an approval chat message."""
    with WorkflowService(log_root=log_root) as service:
        service.resume_run(runtime_id)
        result = service.record_approval_message(
            approval_id=approval_id,
            role=role,
            content=content,
        )
        typer.echo(result.message)


@workflow_app.command("chat-update")
def workflow_chat_update(
    runtime_id: Annotated[str, typer.Argument(help="Runtime ID under log root.")],
    approval_id: Annotated[str, typer.Argument(help="Approval ID.")],
    artifact_id: Annotated[str, typer.Argument(help="Artifact ID.")],
    artifact_type: Annotated[
        str,
        typer.Argument(help="Artifact type: outline, chapter_plan, or scene_plan."),
    ],
    content: Annotated[str, typer.Argument(help="User instruction for the artifact update.")],
    config: Annotated[Path, typer.Option(help="Path to config.toml.")] = Path("config.toml"),
    log_root: Annotated[Path, typer.Option(help="Runtime log root.")] = Path("logs"),
) -> None:
    """Ask the configured LLM to update an approval artifact through update_* tools."""
    version = asyncio.run(
        _chat_update_artifact(
            runtime_id=runtime_id,
            approval_id=approval_id,
            artifact_id=artifact_id,
            artifact_type=artifact_type,
            content=content,
            config=config,
            log_root=log_root,
        )
    )
    typer.echo(f"updated {artifact_id}@{version}")


@workflow_app.command("approve")
def workflow_approve(
    runtime_id: Annotated[str, typer.Argument(help="Runtime ID under log root.")],
    approval_id: Annotated[str, typer.Argument(help="Approval ID.")],
    artifact_id: Annotated[str, typer.Argument(help="Artifact ID.")],
    artifact_version: Annotated[int, typer.Argument(help="Artifact version.")],
    approval_type: Annotated[str | None, typer.Option(help="Approval type.")] = None,
    log_root: Annotated[Path, typer.Option(help="Runtime log root.")] = Path("logs"),
) -> None:
    """Approve an artifact version."""
    with WorkflowService(log_root=log_root) as service:
        service.resume_run(runtime_id)
        result = service.approve_artifact(
            approval_id=approval_id,
            approval_type=approval_type or approval_id,
            artifact_id=artifact_id,
            artifact_version=artifact_version,
        )
        typer.echo(result.message)


@workflow_app.command("retry")
def workflow_retry(
    runtime_id: Annotated[str, typer.Argument(help="Runtime ID under log root.")],
    node_id: Annotated[str, typer.Argument(help="Workflow node ID.")],
    log_root: Annotated[Path, typer.Option(help="Runtime log root.")] = Path("logs"),
) -> None:
    """Record a manual retry request for a node."""
    with WorkflowService(log_root=log_root) as service:
        service.resume_run(runtime_id)
        typer.echo(service.retry_node(node_id).message)


@workflow_app.command("abandon")
def workflow_abandon(
    runtime_id: Annotated[str, typer.Argument(help="Runtime ID under log root.")],
    chapter_number: Annotated[int, typer.Argument(help="Chapter number.")],
    log_root: Annotated[Path, typer.Option(help="Runtime log root.")] = Path("logs"),
) -> None:
    """Abandon a chapter generation and increment its generation."""
    with WorkflowService(log_root=log_root) as service:
        service.resume_run(runtime_id)
        typer.echo(service.abandon_chapter(chapter_number).message)


@workflow_app.command("rewrite")
def workflow_rewrite(
    runtime_id: Annotated[str, typer.Argument(help="Runtime ID under log root.")],
    chapter_number: Annotated[int, typer.Argument(help="Chapter number.")],
    log_root: Annotated[Path, typer.Option(help="Runtime log root.")] = Path("logs"),
) -> None:
    """Request creative rewrite for a chapter and increment its generation."""
    with WorkflowService(log_root=log_root) as service:
        service.resume_run(runtime_id)
        typer.echo(service.rewrite_chapter(chapter_number).message)


async def _chat_update_artifact(
    *,
    runtime_id: str,
    approval_id: str,
    artifact_id: str,
    artifact_type: str,
    content: str,
    config: Path,
    log_root: Path,
) -> int:
    app_config = load_config(config)
    api_keys = {
        name: os.environ.get(profile.api_key_env) for name, profile in app_config.models.items()
    }
    llm = OpenAIToolLLM(app_config, api_keys)
    return await InklinkPipeline(llm=llm).update_artifact_with_chat(
        runtime_id=runtime_id,
        log_root=log_root,
        config_path=config,
        approval_id=approval_id,
        artifact_id=artifact_id,
        artifact_type=artifact_type,
        user_message=content,
    )


def _echo_json(payload: object) -> None:
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
