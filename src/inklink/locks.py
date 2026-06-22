from __future__ import annotations

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
    def acquire(cls, project_dir: Path, run_id: str) -> ProjectLock:
        lock_path = project_dir / ".inklink.lock"
        try:
            with lock_path.open("x", encoding="utf-8") as handle:
                handle.write(run_id)
        except FileExistsError as exc:
            raise ProjectLockError(f"project already has an active run: {project_dir}") from exc
        return cls(project_dir=project_dir, run_id=run_id, lock_path=lock_path)

    def release(self) -> None:
        if not self.lock_path.exists():
            return
        current = self.lock_path.read_text(encoding="utf-8")
        if current == self.run_id:
            self.lock_path.unlink()
