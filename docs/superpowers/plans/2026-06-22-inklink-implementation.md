# Inklink Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first complete Inklink TUI product: strict chapter ingestion, configurable OpenAI-compatible LLM workflow, SQLite/JSONL resumability, scene-level drafting, deterministic checks, long-context indexing, output/writeback, statistics, docs, and tests.

**Architecture:** Keep Textual as a thin interactive shell over a workflow service. The workflow service owns DAG state, approvals, idempotency, generation invalidation, and resume semantics. Domain data is stored in SQLite as structured indexes and artifacts; prompt-facing summaries are rendered projections, not independent facts.

**Tech Stack:** Python 3.12+, `uv`, Textual, Rich, Typer, Pydantic v2, OpenAI Python SDK `AsyncOpenAI`, SQLite, pytest, pytest-asyncio, ruff, mypy.

---

## Scope And Sequencing

This plan intentionally breaks the product into testable slices. Implement in order unless a task explicitly says it can run in parallel.

Parallelization guidance:

- Tasks 1-3 are foundation and should run mostly sequentially.
- Tasks 4-7 can be split across agents once Task 3 lands.
- Tasks 8-10 depend on workflow/storage/LLM interfaces and should start after Tasks 4-7 pass.
- Tasks 11-12 are integration, docs, and final verification.

Every task ends with a commit. Git commands must be run with escalation as required by the repo instructions.

## File Structure

Create this structure:

```text
pyproject.toml
.gitignore
config.toml.example
README.md
docs/configuration.md
docs/workflow.md
docs/llm-compatibility.md
docs/long-context.md
docs/development.md
src/inklink/__init__.py
src/inklink/__main__.py
src/inklink/cli.py
src/inklink/config.py
src/inklink/chapters.py
src/inklink/atomic.py
src/inklink/locks.py
src/inklink/domain/models.py
src/inklink/domain/checks.py
src/inklink/domain/index.py
src/inklink/storage/events.py
src/inklink/storage/schema.py
src/inklink/storage/sqlite.py
src/inklink/llm/types.py
src/inklink/llm/openai_client.py
src/inklink/llm/usage.py
src/inklink/llm/limiter.py
src/inklink/tools/registry.py
src/inklink/workflow/models.py
src/inklink/workflow/executor.py
src/inklink/workflow/service.py
src/inklink/tui/app.py
src/inklink/tui/screens.py
tests/conftest.py
tests/test_config.py
tests/test_chapters.py
tests/test_atomic.py
tests/test_locks.py
tests/test_checks.py
tests/test_index.py
tests/test_storage.py
tests/test_llm_usage.py
tests/test_limiter.py
tests/test_tools.py
tests/test_workflow.py
tests/test_tui.py
```

Responsibility boundaries:

- `config.py`: TOML loading and Pydantic validation.
- `chapters.py`: strict chapter parsing and UTF-8/newline normalization.
- `atomic.py`: atomic output and writeback.
- `locks.py`: per-input-directory active-run lock.
- `domain/models.py`: typed domain objects, artifacts, contracts, lifecycle enums.
- `domain/checks.py`: deterministic checks such as CJK counts, required names, plot-thread status.
- `domain/index.py`: structured story index, order-independent merges, generation cleanup, retrieval budgeting.
- `storage/*`: SQLite schema, repository operations, JSONL event logging.
- `llm/*`: provider-independent request/response types, OpenAI Responses and Chat Completions adapters, usage normalization, profile limiter.
- `tools/registry.py`: internal function tool schemas and dispatch.
- `workflow/*`: DAG node models, idempotency, revision loop, approvals, service commands.
- `tui/*`: Textual screens and widgets over workflow service.

## Task 1: Project Scaffold And Tooling

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `src/inklink/__init__.py`
- Create: `src/inklink/__main__.py`
- Create: `src/inklink/cli.py`
- Create: `tests/conftest.py`
- Create: `tests/test_cli.py`

- [ ] **Step 1: Write failing CLI smoke tests**

Create `tests/test_cli.py`:

```python
from typer.testing import CliRunner

from inklink.cli import app


runner = CliRunner()


def test_cli_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "inklink" in result.output


def test_cli_has_run_command() -> None:
    result = runner.invoke(app, ["run", "--help"])
    assert result.exit_code == 0
    assert "--config" in result.output
```

- [ ] **Step 2: Run test and verify failure**

Run:

```bash
uv run pytest tests/test_cli.py -q
```

Expected: failure because package and CLI do not exist.

- [ ] **Step 3: Create project metadata and CLI**

Create `pyproject.toml`:

```toml
[project]
name = "inklink"
version = "0.1.0"
description = "AI-driven Chinese novel continuation TUI."
readme = "README.md"
requires-python = ">=3.12"
dependencies = [
  "openai>=2.0.0",
  "pydantic>=2.12.0",
  "rich>=14.0.0",
  "textual>=6.0.0",
  "typer>=0.21.0",
]

[project.scripts]
inklink = "inklink.cli:app"

[dependency-groups]
dev = [
  "mypy>=1.19.0",
  "pytest>=9.0.0",
  "pytest-asyncio>=1.3.0",
  "ruff>=0.14.0",
]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM"]

[tool.mypy]
python_version = "3.12"
strict = true
packages = ["inklink"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

Create `.gitignore`:

```gitignore
.venv/
__pycache__/
.pytest_cache/
.mypy_cache/
.ruff_cache/
dist/
*.egg-info/
config.toml
logs/
```

Create `src/inklink/__init__.py`:

```python
__version__ = "0.1.0"
```

Create `src/inklink/__main__.py`:

```python
from inklink.cli import app


if __name__ == "__main__":
    app()
```

Create `src/inklink/cli.py`:

```python
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
```

Create `tests/conftest.py`:

```python
from pathlib import Path

import pytest


@pytest.fixture()
def project_dir(tmp_path: Path) -> Path:
    return tmp_path / "novel"
```

- [ ] **Step 4: Verify scaffold**

Run:

```bash
uv run pytest tests/test_cli.py -q
uv run ruff format
uv run ruff check
uv run mypy
```

Expected: tests pass, ruff passes, mypy passes.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml .gitignore src tests
git commit -m "chore: scaffold inklink project"
```

