from __future__ import annotations

import sqlite3
from pathlib import Path
from types import TracebackType
from typing import cast

from inklink.storage.schema import SCHEMA_SQL


class StateStore:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    @classmethod
    def open(cls, path: Path) -> StateStore:
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.executescript(SCHEMA_SQL)
            connection.commit()
        except BaseException:
            connection.close()
            raise
        return cls(connection)

    def __enter__(self) -> StateStore:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._connection.close()

    def create_run(self, runtime_id: str, input_dir: str, status: str) -> None:
        self._connection.execute(
            "INSERT INTO runs(runtime_id, input_dir, status) VALUES (?, ?, ?)",
            (runtime_id, input_dir, status),
        )
        self._connection.commit()

    def get_run(self, runtime_id: str) -> dict[str, object]:
        row = cast(
            sqlite3.Row | None,
            self._connection.execute(
                "SELECT runtime_id, input_dir, status FROM runs WHERE runtime_id = ?",
                (runtime_id,),
            ).fetchone(),
        )
        if row is None:
            raise KeyError(runtime_id)
        return _row_to_dict(row)

    def upsert_node(self, node_id: str, node_type: str, status: str) -> None:
        self._connection.execute(
            """
            INSERT INTO nodes(node_id, node_type, status)
            VALUES (?, ?, ?)
            ON CONFLICT(node_id) DO UPDATE SET
              node_type = excluded.node_type,
              status = excluded.status
            """,
            (node_id, node_type, status),
        )
        self._connection.commit()

    def get_node(self, node_id: str) -> dict[str, object]:
        row = cast(
            sqlite3.Row | None,
            self._connection.execute(
                "SELECT node_id, node_type, status FROM nodes WHERE node_id = ?",
                (node_id,),
            ).fetchone(),
        )
        if row is None:
            raise KeyError(node_id)
        return _row_to_dict(row)


def _row_to_dict(row: sqlite3.Row) -> dict[str, object]:
    return dict(row)
