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


def test_loads_utf8_bom_and_normalizes_crlf(project_dir: Path) -> None:
    project_dir.mkdir()
    (project_dir / "1.txt").write_text(
        "\ufefftitle: 第一章\r\n---\r\n第一行\r\n第二行\r第三行",
        encoding="utf-8",
    )
    chapters = load_chapters(project_dir)
    assert chapters[0].title == "第一章"
    assert chapters[0].body == "第一行\n第二行\n第三行"


def test_rejects_invalid_title_line(project_dir: Path) -> None:
    project_dir.mkdir()
    (project_dir / "1.txt").write_text("Title: 第一章\n---\n正文", encoding="utf-8")
    with pytest.raises(ChapterFormatError, match="title"):
        load_chapters(project_dir)


def test_rejects_non_standalone_separator(project_dir: Path) -> None:
    project_dir.mkdir()
    (project_dir / "1.txt").write_text("title: 第一章\n --- \n正文", encoding="utf-8")
    with pytest.raises(ChapterFormatError, match="separator"):
        load_chapters(project_dir)
