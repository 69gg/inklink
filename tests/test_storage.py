from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from inklink.storage.events import JsonlEventLog
from inklink.storage.sqlite import StateStore


def test_state_store_creates_schema_tables(tmp_path: Path) -> None:
    db = tmp_path / "nested" / "state.sqlite"

    StateStore.open(db)

    connection = sqlite3.connect(db)
    connection.row_factory = sqlite3.Row
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


def test_state_store_records_run_and_nodes(tmp_path: Path) -> None:
    db = tmp_path / "state.sqlite"
    store = StateStore.open(db)

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
    store = StateStore.open(tmp_path / "state.sqlite")

    store.upsert_node(node_id="n1", node_type="load_project", status="pending")
    store.upsert_node(node_id="n1", node_type="draft_scene", status="complete")

    assert store.get_node("n1") == {
        "node_id": "n1",
        "node_type": "draft_scene",
        "status": "complete",
    }


def test_state_store_raises_key_error_for_missing_records(tmp_path: Path) -> None:
    store = StateStore.open(tmp_path / "state.sqlite")

    with pytest.raises(KeyError, match="run-missing"):
        store.get_run("run-missing")
    with pytest.raises(KeyError, match="node-missing"):
        store.get_node("node-missing")


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