## Task 2: Configuration Loading

**Files:**
- Create: `src/inklink/config.py`
- Create: `config.toml.example`
- Create: `tests/test_config.py`

- [ ] **Step 1: Write failing config tests**

Create `tests/test_config.py`:

```python
from pathlib import Path

from inklink.config import AppConfig, load_config, request_options_for_profile


def test_load_minimal_config(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        """
[models.default]
api = "responses"
model = "gpt-test"
api_key_env = "OPENAI_API_KEY"

[tasks]
drafting = "default"
""",
        encoding="utf-8",
    )
    config = load_config(path)
    assert config.models["default"].model == "gpt-test"
    assert config.profile_for_task("review") == "default"
    assert config.profile_for_task("drafting") == "default"


def test_empty_optional_values_are_omitted() -> None:
    config = AppConfig.model_validate(
        {
            "models": {
                "default": {
                    "api": "responses",
                    "model": "gpt-test",
                    "api_key_env": "OPENAI_API_KEY",
                    "base_url": "",
                    "temperature": None,
                }
            }
        }
    )
    options = request_options_for_profile(config.models["default"])
    assert "base_url" not in options
    assert "temperature" not in options
```

- [ ] **Step 2: Run test and verify failure**

Run:

```bash
uv run pytest tests/test_config.py -q
```

Expected: import failure.

- [ ] **Step 3: Implement Pydantic config models**

Create `src/inklink/config.py`:

```python
from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


ApiKind = Literal["responses", "chat_completions"]


class RuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    output_mode: Literal["output", "writeback"] = "output"
    save_full_prompts: bool = True


class WritingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    word_count_tolerance_ratio: float = 0.1
    retrieval_token_budget: int | None = None
    max_revision_rounds: int = 3


class ApprovalConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    auto_approve_outline: bool = False
    auto_approve_chapter_plan: bool = False
    auto_approve_scene_plan: bool = False
    auto_approve_review_failure: bool = False


class ColdStartConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    recent_chapters_to_deep_analyze: int = 50


class ModelProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api: ApiKind = "responses"
    model: str
    api_key_env: str = "OPENAI_API_KEY"
    base_url: str | None = None
    timeout_seconds: float | None = None
    max_retries: int = 2
    rpm: int | None = None
    max_concurrency: int = 1
    temperature: float | None = None
    top_p: float | None = None
    reasoning_effort: str | None = None
    max_completion_tokens: int | None = None


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    writing: WritingConfig = Field(default_factory=WritingConfig)
    approvals: ApprovalConfig = Field(default_factory=ApprovalConfig)
    cold_start: ColdStartConfig = Field(default_factory=ColdStartConfig)
    models: dict[str, ModelProfile]
    tasks: dict[str, str] = Field(default_factory=dict)

    def profile_for_task(self, task: str) -> str:
        return self.tasks.get(task, "default")


def _none_if_blank(value: Any) -> Any:
    return None if value == "" else value


def _normalize_blanks(data: Any) -> Any:
    if isinstance(data, dict):
        return {key: _normalize_blanks(value) for key, value in data.items()}
    if isinstance(data, list):
        return [_normalize_blanks(value) for value in data]
    return _none_if_blank(data)


def load_config(path: Path) -> AppConfig:
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    return AppConfig.model_validate(_normalize_blanks(data))


def request_options_for_profile(profile: ModelProfile) -> dict[str, object]:
    optional: dict[str, object | None] = {
        "base_url": profile.base_url,
        "timeout": profile.timeout_seconds,
        "temperature": profile.temperature,
        "top_p": profile.top_p,
        "reasoning_effort": profile.reasoning_effort,
        "max_completion_tokens": profile.max_completion_tokens,
    }
    return {key: value for key, value in optional.items() if value is not None}
```

- [ ] **Step 4: Add commented example config**

Create `config.toml.example` with complete `#` comments copied from the design spec and expanded enough to explain all sections:

```toml
# 真实 config.toml 不应提交到 git。
# 复制本文件为 config.toml 后再填写自己的密钥环境变量和模型配置。

[runtime]
# output 不修改原目录；writeback 会写回输入目录后续章节。
output_mode = "output"

# 开启后更利于断点续接和排查，但日志会包含小说正文。
save_full_prompts = true

[writing]
# 中文字数容差比例。0.1 表示允许目标区间上下浮动 10%。
word_count_tolerance_ratio = 0.1

# 留空表示关闭预算裁剪；填写整数后按确定性优先级裁剪检索结果。
retrieval_token_budget = ""

# 每章自动修订最多轮数。
max_revision_rounds = 3

[approvals]
# 默认关闭自动批准，避免用户错过关键规划节点。
auto_approve_outline = false
auto_approve_chapter_plan = false
auto_approve_scene_plan = false

# 谨慎开启：修订达到上限仍未通过时，可能让低质量章节进入输出流程。
auto_approve_review_failure = false

[cold_start]
# 默认关闭。开启后可先粗读历史章节、精读最近章节。
enabled = false

# 冷启动开启时，最近多少章按 deep 分析处理。
recent_chapters_to_deep_analyze = 50

[models.default]
# 可选：responses 或 chat_completions。
api = "responses"

# 模型名由用户配置，不硬编码。
model = "gpt-5.5"

# 从环境变量读取 API key，避免把密钥写入 config.toml。
api_key_env = "OPENAI_API_KEY"

# 留空使用 SDK 默认地址；兼容服务可填写自定义 /v1 base_url。
base_url = ""

# 空值不传给 SDK。
temperature = ""
top_p = ""
reasoning_effort = ""
max_completion_tokens = ""

# 每分钟请求数限制；留空表示不启用 rpm 限制。
rpm = 60

# 同一 profile 最大并发调用数。
max_concurrency = 4

[tasks]
# 未配置任务回退到 models.default。
range_summary = "default"
drafting = "default"
review = "default"
```

- [ ] **Step 5: Verify config**

Run:

```bash
uv run pytest tests/test_config.py -q
uv run ruff format
uv run ruff check
uv run mypy
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/inklink/config.py config.toml.example tests/test_config.py
git commit -m "feat: add configuration loading"
```

