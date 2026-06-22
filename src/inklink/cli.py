from pathlib import Path
from typing import Annotated

import typer

from inklink import __version__

app = typer.Typer(help="墨连 Inklink: AI-driven Chinese novel continuation TUI.")


@app.command()
def version() -> None:
    """Print the Inklink version."""
    typer.echo(f"inklink {__version__}")


@app.command()
def run(
    input_dir: Annotated[Path | None, typer.Argument(help="Chapter directory.")] = None,
    config: Annotated[Path, typer.Option(help="Path to config.toml.")] = Path("config.toml"),
) -> None:
    """Launch the Inklink TUI."""
    typer.echo(f"Starting Inklink with config={config} input_dir={input_dir}")
