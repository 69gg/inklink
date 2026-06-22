from __future__ import annotations

from typing import NoReturn

import pytest
from pydantic import ValidationError

from inklink.workflow.executor import WorkflowExecutor
from inklink.workflow.models import IdempotencyInputs, NodeState, WorkflowNode, idempotency_key


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
