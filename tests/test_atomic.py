import stat
from pathlib import Path

import pytest

from inklink.atomic import atomic_write_text


def test_atomic_write_creates_target(tmp_path: Path) -> None:
    target = tmp_path / "3.txt"
    atomic_write_text(target, "title: 三\n---\n正文")
    assert target.read_text(encoding="utf-8") == "title: 三\n---\n正文"


def test_atomic_write_normalizes_newlines_to_lf(tmp_path: Path) -> None:
    target = tmp_path / "4.txt"
    atomic_write_text(target, "title: 四\r\n---\r正文")
    assert target.read_bytes() == b"title: \xe5\x9b\x9b\n---\n\xe6\xad\xa3\xe6\x96\x87"


def test_atomic_write_does_not_reuse_existing_fixed_tmp_file(tmp_path: Path) -> None:
    target = tmp_path / "5.txt"
    fixed_tmp = tmp_path / ".5.txt.tmp"
    fixed_tmp.write_text("do not touch", encoding="utf-8")
    atomic_write_text(target, "title: 五\n---\n正文")
    assert target.read_text(encoding="utf-8") == "title: 五\n---\n正文"
    assert fixed_tmp.read_text(encoding="utf-8") == "do not touch"


def test_atomic_write_preserves_existing_target_permissions(tmp_path: Path) -> None:
    target = tmp_path / "6.txt"
    target.write_text("old", encoding="utf-8")
    target.chmod(0o644)

    atomic_write_text(target, "title: 六\n---\n正文")

    assert stat.S_IMODE(target.stat().st_mode) == 0o644


def test_atomic_write_cleans_temp_file_when_permission_copy_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "7.txt"
    target.write_text("old", encoding="utf-8")

    def fail_fchmod(fd: int, mode: int) -> None:
        raise OSError("fchmod failed")

    monkeypatch.setattr("inklink.atomic.os.fchmod", fail_fchmod)

    with pytest.raises(OSError, match="fchmod failed"):
        atomic_write_text(target, "new")

    assert target.read_text(encoding="utf-8") == "old"
    assert list(tmp_path.glob(".7.txt.*.tmp")) == []
