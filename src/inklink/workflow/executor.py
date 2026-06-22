from __future__ import annotations

from collections.abc import Callable

from inklink.workflow.models import NodeState, WorkflowNode

WorkflowRunner = Callable[[WorkflowNode], None]


class WorkflowExecutor:
    def __init__(self, nodes: list[WorkflowNode]) -> None:
        self._nodes = nodes
        self._nodes_by_id: dict[str, WorkflowNode] = {}
        for node in nodes:
            if node.node_id in self._nodes_by_id:
                raise ValueError(f"duplicate node_id: {node.node_id}")
            self._nodes_by_id[node.node_id] = node
        for node in nodes:
            for dependency in node.depends_on:
                if dependency not in self._nodes_by_id:
                    raise ValueError(f"unknown dependency: {dependency}")
        self._reject_cycles()

    def run(self, runner: WorkflowRunner) -> None:
        while True:
            ready_nodes = self._ready_nodes()
            if not ready_nodes:
                return
            for node in ready_nodes:
                self._run_node(node, runner)

    def state_for(self, node_id: str) -> NodeState:
        return self._nodes_by_id[node_id].state

    def _ready_nodes(self) -> list[WorkflowNode]:
        return [
            node
            for node in self._nodes
            if node.state is NodeState.PENDING and self._dependencies_completed(node)
        ]

    def _dependencies_completed(self, node: WorkflowNode) -> bool:
        return all(
            self._nodes_by_id[dependency].state is NodeState.COMPLETED
            for dependency in node.depends_on
        )

    def _run_node(self, node: WorkflowNode, runner: WorkflowRunner) -> None:
        node.state = NodeState.RUNNING
        node.attempt += 1
        try:
            runner(node)
        except Exception:
            node.state = NodeState.FAILED
            raise
        node.state = NodeState.COMPLETED

    def _reject_cycles(self) -> None:
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node: WorkflowNode) -> None:
            if node.node_id in visited:
                return
            if node.node_id in visiting:
                raise ValueError(f"dependency cycle includes node_id: {node.node_id}")
            visiting.add(node.node_id)
            for dependency in node.depends_on:
                visit(self._nodes_by_id[dependency])
            visiting.remove(node.node_id)
            visited.add(node.node_id)

        for node in self._nodes:
            visit(node)