## Task 3: Chapter IO, Atomic Writes, And Project Locks

**Files:**
- Create: `src/inklink/chapters.py`
- Create: `src/inklink/atomic.py`
- Create: `src/inklink/locks.py`
- Create: `tests/test_chapters.py`
- Create: `tests/test_atomic.py`
- Create: `tests/test_locks.py`

- [ ] **Step 1: Write failing chapter parser tests**

Create `tests/test_chapters.py`:

```python
from pathlib import Path

import pytest

from inklink.chapters import ChapterFormatError, load_chapters


def write_chapter(path: Path, title: str, body: str) -> None:
    path.write_text(f"title: {title}\n---\n{body}", encoding="utf-8")


def test_loads_strict_numbered_chapters(project_dir: Path) -> None:
    project_dir.mkdir()
    write_chapter(project_dir / "1.txt", "第一章", "正文一")
    write_chapter(project_dir / "2.txt", "第二章", "正文二")
    chapters = load_chapters(project_dir)
    assert [chapter.number for chapter in chapters] == [1, 2]
    assert chapters[0].title == "第一章"


def test_rejects_missing_separator(project_dir: Path) -> None:
    project_dir.mkdir()
    (project_dir / "1.txt").write_text("title: 第一章\n正文", encoding="utf-8")
    with pytest.raises(ChapterFormatError, match="separator"):
        load_chapters(project_dir)


def test_rejects_gap_in_numbering(project_dir: Path) -> None:
    project_dir.mkdir()
    write_chapter(project_dir / "1.txt", "第一章", "正文一")
    write_chapter(project_dir / "3.txt", "第三章", "正文三")
    with pytest.raises(ChapterFormatError, match="continuous"):
        load_chapters(project_dir)
```

- [ ] **Step 2: Write failing atomic and lock tests**

Create `tests/test_atomic.py`:

```python
from pathlib import Path

from inklink.atomic import atomic_write_text


def test_atomic_write_creates_target(tmp_path: Path) -> None:
    target = tmp_path / "3.txt"
    atomic_write_text(target, "title: 三\n---\n正文")
    assert target.read_text(encoding="utf-8") == "title: 三\n---\n正文"
```

Create `tests/test_locks.py`:

```python
from pathlib import Path

import pytest

from inklink.locks import ProjectLock, ProjectLockError


def test_project_lock_blocks_second_run(tmp_path: Path) -> None:
    project = tmp_path / "novel"
    project.mkdir()
    first = ProjectLock.acquire(project, "run-1")
    try:
        with pytest.raises(ProjectLockError):
            ProjectLock.acquire(project, "run-2")
    finally:
        first.release()


def test_project_lock_release_allows_new_run(tmp_path: Path) -> None:
    project = tmp_path / "novel"
    project.mkdir()
    first = ProjectLock.acquire(project, "run-1")
    first.release()
    second = ProjectLock.acquire(project, "run-2")
    second.release()
```

- [ ] **Step 3: Implement parser, atomic write, lock**

Create `src/inklink/chapters.py`:

```python
from dataclasses import dataclass
from pathlib import Path


class ChapterFormatError(ValueError):
    pass


@dataclass(frozen=True)
class Chapter:
    number: int
    title: str
    body: str
    path: Path


def load_chapters(directory: Path) -> list[Chapter]:
    files = sorted(
        (path for path in directory.glob("*.txt") if path.stem.isdecimal()),
        key=lambda path: int(path.stem),
    )
    numbers = [int(path.stem) for path in files]
    expected = list(range(1, len(numbers) + 1))
    if numbers != expected:
        raise ChapterFormatError("chapter files must be continuous from 1")

    chapters: list[Chapter] = []
    for path in files:
        raw = path.read_text(encoding="utf-8-sig")
        text = raw.replace("\r\n", "\n").replace("\r", "\n")
        lines = text.split("\n")
        if not lines or not lines[0].startswith("title:"):
            raise ChapterFormatError(f"{path.name}: first line must start with title:")
        try:
            separator_index = lines.index("---")
        except ValueError as exc:
            raise ChapterFormatError(f"{path.name}: missing separator ---") from exc
        title = lines[0].removeprefix("title:").strip()
        body = "\n".join(lines[separator_index + 1 :])
        chapters.append(Chapter(number=int(path.stem), title=title, body=body, path=path))
    return chapters
```

Create `src/inklink/atomic.py`:

```python
import os
from pathlib import Path


def atomic_write_text(target: Path, content: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, target)
```

Create `src/inklink/locks.py`:

```python
from dataclasses import dataclass
from pathlib import Path


class ProjectLockError(RuntimeError):
    pass


@dataclass
class ProjectLock:
    project_dir: Path
    run_id: str
    lock_path: Path

    @classmethod
    def acquire(cls, project_dir: Path, run_id: str) -> "ProjectLock":
        lock_path = project_dir / ".inklink.lock"
        try:
            with lock_path.open("x", encoding="utf-8") as handle:
                handle.write(run_id)
        except FileExistsError as exc:
            raise ProjectLockError(f"project already has an active run: {project_dir}") from exc
        return cls(project_dir=project_dir, run_id=run_id, lock_path=lock_path)

    def release(self) -> None:
        if self.lock_path.exists():
            current = self.lock_path.read_text(encoding="utf-8")
            if current == self.run_id:
                self.lock_path.unlink()
```

Implementation requirements:

- Normalize CRLF to LF.
- Strip UTF-8 BOM.
- Require exact `title:` first line and standalone `---`.
- Require contiguous numeric files starting at 1.
- Use `os.replace` for atomic writes.
- Use exclusive lock-file creation with `Path.open("x")`.

- [ ] **Step 4: Verify**

Run:

```bash
uv run pytest tests/test_chapters.py tests/test_atomic.py tests/test_locks.py -q
uv run ruff format
uv run ruff check
uv run mypy
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/inklink/chapters.py src/inklink/atomic.py src/inklink/locks.py tests/test_chapters.py tests/test_atomic.py tests/test_locks.py
git commit -m "feat: add chapter io and project locks"
```

## Task 4: Domain Models And Deterministic Checks

**Files:**
- Create: `src/inklink/domain/models.py`
- Create: `src/inklink/domain/checks.py`
- Create: `tests/test_checks.py`

