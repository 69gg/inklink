from __future__ import annotations

from collections.abc import Callable

from inklink.workflow.models import NodeState, WorkflowNode

WorkflowRunner = Callable[[WorkflowNode], None]


class WorkflowExecutor:
    def __init__(self, nodes: list[WorkflowNode]) -> None:
        self._nodes = tuple(nodes)
        self._nodes_by_id: dict[str, WorkflowNode] = {}
        self._dependencies_by_id: dict[str, tuple[str, ...]] = {}
        self._node_ids_by_identity: dict[int, str] = {}
        for node in self._nodes:
            node_id = node.node_id
            if node_id in self._nodes_by_id:
                raise ValueError(f"duplicate node_id: {node_id}")
            self._nodes_by_id[node_id] = node
            self._dependencies_by_id[node_id] = tuple(node.depends_on)
            self._node_ids_by_identity[id(node)] = node_id
        for node in self._nodes:
            node_id = self._snapshot_id_for(node)
            for dependency in self._dependencies_by_id[node_id]:
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
        node_id = self._snapshot_id_for(node)
        return all(
            self._nodes_by_id[dependency].state is NodeState.COMPLETED
            for dependency in self._dependencies_by_id[node_id]
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
            node_id = self._snapshot_id_for(node)
            if node_id in visited:
                return
            if node_id in visiting:
                raise ValueError(f"dependency cycle includes node_id: {node_id}")
            visiting.add(node_id)
            for dependency in self._dependencies_by_id[node_id]:
                visit(self._nodes_by_id[dependency])
            visiting.remove(node_id)
            visited.add(node_id)

        for node in self._nodes:
            visit(node)

    def _snapshot_id_for(self, node: WorkflowNode) -> str:
        return self._node_ids_by_identity[id(node)]
