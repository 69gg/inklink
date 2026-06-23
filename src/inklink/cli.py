import asyncio
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
)

app = typer.Typer(help="墨连 Inklink: AI-driven Chinese novel continuation TUI.")


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
    output_mode: Annotated[
        str | None,
        typer.Option(help="Override output mode: output or writeback."),
    ] = None,
    log_root: Annotated[Path, typer.Option(help="Runtime log root.")] = Path("logs"),
    auto_approve: Annotated[
        bool,
        typer.Option(help="Automatically accept workflow approval gates for this run."),
    ] = False,
) -> None:
    """Launch the Inklink TUI."""
    if execute:
        if input_dir is None:
            raise typer.BadParameter("input_dir is required when --execute is used")
        summary = asyncio.run(
            _run_pipeline(
                input_dir=input_dir,
                config=config,
                log_root=log_root,
                output_mode=output_mode,
                chapter_count=chapter_count,
                start_chapter=start_chapter,
                min_chars=min_chars,
                max_chars=max_chars,
                auto_approve=auto_approve,
            )
        )
        typer.echo(f"runtime_id: {summary.runtime_id}")
        typer.echo(f"log_dir: {summary.log_dir}")
        generated_chapters = ", ".join(str(item) for item in summary.generated_chapters)
        typer.echo(f"generated_chapters: {generated_chapters}")
        for output_file in summary.output_files:
            typer.echo(f"output: {output_file}")
        typer.echo(f"llm_calls: {summary.stats.total_calls}")
        return
    InklinkApp(input_dir=input_dir, config=config).run()


async def _run_pipeline(
    *,
    input_dir: Path,
    config: Path,
    log_root: Path,
    output_mode: str | None,
    chapter_count: int,
    start_chapter: int | None,
    min_chars: int,
    max_chars: int,
    auto_approve: bool,
) -> PipelineSummary:
    app_config = load_config(config)
    api_keys = {
        name: os.environ.get(profile.api_key_env) for name, profile in app_config.models.items()
    }
    llm = OpenAIToolLLM(app_config, api_keys)
    return await InklinkPipeline(llm=llm).run(
        GenerationOptions(
            input_dir=input_dir,
            config_path=config,
            log_root=log_root,
            output_mode=output_mode,
            chapter_count=chapter_count,
            start_chapter=start_chapter,
            min_chars=min_chars,
            max_chars=max_chars,
            auto_approve=auto_approve,
        )
    )