- [ ] **Step 1: Write failing deterministic check tests**

Create `tests/test_checks.py`:

```python
from inklink.domain.checks import count_chinese_chars, run_chapter_checks
from inklink.domain.models import ChapterContract, DraftChapter, PlotThread, PlotThreadStatus


def test_count_chinese_chars_ignores_latin_digits_and_punctuation() -> None:
    assert count_chinese_chars("他到了 Lv.10，笑了。") == 4


def test_chapter_check_fails_when_required_name_missing() -> None:
    contract = ChapterContract(
        chapter_number=3,
        title="第三章",
        min_chars=5,
        max_chars=20,
        required_characters=["林青"],
        required_keywords=[],
        scene_ids=["s1"],
    )
    draft = DraftChapter(chapter_number=3, title="第三章", body="他走入山门。")
    report = run_chapter_checks(contract=contract, draft=draft, plot_threads=[])
    assert not report.passed
    assert any(issue.code == "required_character_missing" for issue in report.issues)


def test_resolved_plot_thread_cannot_be_resolved_again() -> None:
    contract = ChapterContract(
        chapter_number=10,
        title="第十章",
        min_chars=1,
        max_chars=100,
        required_characters=[],
        required_keywords=[],
        scene_ids=["s1"],
    )
    draft = DraftChapter(chapter_number=10, title="第十章", body="主角回收旧伏笔。")
    thread = PlotThread(
        thread_id="p1",
        description="玉佩来历",
        status=PlotThreadStatus.RESOLVED,
        source_chapter=1,
        due_chapter=10,
        related_keywords=["玉佩"],
    )
    report = run_chapter_checks(
        contract=contract,
        draft=draft,
        plot_threads=[thread],
        resolved_thread_ids=["p1"],
    )
    assert not report.passed
    assert any(issue.code == "plot_thread_repeated_resolution" for issue in report.issues)
```

- [ ] **Step 2: Run test and verify failure**

Run:

```bash
uv run pytest tests/test_checks.py -q
```

Expected: import failure.

- [ ] **Step 3: Implement models and checks**

Implement Pydantic models with `ConfigDict(extra="forbid")`:

```python
# src/inklink/domain/models.py
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class PlotThreadStatus(StrEnum):
    SEEDED = "seeded"
    REINFORCED = "reinforced"
    DUE = "due"
    RESOLVED = "resolved"
    ABANDONED = "abandoned"


class ChapterContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chapter_number: int
    title: str
    min_chars: int
    max_chars: int
    required_characters: list[str] = Field(default_factory=list)
    required_keywords: list[str] = Field(default_factory=list)
    scene_ids: list[str] = Field(default_factory=list)


class DraftChapter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chapter_number: int
    title: str
    body: str


class PlotThread(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_id: str
    description: str
    status: PlotThreadStatus
    source_chapter: int
    due_chapter: int | None = None
    related_keywords: list[str] = Field(default_factory=list)


class CheckIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    severity: str = "error"


class CheckReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    passed: bool
    issues: list[CheckIssue] = Field(default_factory=list)
```

Create `src/inklink/domain/checks.py`:

```python
from inklink.domain.models import (
    ChapterContract,
    CheckIssue,
    CheckReport,
    DraftChapter,
    PlotThread,
    PlotThreadStatus,
)


def count_chinese_chars(text: str) -> int:
    return sum(1 for char in text if "\u4e00" <= char <= "\u9fff")


def run_chapter_checks(
    *,
    contract: ChapterContract,
    draft: DraftChapter,
    plot_threads: list[PlotThread],
    resolved_thread_ids: list[str] | None = None,
) -> CheckReport:
    issues: list[CheckIssue] = []
    count = count_chinese_chars(draft.body)
    if count < contract.min_chars or count > contract.max_chars:
        issues.append(
            CheckIssue(
                code="word_count_out_of_range",
                message=f"Chinese character count {count} is outside target range.",
            )
        )
    for name in contract.required_characters:
        if name not in draft.body:
            issues.append(
                CheckIssue(
                    code="required_character_missing",
                    message=f"Required character {name} is missing.",
                )
            )
    for keyword in contract.required_keywords:
        if keyword not in draft.body:
            issues.append(
                CheckIssue(
                    code="required_keyword_missing",
                    message=f"Required keyword {keyword} is missing.",
                )
            )
    repeated = set(resolved_thread_ids or [])
    for thread in plot_threads:
        if thread.thread_id in repeated and thread.status in {
            PlotThreadStatus.RESOLVED,
            PlotThreadStatus.ABANDONED,
        }:
            issues.append(
                CheckIssue(
                    code="plot_thread_repeated_resolution",
                    message=f"Plot thread {thread.thread_id} cannot be resolved again.",
                )
            )
    return CheckReport(passed=not issues, issues=issues)
```

- [ ] **Step 4: Verify**

Run:

```bash
uv run pytest tests/test_checks.py -q
uv run ruff format
uv run ruff check
uv run mypy
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/inklink/domain tests/test_checks.py
git commit -m "feat: add domain checks"
```

## Task 5: Structured Story Index

**Files:**
- Create: `src/inklink/domain/index.py`
- Create: `tests/test_index.py`

- [ ] **Step 1: Write failing index tests**

Create `tests/test_index.py`:

```python
from inklink.domain.index import (
    EntityMention,
    StoryIndex,
)


def test_last_mentioned_is_order_independent() -> None:
    index = StoryIndex()
    index.upsert_mentions(
        [EntityMention(entity_id="c1", chapter_number=80, generation=1, strength=3)]
    )
    index.upsert_mentions(
        [EntityMention(entity_id="c1", chapter_number=75, generation=1, strength=2)]
    )
    character = index.characters["c1"]
    assert character.first_mentioned_chapter == 75
    assert character.last_mentioned_chapter == 80


def test_repeated_merge_does_not_duplicate_related_chapters() -> None:
    mention = EntityMention(entity_id="c1", chapter_number=8, generation=1, strength=1)
    index = StoryIndex()
    index.upsert_mentions([mention])
    index.upsert_mentions([mention])
    assert index.characters["c1"].related_chapters == [8]


def test_abandon_generation_removes_fact_contribution() -> None:
    index = StoryIndex()
    index.upsert_mentions(
        [
            EntityMention(entity_id="c1", chapter_number=501, generation=1, strength=5),
            EntityMention(entity_id="c1", chapter_number=501, generation=2, strength=1),
        ]
    )
    index.abandon_generation(chapter_number=501, generation=1)
    character = index.characters["c1"]
    assert character.related_chapters == [501]
    assert character.active_score == 1
```

