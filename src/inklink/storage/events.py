from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path


class JsonlEventLog:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, event_type: str, payload: dict[str, object]) -> None:
        should_sync_directory = not self._path.exists()
        event: dict[str, object] = {
            "timestamp": datetime.now(tz=UTC).isoformat(),
            "event_type": event_type,
            "payload": payload,
        }
        line = json.dumps(event, ensure_ascii=False)
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(f"{line}\n")
            handle.flush()
            os.fsync(handle.fileno())
        if should_sync_directory:
            _fsync_directory(self._path.parent)


def _fsync_directory(path: Path) -> None:
    try:
        directory_fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(directory_fd)
    except OSError:
        pass
    finally:
        os.close(directory_fd)
