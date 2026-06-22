from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from inklink.storage.events import JsonlEventLog
from inklink.storage.schema import SCHEMA_VERSION
from inklink.storage.sqlite import StateStore, UnsupportedSchemaError

OLD_SCHEMA_SQL = """
CREATE TABLE runs (
  runtime_id TEXT PRIMARY KEY,
  input_dir TEXT NOT NULL,
  status TEXT NOT NULL
);

CREATE TABLE nodes (
  node_id TEXT PRIMARY KEY,
  node_type TEXT NOT NULL,
  status TEXT NOT NULL
);

CREATE TABLE llm_calls (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  runtime_id TEXT,
  task_type TEXT,
  model TEXT,
  usage_json TEXT
);

CREATE TABLE tool_calls (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  llm_call_id INTEGER,
  name TEXT NOT NULL,
  arguments_json TEXT NOT NULL,
  result_json TEXT
);

CREATE TABLE artifacts (
  artifact_id TEXT PRIMARY KEY,
  artifact_type TEXT NOT NULL,
  version INTEGER NOT NULL,
  payload_json TEXT NOT NULL
);

CREATE TABLE approvals (
  approval_id TEXT PRIMARY KEY,
  status TEXT NOT NULL
);

CREATE TABLE messages (
  message_id TEXT PRIMARY KEY,
  approval_id TEXT,
  role TEXT NOT NULL,
  content TEXT NOT NULL
);
"""


def test_state_store_creates_schema_tables(tmp_path: Path) -> None:
    db = tmp_path / "nested" / "state.sqlite"

    with StateStore.open(db):
        pass

    connection = sqlite3.connect(db)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        table_names = {row["name"] for row in rows}
        assert {
            "runs",
            "nodes",
            "llm_calls",
            "tool_calls",
            "artifacts",
            "approvals",
            "messages",
        } <= table_names
    finally:
        connection.close()


def test_state_store_sets_schema_version_for_new_database(tmp_path: Path) -> None:
    db = tmp_path / "state.sqlite"

    with StateStore.open(db):
        pass

    connection = sqlite3.connect(db)
    try:
        user_version = connection.execute("PRAGMA user_version").fetchone()[0]
        assert user_version == SCHEMA_VERSION
    finally:
        connection.close()


def test_state_store_rejects_legacy_database_without_schema_version(tmp_path: Path) -> None:
    db = tmp_path / "state.sqlite"
    connection = sqlite3.connect(db)
    try:
        connection.executescript(OLD_SCHEMA_SQL)
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(UnsupportedSchemaError, match="unsupported SQLite schema version 0"):
        StateStore.open(db)


def test_state_store_records_run_and_nodes(tmp_path: Path) -> None:
    db = tmp_path / "state.sqlite"

    with StateStore.open(db) as store:
        store.create_run(runtime_id="run-1", input_dir="/novel", status="running")
        store.upsert_node(node_id="n1", node_type="load_project", status="pending")

        assert store.get_run("run-1") == {
            "runtime_id": "run-1",
            "input_dir": "/novel",
            "status": "running",
        }
        assert store.get_node("n1") == {
            "node_id": "n1",
            "node_type": "load_project",
            "status": "pending",
        }


def test_state_store_upsert_node_updates_existing_row(tmp_path: Path) -> None:
    with StateStore.open(tmp_path / "state.sqlite") as store:
        store.upsert_node(node_id="n1", node_type="load_project", status="pending")
        store.upsert_node(node_id="n1", node_type="draft_scene", status="complete")

        assert store.get_node("n1") == {
            "node_id": "n1",
            "node_type": "draft_scene",
            "status": "complete",
        }


def test_state_store_raises_key_error_for_missing_records(tmp_path: Path) -> None:
    with StateStore.open(tmp_path / "state.sqlite") as store:
        with pytest.raises(KeyError, match="run-missing"):
            store.get_run("run-missing")
        with pytest.raises(KeyError, match="node-missing"):
            store.get_node("node-missing")


def test_state_store_close_releases_connection(tmp_path: Path) -> None:
    store = StateStore.open(tmp_path / "state.sqlite")

    store.close()

    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        store.get_run("run-1")


def test_state_store_context_manager_closes_connection(tmp_path: Path) -> None:
    with StateStore.open(tmp_path / "state.sqlite") as store:
        store.create_run(runtime_id="run-1", input_dir="/novel", status="running")

    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        store.get_run("run-1")


