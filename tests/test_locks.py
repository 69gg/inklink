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


def test_project_lock_release_does_not_delete_other_run(tmp_path: Path) -> None:
    project = tmp_path / "novel"
    project.mkdir()
    first = ProjectLock.acquire(project, "run-1")
    first.lock_path.write_text("run-2", encoding="utf-8")
    first.release()
    assert first.lock_path.read_text(encoding="utf-8") == "run-2"


def test_project_lock_release_missing_lock_is_noop(tmp_path: Path) -> None:
    project = tmp_path / "novel"
    project.mkdir()
    first = ProjectLock.acquire(project, "run-1")
    first.lock_path.unlink()
    first.release()
    assert not first.lock_path.exists()


def test_project_lock_blocks_second_run_when_marker_file_is_removed(tmp_path: Path) -> None:
    project = tmp_path / "novel"
    project.mkdir()
    first = ProjectLock.acquire(project, "run-1")
    first.lock_path.unlink()
    try:
        with pytest.raises(ProjectLockError):
            ProjectLock.acquire(project, "run-2")
    finally:
        first.release()


def test_project_lock_marker_replaces_symlink_without_touching_target(tmp_path: Path) -> None:
    project = tmp_path / "novel"
    project.mkdir()
    victim = tmp_path / "victim.txt"
    victim.write_text("KEEP", encoding="utf-8")
    (project / ".inklink.lock").symlink_to(victim)

    lock = ProjectLock.acquire(project, "run-1")
    try:
        assert victim.read_text(encoding="utf-8") == "KEEP"
        assert lock.lock_path.read_text(encoding="utf-8") == "run-1"
        assert not lock.lock_path.is_symlink()
    finally:
        lock.release()


def test_project_lock_release_does_not_unlink_lock_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "novel"
    project.mkdir()
    first = ProjectLock.acquire(project, "run-1")

    def reject_unlink(self: Path, missing_ok: bool = False) -> None:
        raise AssertionError(f"release must not unlink lock path: {self}")

    monkeypatch.setattr(Path, "unlink", reject_unlink)
    first.release()

    second = ProjectLock.acquire(project, "run-2")
    second.release()
