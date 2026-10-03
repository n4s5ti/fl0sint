"""Capture-only graph repository for isolated acquisition.

The repository records graph-publication candidates locally.  It never creates
Neo4j element identifiers, reads graph state, or delegates an operation to a
live repository.  Every captured candidate is explicitly unreviewed and
carries the input lineage active when it was discovered.
"""

from __future__ import annotations

import copy
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal, NoReturn

from .types import GraphDict


@dataclass(frozen=True)
class CapturedOperation:
    """One local, unreviewed graph-publication candidate."""

    sequence: int
    captured_at: str
    method_name: str
    parameters: dict[str, Any]
    source_input: Any | None
    operation_type: str
    disposition: Literal["unreviewed"] = "unreviewed"
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable snapshot without a graph element ID."""
        return {
            "sequence": self.sequence,
            "captured_at": self.captured_at,
            "method_name": self.method_name,
            "parameters": copy.deepcopy(self.parameters),
            "source_input": copy.deepcopy(self.source_input),
            "operation_type": self.operation_type,
            "disposition": self.disposition,
            "error": self.error,
        }


class CaptureGraphRepository:
    """Capture safe creation candidates without a live graph dependency."""

    def __init__(self, sketch_id: str = "") -> None:
        self.sketch_id = sketch_id
        self.operations: list[CapturedOperation] = []
        self.batch_queue: list[int] = []
        self.batch_size = 10
        self._source_input: Any | None = None
        self._sequence = 0

    @contextmanager
    def source_context(self, source_input: Any) -> Generator[None, None, None]:
        """Attach one acquisition input to candidates produced in this scope."""
        previous = self._source_input
        self._source_input = copy.deepcopy(source_input)
        try:
            yield
        finally:
            self._source_input = previous

    def _capture(
        self,
        method_name: str,
        parameters: dict[str, Any],
        *,
        operation_type: str,
        error: str | None = None,
    ) -> CapturedOperation:
        self._sequence += 1
        operation = CapturedOperation(
            sequence=self._sequence,
            captured_at=datetime.now(timezone.utc).isoformat(),
            method_name=method_name,
            parameters=copy.deepcopy(parameters),
            source_input=copy.deepcopy(self._source_input),
            operation_type=operation_type,
            error=error,
        )
        self.operations.append(operation)
        return operation

    def _capture_candidate(
        self, method_name: str, parameters: dict[str, Any]
    ) -> CapturedOperation:
        return self._capture(
            method_name,
            parameters,
            operation_type="candidate_graph_mutation",
        )

    def _unsupported(self, method_name: str, parameters: dict[str, Any]) -> NoReturn:
        message = (
            f"Unsupported method '{method_name}' in capture-only repository; "
            "capture mode cannot read, mutate, or publish graph state."
        )
        self._capture(
            method_name,
            parameters,
            operation_type="unsupported_publication_intent",
            error=message,
        )
        raise NotImplementedError(message)

    def create_node(self, node_obj: GraphDict, sketch_id: str) -> str | None:
        """Record a node candidate, without manufacturing a graph element ID."""
        self._capture_candidate(
            "create_node", {"node_obj": node_obj, "sketch_id": sketch_id}
        )
        return None

    def create_relationship(self, rel_obj: GraphDict, sketch_id: str) -> None:
        """Record a relationship candidate without publishing it."""
        self._capture_candidate(
            "create_relationship", {"rel_obj": rel_obj, "sketch_id": sketch_id}
        )

    def batch_create_nodes(
        self, nodes: list[GraphDict], sketch_id: str
    ) -> dict[str, Any]:
        """Record each batch member as an unreviewed node candidate."""
        for node in nodes:
            self.create_node(node, sketch_id)
        return {"candidates": len(nodes), "disposition": "unreviewed"}

    def batch_create_edges_by_element_id(
        self, edges: list[GraphDict], sketch_id: str
    ) -> dict[str, Any]:
        """Record edge candidates only when no published element IDs are needed."""
        for edge in edges:
            self.create_relationship(edge, sketch_id)
        return {"candidates": len(edges), "disposition": "unreviewed"}

    def add_to_batch(self, operation_type: str, **kwargs: Any) -> None:
        """Capture an enqueued create candidate and retain its flush lineage."""
        method_by_type = {
            "node": "create_node",
            "relationship": "create_relationship",
        }
        method_name = method_by_type.get(operation_type)
        if method_name is None:
            self._unsupported(
                "add_to_batch", {"operation_type": operation_type, **kwargs}
            )
        operation = self._capture_candidate(method_name, kwargs)
        self.batch_queue.append(operation.sequence)
        if len(self.batch_queue) >= self.batch_size:
            self.flush_batch()

    def flush_batch(self) -> None:
        """Record a local flush boundary and discard no captured candidates."""
        if not self.batch_queue:
            return
        self._capture(
            "flush_batch",
            {"candidate_sequences": list(self.batch_queue)},
            operation_type="capture_batch_flush",
        )
        self.batch_queue.clear()

    def set_batch_size(self, size: int) -> None:
        """Configure a positive local auto-flush threshold."""
        if type(size) is not int or size <= 0:
            raise ValueError("batch size must be a positive integer")
        self.batch_size = size

    def update_node(
        self, element_id: str, updates: GraphDict, sketch_id: str
    ) -> str | None:
        self._unsupported(
            "update_node",
            {"element_id": element_id, "updates": updates, "sketch_id": sketch_id},
        )

    def delete_nodes(self, node_ids: list[str], sketch_id: str) -> int:
        self._unsupported(
            "delete_nodes", {"node_ids": node_ids, "sketch_id": sketch_id}
        )

    def delete_all_sketch_nodes(self, sketch_id: str) -> int:
        self._unsupported("delete_all_sketch_nodes", {"sketch_id": sketch_id})

    def get_nodes_by_ids(
        self, node_ids: list[str], sketch_id: str
    ) -> list[dict[str, Any]]:
        self._unsupported(
            "get_nodes_by_ids", {"node_ids": node_ids, "sketch_id": sketch_id}
        )

    def update_nodes_positions(
        self, positions: list[dict[str, Any]], sketch_id: str
    ) -> int:
        self._unsupported(
            "update_nodes_positions", {"positions": positions, "sketch_id": sketch_id}
        )

    def create_relationship_by_element_id(
        self,
        from_element_id: str,
        to_element_id: str,
        rel_label: str,
        sketch_id: str,
    ) -> dict[str, Any] | None:
        self._unsupported(
            "create_relationship_by_element_id",
            {
                "from_element_id": from_element_id,
                "to_element_id": to_element_id,
                "rel_label": rel_label,
                "sketch_id": sketch_id,
            },
        )

    def update_relationship(
        self, element_id: str, rel_obj: GraphDict, sketch_id: str
    ) -> dict[str, Any] | None:
        self._unsupported(
            "update_relationship",
            {"element_id": element_id, "rel_obj": rel_obj, "sketch_id": sketch_id},
        )

    def delete_relationships(self, relationship_ids: list[str], sketch_id: str) -> int:
        self._unsupported(
            "delete_relationships",
            {"relationship_ids": relationship_ids, "sketch_id": sketch_id},
        )

    def get_sketch_graph(
        self, sketch_id: str, limit: int = 100000
    ) -> dict[str, list[dict[str, Any]]]:
        self._unsupported("get_sketch_graph", {"sketch_id": sketch_id, "limit": limit})

    def get_neighbors(self, node_id: str, sketch_id: str) -> dict[str, Any]:
        self._unsupported("get_neighbors", {"node_id": node_id, "sketch_id": sketch_id})

    def merge_nodes(
        self,
        old_node_ids: list[str],
        new_node_data: dict[str, Any],
        new_node_id: str | None,
        sketch_id: str,
    ) -> str | None:
        self._unsupported(
            "merge_nodes",
            {
                "old_node_ids": old_node_ids,
                "new_node_data": new_node_data,
                "new_node_id": new_node_id,
                "sketch_id": sketch_id,
            },
        )

    def query(
        self, cypher: str, parameters: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        self._unsupported("query", {"cypher": cypher, "parameters": parameters or {}})

    def get_captured_operations(self) -> tuple[CapturedOperation, ...]:
        """Return all audit records, including rejected publication intent."""
        return tuple(self.operations)

    def get_captured_candidates(self) -> tuple[CapturedOperation, ...]:
        """Return only safe, unreviewed create candidates."""
        return tuple(
            operation
            for operation in self.operations
            if operation.operation_type == "candidate_graph_mutation"
        )

    def clear_captures(self) -> None:
        """Discard this process-local capture buffer without touching graph state."""
        self.operations.clear()
        self.batch_queue.clear()

    def export_captures(self) -> dict[str, Any]:
        """Serialize local audit records and candidates for caller review."""
        return {
            "sketch_id": self.sketch_id,
            "disposition": "unreviewed",
            "total_operations": len(self.operations),
            "candidate_operations": len(self.get_captured_candidates()),
            "operations": [operation.to_dict() for operation in self.operations],
            "pending_batch_candidates": list(self.batch_queue),
        }
