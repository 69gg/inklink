from __future__ import annotations

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
        chapters.append(_load_chapter(path))
    return chapters


def _load_chapter(path: Path) -> Chapter:
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
    return Chapter(number=int(path.stem), title=title, body=body, path=path)
