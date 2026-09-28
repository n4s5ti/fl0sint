"""Direct Neo4j repository fencing behavior."""

from __future__ import annotations

import pytest

from flowsint_core.core.forensics import (
    LegacyExecutionFenced,
    LegacyExecutionMode,
    forensic_case_scope,
)
from flowsint_core.core.graph.repository import Neo4jGraphRepository


class InstrumentedConnection:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def query(self, *_: object, **__: object) -> list[object]:
        self.calls.append("query")
        return []

    def execute_write(self, *_: object, **__: object) -> None:
        self.calls.append("execute_write")

    def execute_batch(self, *_: object, **__: object) -> None:
        self.calls.append("execute_batch")


def test_repository_construction_is_fenced_before_singleton_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    singleton_lookups = 0

    def fail_if_looked_up() -> object:
        nonlocal singleton_lookups
        singleton_lookups += 1
        raise AssertionError("Neo4j singleton lookup occurred")

    monkeypatch.setattr(
        "flowsint_core.core.graph.repository.Neo4jConnection.get_instance",
        fail_if_looked_up,
    )

    with forensic_case_scope("case-repository-construction"):
        with pytest.raises(LegacyExecutionFenced) as error:
            Neo4jGraphRepository()

    assert error.value.decision.operation == "legacy_neo4j_graph_repository.__init__"
    assert singleton_lookups == 0


def test_precreated_repository_fences_every_legacy_operation_without_state_change() -> None:
    connection = InstrumentedConnection()
    repository = Neo4jGraphRepository(connection)  # type: ignore[arg-type]
    repository._batch_operations.append(("node", {"node_obj": object()}))
    original_batch = list(repository._batch_operations)

    with forensic_case_scope("case-repository-operation"):
        with pytest.raises(LegacyExecutionFenced) as create_error:
            repository.create_node(object(), "sketch")  # type: ignore[arg-type]
        with pytest.raises(LegacyExecutionFenced) as read_error:
            repository.get_sketch_graph("sketch")
        with pytest.raises(LegacyExecutionFenced) as delete_error:
            repository.delete_nodes([], "sketch")
        with pytest.raises(LegacyExecutionFenced) as batch_error:
            repository.add_to_batch("node")
        with pytest.raises(LegacyExecutionFenced) as flush_error:
            repository.flush_batch()

    assert create_error.value.decision.operation == "legacy_neo4j_graph_repository.create_node"
    assert read_error.value.decision.operation == "legacy_neo4j_graph_repository.get_sketch_graph"
    assert delete_error.value.decision.operation == "legacy_neo4j_graph_repository.delete_nodes"
    assert batch_error.value.decision.operation == "legacy_neo4j_graph_repository.add_to_batch"
    assert flush_error.value.decision.operation == "legacy_neo4j_graph_repository.flush_batch"
    assert connection.calls == []
    assert repository._batch_operations == original_batch


def test_repository_default_legacy_access_remains_operational() -> None:
    connection = InstrumentedConnection()
    repository = Neo4jGraphRepository(connection)  # type: ignore[arg-type]

    assert repository.get_sketch_graph("legacy-sketch") == {"nodes": [], "edges": []}
    assert connection.calls == ["query"]
    assert (
        LegacyExecutionMode.LEGACY_CANVAS.value == "legacy_canvas"
    )
