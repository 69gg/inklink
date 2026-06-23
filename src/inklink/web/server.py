from __future__ import annotations

import subprocess
from pathlib import Path

import uvicorn

from .app import create_app


def serve_web(
    *,
    host: str,
    port: int,
    log_root: Path,
    input_dir: Path | None = None,
    config: Path = Path("config.toml"),
    static_dir: Path | None = None,
    build_frontend: bool = True,
) -> None:
    resolved_static_dir = static_dir or _default_static_dir()
    if build_frontend:
        ensure_frontend_build(resolved_static_dir)
    app = create_app(
        log_root=log_root,
        static_dir=resolved_static_dir,
        default_options={
            "input_dir": str(input_dir) if input_dir is not None else "",
            "config_path": str(config),
            "log_root": str(log_root),
        },
    )
    uvicorn.run(app, host=host, port=port)


def ensure_frontend_build(static_dir: Path) -> None:
    index_html = static_dir / "index.html"
    if index_html.is_file():
        return
    web_root = _web_root()
    package_json = web_root / "package.json"
    if not package_json.is_file():
        raise RuntimeError(f"frontend package is missing: {package_json}")
    node_modules = web_root / "node_modules"
    if not node_modules.is_dir():
        subprocess.run(["npm", "install"], cwd=web_root, check=True)
    subprocess.run(["npm", "run", "build"], cwd=web_root, check=True)
    if not index_html.is_file():
        raise RuntimeError(f"frontend build did not create {index_html}")


def _default_static_dir() -> Path:
    return _web_root() / "dist"


def _web_root() -> Path:
    return Path(__file__).resolve().parents[3] / "web"
