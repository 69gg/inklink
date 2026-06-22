from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path


class JsonlEventLog:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, event_type: str, payload: dict[str, object]) -> None:
        event: dict[str, object] = {
            "timestamp": datetime.now(tz=UTC).isoformat(),
            "event_type": event_type,
            "payload": payload,
        }
        line = json.dumps(event, ensure_ascii=False)
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(f"{line}\n")
