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
