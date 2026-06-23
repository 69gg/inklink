from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from types import TracebackType
from typing import cast

from pydantic import TypeAdapter

from inklink.domain.index import EntityMention, StoryIndex
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
            if version != 0 and version != SCHEMA_VERSION:
                raise UnsupportedSchemaError(f"unsupported SQLite schema version {version}")
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
        error_summary: str | None = None,
    ) -> None:
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
              ?,
              CASE WHEN ? = 'running' THEN CURRENT_TIMESTAMP ELSE NULL END,
              CASE WHEN ? IN ('completed', 'failed') THEN CURRENT_TIMESTAMP ELSE NULL END
            )
            ON CONFLICT(node_id) DO UPDATE SET
              node_type = excluded.node_type,
              status = excluded.status,
              attempt = COALESCE(?, nodes.attempt),
              idempotency_key = COALESCE(?, nodes.idempotency_key),
              input_version = COALESCE(?, nodes.input_version),
              output_version = COALESCE(?, nodes.output_version),
              error_summary = ?,
              started_at = CASE
                WHEN excluded.status = 'running' THEN CURRENT_TIMESTAMP
                ELSE nodes.started_at
              END,
              finished_at = CASE
                WHEN excluded.status IN ('completed', 'failed') THEN CURRENT_TIMESTAMP
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
                error_summary,
                status,
                status,
                attempt,
                idempotency_key,
                input_version,
                output_version,
                error_summary,
            ),
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
                  error_summary
                FROM nodes
                WHERE node_id = ?
                """,
                (node_id,),
            ).fetchone(),
        )
        if row is None:
            raise KeyError(node_id)
        return _row_to_dict(row)

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
        latest = self.get_latest_artifact(artifact_id)
        parent_version = None if latest is None else cast(int, latest["version"])
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
              approval_id,
              source_node_id,
              source_tool_call_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
              approval_id,
              source_node_id,
              source_tool_call_id
            FROM artifacts
            WHERE artifact_id = ?
        """
        parameters: tuple[object, ...] = (artifact_id,)
        if approved_only:
            sql += " AND is_approved = 1"
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
        return result

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

    def approval_messages_hash(self, approval_id: str) -> str:
        rows = self._connection.execute(
            """
            SELECT role, content_hash
            FROM messages
            WHERE approval_id = ?
            ORDER BY created_at, message_id
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
        return StoryIndex.model_validate(
            {
                "mentions": [mention.model_dump(mode="json") for mention in mentions],
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


def _schema_version(connection: sqlite3.Connection) -> int:
    return cast(int, connection.execute("PRAGMA user_version").fetchone()[0])


def _has_existing_tables(connection: sqlite3.Connection) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' LIMIT 1"
    ).fetchone()
    return row is not None