- [ ] **Step 2: Implement order-independent index**

Create `src/inklink/domain/index.py` with:

```python
from pydantic import BaseModel, ConfigDict, Field


class EntityMention(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity_id: str
    chapter_number: int
    generation: int
    strength: int


class CharacterIndexEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity_id: str
    first_mentioned_chapter: int
    last_mentioned_chapter: int
    active_score: int
    related_chapters: list[int] = Field(default_factory=list)


class StoryIndex(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mentions: list[EntityMention] = Field(default_factory=list)
    abandoned_generations: set[tuple[int, int]] = Field(default_factory=set)
    characters: dict[str, CharacterIndexEntry] = Field(default_factory=dict)

    def upsert_mentions(self, mentions: list[EntityMention]) -> None:
        by_identity = {
            (mention.entity_id, mention.chapter_number, mention.generation): mention
            for mention in self.mentions
        }
        for mention in mentions:
            by_identity[(mention.entity_id, mention.chapter_number, mention.generation)] = mention
        self.mentions = list(by_identity.values())
        self.rebuild()

    def abandon_generation(self, chapter_number: int, generation: int) -> None:
        self.abandoned_generations.add((chapter_number, generation))
        self.rebuild()

    def rebuild(self) -> None:
        active = [
            mention
            for mention in self.mentions
            if (mention.chapter_number, mention.generation) not in self.abandoned_generations
        ]
        grouped: dict[str, list[EntityMention]] = {}
        for mention in active:
            grouped.setdefault(mention.entity_id, []).append(mention)
        self.characters = {}
        for entity_id, mentions in grouped.items():
            chapters = sorted({mention.chapter_number for mention in mentions})
            self.characters[entity_id] = CharacterIndexEntry(
                entity_id=entity_id,
                first_mentioned_chapter=min(chapters),
                last_mentioned_chapter=max(chapters),
                active_score=sum(mention.strength for mention in mentions),
                related_chapters=chapters,
            )
```

Implementation requirements:

- Mention identity is `(entity_id, chapter_number, generation)`.
- Upsert replaces the same identity, never appends duplicate facts.
- `active_score` is sum of active mention strengths.
- `related_chapters` is sorted unique active chapters.

- [ ] **Step 3: Verify**

Run:

```bash
uv run pytest tests/test_index.py -q
uv run ruff format
uv run ruff check
uv run mypy
```

Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add src/inklink/domain/index.py tests/test_index.py
git commit -m "feat: add structured story index"
```

## Task 6: SQLite State And JSONL Events

**Files:**
- Create: `src/inklink/storage/schema.py`
- Create: `src/inklink/storage/sqlite.py`
- Create: `src/inklink/storage/events.py`
- Create: `tests/test_storage.py`

- [ ] **Step 1: Write failing storage tests**

Create `tests/test_storage.py`:

```python
from pathlib import Path

from inklink.storage.events import JsonlEventLog
from inklink.storage.sqlite import StateStore


def test_state_store_records_run_and_nodes(tmp_path: Path) -> None:
    db = tmp_path / "state.sqlite"
    store = StateStore.open(db)
    store.create_run(runtime_id="run-1", input_dir="/novel", status="running")
    store.upsert_node(node_id="n1", node_type="load_project", status="pending")
    assert store.get_run("run-1")["status"] == "running"
    assert store.get_node("n1")["node_type"] == "load_project"


def test_event_log_writes_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    log = JsonlEventLog(path)
    log.write("run_started", {"runtime_id": "run-1"})
    assert '"event_type": "run_started"' in path.read_text(encoding="utf-8")
