from __future__ import annotations

SCHEMA_VERSION = 2

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS runs (
  runtime_id TEXT PRIMARY KEY,
  input_dir TEXT NOT NULL,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS nodes (
  node_id TEXT PRIMARY KEY,
  node_type TEXT NOT NULL,
  status TEXT NOT NULL,
  attempt INTEGER NOT NULL DEFAULT 0,
  idempotency_key TEXT,
  input_version TEXT,
  output_version TEXT,
  error_summary TEXT,
  started_at TEXT,
  finished_at TEXT
);

CREATE TABLE IF NOT EXISTS llm_calls (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  runtime_id TEXT NOT NULL,
  idempotency_key TEXT NOT NULL,
  task_type TEXT NOT NULL,
  profile TEXT NOT NULL,
  api_type TEXT NOT NULL,
  model TEXT NOT NULL,
  status TEXT NOT NULL,
  attempt INTEGER NOT NULL,
  request_id TEXT,
  request_json TEXT NOT NULL,
  response_json TEXT,
  usage_json TEXT,
  normalized_usage_json TEXT,
  error TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  finished_at TEXT,
  FOREIGN KEY(runtime_id) REFERENCES runs(runtime_id)
);

CREATE INDEX IF NOT EXISTS idx_llm_calls_key_status
ON llm_calls(idempotency_key, status, id DESC);

CREATE TABLE IF NOT EXISTS tool_calls (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  llm_call_id INTEGER NOT NULL,
  idempotency_key TEXT NOT NULL,
  name TEXT NOT NULL,
  call_id TEXT,
  arguments_json TEXT NOT NULL,
  result_json TEXT,
  FOREIGN KEY(llm_call_id) REFERENCES llm_calls(id)
);

CREATE INDEX IF NOT EXISTS idx_tool_calls_key_name
ON tool_calls(idempotency_key, name, id DESC);

CREATE TABLE IF NOT EXISTS artifacts (
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
  PRIMARY KEY(artifact_id, version),
  FOREIGN KEY(source_tool_call_id) REFERENCES tool_calls(id)
);

CREATE INDEX IF NOT EXISTS idx_artifacts_latest
ON artifacts(artifact_id, version DESC);

CREATE TABLE IF NOT EXISTS approvals (
  approval_id TEXT PRIMARY KEY,
  approval_type TEXT NOT NULL,
  artifact_id TEXT,
  artifact_version INTEGER,
  status TEXT NOT NULL,
  auto_approve INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS messages (
  message_id TEXT PRIMARY KEY,
  approval_id TEXT,
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(approval_id) REFERENCES approvals(approval_id)
);

CREATE TABLE IF NOT EXISTS chapter_generations (
  chapter_number INTEGER PRIMARY KEY,
  generation INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS entity_mentions (
  entity_id TEXT NOT NULL,
  chapter_number INTEGER NOT NULL,
  generation INTEGER NOT NULL,
  strength INTEGER NOT NULL,
  source TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY(entity_id, chapter_number, generation, source)
);

CREATE TABLE IF NOT EXISTS abandoned_generations (
  chapter_number INTEGER NOT NULL,
  generation INTEGER NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY(chapter_number, generation)
);
"""
