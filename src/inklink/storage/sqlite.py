from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path
from types import TracebackType
from typing import cast

from pydantic import TypeAdapter

from inklink.domain.index import EntityMention, StoryIndex, StructuredFact
from inklink.llm.types import NormalizedUsage
from inklink.storage.schema import SCHEMA_SQL, SCHEMA_VERSION


class UnsupportedSchemaError(RuntimeError):
    pass


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
            version = _schema_version(connection)
            if version == 0 and _has_existing_tables(connection):
                message = (
                    "unsupported SQLite schema version 0; recreate the state database "
                    "or run a future migration before opening it"
                )
                raise UnsupportedSchemaError(message)
            if version > SCHEMA_VERSION:
                raise UnsupportedSchemaError(f"unsupported SQLite schema version {version}")
            if version not in {0, SCHEMA_VERSION}:
                _migrate_schema(connection, version)
            connection.executescript(SCHEMA_SQL)
            if version == 0:
                connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
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

    def update_run_status(self, runtime_id: str, status: str) -> None:
        self._connection.execute(
            """
            UPDATE runs
            SET status = ?, updated_at = CURRENT_TIMESTAMP
            WHERE runtime_id = ?
            """,
            (status, runtime_id),
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

    def upsert_node(
        self,
        node_id: str,
        node_type: str,
        status: str,
        *,
        attempt: int | None = None,
        idempotency_key: str | None = None,
        input_version: str | None = None,
        output_version: str | None = None,
        depends_on: list[str] | None = None,
        waiting_reason: str | None = None,
        error_summary: str | None = None,
    ) -> None:
        depends_on_json = _dump_json(sorted(set(depends_on))) if depends_on is not None else None
        self._connection.execute(
            """
            INSERT INTO nodes(
              node_id,
              node_type,
              status,
              attempt,
              idempotency_key,
              input_version,
              output_version,
              depends_on_json,
              waiting_reason,
              error_summary,
              started_at,
              finished_at
            )
            VALUES (
              ?,
              ?,
              ?,
              COALESCE(?, 0),
              ?,
              ?,
              ?,
              COALESCE(?, '[]'),
              ?,
              ?,
              CASE WHEN ? = 'running' THEN CURRENT_TIMESTAMP ELSE NULL END,
              CASE WHEN ? IN ('completed', 'failed', 'waiting') THEN CURRENT_TIMESTAMP ELSE NULL END
            )
            ON CONFLICT(node_id) DO UPDATE SET
              node_type = excluded.node_type,
              status = excluded.status,
              attempt = COALESCE(?, nodes.attempt),
              idempotency_key = COALESCE(?, nodes.idempotency_key),
              input_version = COALESCE(?, nodes.input_version),
              output_version = COALESCE(?, nodes.output_version),
              depends_on_json = COALESCE(?, nodes.depends_on_json),
              waiting_reason = ?,
              error_summary = ?,
              started_at = CASE
                WHEN excluded.status = 'running' THEN CURRENT_TIMESTAMP
                ELSE nodes.started_at
              END,
              finished_at = CASE
                WHEN excluded.status IN ('completed', 'failed', 'waiting') THEN CURRENT_TIMESTAMP
                ELSE nodes.finished_at
              END
            """,
            (
                node_id,
                node_type,
                status,
                attempt,
                idempotency_key,
                input_version,
                output_version,
                depends_on_json,
                waiting_reason,
                error_summary,
                status,
                status,
                attempt,
                idempotency_key,
                input_version,
                output_version,
                depends_on_json,
                waiting_reason,
                error_summary,
            ),
        )
        self._connection.commit()

    def record_node_artifact(
        self,
        *,
        node_id: str,
        artifact_id: str,
        artifact_version: int,
        direction: str,
    ) -> None:
        if direction not in {"input", "output"}:
            raise ValueError("direction must be input or output")
        self._connection.execute(
            """
            INSERT OR IGNORE INTO node_artifacts(
              node_id,
              artifact_id,
              artifact_version,
              direction
            )
            VALUES (?, ?, ?, ?)
            """,
            (node_id, artifact_id, artifact_version, direction),
        )
        self._connection.commit()

    def get_node(self, node_id: str) -> dict[str, object]:
        row = cast(
            sqlite3.Row | None,
            self._connection.execute(
                """
                SELECT
                  node_id,
                  node_type,
                  status,
                  attempt,
                  idempotency_key,
                  input_version,
                  output_version,
                  depends_on_json,
                  waiting_reason,
                  error_summary
                FROM nodes
                WHERE node_id = ?
                """,
                (node_id,),
            ).fetchone(),
        )
        if row is None:
            raise KeyError(node_id)
        return _node_row_to_dict(row)

    def list_nodes(self) -> list[dict[str, object]]:
        rows = self._connection.execute(
            """
            SELECT
              node_id,
              node_type,
              status,
              attempt,
              idempotency_key,
              input_version,
              output_version,
              depends_on_json,
              waiting_reason,
              error_summary
            FROM nodes
            ORDER BY node_id
            """
        ).fetchall()
        return [_node_row_to_dict(row) for row in rows]

    def get_successful_tool_payload(
        self,
        *,
        idempotency_key: str,
        tool_name: str,
    ) -> dict[str, object] | None:
        row = cast(
            sqlite3.Row | None,
            self._connection.execute(
                """
                SELECT
                  tc.arguments_json AS arguments_json
                FROM tool_calls tc
                JOIN llm_calls lc ON lc.id = tc.llm_call_id
                WHERE tc.idempotency_key = ?
                  AND tc.name = ?
                  AND lc.status = 'succeeded'
                  AND tc.result_json IS NOT NULL
                ORDER BY tc.id DESC
                LIMIT 1
                """,
                (idempotency_key, tool_name),
            ).fetchone(),
        )
        if row is None:
            return None
        payload = json.loads(cast(str, row["arguments_json"]))
        if not isinstance(payload, dict):
            raise ValueError("cached tool arguments must be a JSON object")
        return cast(dict[str, object], payload)

    def next_llm_attempt(self, idempotency_key: str) -> int:
        row = cast(
            sqlite3.Row,
            self._connection.execute(
                """
                SELECT COALESCE(MAX(attempt), 0) AS attempt
                FROM llm_calls
                WHERE idempotency_key = ?
                """,
                (idempotency_key,),
            ).fetchone(),
        )
        return cast(int, row["attempt"]) + 1

    def create_llm_call(
        self,
        *,
        runtime_id: str,
        idempotency_key: str,
        task_type: str,
        profile: str,
        api_type: str,
        model: str,
        attempt: int,
        request: dict[str, object],
    ) -> int:
        cursor = self._connection.execute(
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
            VALUES (?, ?, ?, ?, ?, ?, 'running', ?, ?)
            """,
            (
                runtime_id,
                idempotency_key,
                task_type,
                profile,
                api_type,
                model,
                attempt,
                _dump_json(request),
            ),
        )
        self._connection.commit()
        return cast(int, cursor.lastrowid)

    def complete_llm_call(
        self,
        *,
        call_id: int,
        request_id: str | None,
        response: dict[str, object],
        usage: NormalizedUsage,
    ) -> None:
        self._connection.execute(
            """
            UPDATE llm_calls
            SET
              status = 'succeeded',
              request_id = ?,
              response_json = ?,
              usage_json = ?,
              normalized_usage_json = ?,
              error = NULL,
              finished_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                request_id,
                _dump_json(response),
                _dump_json(usage.model_dump(mode="json", exclude_none=True)),
                _dump_json(usage.model_dump(mode="json", exclude_none=True)),
                call_id,
            ),
        )
        self._connection.commit()

    def fail_llm_call(self, *, call_id: int, error: str) -> None:
        self._connection.execute(
            """
            UPDATE llm_calls
            SET status = 'failed', error = ?, finished_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (error, call_id),
        )
        self._connection.commit()

    def record_tool_call(
        self,
        *,
        llm_call_id: int,
        idempotency_key: str,
        name: str,
        arguments: dict[str, object],
        result: dict[str, object],
        call_id: str | None = None,
    ) -> int:
        cursor = self._connection.execute(
            """
            INSERT INTO tool_calls(
              llm_call_id,
              idempotency_key,
              name,
              call_id,
              arguments_json,
              result_json
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                llm_call_id,
                idempotency_key,
                name,
                call_id,
                _dump_json(arguments),
                _dump_json(result),
            ),
        )
        self._connection.commit()
        return cast(int, cursor.lastrowid)

    def upsert_artifact(
        self,
        *,
        artifact_id: str,
        artifact_type: str,
        payload: object,
        is_draft: bool = False,
        is_approved: bool = False,
        approval_id: str | None = None,
        source_node_id: str | None = None,
        source_tool_call_id: int | None = None,
    ) -> int:
        parent_version = self._latest_artifact_version(artifact_id)
        version = 1 if parent_version is None else parent_version + 1
        self._connection.execute(
            """
            INSERT INTO artifacts(
              artifact_id,
              artifact_type,
              version,
              parent_version,
              payload_json,
              is_draft,
              is_approved,
              is_invalidated,
              approval_id,
              source_node_id,
              source_tool_call_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
            """,
            (
                artifact_id,
                artifact_type,
                version,
                parent_version,
                _dump_json(payload),
                int(is_draft),
                int(is_approved),
                approval_id,
                source_node_id,
                source_tool_call_id,
            ),
        )
        self._connection.commit()
        return version

    def _latest_artifact_version(self, artifact_id: str) -> int | None:
        row = cast(
            sqlite3.Row | None,
            self._connection.execute(
                """
                SELECT version
                FROM artifacts
                WHERE artifact_id = ?
                ORDER BY version DESC
                LIMIT 1
                """,
                (artifact_id,),
            ).fetchone(),
        )
        if row is None:
            return None
        return cast(int, row["version"])

    def get_latest_artifact(
        self,
        artifact_id: str,
        *,
        approved_only: bool = False,
    ) -> dict[str, object] | None:
        sql = """
            SELECT
              artifact_id,
              artifact_type,
              version,
              parent_version,
              payload_json,
              is_draft,
              is_approved,
              is_invalidated,
              approval_id,
              source_node_id,
              source_tool_call_id
            FROM artifacts
            WHERE artifact_id = ?
        """
        parameters: tuple[object, ...] = (artifact_id,)
        if approved_only:
            sql += " AND is_approved = 1"
        sql += " AND is_invalidated = 0"
        sql += " ORDER BY version DESC LIMIT 1"
        row = cast(sqlite3.Row | None, self._connection.execute(sql, parameters).fetchone())
        if row is None:
            return None
        result = _row_to_dict(row)
        payload = json.loads(cast(str, result["payload_json"]))
        result["payload"] = payload
        del result["payload_json"]
        result["is_draft"] = bool(result["is_draft"])
        result["is_approved"] = bool(result["is_approved"])
        result["is_invalidated"] = bool(result["is_invalidated"])
        return result

    def list_artifacts(self) -> list[dict[str, object]]:
        rows = self._connection.execute(
            """
            SELECT
              artifact_id,
              artifact_type,
              version,
              parent_version,
              is_draft,
              is_approved,
              is_invalidated,
              approval_id,
              source_node_id,
              source_tool_call_id,
              created_at
            FROM artifacts
            ORDER BY artifact_id, version
            """
        ).fetchall()
        results: list[dict[str, object]] = []
        for row in rows:
            item = _row_to_dict(row)
            item["is_draft"] = bool(item["is_draft"])
            item["is_approved"] = bool(item["is_approved"])
            item["is_invalidated"] = bool(item["is_invalidated"])
            results.append(item)
        return results

    def get_artifact_version(self, artifact_id: str, version: int) -> dict[str, object]:
        row = cast(
            sqlite3.Row | None,
            self._connection.execute(
                """
                SELECT
                  artifact_id,
                  artifact_type,
                  version,
                  parent_version,
                  payload_json,
                  is_draft,
                  is_approved,
                  is_invalidated,
                  approval_id,
                  source_node_id,
                  source_tool_call_id,
                  created_at
                FROM artifacts
                WHERE artifact_id = ? AND version = ?
                """,
                (artifact_id, version),
            ).fetchone(),
        )
        if row is None:
            raise KeyError(f"{artifact_id}@{version}")
        result = _row_to_dict(row)
        result["payload"] = json.loads(cast(str, result["payload_json"]))
        del result["payload_json"]
        result["is_draft"] = bool(result["is_draft"])
        result["is_approved"] = bool(result["is_approved"])
        result["is_invalidated"] = bool(result["is_invalidated"])
        return result

    def approve_artifact_version(self, artifact_id: str, version: int) -> None:
        artifact = self.get_artifact_version(artifact_id, version)
        if artifact["is_invalidated"]:
            raise ValueError(f"cannot approve invalidated artifact: {artifact_id}@{version}")
        self._connection.execute(
            """
            UPDATE artifacts
            SET is_draft = 0, is_approved = 1
            WHERE artifact_id = ? AND version = ?
            """,
            (artifact_id, version),
        )
        self._connection.commit()

    def invalidate_artifacts_from_chapter(self, chapter_number: int) -> list[str]:
        if chapter_number <= 0:
            raise ValueError("chapter_number must be positive")
        rows = self._connection.execute(
            """
            SELECT DISTINCT artifact_id
            FROM artifacts
            WHERE is_invalidated = 0
            ORDER BY artifact_id
            """
        ).fetchall()
        artifact_ids = [
            str(row["artifact_id"])
            for row in rows
            if _artifact_depends_on_chapter(str(row["artifact_id"]), chapter_number)
        ]
        if artifact_ids:
            placeholders = ",".join("?" for _ in artifact_ids)
            self._connection.execute(
                f"""
                UPDATE artifacts
                SET is_invalidated = 1, is_approved = 0
                WHERE artifact_id IN ({placeholders})
                """,
                tuple(artifact_ids),
            )
            self._connection.commit()
        return artifact_ids

    def invalidate_nodes_from_chapter(self, chapter_number: int) -> list[str]:
        if chapter_number <= 0:
            raise ValueError("chapter_number must be positive")
        rows = self._connection.execute(
            """
            SELECT node_id
            FROM nodes
            WHERE status != 'invalidated'
            ORDER BY node_id
            """
        ).fetchall()
        node_ids = [
            str(row["node_id"])
            for row in rows
            if _node_depends_on_chapter(str(row["node_id"]), chapter_number)
        ]
        for node_id in node_ids:
            self._connection.execute(
                """
                UPDATE nodes
                SET
                  status = 'invalidated',
                  error_summary = ?,
                  finished_at = CURRENT_TIMESTAMP
                WHERE node_id = ?
                """,
                (f"invalidated by chapter {chapter_number} generation change", node_id),
            )
        if node_ids:
            self._connection.commit()
        return node_ids

    def create_or_update_approval(
        self,
        *,
        approval_id: str,
        approval_type: str,
        status: str,
        auto_approve: bool,
        artifact_id: str | None = None,
        artifact_version: int | None = None,
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO approvals(
              approval_id,
              approval_type,
              artifact_id,
              artifact_version,
              status,
              auto_approve
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(approval_id) DO UPDATE SET
              approval_type = excluded.approval_type,
              artifact_id = excluded.artifact_id,
              artifact_version = excluded.artifact_version,
              status = excluded.status,
              auto_approve = excluded.auto_approve,
              updated_at = CURRENT_TIMESTAMP
            """,
            (
                approval_id,
                approval_type,
                artifact_id,
                artifact_version,
                status,
                int(auto_approve),
            ),
        )
        self._connection.commit()

    def list_approvals(self) -> list[dict[str, object]]:
        rows = self._connection.execute(
            """
            SELECT
              approval_id,
              approval_type,
              artifact_id,
              artifact_version,
              status,
              auto_approve,
              created_at,
              updated_at
            FROM approvals
            ORDER BY approval_id
            """
        ).fetchall()
        results: list[dict[str, object]] = []
        for row in rows:
            item = _row_to_dict(row)
            item["auto_approve"] = bool(item["auto_approve"])
            results.append(item)
        return results

    def get_approval(self, approval_id: str) -> dict[str, object] | None:
        row = cast(
            sqlite3.Row | None,
            self._connection.execute(
                """
                SELECT
                  approval_id,
                  approval_type,
                  artifact_id,
                  artifact_version,
                  status,
                  auto_approve,
                  created_at,
                  updated_at
                FROM approvals
                WHERE approval_id = ?
                """,
                (approval_id,),
            ).fetchone(),
        )
        if row is None:
            return None
        item = _row_to_dict(row)
        item["auto_approve"] = bool(item["auto_approve"])
        return item

    def add_message(
        self,
        *,
        message_id: str,
        approval_id: str,
        role: str,
        content: str,
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO messages(message_id, approval_id, role, content, content_hash)
            VALUES (?, ?, ?, ?, ?)
            """,
            (message_id, approval_id, role, content, _hash_text(content)),
        )
        self._connection.commit()

    def list_messages(self, approval_id: str | None = None) -> list[dict[str, object]]:
        if approval_id is None:
            rows = self._connection.execute(
                """
                SELECT message_id, approval_id, role, content, content_hash, created_at
                FROM messages
                ORDER BY rowid
                """
            ).fetchall()
        else:
            rows = self._connection.execute(
                """
                SELECT message_id, approval_id, role, content, content_hash, created_at
                FROM messages
                WHERE approval_id = ?
                ORDER BY rowid
                """,
                (approval_id,),
            ).fetchall()
        return [_row_to_dict(row) for row in rows]

    def approval_messages_hash(self, approval_id: str) -> str:
        rows = self._connection.execute(
            """
            SELECT role, content_hash
            FROM messages
            WHERE approval_id = ?
            ORDER BY rowid
            """,
            (approval_id,),
        ).fetchall()
        payload = [(row["role"], row["content_hash"]) for row in rows]
        return _hash_json(payload)

    def get_chapter_generation(self, chapter_number: int) -> int:
        row = cast(
            sqlite3.Row | None,
            self._connection.execute(
                "SELECT generation FROM chapter_generations WHERE chapter_number = ?",
                (chapter_number,),
            ).fetchone(),
        )
        if row is None:
            return 1
        return cast(int, row["generation"])

    def set_chapter_generation(self, chapter_number: int, generation: int) -> None:
        if chapter_number <= 0:
            raise ValueError("chapter_number must be positive")
        if generation <= 0:
            raise ValueError("generation must be positive")
        self._connection.execute(
            """
            INSERT INTO chapter_generations(chapter_number, generation)
            VALUES (?, ?)
            ON CONFLICT(chapter_number) DO UPDATE SET generation = excluded.generation
            """,
            (chapter_number, generation),
        )
        self._connection.commit()

    def increment_chapter_generation(self, chapter_number: int) -> int:
        current = self.get_chapter_generation(chapter_number)
        next_generation = current + 1
        self.abandon_generation(chapter_number=chapter_number, generation=current)
        self.set_chapter_generation(chapter_number, next_generation)
        return next_generation

    def upsert_entity_mentions(
        self,
        mentions: list[EntityMention],
        *,
        source: str,
    ) -> None:
        for mention in mentions:
            self._connection.execute(
                """
                INSERT INTO entity_mentions(
                  entity_id,
                  chapter_number,
                  generation,
                  strength,
                  source,
                  payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(entity_id, chapter_number, generation, source) DO UPDATE SET
                  strength = excluded.strength,
                  payload_json = excluded.payload_json
                """,
                (
                    mention.entity_id,
                    mention.chapter_number,
                    mention.generation,
                    mention.strength,
                    source,
                    mention.model_dump_json(),
                ),
            )
        self._connection.commit()

    def upsert_structured_facts(
        self,
        facts: list[StructuredFact],
        *,
        source: str,
    ) -> None:
        for fact in facts:
            self._connection.execute(
                """
                INSERT INTO structured_facts(
                  fact_id,
                  kind,
                  chapter_number,
                  generation,
                  priority,
                  payload_json,
                  source
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(fact_id, chapter_number, generation, source) DO UPDATE SET
                  kind = excluded.kind,
                  priority = excluded.priority,
                  payload_json = excluded.payload_json
                """,
                (
                    fact.fact_id,
                    fact.kind,
                    fact.chapter_number,
                    fact.generation,
                    fact.priority,
                    fact.model_dump_json(),
                    source,
                ),
            )
        self._connection.commit()

    def abandon_generation(self, *, chapter_number: int, generation: int) -> None:
        if chapter_number <= 0:
            raise ValueError("chapter_number must be positive")
        if generation <= 0:
            raise ValueError("generation must be positive")
        self._connection.execute(
            """
            INSERT OR IGNORE INTO abandoned_generations(chapter_number, generation)
            VALUES (?, ?)
            """,
            (chapter_number, generation),
        )
        self._connection.commit()

    def load_story_index(self) -> StoryIndex:
        mention_rows = self._connection.execute(
            """
            SELECT payload_json
            FROM entity_mentions
            ORDER BY entity_id, chapter_number, generation, source
            """
        ).fetchall()
        abandoned_rows = self._connection.execute(
            """
            SELECT chapter_number, generation
            FROM abandoned_generations
            ORDER BY chapter_number, generation
            """
        ).fetchall()
        mentions = [
            EntityMention.model_validate_json(cast(str, row["payload_json"]))
            for row in mention_rows
        ]
        fact_rows = self._connection.execute(
            """
            SELECT payload_json
            FROM structured_facts
            ORDER BY fact_id, chapter_number, generation, source
            """
        ).fetchall()
        facts = [
            StructuredFact.model_validate_json(cast(str, row["payload_json"])) for row in fact_rows
        ]
        return StoryIndex.model_validate(
            {
                "mentions": [mention.model_dump(mode="json") for mention in mentions],
                "facts": [fact.model_dump(mode="json") for fact in facts],
                "abandoned_generations": [
                    [row["chapter_number"], row["generation"]] for row in abandoned_rows
                ],
            }
        )

    def usage_summary(self) -> list[dict[str, object]]:
        rows = self._connection.execute(
            """
            SELECT profile, model, task_type, normalized_usage_json
            FROM llm_calls
            WHERE status = 'succeeded'
            ORDER BY id
            """
        ).fetchall()
        return [_row_to_dict(row) for row in rows]


def _row_to_dict(row: sqlite3.Row) -> dict[str, object]:
    return dict(row)


def _node_row_to_dict(row: sqlite3.Row) -> dict[str, object]:
    item = _row_to_dict(row)
    depends_on_raw = item.pop("depends_on_json", "[]")
    depends_on = json.loads(str(depends_on_raw))
    if not isinstance(depends_on, list) or not all(isinstance(value, str) for value in depends_on):
        raise ValueError("node depends_on_json must decode to a list of strings")
    item["depends_on"] = depends_on
    return item


def _dump_json(payload: object) -> str:
    TypeAdapter(object).validate_python(payload)
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _hash_json(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def _migrate_schema(connection: sqlite3.Connection, version: int) -> None:
    if version == 2:
        columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(artifacts)").fetchall()
        }
        if "is_invalidated" not in columns:
            connection.execute(
                "ALTER TABLE artifacts ADD COLUMN is_invalidated INTEGER NOT NULL DEFAULT 0"
            )
        version = 3
    if version == 3:
        nodes_exists = (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'nodes'"
            ).fetchone()
            is not None
        )
        if nodes_exists:
            columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(nodes)").fetchall()
            }
            if "depends_on_json" not in columns:
                connection.execute(
                    "ALTER TABLE nodes ADD COLUMN depends_on_json TEXT NOT NULL DEFAULT '[]'"
                )
            if "waiting_reason" not in columns:
                connection.execute("ALTER TABLE nodes ADD COLUMN waiting_reason TEXT")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS node_artifacts (
              node_id TEXT NOT NULL,
              artifact_id TEXT NOT NULL,
              artifact_version INTEGER NOT NULL,
              direction TEXT NOT NULL CHECK(direction IN ('input', 'output')),
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              PRIMARY KEY(node_id, artifact_id, artifact_version, direction),
              FOREIGN KEY(node_id) REFERENCES nodes(node_id)
            )
            """
        )
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        connection.commit()
        return
    raise UnsupportedSchemaError(f"unsupported SQLite schema version {version}")


def _schema_version(connection: sqlite3.Connection) -> int:
    return cast(int, connection.execute("PRAGMA user_version").fetchone()[0])


def _has_existing_tables(connection: sqlite3.Connection) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' LIMIT 1"
    ).fetchone()
    return row is not None


def _artifact_depends_on_chapter(artifact_id: str, chapter_number: int) -> bool:
    if artifact_id in {"run_summary", "story_index", "story_state"}:
        return True
    if artifact_id.startswith("range_summary:"):
        return True
    for prefix in (
        "scene_plan:",
        "scene_draft:",
        "chapter_draft:",
        "chapter_analysis:",
    ):
        if artifact_id.startswith(prefix):
            parsed = _parse_chapter_number_after_prefix(artifact_id, prefix)
            return parsed is not None and parsed >= chapter_number
    return False


def _node_depends_on_chapter(node_id: str, chapter_number: int) -> bool:
    match = re.fullmatch(r"chapter-(\d+)", node_id)
    if match is not None:
        return int(match.group(1)) >= chapter_number
    for prefix in (
        "plan_scenes:",
        "draft_scene:",
        "assemble_chapter:",
        "check_chapter:",
        "review_chapter:",
        "revise_chapter:",
        "integrate_generated_chapter:",
        "write_output:",
    ):
        if node_id.startswith(prefix):
            parsed = _parse_chapter_number_after_prefix(node_id, prefix)
            return parsed is not None and parsed >= chapter_number
    return False


def _parse_chapter_number_after_prefix(value: str, prefix: str) -> int | None:
    suffix = value.removeprefix(prefix)
    token = suffix.split(":", maxsplit=1)[0]
    if not token.isascii() or not token.isdecimal():
        return None
    return int(token)