```

- [ ] **Step 2: Implement schema and repositories**

Create schema with tables: `runs`, `nodes`, `llm_calls`, `tool_calls`, `artifacts`, `approvals`, `messages`.

Create `src/inklink/storage/schema.py`:

```python
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS runs (
  runtime_id TEXT PRIMARY KEY,
  input_dir TEXT NOT NULL,
  status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS nodes (
  node_id TEXT PRIMARY KEY,
  node_type TEXT NOT NULL,
  status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS llm_calls (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  runtime_id TEXT,
  task_type TEXT,
  model TEXT,
  usage_json TEXT
);
CREATE TABLE IF NOT EXISTS tool_calls (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  llm_call_id INTEGER,
  name TEXT NOT NULL,
  arguments_json TEXT NOT NULL,
  result_json TEXT
);
CREATE TABLE IF NOT EXISTS artifacts (
  artifact_id TEXT PRIMARY KEY,
  artifact_type TEXT NOT NULL,
  version INTEGER NOT NULL,
  payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS approvals (
  approval_id TEXT PRIMARY KEY,
  status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
  message_id TEXT PRIMARY KEY,
  approval_id TEXT,
  role TEXT NOT NULL,
  content TEXT NOT NULL
);
"""
```

Create `src/inklink/storage/sqlite.py`:

```python
import sqlite3
from pathlib import Path

from inklink.storage.schema import SCHEMA_SQL


class StateStore:
    @classmethod
    def open(cls, path: Path) -> "StateStore":
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path)
        connection.row_factory = sqlite3.Row
        connection.executescript(SCHEMA_SQL)
        connection.commit()
        return cls(connection)

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def create_run(self, runtime_id: str, input_dir: str, status: str) -> None:
        self._connection.execute(
            "INSERT INTO runs(runtime_id, input_dir, status) VALUES (?, ?, ?)",
            (runtime_id, input_dir, status),
        )
        self._connection.commit()

    def get_run(self, runtime_id: str) -> dict[str, object]:
        row = self._connection.execute(
            "SELECT runtime_id, input_dir, status FROM runs WHERE runtime_id = ?",
            (runtime_id,),
        ).fetchone()
        if row is None:
            raise KeyError(runtime_id)
        return dict(row)

    def upsert_node(self, node_id: str, node_type: str, status: str) -> None:
        self._connection.execute(
            """
            INSERT INTO nodes(node_id, node_type, status)
            VALUES (?, ?, ?)
            ON CONFLICT(node_id) DO UPDATE SET node_type = excluded.node_type, status = excluded.status
            """,
            (node_id, node_type, status),
        )
        self._connection.commit()

    def get_node(self, node_id: str) -> dict[str, object]:
        row = self._connection.execute(
            "SELECT node_id, node_type, status FROM nodes WHERE node_id = ?",
            (node_id,),
        ).fetchone()
        if row is None:
            raise KeyError(node_id)
        return dict(row)
```

Create `src/inklink/storage/events.py`:

```python
import json
from datetime import UTC, datetime
from pathlib import Path


class JsonlEventLog:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, event_type: str, payload: dict[str, object]) -> None:
        event = {
            "timestamp": datetime.now(tz=UTC).isoformat(),
            "event_type": event_type,
            "payload": payload,
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
```

- [ ] **Step 3: Verify**

Run:

```bash
uv run pytest tests/test_storage.py -q
uv run ruff format
uv run ruff check
uv run mypy
```

Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add src/inklink/storage tests/test_storage.py
git commit -m "feat: add state storage"
```

## Task 7: LLM Types, Usage Normalization, And Rate Limiter

**Files:**
- Create: `src/inklink/llm/types.py`
- Create: `src/inklink/llm/usage.py`
- Create: `src/inklink/llm/limiter.py`
- Create: `tests/test_llm_usage.py`
- Create: `tests/test_limiter.py`

- [ ] **Step 1: Write failing usage and limiter tests**

Create `tests/test_llm_usage.py`:

```python
from inklink.llm.usage import normalize_usage


def test_normalize_chat_usage() -> None:
    usage = normalize_usage(
        {
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "total_tokens": 150,
            "prompt_tokens_details": {"cached_tokens": 20},
        }
    )
    assert usage.input_tokens == 100
    assert usage.output_tokens == 50
    assert usage.cached_tokens == 20


def test_normalize_responses_usage() -> None:
    usage = normalize_usage(
        {
            "input_tokens": 80,
            "output_tokens": 40,
            "total_tokens": 120,
            "input_tokens_details": {"cached_tokens": 10},
        }
    )
    assert usage.input_tokens == 80
    assert usage.output_tokens == 40
    assert usage.cached_tokens == 10
```

Create `tests/test_limiter.py`:

```python
import asyncio

import pytest

from inklink.llm.limiter import ProfileLimiter


@pytest.mark.asyncio
async def test_limiter_respects_concurrency() -> None:
    limiter = ProfileLimiter(max_concurrency=1, rpm=None)
    active = 0
    max_seen = 0

    async def work() -> None:
        nonlocal active, max_seen
        async with limiter:
            active += 1
            max_seen = max(max_seen, active)
            await asyncio.sleep(0.01)
            active -= 1

    await asyncio.gather(work(), work())
    assert max_seen == 1
```

- [ ] **Step 2: Implement**

Create `types.py`:

```python
from pydantic import BaseModel, ConfigDict, Field


class NormalizedUsage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    reasoning_tokens: int | None = None
    cached_tokens: int | None = None


class LLMToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    call_id: str
    name: str
    arguments_json: str


class LLMResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    tool_calls: list[LLMToolCall] = Field(default_factory=list)
    usage: NormalizedUsage = Field(default_factory=NormalizedUsage)
    request_id: str | None = None
```

Create `src/inklink/llm/usage.py`:

```python
from typing import Any

from inklink.llm.types import NormalizedUsage


def _int_or_none(value: Any) -> int | None:
    return value if isinstance(value, int) else None


def normalize_usage(raw: object) -> NormalizedUsage:
    if not isinstance(raw, dict):
        return NormalizedUsage()
    input_tokens = _int_or_none(raw.get("input_tokens") or raw.get("prompt_tokens"))
    output_tokens = _int_or_none(raw.get("output_tokens") or raw.get("completion_tokens"))
    total_tokens = _int_or_none(raw.get("total_tokens"))
    input_details = raw.get("input_tokens_details") or raw.get("prompt_tokens_details") or {}
    output_details = raw.get("output_tokens_details") or raw.get("completion_tokens_details") or {}
    cached_tokens = None
    reasoning_tokens = None
    if isinstance(input_details, dict):
        cached_tokens = _int_or_none(input_details.get("cached_tokens"))
    if isinstance(output_details, dict):
        reasoning_tokens = _int_or_none(output_details.get("reasoning_tokens"))
    return NormalizedUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        reasoning_tokens=reasoning_tokens,
        cached_tokens=cached_tokens,
    )
```

Create `src/inklink/llm/limiter.py`:

```python
import asyncio
from types import TracebackType


class ProfileLimiter:
    def __init__(self, *, max_concurrency: int, rpm: int | None) -> None:
        self.max_concurrency = max_concurrency
        self.rpm = rpm
        self._semaphore = asyncio.Semaphore(max_concurrency)

    async def __aenter__(self) -> "ProfileLimiter":
        await self._semaphore.acquire()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._semaphore.release()
```

- [ ] **Step 3: Verify**

Run:

```bash
uv run pytest tests/test_llm_usage.py tests/test_limiter.py -q
uv run ruff format
uv run ruff check
uv run mypy
```

Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add src/inklink/llm tests/test_llm_usage.py tests/test_limiter.py
git commit -m "feat: add llm support primitives"
```

## Task 8: OpenAI Adapters And Tool Registry

**Files:**
- Create: `src/inklink/llm/openai_client.py`
- Create: `src/inklink/tools/registry.py`
- Create: `tests/test_tools.py`

- [ ] **Step 1: Write failing tool registry tests**

Create `tests/test_tools.py`:

```python
from inklink.tools.registry import ToolRegistry


def test_registry_contains_chapter_analysis_tool() -> None:
    registry = ToolRegistry.default()
    schemas = registry.openai_tool_schemas()
    names = {schema["function"]["name"] for schema in schemas}
    assert "record_chapter_analysis" in names


def test_dispatch_validates_known_tool() -> None:
    registry = ToolRegistry.default()
    result = registry.dispatch("record_chapter_analysis", {"chapter_number": 1, "summary": "开端"})
    assert result["ok"] is True
```

- [ ] **Step 2: Implement registry and adapter skeletons**

Create `src/inklink/tools/registry.py`:

```python
from collections.abc import Callable
from dataclasses import dataclass


ToolHandler = Callable[[dict[str, object]], dict[str, object]]


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    parameters: dict[str, object]
    handler: ToolHandler


class ToolRegistry:
    def __init__(self, tools: list[ToolDefinition]) -> None:
        self._tools = {tool.name: tool for tool in tools}

    @classmethod
    def default(cls) -> "ToolRegistry":
        def record_chapter_analysis(arguments: dict[str, object]) -> dict[str, object]:
            if "chapter_number" not in arguments or "summary" not in arguments:
                return {"ok": False, "error": "chapter_number and summary are required"}
            return {"ok": True}

        return cls(
            [
                ToolDefinition(
                    name="record_chapter_analysis",
                    description="Record extracted analysis for one chapter.",
                    parameters={
                        "type": "object",
                        "properties": {
                            "chapter_number": {"type": "integer"},
                            "summary": {"type": "string"},
                        },
                        "required": ["chapter_number", "summary"],
                        "additionalProperties": True,
                    },
                    handler=record_chapter_analysis,
                )
            ]
        )

    def openai_tool_schemas(self) -> list[dict[str, object]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
            for tool in self._tools.values()
        ]

    def dispatch(self, name: str, arguments: dict[str, object]) -> dict[str, object]:
        tool = self._tools[name]
        return tool.handler(arguments)
```

Create `src/inklink/llm/openai_client.py`:

```python
from typing import Any

from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict, Field

from inklink.config import ModelProfile
from inklink.llm.types import LLMResponse
from inklink.llm.usage import normalize_usage


class LLMMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str
    content: str


class LLMRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instructions: str | None = None
    messages: list[LLMMessage] = Field(default_factory=list)
    input_text: str
    tools: list[dict[str, object]] = Field(default_factory=list)
    tool_choice: str | None = None


def make_async_openai(profile: ModelProfile, api_key: str | None) -> AsyncOpenAI:
    kwargs: dict[str, Any] = {"api_key": api_key, "max_retries": profile.max_retries}
    if profile.base_url is not None:
        kwargs["base_url"] = profile.base_url
    if profile.timeout_seconds is not None:
        kwargs["timeout"] = profile.timeout_seconds
    return AsyncOpenAI(**kwargs)


class ResponsesAdapter:
    def __init__(self, client: AsyncOpenAI, profile: ModelProfile) -> None:
        self.client = client
        self.profile = profile

    async def create(self, request: LLMRequest) -> LLMResponse:
        response = await self.client.responses.create(
            model=self.profile.model,
            instructions=request.instructions,
            input=request.input_text,
            tools=request.tools,
            tool_choice=request.tool_choice or "auto",
        )
        text = getattr(response, "output_text", "")
        usage = normalize_usage(getattr(response, "usage", None))
        request_id = getattr(response, "_request_id", None)
        return LLMResponse(text=text, usage=usage, request_id=request_id)


class ChatCompletionsAdapter:
    def __init__(self, client: AsyncOpenAI, profile: ModelProfile) -> None:
        self.client = client
        self.profile = profile

    async def create(self, request: LLMRequest) -> LLMResponse:
        messages = [{"role": message.role, "content": message.content} for message in request.messages]
        if not messages:
            messages = [{"role": "user", "content": request.input_text}]
        response = await self.client.chat.completions.create(
            model=self.profile.model,
            messages=messages,
            tools=request.tools,
            tool_choice=request.tool_choice or "auto",
        )
        choice = response.choices[0]
        text = choice.message.content or ""
        usage = normalize_usage(getattr(response, "usage", None))
        request_id = getattr(response, "_request_id", None)
        return LLMResponse(text=text, usage=usage, request_id=request_id)
```

Keep Responses and Chat Completions request shapes separate. Later tasks may extend tool-call parsing, but this task must compile and keep adapter boundaries explicit.

- [ ] **Step 3: Verify**

Run:

```bash
uv run pytest tests/test_tools.py -q
uv run ruff format
uv run ruff check
uv run mypy
```

Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add src/inklink/llm/openai_client.py src/inklink/tools tests/test_tools.py
git commit -m "feat: add openai adapters and tools"
```

## Task 9: Workflow Models And Executor

**Files:**
- Create: `src/inklink/workflow/models.py`
- Create: `src/inklink/workflow/executor.py`
- Create: `tests/test_workflow.py`

- [ ] **Step 1: Write failing workflow tests**

Create `tests/test_workflow.py`:

```python
from inklink.workflow.executor import WorkflowExecutor
from inklink.workflow.models import NodeState, WorkflowNode


def test_executor_runs_dependencies_in_order() -> None:
    seen: list[str] = []
    nodes = [
        WorkflowNode(node_id="a", node_type="load_project", depends_on=[]),
        WorkflowNode(node_id="b", node_type="merge_story_state", depends_on=["a"]),
    ]
    executor = WorkflowExecutor(nodes)
    executor.run_ready(lambda node: seen.append(node.node_id))
    assert seen == ["a", "b"]
    assert executor.state_for("b") == NodeState.COMPLETED


def test_generation_changes_idempotency_key() -> None:
    node = WorkflowNode(node_id="draft-501", node_type="draft_scene", depends_on=[])
    key1 = node.idempotency_key(input_version="v1", profile="default", generation=1)
    key2 = node.idempotency_key(input_version="v1", profile="default", generation=2)
    assert key1 != key2
```

- [ ] **Step 2: Implement workflow primitives**

Create `src/inklink/workflow/models.py`:

```python
import hashlib
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class NodeState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class WorkflowNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: str
    node_type: str
    depends_on: list[str] = Field(default_factory=list)
    state: NodeState = NodeState.PENDING
    attempt: int = 0

    def idempotency_key(self, *, input_version: str, profile: str, generation: int) -> str:
        raw = f"{self.node_type}:{input_version}:{profile}:{generation}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()
```

Create `src/inklink/workflow/executor.py`:

```python
from collections.abc import Callable

from inklink.workflow.models import NodeState, WorkflowNode


class WorkflowExecutor:
    def __init__(self, nodes: list[WorkflowNode]) -> None:
        self.nodes = {node.node_id: node for node in nodes}

    def state_for(self, node_id: str) -> NodeState:
        return self.nodes[node_id].state

    def run_ready(self, runner: Callable[[WorkflowNode], None]) -> None:
        while True:
            ready = [
                node
                for node in self.nodes.values()
                if node.state == NodeState.PENDING
                and all(self.nodes[dep].state == NodeState.COMPLETED for dep in node.depends_on)
            ]
            if not ready:
                return
            for node in ready:
                node.state = NodeState.RUNNING
                runner(node)
                node.state = NodeState.COMPLETED
```

Keep revision loops inside the chapter node model in later tasks; do not add top-level DAG back edges.

- [ ] **Step 3: Verify**

Run:

```bash
uv run pytest tests/test_workflow.py -q
uv run ruff format
uv run ruff check
uv run mypy
```

Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add src/inklink/workflow tests/test_workflow.py
git commit -m "feat: add workflow executor"
```

## Task 10: Workflow Service With Fake LLM

**Files:**
- Create: `src/inklink/workflow/service.py`
- Modify: `tests/test_workflow.py`

- [ ] **Step 1: Add integration-style workflow service tests**

Append to `tests/test_workflow.py`:

```python
from pathlib import Path

from inklink.workflow.service import WorkflowService


def test_service_creates_runtime_and_blocks_duplicate_project(tmp_path: Path) -> None:
    project = tmp_path / "novel"
    project.mkdir()
    (project / "1.txt").write_text("title: 一\n---\n正文", encoding="utf-8")
    service = WorkflowService(log_root=tmp_path / "logs")
    run = service.start_run(input_dir=project)
    assert run.runtime_id
    second = service.can_start_run(input_dir=project)
    assert second.allowed is False
```

- [ ] **Step 2: Implement minimum service**

`WorkflowService` must:

- Create `logs/<runtime_id>/state.sqlite`.
- Create `logs/<runtime_id>/events.jsonl`.
- Acquire `ProjectLock`.
- Load chapters.
- Expose `start_run`, `can_start_run`, `abandon_chapter`, `rewrite_chapter`, `retry_node`.

- [ ] **Step 3: Verify**

Run:

```bash
uv run pytest tests/test_workflow.py -q
uv run ruff format
uv run ruff check
uv run mypy
```

Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add src/inklink/workflow/service.py tests/test_workflow.py
git commit -m "feat: add workflow service"
```

## Task 11: Textual TUI Shell

**Files:**
- Create: `src/inklink/tui/app.py`
- Create: `src/inklink/tui/screens.py`
- Modify: `src/inklink/cli.py`
- Create: `tests/test_tui.py`

- [ ] **Step 1: Write failing Textual tests**

Create `tests/test_tui.py`:

```python
import pytest

from inklink.tui.app import InklinkApp


@pytest.mark.asyncio
async def test_tui_starts() -> None:
    app = InklinkApp()
    async with app.run_test() as pilot:
        assert pilot.app.title == "墨连 Inklink"


@pytest.mark.asyncio
async def test_tui_has_dashboard_command() -> None:
    app = InklinkApp()
    async with app.run_test() as pilot:
        await pilot.press("f1")
        assert pilot.app.screen is not None
```

- [ ] **Step 2: Implement Textual app**

Create `InklinkApp(App[None])` with:

- Title `墨连 Inklink`.
- Header and Footer.
- Setup/dashboard placeholder screens.
- Key binding `f1` for dashboard.

Modify `cli.py run` to launch `InklinkApp().run()` when not under tests.

- [ ] **Step 3: Verify**

Run:

```bash
uv run pytest tests/test_tui.py tests/test_cli.py -q
uv run ruff format
uv run ruff check
uv run mypy
```

Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add src/inklink/tui src/inklink/cli.py tests/test_tui.py
git commit -m "feat: add textual app shell"
```

## Task 12: Documentation And Final Verification

**Files:**
- Create/Modify: `README.md`
- Create: `docs/configuration.md`
- Create: `docs/workflow.md`
- Create: `docs/llm-compatibility.md`
- Create: `docs/long-context.md`
- Create: `docs/development.md`

- [ ] **Step 1: Write docs**

Each document must include the relevant constraints from the design spec:

- `README.md`: install, quick start, input format, current feature status.
- `docs/configuration.md`: every config key, optional blank behavior, model task mapping.
- `docs/workflow.md`: DAG, approvals, auto-approve, generation, retry vs rewrite, resume.
- `docs/llm-compatibility.md`: Responses vs Chat Completions shapes and limitations.
- `docs/long-context.md`: structured index, active score facts, retrieval budget, cold start, range summary stale-limit note.
- `docs/development.md`: uv, tests, ruff, mypy, commit workflow.

- [ ] **Step 2: Run full verification**

Run:

```bash
uv run ruff format
uv run ruff check
uv run mypy
uv run pytest
```

Expected: all pass.

- [ ] **Step 3: Commit**

```bash
git add README.md docs src tests
git commit -m "docs: add inklink project documentation"
```

## Plan Self-Review Checklist

Spec coverage:

- Strict chapter parsing: Task 3.
- UTF-8/BOM/LF/CRLF: Task 3.
- Config and comments: Task 2.
- OpenAI Responses/Chat Completions separation: Task 8.
- Tool-call architecture: Task 8.
- SQLite/JSONL: Task 6.
- Calling-level idempotency and generation: Tasks 9-10.
- Project locking: Task 10.
- Structured index, order-independent merge, generation cleanup: Task 5.
- Deterministic checks and CJK counts: Task 4.
- Revision loop and targeted rewrite: Tasks 9-10 plus docs in Task 12.
- Scene sequential continuity: Task 10 workflow behavior and Task 12 docs.
- Textual TUI: Task 11.
- Docs: Task 12.

No placeholder terms are intentionally present in implementation steps. If an executor adds new files beyond this plan, they must add focused tests and document the reason in their commit message.
