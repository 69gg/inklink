from __future__ import annotations

import errno
import fcntl
from dataclasses import dataclass
from io import TextIOWrapper
from pathlib import Path


class ProjectLockError(RuntimeError):
    pass


@dataclass
class ProjectLock:
    project_dir: Path
    run_id: str
    lock_path: Path
    _handle: TextIOWrapper | None

    @classmethod
    def acquire(cls, project_dir: Path, run_id: str) -> ProjectLock:
        lock_path = project_dir / ".inklink.lock"
        handle = lock_path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            if exc.errno not in {errno.EACCES, errno.EAGAIN}:
                raise
            raise ProjectLockError(f"project already has an active run: {project_dir}") from exc
        handle.seek(0)
        handle.truncate()
        handle.write(run_id)
        handle.flush()
        return cls(project_dir=project_dir, run_id=run_id, lock_path=lock_path, _handle=handle)

    def release(self) -> None:
        if self._handle is None:
            return
        try:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        finally:
            self._handle.close()
            self._handle = None
