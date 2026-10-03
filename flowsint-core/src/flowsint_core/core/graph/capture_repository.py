"""
Capture-only graph repository for isolated acquisition.

This module provides a GraphRepository implementation that captures all
graph operations without writing to Neo4j. It preserves input/source lineage
and fails explicitly on unsupported methods.

Used for: Isolated local acquisition with no Neo4j requirement.
"""

from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field
from datetime import datetime
import json
import uuid

from .repository_protocol import GraphRepositoryProtocol
from .types import GraphDict


@dataclass
class CapturedOperation:
    """Records a single captured graph operation with lineage."""

    operation_id: str
    operation_type: str
    timestamp: str
    method_name: str
    parameters: Dict[str, Any]
    source_input: Optional[Any] = None
    result: Optional[Any] = None
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Serialize operation to dict."""
        return {
            "operation_id": self.operation_id,
            "operation_type": self.operation_type,
            "timestamp": self.timestamp,
            "method_name": self.method_name,
            "parameters": self.parameters,
            "source_input": self.source_input,
            "result": self.result,
            "error": self.error,
        }


class CaptureGraphRepository:
    """
    Capture-only graph repository that records operations without Neo4j writes.
    
    Features:
    - Records all graph operations with timestamps
    - Preserves input/source lineage
    - Fails explicitly on unsupported methods
    - No Neo4j connection required
    - Returns plausible IDs for captured creates
    """

    def __init__(self, sketch_id: str = ""):
        """Initialize capture repository."""
        self.sketch_id = sketch_id
        self.operations: List[CapturedOperation] = []
        self.batch_queue: List[Dict[str, Any]] = []
        self.batch_size = 10
        self.node_id_counter = 0
        self.relationship_id_counter = 0
        self._source_lineage: Dict[str, Any] = {}

    def _generate_node_id(self) -> str:
        """Generate a plausible node element ID."""
        self.node_id_counter += 1
        return f"node_{self.node_id_counter}_{uuid.uuid4().hex[:8]}"

    def _generate_rel_id(self) -> str:
        """Generate a plausible relationship element ID."""
        self.relationship_id_counter += 1
        return f"rel_{self.relationship_id_counter}_{uuid.uuid4().hex[:8]}"

    def _capture_operation(
        self,
        method_name: str,
        parameters: Dict[str, Any],
        result: Optional[Any] = None,
        error: Optional[str] = None,
    ) -> CapturedOperation:
        """Record an operation to capture list."""
        operation = CapturedOperation(
            operation_id=str(uuid.uuid4()),
            operation_type="graph_write",
            timestamp=datetime.utcnow().isoformat(),
            method_name=method_name,
            parameters=parameters,
            source_input=self._source_lineage.get(method_name),
            result=result,
            error=error,
        )
        self.operations.append(operation)
        return operation

    def _unsupported(self, method_name: str) -> None:
        """Fail explicitly on unsupported method."""
        error_msg = (
            f"Unsupported method '{method_name}' in capture-only repository. "
            f"This method cannot be captured safely or requires live Neo4j access."
        )
        self._capture_operation(method_name, {}, error=error_msg)
        raise NotImplementedError(error_msg)

    # Core node operations
    def create_node(self, node_obj: GraphDict, sketch_id: str) -> Optional[str]:
        """Capture node creation."""
        element_id = self._generate_node_id()
        self._capture_operation(
            "create_node",
            {"node_obj": node_obj, "sketch_id": sketch_id},
            result=element_id,
        )
        return element_id

    def update_node(
        self, element_id: str, updates: GraphDict, sketch_id: str
    ) -> Optional[str]:
        """Capture node update."""
        self._capture_operation(
            "update_node",
            {"element_id": element_id, "updates": updates, "sketch_id": sketch_id},
            result=element_id,
        )
        return element_id

    def delete_nodes(self, node_ids: List[str], sketch_id: str) -> int:
        """Capture node deletion."""
        count = len(node_ids)
        self._capture_operation(
            "delete_nodes",
            {"node_ids": node_ids, "sketch_id": sketch_id},
            result=count,
        )
        return count

    def delete_all_sketch_nodes(self, sketch_id: str) -> int:
        """Capture deletion of all sketch nodes."""
        # This is unsupported as it's destructive without review
        self._unsupported("delete_all_sketch_nodes")

    def get_nodes_by_ids(
        self, node_ids: List[str], sketch_id: str
    ) -> List[Dict[str, Any]]:
        """Reads are not captured (no side effects)."""
        # Get operations don't have side effects in capture mode
        return []

    def update_nodes_positions(
        self, positions: List[Dict[str, Any]], sketch_id: str
    ) -> int:
        """Capture position updates."""
        count = len(positions)
        self._capture_operation(
            "update_nodes_positions",
            {"positions": positions, "sketch_id": sketch_id},
            result=count,
        )
        return count

    # Core relationship operations
    def create_relationship(self, rel_obj: GraphDict, sketch_id: str) -> None:
        """Capture relationship creation."""
        self._capture_operation(
            "create_relationship",
            {"rel_obj": rel_obj, "sketch_id": sketch_id},
        )

    def create_relationship_by_element_id(
        self,
        from_element_id: str,
        to_element_id: str,
        rel_label: str,
        sketch_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Capture relationship creation by element ID."""
        rel_id = self._generate_rel_id()
        result = {
            "element_id": rel_id,
            "from": from_element_id,
            "to": to_element_id,
            "label": rel_label,
        }
        self._capture_operation(
            "create_relationship_by_element_id",
            {
                "from_element_id": from_element_id,
                "to_element_id": to_element_id,
                "rel_label": rel_label,
                "sketch_id": sketch_id,
            },
            result=result,
        )
        return result

    def update_relationship(
        self, element_id: str, rel_obj: GraphDict, sketch_id: str
    ) -> Optional[Dict[str, Any]]:
        """Capture relationship update."""
        result = {"element_id": element_id, **rel_obj}
        self._capture_operation(
            "update_relationship",
            {"element_id": element_id, "rel_obj": rel_obj, "sketch_id": sketch_id},
            result=result,
        )
        return result

    def delete_relationships(self, relationship_ids: List[str], sketch_id: str) -> int:
        """Capture relationship deletion."""
        count = len(relationship_ids)
        self._capture_operation(
            "delete_relationships",
            {"relationship_ids": relationship_ids, "sketch_id": sketch_id},
            result=count,
        )
        return count

    # Graph queries
    def get_sketch_graph(
        self, sketch_id: str, limit: int = 100000
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Reads are not captured."""
        return {"nodes": [], "edges": []}

    def get_neighbors(self, node_id: str, sketch_id: str) -> Dict[str, Any]:
        """Reads are not captured."""
        return {"node": {}, "neighbors": []}

    # Merge operations
    def merge_nodes(
        self,
        old_node_ids: List[str],
        new_node_data: Dict[str, Any],
        new_node_id: Optional[str],
        sketch_id: str,
    ) -> Optional[str]:
        """Capture node merge."""
        element_id = new_node_id or self._generate_node_id()
        self._capture_operation(
            "merge_nodes",
            {
                "old_node_ids": old_node_ids,
                "new_node_data": new_node_data,
                "sketch_id": sketch_id,
            },
            result=element_id,
        )
        return element_id

    # Batch operations
    def batch_create_nodes(
        self, nodes: List[GraphDict], sketch_id: str
    ) -> Dict[str, Any]:
        """Capture batch node creation."""
        node_ids = [self._generate_node_id() for _ in nodes]
        result = {
            "created": len(node_ids),
            "element_ids": node_ids,
        }
        self._capture_operation(
            "batch_create_nodes",
            {"nodes_count": len(nodes), "sketch_id": sketch_id},
            result=result,
        )
        return result

    def batch_create_edges_by_element_id(
        self, edges: List[GraphDict], sketch_id: str
    ) -> Dict[str, Any]:
        """Capture batch relationship creation."""
        edge_ids = [self._generate_rel_id() for _ in edges]
        result = {
            "created": len(edge_ids),
            "element_ids": edge_ids,
        }
        self._capture_operation(
            "batch_create_edges_by_element_id",
            {"edges_count": len(edges), "sketch_id": sketch_id},
            result=result,
        )
        return result

    def add_to_batch(self, operation_type: str, **kwargs: Any) -> None:
        """Queue operation for batch."""
        operation = {
            "type": operation_type,
            "params": kwargs,
            "timestamp": datetime.utcnow().isoformat(),
        }
        self.batch_queue.append(operation)
        
        # Auto-flush if batch size exceeded
        if len(self.batch_queue) >= self.batch_size:
            self.flush_batch()

    def flush_batch(self) -> None:
        """Flush batched operations."""
        if not self.batch_queue:
            return
            
        count = len(self.batch_queue)
        self._capture_operation(
            "flush_batch",
            {"batched_operations": count},
            result={"flushed": count},
        )
        self.batch_queue.clear()

    def set_batch_size(self, size: int) -> None:
        """Set the batch size."""
        self.batch_size = size

    # Custom queries
    def query(
        self, cypher: str, parameters: Dict[str, Any] = {}
    ) -> List[Dict[str, Any]]:
        """Custom queries are not supported in capture mode."""
        self._unsupported("query")

    # Capture-specific methods
    def get_captured_operations(self) -> List[CapturedOperation]:
        """Get all captured operations."""
        return self.operations.copy()

    def get_captured_writes(self) -> List[CapturedOperation]:
        """Get only write operations (excludes reads)."""
        return [
            op
            for op in self.operations
            if op.method_name
            not in ["get_nodes_by_ids", "get_sketch_graph", "get_neighbors"]
        ]

    def clear_captures(self) -> None:
        """Clear all captured operations."""
        self.operations.clear()
        self.batch_queue.clear()

    def export_captures(self) -> Dict[str, Any]:
        """Export all captures as JSON-serializable dict."""
        return {
            "sketch_id": self.sketch_id,
            "capture_timestamp": datetime.utcnow().isoformat(),
            "total_operations": len(self.operations),
            "write_operations": len(self.get_captured_writes()),
            "operations": [op.to_dict() for op in self.operations],
            "batch_queue_at_end": len(self.batch_queue),
        }
