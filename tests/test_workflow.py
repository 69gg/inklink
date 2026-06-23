from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import NoReturn, cast

import pytest
from pydantic import ValidationError

from inklink.locks import ProjectLock
from inklink.storage.events import JsonlEventLog
from inklink.storage.sqlite import StateStore
from inklink.workflow.executor import WorkflowExecutor
from inklink.workflow.models import IdempotencyInputs, NodeState, WorkflowNode, idempotency_key
from inklink.workflow.service import WorkflowService, WorkflowServiceError


def make_idempotency_inputs(
    *,
    node_type: str = "draft",
    input_version: str = "chapter-1",
    profile: str = "default",
    toolset_version: str = "tools-v1",
    prompt_version: str = "prompt-v1",
    task_parameters_hash: str = "params-a",
    approval_messages_hash: str = "chat-a",
    generation: int = 1,
) -> IdempotencyInputs:
    return IdempotencyInputs(
        node_type=node_type,
        input_version=input_version,
        profile=profile,
        toolset_version=toolset_version,
        prompt_version=prompt_version,
        task_parameters_hash=task_parameters_hash,
        approval_messages_hash=approval_messages_hash,
        generation=generation,
    )


def write_workflow_chapter(path: Path, title: str = "第一章", body: str = "正文") -> None:
    path.write_text(f"title: {title}\n---\n{body}", encoding="utf-8")


