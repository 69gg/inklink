from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_CHAPTER_FILE_RE = re.compile(r"^[1-9][0-9]*\.txt$")


class ChapterFormatError(ValueError):
    pass


@dataclass(frozen=True)
class Chapter:
    number: int
    title: str
    body: str
    path: Path


def load_chapters(directory: Path) -> list[Chapter]:
    files = sorted(directory.glob("*.txt"), key=_chapter_sort_key)
    if not files:
        raise ChapterFormatError("chapter directory must contain at least one chapter file")
    numbers = [int(path.stem) for path in files]
    expected = list(range(1, len(numbers) + 1))
    if numbers != expected:
        raise ChapterFormatError("chapter files must be continuous from 1")

    chapters: list[Chapter] = []
    for path in files:
        chapters.append(_load_chapter(path))
    return chapters


def _chapter_sort_key(path: Path) -> int:
    if not _CHAPTER_FILE_RE.fullmatch(path.name):
        raise ChapterFormatError(
            f"{path.name}: filename must be an ASCII positive integer .txt file"
        )
    return int(path.stem)


def _load_chapter(path: Path) -> Chapter:
    if not path.is_file():
        raise ChapterFormatError(f"{path.name}: chapter path must be a file")
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ChapterFormatError(f"{path.name}: invalid UTF-8 encoding") from exc

    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    if not lines or not lines[0].startswith("title:"):
        raise ChapterFormatError(f"{path.name}: first line must start with title:")

    title = lines[0].removeprefix("title:").strip()
    if not title:
        raise ChapterFormatError(f"{path.name}: title must not be empty")
    if len(lines) < 2 or lines[1] != "---":
        raise ChapterFormatError(f"{path.name}: second line must be separator ---")

    body = "\n".join(lines[2:])
    return Chapter(number=int(path.stem), title=title, body=body, path=path)
