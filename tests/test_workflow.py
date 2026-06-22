from __future__ import annotations

from typing import NoReturn

import pytest
from pydantic import ValidationError

from inklink.workflow.executor import WorkflowExecutor
from inklink.workflow.models import NodeState, WorkflowNode, idempotency_key


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
    first = idempotency_key(node_type="draft", input_version="chapter-1", generation=0)
    regenerated = idempotency_key(node_type="draft", input_version="chapter-1", generation=1)

    assert first != regenerated


def test_approval_messages_hash_changes_idempotency_key() -> None:
    first = idempotency_key(
        node_type="draft",
        input_version="chapter-1",
        approval_messages_hash="chat-a",
    )
    second = idempotency_key(
        node_type="draft",
        input_version="chapter-1",
        approval_messages_hash="chat-b",
    )

    assert first != second


@pytest.mark.parametrize(
    ("field", "first_value", "second_value"),
    [
        ("task_parameters_hash", "params-a", "params-b"),
        ("prompt_version", "prompt-v1", "prompt-v2"),
        ("toolset_version", "tools-v1", "tools-v2"),
        ("profile", "fast", "careful"),
    ],
)
def test_design_inputs_change_idempotency_key(
    field: str,
    first_value: str,
    second_value: str,
) -> None:
    base_kwargs: dict[str, object] = {
        "node_type": "draft",
        "input_version": "chapter-1",
        "profile": "default",
        "toolset_version": "tools-v1",
        "prompt_version": "prompt-v1",
        "task_parameters_hash": "params-a",
    }

    first_kwargs = base_kwargs | {field: first_value}
    second_kwargs = base_kwargs | {field: second_value}

    assert idempotency_key(**first_kwargs) != idempotency_key(**second_kwargs)


def test_idempotency_key_is_stable_and_excludes_node_id() -> None:
    first = idempotency_key(
        node_type="draft",
        input_version="chapter-1",
        node_id="a",
        task_parameters_hash="params-a",
    )
    second = idempotency_key(
        node_type="draft",
        input_version="chapter-1",
        node_id="b",
        task_parameters_hash="params-a",
    )

    assert first == second
    assert len(first) == 64


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
    with pytest.raises(ValidationError, match="duplicate dependency"):
        WorkflowNode(node_id="draft", node_type="draft", depends_on=["a", "a"])
    with pytest.raises(ValidationError):
        WorkflowNode(node_id="draft", node_type="draft", extra_field=True)


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
