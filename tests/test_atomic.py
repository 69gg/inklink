from pathlib import Path

from inklink.atomic import atomic_write_text


def test_atomic_write_creates_target(tmp_path: Path) -> None:
    target = tmp_path / "3.txt"
    atomic_write_text(target, "title: 三\n---\n正文")
    assert target.read_text(encoding="utf-8") == "title: 三\n---\n正文"


def test_atomic_write_normalizes_newlines_to_lf(tmp_path: Path) -> None:
    target = tmp_path / "4.txt"
    atomic_write_text(target, "title: 四\r\n---\r正文")
    assert target.read_bytes() == b"title: \xe5\x9b\x9b\n---\n\xe6\xad\xa3\xe6\x96\x87"