def read_workflow_events(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_runs_nodes_in_dependency_order() -> None:
    node_a = WorkflowNode(node_id="a", node_type="draft")
    node_b = WorkflowNode(node_id="b", node_type="check", depends_on=["a"])
    seen: list[str] = []

    executor = WorkflowExecutor([node_b, node_a])

    executor.run(lambda node: seen.append(node.node_id))

    assert seen == ["a", "b"]
    assert node_a.state is NodeState.COMPLETED
    assert node_b.state is NodeState.COMPLETED


def test_runs_ready_branches_in_input_order() -> None:
    root = WorkflowNode(node_id="root", node_type="outline")
    branch_b = WorkflowNode(node_id="branch-b", node_type="draft", depends_on=["root"])
    branch_a = WorkflowNode(node_id="branch-a", node_type="draft", depends_on=["root"])
    final = WorkflowNode(
        node_id="final",
        node_type="check",
        depends_on=["branch-a", "branch-b"],
    )
    seen: list[str] = []

    executor = WorkflowExecutor([branch_b, final, root, branch_a])

    executor.run(lambda node: seen.append(node.node_id))

    assert seen == ["root", "branch-b", "branch-a", "final"]


def test_generation_changes_idempotency_key() -> None:
    first = idempotency_key(make_idempotency_inputs(generation=1))
    regenerated = idempotency_key(make_idempotency_inputs(generation=2))

    assert first != regenerated


def test_approval_messages_hash_changes_idempotency_key() -> None:
    first = idempotency_key(make_idempotency_inputs(approval_messages_hash="chat-a"))
    second = idempotency_key(make_idempotency_inputs(approval_messages_hash="chat-b"))

    assert first != second


def test_task_parameters_hash_changes_idempotency_key() -> None:
    first = idempotency_key(make_idempotency_inputs(task_parameters_hash="params-a"))
    second = idempotency_key(make_idempotency_inputs(task_parameters_hash="params-b"))

    assert first != second


def test_prompt_version_changes_idempotency_key() -> None:
    first = idempotency_key(make_idempotency_inputs(prompt_version="prompt-v1"))
    second = idempotency_key(make_idempotency_inputs(prompt_version="prompt-v2"))

    assert first != second


def test_toolset_version_changes_idempotency_key() -> None:
    first = idempotency_key(make_idempotency_inputs(toolset_version="tools-v1"))
    second = idempotency_key(make_idempotency_inputs(toolset_version="tools-v2"))

    assert first != second


def test_profile_changes_idempotency_key() -> None:
    first = idempotency_key(make_idempotency_inputs(profile="fast"))
    second = idempotency_key(make_idempotency_inputs(profile="careful"))

    assert first != second


def test_idempotency_key_is_stable_and_excludes_node_id() -> None:
    first_node = WorkflowNode(node_id="a", node_type="draft")
    second_node = WorkflowNode(node_id="b", node_type="draft")

    first = first_node.idempotency_key(
        input_version="chapter-1",
        profile="default",
        toolset_version="tools-v1",
        prompt_version="prompt-v1",
        task_parameters_hash="params-a",
        approval_messages_hash="chat-a",
        generation=1,
    )
    second = second_node.idempotency_key(
        input_version="chapter-1",
        profile="default",
        toolset_version="tools-v1",
        prompt_version="prompt-v1",
        task_parameters_hash="params-a",
        approval_messages_hash="chat-a",
        generation=1,
    )

    assert first == second
    assert len(first) == 64


def test_workflow_node_idempotency_key_matches_input_model_key() -> None:
    node = WorkflowNode(node_id="draft-1", node_type="draft")

    node_key = node.idempotency_key(
        input_version="chapter-1",
        profile="default",
        toolset_version="tools-v1",
        prompt_version="prompt-v1",
        task_parameters_hash="params-a",
        approval_messages_hash="chat-a",
        generation=1,
    )

    assert node_key == idempotency_key(make_idempotency_inputs(node_type="draft"))


def test_idempotency_inputs_reject_missing_required_dimension() -> None:
    with pytest.raises(ValidationError, match="profile"):
        IdempotencyInputs.model_validate(
            {
                "node_type": "draft",
                "input_version": "chapter-1",
                "toolset_version": "tools-v1",
                "prompt_version": "prompt-v1",
                "task_parameters_hash": "params-a",
                "approval_messages_hash": "chat-a",
                "generation": 1,
            }
        )


def test_idempotency_inputs_reject_wrong_generation_type() -> None:
    with pytest.raises(ValidationError, match="generation"):
        IdempotencyInputs.model_validate(
            {
                "node_type": "draft",
                "input_version": "chapter-1",
                "profile": "default",
                "toolset_version": "tools-v1",
                "prompt_version": "prompt-v1",
                "task_parameters_hash": "params-a",
                "approval_messages_hash": "chat-a",
                "generation": "1",
            }
        )


def test_idempotency_inputs_reject_non_positive_generation() -> None:
    with pytest.raises(ValidationError, match="generation"):
        make_idempotency_inputs(generation=0)


def test_idempotency_inputs_reject_blank_profile() -> None:
    with pytest.raises(ValidationError, match="value must not be blank"):
        make_idempotency_inputs(profile=" ")


def test_rejects_duplicate_node_id() -> None:
    nodes = [
        WorkflowNode(node_id="draft", node_type="draft"),
        WorkflowNode(node_id="draft", node_type="check"),
    ]

    with pytest.raises(ValueError, match="duplicate node_id: draft"):
        WorkflowExecutor(nodes)


def test_rejects_unknown_dependency() -> None:
    node = WorkflowNode(node_id="check", node_type="check", depends_on=["draft"])

    with pytest.raises(ValueError, match="unknown dependency: draft"):
        WorkflowExecutor([node])


def test_rejects_dependency_cycle() -> None:
    node_a = WorkflowNode(node_id="a", node_type="draft", depends_on=["b"])
    node_b = WorkflowNode(node_id="b", node_type="check", depends_on=["a"])

    with pytest.raises(ValueError, match="dependency cycle"):
        WorkflowExecutor([node_a, node_b])


def test_executor_uses_dependency_snapshot_after_construction() -> None:
    root = WorkflowNode(node_id="root", node_type="outline")
    dependent = WorkflowNode(node_id="dependent", node_type="draft", depends_on=["root"])
    seen: list[str] = []

    executor = WorkflowExecutor([dependent, root])
    dependent.depends_on.clear()

    executor.run(lambda node: seen.append(node.node_id))

    assert seen == ["root", "dependent"]


def test_executor_uses_node_list_snapshot_after_construction() -> None:
    root = WorkflowNode(node_id="root", node_type="outline")
    dependent = WorkflowNode(node_id="dependent", node_type="draft", depends_on=["root"])
    nodes = [dependent, root]
    seen: list[str] = []

    executor = WorkflowExecutor(nodes)
    nodes.clear()

    executor.run(lambda node: seen.append(node.node_id))

    assert seen == ["root", "dependent"]


def test_executor_uses_node_identity_snapshot_after_construction() -> None:
    root = WorkflowNode(node_id="root", node_type="outline")
    dependent = WorkflowNode(node_id="dependent", node_type="draft", depends_on=["root"])
    seen: list[str] = []

    executor = WorkflowExecutor([dependent, root])
    dependent.node_id = "renamed"

    executor.run(lambda node: seen.append(node.node_id))

    assert seen == ["root", "renamed"]
    assert executor.state_for("dependent") is NodeState.COMPLETED


def test_runner_failure_marks_failed_and_blocks_dependents() -> None:
    draft = WorkflowNode(node_id="draft", node_type="draft")
    check = WorkflowNode(node_id="check", node_type="check", depends_on=["draft"])
    seen: list[str] = []

    def runner(node: WorkflowNode) -> None:
        seen.append(node.node_id)
        raise RuntimeError("runner failed")

    executor = WorkflowExecutor([draft, check])

    with pytest.raises(RuntimeError, match="runner failed"):
        executor.run(runner)

    assert seen == ["draft"]
    assert draft.state is NodeState.FAILED
    assert draft.attempt == 1
    assert check.state is NodeState.PENDING


def test_rerun_does_not_rerun_completed_nodes() -> None:
    draft = WorkflowNode(node_id="draft", node_type="draft")
    check = WorkflowNode(node_id="check", node_type="check", depends_on=["draft"])
    seen: list[str] = []
    executor = WorkflowExecutor([draft, check])

    executor.run(lambda node: seen.append(node.node_id))
    executor.run(lambda node: seen.append(node.node_id))

    assert seen == ["draft", "check"]
    assert draft.attempt == 1
    assert check.attempt == 1


def test_workflow_node_validation() -> None:
    with pytest.raises(ValidationError):
        WorkflowNode(node_id="", node_type="draft")
    with pytest.raises(ValidationError):
        WorkflowNode(node_id="draft", node_type=" ")
    with pytest.raises(ValidationError):
        WorkflowNode(node_id="draft", node_type="draft", attempt=-1)
    with pytest.raises(ValidationError):
        WorkflowNode.model_validate({"node_id": "draft", "node_type": "draft", "attempt": "1"})
    with pytest.raises(ValidationError, match="duplicate dependency"):
        WorkflowNode(node_id="draft", node_type="draft", depends_on=["a", "a"])
    with pytest.raises(ValidationError):
        WorkflowNode.model_validate({"node_id": "draft", "node_type": "draft", "extra_field": True})


def test_workflow_node_assignment_is_validated() -> None:
    node = WorkflowNode(node_id="draft", node_type="draft")

    with pytest.raises(ValidationError, match="attempt"):
        node.attempt = cast(int, "1")


def test_idempotency_inputs_assignment_is_validated() -> None:
    inputs = make_idempotency_inputs()

    with pytest.raises(ValidationError, match="generation"):
        inputs.generation = cast(int, "1")
    with pytest.raises(ValidationError, match="value must not be blank"):
        inputs.profile = " "


def test_failed_dependency_blocks_dependents_on_later_run() -> None:
    draft = WorkflowNode(node_id="draft", node_type="draft")
    check = WorkflowNode(node_id="check", node_type="check", depends_on=["draft"])
    executor = WorkflowExecutor([draft, check])

    def fail_draft(node: WorkflowNode) -> NoReturn:
        raise RuntimeError(node.node_id)

    with pytest.raises(RuntimeError, match="draft"):
        executor.run(fail_draft)

    executor.run(lambda node: None)

    assert check.state is NodeState.PENDING


def test_service_creates_runtime_state_events_and_loads_chapter_count(tmp_path: Path) -> None:
    project = tmp_path / "novel"
    project.mkdir()
    write_workflow_chapter(project / "1.txt", "第一章", "正文一")
    write_workflow_chapter(project / "2.txt", "第二章", "正文二")

    with WorkflowService(log_root=tmp_path / "logs") as service:
        run = service.start_run(project)

        assert run.runtime_id
        assert run.input_dir == project.resolve()
        assert run.log_dir == tmp_path / "logs" / run.runtime_id
        assert run.chapter_count == 2
        assert (run.log_dir / "state.sqlite").is_file()
        assert (run.log_dir / "events.jsonl").is_file()

        connection = sqlite3.connect(run.log_dir / "state.sqlite")
        connection.row_factory = sqlite3.Row
        try:
            row = connection.execute(
                "SELECT runtime_id, input_dir, status FROM runs WHERE runtime_id = ?",
                (run.runtime_id,),
            ).fetchone()
        finally:
            connection.close()

        assert dict(row) == {
            "runtime_id": run.runtime_id,
            "input_dir": str(project.resolve()),
            "status": "running",
        }
        events = [
            json.loads(line)
            for line in (run.log_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        assert events[0]["event_type"] == "run_started"
        assert events[0]["payload"] == {
            "runtime_id": run.runtime_id,
            "input_dir": str(project.resolve()),
            "chapter_count": 2,
        }


def test_service_blocks_duplicate_project_within_same_service(tmp_path: Path) -> None:
    project = tmp_path / "novel"
    project.mkdir()
    write_workflow_chapter(project / "1.txt")

    with WorkflowService(log_root=tmp_path / "logs") as service:
        service.start_run(project)

        check = service.can_start_run(project)

        assert check.allowed is False
        assert str(project.resolve()) in check.reason
        with pytest.raises(WorkflowServiceError, match="active run"):
            service.start_run(project)


def test_service_close_releases_project_lock_for_another_service(tmp_path: Path) -> None:
    project = tmp_path / "novel"
    project.mkdir()
    write_workflow_chapter(project / "1.txt")

    first = WorkflowService(log_root=tmp_path / "first-logs")
    first.start_run(project)
    first.close()

    with WorkflowService(log_root=tmp_path / "second-logs") as second:
        run = second.start_run(project)

    assert run.input_dir == project.resolve()


def test_service_can_resume_existing_run_by_runtime_id(tmp_path: Path) -> None:
    project = tmp_path / "novel"
    project.mkdir()
    write_workflow_chapter(project / "1.txt")
    log_root = tmp_path / "logs"

    first = WorkflowService(log_root=log_root)
    original = first.start_run(project)
    first.close()

    with WorkflowService(log_root=log_root) as second:
        resumed = second.resume_run(original.runtime_id)

        assert resumed.runtime_id == original.runtime_id
        assert resumed.input_dir == project.resolve()
        assert resumed.chapter_count == 1

    events = read_workflow_events(original.log_dir / "events.jsonl")
    assert [event["event_type"] for event in events][-1] == "run_resumed"


def test_service_resume_rejects_bad_runtime_id(tmp_path: Path) -> None:
    with (
        WorkflowService(log_root=tmp_path / "logs") as service,
        pytest.raises(ValueError, match="runtime_id"),
    ):
        service.resume_run(" bad ")


def test_service_start_failure_releases_project_lock(tmp_path: Path) -> None:
    project = tmp_path / "novel"
    project.mkdir()
    (project / "1.txt").write_text("title: 第一章\n正文缺少分隔符", encoding="utf-8")

    service = WorkflowService(log_root=tmp_path / "failed-logs")
    with pytest.raises(ValueError, match="separator"):
        service.start_run(project)
    service.close()

    write_workflow_chapter(project / "1.txt")
    with WorkflowService(log_root=tmp_path / "valid-logs") as next_service:
        run = next_service.start_run(project)

    assert run.chapter_count == 1


def test_service_command_methods_validate_inputs_and_write_events(tmp_path: Path) -> None:
    project = tmp_path / "novel"
    project.mkdir()
    write_workflow_chapter(project / "1.txt")

    with WorkflowService(log_root=tmp_path / "logs") as service:
        run = service.start_run(project)

        abandon = service.abandon_chapter(1)
        rewrite = service.rewrite_chapter(1)
        retry = service.retry_node("draft-1")

        assert abandon.accepted is True
        assert "accepted" in abandon.message
        assert "generation=2" in abandon.message
        assert rewrite.accepted is True
        assert "accepted" in rewrite.message
        assert "generation=3" in rewrite.message
        assert retry.accepted is True
        assert "accepted" in retry.message
        with pytest.raises(ValueError, match="chapter_number"):
            service.abandon_chapter(0)
        with pytest.raises(ValueError, match="chapter_number"):
            service.rewrite_chapter(-1)
        with pytest.raises(ValueError, match="node_id"):
            service.retry_node(" ")
        with pytest.raises(ValueError, match="node_id"):
            service.retry_node(" draft-1 ")

    events = read_workflow_events(run.log_dir / "events.jsonl")
    assert [event["event_type"] for event in events] == [
        "run_started",
        "chapter_abandon_requested",
        "chapter_rewrite_requested",
        "node_retry_requested",
    ]
    assert events[1]["payload"] == {
        "runtime_id": run.runtime_id,
        "chapter_number": 1,
        "next_generation": 2,
    }
    assert events[2]["payload"] == {
        "runtime_id": run.runtime_id,
        "chapter_number": 1,
        "next_generation": 3,
    }
    assert events[3]["payload"] == {"runtime_id": run.runtime_id, "node_id": "draft-1"}


def test_service_chapter_commands_reject_out_of_range_without_events(tmp_path: Path) -> None:
    project = tmp_path / "novel"
    project.mkdir()
    write_workflow_chapter(project / "1.txt")

    with WorkflowService(log_root=tmp_path / "logs") as service:
        run = service.start_run(project)

        with pytest.raises(ValueError, match="chapter_number.*1"):
            service.abandon_chapter(999)
        with pytest.raises(ValueError, match="chapter_number.*1"):
            service.rewrite_chapter(999)

    events = read_workflow_events(run.log_dir / "events.jsonl")
    assert [event["event_type"] for event in events] == ["run_started"]


def test_service_close_failure_keeps_active_run_until_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "novel"
    project.mkdir()
    write_workflow_chapter(project / "1.txt")
    service = WorkflowService(log_root=tmp_path / "logs")
    service.start_run(project)
    original_release = ProjectLock.release
    original_close = StateStore.close
    release_attempts = 0
    store_close_calls = 0
    fail_next_release = True

    def flaky_release(self: ProjectLock) -> None:
        nonlocal fail_next_release, release_attempts
        release_attempts += 1
        if fail_next_release:
            fail_next_release = False
            raise RuntimeError("release failed once")
        original_release(self)

    def record_close(self: StateStore) -> None:
        nonlocal store_close_calls
        store_close_calls += 1
        original_close(self)

    monkeypatch.setattr(ProjectLock, "release", flaky_release)
    monkeypatch.setattr(StateStore, "close", record_close)

    with pytest.raises(RuntimeError, match="release failed once"):
        service.close()

    assert service.can_start_run(project).allowed is False
    assert release_attempts == 1
    assert store_close_calls == 1

    service.close()

    assert service.can_start_run(project).allowed is True
    assert release_attempts == 2
    assert store_close_calls == 1
    with WorkflowService(log_root=tmp_path / "next-logs") as next_service:
        next_run = next_service.start_run(project)

    assert next_run.chapter_count == 1


def test_service_start_failure_from_event_log_write_cleans_up_resources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "novel"
    project.mkdir()
    write_workflow_chapter(project / "1.txt")
    original_close = StateStore.close
    store_close_calls = 0

    def record_close(self: StateStore) -> None:
        nonlocal store_close_calls
        store_close_calls += 1
        original_close(self)

    def fail_write(
        self: JsonlEventLog,
        event_type: str,
        payload: dict[str, object],
    ) -> None:
        raise RuntimeError(f"event write failed: {event_type}")

    service = WorkflowService(log_root=tmp_path / "failed-logs")
    with monkeypatch.context() as patcher:
        patcher.setattr(StateStore, "close", record_close)
        patcher.setattr(JsonlEventLog, "write", fail_write)
        with pytest.raises(RuntimeError, match="event write failed: run_started"):
            service.start_run(project)

    assert store_close_calls == 1
    service.close()
    with WorkflowService(log_root=tmp_path / "valid-logs") as next_service:
        run = next_service.start_run(project)

    assert run.chapter_count == 1
