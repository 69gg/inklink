from __future__ import annotations

import errno
import fcntl
import os
from dataclasses import dataclass
from pathlib import Path


class ProjectLockError(RuntimeError):
    pass


@dataclass
class ProjectLock:
    project_dir: Path
    run_id: str
    lock_path: Path
    _fd: int | None = None

    @classmethod
    def acquire(cls, project_dir: Path, run_id: str) -> ProjectLock:
        lock_path = project_dir / ".inklink.lock"
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        fd = os.open(project_dir, flags)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(fd)
            if exc.errno not in {errno.EACCES, errno.EAGAIN}:
                raise
            raise ProjectLockError(f"project already has an active run: {project_dir}") from exc
        try:
            _write_marker(lock_path, run_id)
        except Exception:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
            raise
        return cls(project_dir=project_dir, run_id=run_id, lock_path=lock_path, _fd=fd)

    def release(self) -> None:
        if self._fd is None:
            return
        fd = self._fd
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
            self._fd = None


def _write_marker(lock_path: Path, run_id: str) -> None:
    fd = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(run_id)
        handle.flush()
        os.fsync(handle.fileno())
