from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from inklink.domain.index import EntityMention, StructuredFact
from inklink.llm.types import NormalizedUsage
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

SCHEMA_V2_SQL = """
PRAGMA user_version = 2;

CREATE TABLE runs (
  runtime_id TEXT PRIMARY KEY,
  input_dir TEXT NOT NULL,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE artifacts (
  artifact_id TEXT NOT NULL,
  artifact_type TEXT NOT NULL,
  version INTEGER NOT NULL,
  parent_version INTEGER,
  payload_json TEXT NOT NULL,
  is_draft INTEGER NOT NULL DEFAULT 0,
  is_approved INTEGER NOT NULL DEFAULT 0,
  approval_id TEXT,
  source_node_id TEXT,
  source_tool_call_id INTEGER,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY(artifact_id, version)
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
            "node_artifacts",
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


def test_state_store_migrates_schema_v2_to_current(tmp_path: Path) -> None:
    db = tmp_path / "state.sqlite"
    connection = sqlite3.connect(db)
    try:
        connection.executescript(SCHEMA_V2_SQL)
        connection.commit()
    finally:
        connection.close()

    with StateStore.open(db) as store:
        version = store.upsert_artifact(
            artifact_id="outline",
            artifact_type="outline",
            payload={"outline": "初稿"},
            is_draft=True,
        )
        artifact = store.get_artifact_version("outline", version)

    connection = sqlite3.connect(db)
    try:
        user_version = connection.execute("PRAGMA user_version").fetchone()[0]
    finally:
        connection.close()

    assert user_version == SCHEMA_VERSION
    assert artifact["is_invalidated"] is False


def test_state_store_records_run_and_nodes(tmp_path: Path) -> None:
    db = tmp_path / "state.sqlite"

    with StateStore.open(db) as store:
        store.create_run(runtime_id="run-1", input_dir="/novel", status="running")
        store.upsert_node(node_id="n1", node_type="load_project", status="pending")

        assert store.get_run("run-1") == {
            "runtime_id": "run-1",
            "input_dir": "/novel",
            "status": "running",
            "settings": {},
        }
        store.update_run_settings("run-1", {"notes": "保留悬念"})
        assert store.get_run_settings("run-1") == {"notes": "保留悬念"}
        assert store.get_node("n1") == {
            "node_id": "n1",
            "node_type": "load_project",
            "status": "pending",
            "attempt": 0,
            "idempotency_key": None,
            "input_version": None,
            "output_version": None,
            "depends_on": [],
            "waiting_reason": None,
            "error_summary": None,
        }


def test_state_store_upsert_node_updates_existing_row(tmp_path: Path) -> None:
    with StateStore.open(tmp_path / "state.sqlite") as store:
        store.upsert_node(node_id="n1", node_type="load_project", status="pending")
        store.upsert_node(node_id="n1", node_type="draft_scene", status="complete")

        assert store.get_node("n1") == {
            "node_id": "n1",
            "node_type": "draft_scene",
            "status": "complete",
            "attempt": 0,
            "idempotency_key": None,
            "input_version": None,
            "output_version": None,
            "depends_on": [],
            "waiting_reason": None,
            "error_summary": None,
        }


def test_state_store_marks_running_work_failed(tmp_path: Path) -> None:
    with StateStore.open(tmp_path / "state.sqlite") as store:
        store.create_run(runtime_id="run-1", input_dir="/novel", status="running")
        store.upsert_node(node_id="running-node", node_type="draft_scene", status="running")
        store.upsert_node(node_id="done-node", node_type="load_project", status="completed")
        running_call = store.create_llm_call(
            runtime_id="run-1",
            idempotency_key="running-key",
            task_type="drafting",
            profile="default",
            api_type="responses",
            model="fake-model",
            attempt=1,
            request={},
        )
        done_call = store.create_llm_call(
            runtime_id="run-1",
            idempotency_key="done-key",
            task_type="review",
            profile="default",
            api_type="responses",
            model="fake-model",
            attempt=1,
            request={},
        )
        store.complete_llm_call(
            call_id=done_call,
            request_id="req-done",
            response={},
            usage=NormalizedUsage(total_tokens=1),
        )

        failed_nodes = store.fail_running_nodes(error_summary="run failed")
        failed_calls = store.fail_running_llm_calls(runtime_id="run-1", error="run failed")

        assert failed_nodes == 1
        assert failed_calls == 1
        assert store.get_node("running-node")["status"] == "failed"
        assert store.get_node("running-node")["error_summary"] == "run failed"
        assert store.get_node("done-node")["status"] == "completed"
        rows = store._connection.execute(
            "SELECT id, status, error FROM llm_calls ORDER BY id"
        ).fetchall()
        assert [dict(row) for row in rows] == [
            {"id": running_call, "status": "failed", "error": "run failed"},
            {"id": done_call, "status": "succeeded", "error": None},
        ]


def test_state_store_records_node_dependencies_and_waiting_reason(tmp_path: Path) -> None:
    with StateStore.open(tmp_path / "state.sqlite") as store:
        store.upsert_node(
            node_id="write_output:3",
            node_type="write_output",
            status="waiting",
            depends_on=["integrate_generated_chapter:3", "check_chapter:3"],
            waiting_reason="writeback target exists",
            input_version="chapter_draft:3@1",
        )

        node = store.get_node("write_output:3")

        assert node["status"] == "waiting"
        assert node["depends_on"] == ["check_chapter:3", "integrate_generated_chapter:3"]
        assert node["waiting_reason"] == "writeback target exists"
        assert node["input_version"] == "chapter_draft:3@1"


def test_state_store_invalidates_single_node_for_manual_retry(tmp_path: Path) -> None:
    with StateStore.open(tmp_path / "state.sqlite") as store:
        store.upsert_node(node_id="review_chapter:3", node_type="review_chapter", status="failed")

        assert store.invalidate_node("review_chapter:3", reason="manual retry requested") is True
        assert store.invalidate_node("missing", reason="manual retry requested") is False
        node = store.get_node("review_chapter:3")

    assert node["status"] == "invalidated"
    assert node["error_summary"] == "manual retry requested"


def test_state_store_raises_key_error_for_missing_records(tmp_path: Path) -> None:
    with StateStore.open(tmp_path / "state.sqlite") as store:
        with pytest.raises(KeyError, match="run-missing"):
            store.get_run("run-missing")
        with pytest.raises(KeyError, match="node-missing"):
            store.get_node("node-missing")


def test_state_store_records_llm_tool_cache_and_usage(tmp_path: Path) -> None:
    with StateStore.open(tmp_path / "state.sqlite") as store:
        store.create_run(runtime_id="run-1", input_dir="/novel", status="running")
        call_id = store.create_llm_call(
            runtime_id="run-1",
            idempotency_key="key-1",
            task_type="drafting",
            profile="default",
            api_type="responses",
            model="gpt-test",
            attempt=1,
            request={"prompt": "写一章"},
        )
        tool_id = store.record_tool_call(
            llm_call_id=call_id,
            idempotency_key="key-1",
            name="submit_scene_draft",
            arguments={"scene_id": "s1", "text": "正文"},
            result={"ok": True},
            call_id="call-1",
        )
        store.complete_llm_call(
            call_id=call_id,
            request_id="req-1",
            response={"ok": True},
            usage=NormalizedUsage(input_tokens=3, output_tokens=4, total_tokens=7),
        )

        cached = store.get_successful_tool_payload(
            idempotency_key="key-1",
            tool_name="submit_scene_draft",
        )

        assert tool_id > 0
        assert cached == {"scene_id": "s1", "text": "正文"}
        assert store.next_llm_attempt("key-1") == 2
        usage_rows = store.usage_summary()
        assert usage_rows[0]["profile"] == "default"
        assert usage_rows[0]["model"] == "gpt-test"


def test_state_store_artifacts_version_and_approval_messages(tmp_path: Path) -> None:
    with StateStore.open(tmp_path / "state.sqlite") as store:
        first_version = store.upsert_artifact(
            artifact_id="outline",
            artifact_type="outline",
            payload={"outline": "初稿"},
            is_draft=True,
        )
        second_version = store.upsert_artifact(
            artifact_id="outline",
            artifact_type="outline",
            payload={"outline": "定稿"},
            is_approved=True,
        )
        store.create_or_update_approval(
            approval_id="outline",
            approval_type="outline",
            status="waiting",
            auto_approve=False,
            artifact_id="outline",
            artifact_version=first_version,
        )
        before = store.approval_messages_hash("outline")
        store.add_message(
            message_id="msg-1",
            approval_id="outline",
            role="user",
            content="改得更紧凑",
        )
        after = store.approval_messages_hash("outline")

        latest = store.get_latest_artifact("outline")

        assert first_version == 1
        assert second_version == 2
        assert latest is not None
        assert latest["version"] == 2
        assert latest["payload"] == {"outline": "定稿"}
        assert latest["is_invalidated"] is False
        assert before != after
        artifacts = store.list_artifacts()
        assert artifacts[-1]["artifact_id"] == "outline"
        assert artifacts[-1]["is_approved"] is True
        assert store.get_artifact_version("outline", 1)["payload"] == {"outline": "初稿"}
        assert store.list_approvals()[0]["approval_id"] == "outline"
        assert store.list_messages("outline")[0]["content"] == "改得更紧凑"


def test_state_store_approves_artifact_version(tmp_path: Path) -> None:
    with StateStore.open(tmp_path / "state.sqlite") as store:
        version = store.upsert_artifact(
            artifact_id="outline",
            artifact_type="outline",
            payload={"outline": "讨论稿"},
            is_draft=True,
        )

        store.approve_artifact_version("outline", version)

        artifact = store.get_artifact_version("outline", version)
        assert artifact["is_draft"] is False
        assert artifact["is_approved"] is True


def test_state_store_invalidates_chapter_artifacts_and_nodes(tmp_path: Path) -> None:
    with StateStore.open(tmp_path / "state.sqlite") as store:
        store.upsert_node(node_id="chapter-3", node_type="chapter_generation", status="completed")
        store.upsert_node(node_id="chapter-4", node_type="chapter_generation", status="completed")
        store.upsert_node(node_id="chapter-2", node_type="chapter_generation", status="completed")
        store.upsert_artifact(
            artifact_id="scene_plan:3",
            artifact_type="scene_plan",
            payload={"chapter_number": 3, "scenes": []},
            is_approved=True,
        )
        store.upsert_artifact(
            artifact_id="chapter_draft:4",
            artifact_type="chapter_draft",
            payload={"chapter_number": 4},
            is_approved=True,
        )
        store.upsert_artifact(
            artifact_id="chapter_draft:2",
            artifact_type="chapter_draft",
            payload={"chapter_number": 2},
            is_approved=True,
        )

        invalidated_artifacts = store.invalidate_artifacts_from_chapter(3)
        invalidated_nodes = store.invalidate_nodes_from_chapter(3)

        assert invalidated_artifacts == ["chapter_draft:4", "scene_plan:3"]
        assert invalidated_nodes == ["chapter-3", "chapter-4"]
        assert store.get_latest_artifact("scene_plan:3") is None
        assert store.get_artifact_version("scene_plan:3", 1)["is_invalidated"] is True
        assert store.get_artifact_version("chapter_draft:2", 1)["is_invalidated"] is False
        assert store.get_node("chapter-3")["status"] == "invalidated"
        assert store.get_node("chapter-2")["status"] == "completed"


def test_state_store_approval_messages_hash_preserves_insertion_order(tmp_path: Path) -> None:
    with StateStore.open(tmp_path / "state.sqlite") as store:
        store.create_or_update_approval(
            approval_id="outline",
            approval_type="outline",
            status="waiting",
            auto_approve=False,
        )
        store.add_message(message_id="z-message", approval_id="outline", role="user", content="A")
        store.add_message(message_id="a-message", approval_id="outline", role="user", content="B")

        messages = store.list_messages("outline")

        assert [message["content"] for message in messages] == ["A", "B"]


def test_state_store_generation_abandon_rebuilds_story_index(tmp_path: Path) -> None:
    with StateStore.open(tmp_path / "state.sqlite") as store:
        store.upsert_entity_mentions(
            [
                EntityMention(entity_id="林青", chapter_number=3, generation=1, strength=5),
                EntityMention(entity_id="林青", chapter_number=3, generation=2, strength=1),
            ],
            source="test",
        )

        next_generation = store.increment_chapter_generation(3)
        index = store.load_story_index()

        assert next_generation == 2
        assert index.characters["林青"].active_score == 1


def test_state_store_persists_structured_facts_in_story_index(tmp_path: Path) -> None:
    with StateStore.open(tmp_path / "state.sqlite") as store:
        store.upsert_structured_facts(
            [
                StructuredFact(
                    fact_id="thread-1",
                    kind="plot_thread",
                    text="旧钥匙尚未解释",
                    chapter_number=3,
                    generation=1,
                    priority=2,
                    keywords=["旧钥匙"],
                )
            ],
            source="test",
        )

        index = store.load_story_index()

        assert [fact.text for fact in index.active_facts()] == ["旧钥匙尚未解释"]
        assert index.retrieval_items(keywords=["旧钥匙"])[0]["text"] == "旧钥匙尚未解释"


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
            INSERT INTO llm_calls(
              runtime_id,
              idempotency_key,
              task_type,
              profile,
              api_type,
              model,
              status,
              attempt,
              request_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "missing-run",
                "key",
                "drafting",
                "default",
                "responses",
                "gpt-test",
                "running",
                1,
                "{}",
            ),
        )


def test_state_store_fresh_database_foreign_keys_remain_enforced(tmp_path: Path) -> None:
    db = tmp_path / "state.sqlite"

    with StateStore.open(db):
        pass

    with StateStore.open(db) as store:
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            store._connection.execute(
                """
                INSERT INTO llm_calls(
                  runtime_id,
                  idempotency_key,
                  task_type,
                  profile,
                  api_type,
                  model,
                  status,
                  attempt,
                  request_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "missing-run",
                    "key",
                    "drafting",
                    "default",
                    "responses",
                    "gpt-test",
                    "running",
                    1,
                    "{}",
                ),
            )
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            store._connection.execute(
                """
                INSERT INTO tool_calls(
                  llm_call_id,
                  idempotency_key,
                  name,
                  arguments_json,
                  result_json
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (1, "key", "tool", "{}", "{}"),
            )
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            store._connection.execute(
                """
                INSERT INTO messages(message_id, approval_id, role, content, content_hash)
                VALUES (?, ?, ?, ?, ?)
                """,
                ("message-1", "missing-approval", "assistant", "content", "hash"),
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
