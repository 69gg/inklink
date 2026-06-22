from __future__ import annotations

SCHEMA_VERSION = 1

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS runs (
  runtime_id TEXT PRIMARY KEY,
  input_dir TEXT NOT NULL,
  status TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS nodes (
  node_id TEXT PRIMARY KEY,
  node_type TEXT NOT NULL,
  status TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS llm_calls (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  runtime_id TEXT,
  task_type TEXT,
  model TEXT,
  usage_json TEXT,
  FOREIGN KEY(runtime_id) REFERENCES runs(runtime_id)
);

CREATE TABLE IF NOT EXISTS tool_calls (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  llm_call_id INTEGER,
  name TEXT NOT NULL,
  arguments_json TEXT NOT NULL,
  result_json TEXT,
  FOREIGN KEY(llm_call_id) REFERENCES llm_calls(id)
);

CREATE TABLE IF NOT EXISTS artifacts (
  artifact_id TEXT PRIMARY KEY,
  artifact_type TEXT NOT NULL,
  version INTEGER NOT NULL,
  payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS approvals (
  approval_id TEXT PRIMARY KEY,
  status TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
  message_id TEXT PRIMARY KEY,
  approval_id TEXT,
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  FOREIGN KEY(approval_id) REFERENCES approvals(approval_id)
);
"""