def test_state_store_open_closes_connection_when_schema_initialization_fails(
    tmp_path: Path,
) -> None:
    class QueryResult:
        def __init__(self, row: tuple[int] | None) -> None:
            self._row = row

        def fetchone(self) -> tuple[int] | None:
            return self._row

    class FailingConnection:
        row_factory: object = None

        def __init__(self) -> None:
            self.closed = False

        def execute(self, sql: str) -> QueryResult:
            if sql == "PRAGMA user_version":
                return QueryResult((0,))
            return QueryResult(None)

        def executescript(self, _schema_sql: str) -> None:
            raise sqlite3.OperationalError("schema failed")

        def commit(self) -> None:
            raise AssertionError("commit should not run after schema failure")

        def close(self) -> None:
            self.closed = True

    connection = FailingConnection()
    with (
        patch("sqlite3.connect", return_value=connection),
        pytest.raises(sqlite3.OperationalError, match="schema failed"),
    ):
        StateStore.open(tmp_path / "broken.sqlite")

    assert connection.closed


def test_state_store_enforces_foreign_keys(tmp_path: Path) -> None:
    with (
        StateStore.open(tmp_path / "state.sqlite") as store,
        pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"),
    ):
        store._connection.execute(
            """
            INSERT INTO llm_calls(runtime_id, task_type, model, usage_json)
            VALUES (?, ?, ?, ?)
            """,
            ("missing-run", "drafting", "gpt-test", "{}"),
        )


def test_state_store_fresh_database_foreign_keys_remain_enforced(tmp_path: Path) -> None:
    db = tmp_path / "state.sqlite"

    with StateStore.open(db):
        pass

    with StateStore.open(db) as store:
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            store._connection.execute(
                """
                INSERT INTO llm_calls(runtime_id, task_type, model, usage_json)
                VALUES (?, ?, ?, ?)
                """,
                ("missing-run", "drafting", "gpt-test", "{}"),
            )
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            store._connection.execute(
                """
                INSERT INTO tool_calls(llm_call_id, name, arguments_json, result_json)
                VALUES (?, ?, ?, ?)
                """,
                (1, "tool", "{}", "{}"),
            )
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            store._connection.execute(
                """
                INSERT INTO messages(message_id, approval_id, role, content)
                VALUES (?, ?, ?, ?)
                """,
                ("message-1", "missing-approval", "assistant", "content"),
            )


def test_event_log_writes_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    log = JsonlEventLog(path)

    log.write("run_started", {"runtime_id": "run-1", "title": "第一章"})

    event = json.loads(path.read_text(encoding="utf-8"))
    assert event["event_type"] == "run_started"
    assert event["payload"] == {"runtime_id": "run-1", "title": "第一章"}
    assert event["timestamp"]
    assert "第一章" in path.read_text(encoding="utf-8")


def test_event_log_writes_one_valid_json_object_per_line(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "events.jsonl"
    log = JsonlEventLog(path)

    log.write("run_started", {"runtime_id": "run-1"})
    log.write("node_updated", {"node_id": "n1", "status": "complete"})

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert [json.loads(line)["event_type"] for line in lines] == [
        "run_started",
        "node_updated",
    ]


def test_event_log_flushes_and_fsyncs_each_event(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    fsync_calls: list[int] = []

    def record_fsync(fd: int) -> None:
        fsync_calls.append(fd)

    with patch.object(os, "fsync", side_effect=record_fsync):
        log = JsonlEventLog(path)
        log.write("run_started", {"runtime_id": "run-1"})
        log.write("node_updated", {"node_id": "n1"})

    assert len(fsync_calls) >= 2
    assert all(isinstance(fd, int) for fd in fsync_calls)


def test_event_log_fsyncs_parent_directory_when_file_is_created(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "events.jsonl"
    fsync_calls: list[int] = []
    open_calls: list[tuple[str, int]] = []
    close_calls: list[int] = []
    directory_fd = 9001

    def record_open(
        path_like: str | bytes | os.PathLike[str] | os.PathLike[bytes], flags: int
    ) -> int:
        open_calls.append((os.fspath(path_like), flags))
        return directory_fd

    def record_fsync(fd: int) -> None:
        fsync_calls.append(fd)

    def record_close(fd: int) -> None:
        close_calls.append(fd)

    with (
        patch.object(os, "open", side_effect=record_open),
        patch.object(os, "fsync", side_effect=record_fsync),
        patch.object(os, "close", side_effect=record_close),
    ):
        JsonlEventLog(path).write("run_started", {"runtime_id": "run-1"})

    assert open_calls == [(str(path.parent), os.O_RDONLY)]
    assert close_calls == [directory_fd]
    assert len(fsync_calls) == 2
    assert fsync_calls[1] == directory_fd


def test_event_log_does_not_fsync_parent_directory_when_file_already_exists(
    tmp_path: Path,
) -> None:
    path = tmp_path / "events.jsonl"
    path.write_text("", encoding="utf-8")
    fsync_calls: list[int] = []

    def record_fsync(fd: int) -> None:
        fsync_calls.append(fd)

    with (
        patch.object(os, "open") as open_mock,
        patch.object(os, "fsync", side_effect=record_fsync),
        patch.object(os, "close") as close_mock,
    ):
        JsonlEventLog(path).write("node_updated", {"node_id": "n1"})

    open_mock.assert_not_called()
    close_mock.assert_not_called()
    assert len(fsync_calls) == 1
