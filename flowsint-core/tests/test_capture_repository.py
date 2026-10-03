"""Focused unit coverage for the capture-only graph repository."""

import pytest

from flowsint_core.core.graph import (
    CaptureGraphRepository,
    create_graph_service,
)
from flowsint_core.core.forensics.fencing import (
    LegacyExecutionFenced,
    forensic_case_scope,
)


def test_create_candidate_is_unreviewed_and_has_no_graph_element_id():
    repository = CaptureGraphRepository(sketch_id="capture-sketch")

    with repository.source_context(
        {"input_index": 3, "source_url": "https://site.test"}
    ):
        created = repository.create_node(
            {"type": "website", "url": "https://site.test"}, "capture-sketch"
        )

    assert created is None
    candidate = repository.get_captured_candidates()[0].to_dict()
    assert candidate["method_name"] == "create_node"
    assert candidate["disposition"] == "unreviewed"
    assert candidate["source_input"] == {
        "input_index": 3,
        "source_url": "https://site.test",
    }
    assert "element_id" not in candidate


def test_batch_keeps_each_candidate_and_records_local_flush_boundaries():
    repository = CaptureGraphRepository()
    repository.set_batch_size(2)

    with repository.source_context(
        {"input_index": 0, "source_url": "https://site.test"}
    ):
        for index in range(3):
            repository.add_to_batch(
                "node",
                node_obj={"type": "website", "url": f"https://site.test/{index}"},
            )
        repository.flush_batch()

    candidates = repository.get_captured_candidates()
    flushes = [
        operation
        for operation in repository.get_captured_operations()
        if operation.operation_type == "capture_batch_flush"
    ]
    assert len(candidates) == 3
    assert [flush.parameters["candidate_sequences"] for flush in flushes] == [
        [1, 2],
        [4],
    ]
    assert repository.batch_queue == []


@pytest.mark.parametrize(
    ("operation", "args"),
    [
        ("query", ("MATCH (n) RETURN n",)),
        ("get_nodes_by_ids", (["node-1"], "capture-sketch")),
        ("delete_all_sketch_nodes", ("capture-sketch",)),
    ],
)
def test_unsafe_graph_intent_fails_instead_of_returning_an_empty_stub(operation, args):
    repository = CaptureGraphRepository()

    with pytest.raises(NotImplementedError, match=operation):
        getattr(repository, operation)(*args)

    rejected = repository.get_captured_operations()[-1]
    assert rejected.operation_type == "unsupported_publication_intent"
    assert rejected.error is not None


def test_factory_selects_capture_repository_without_live_graph_construction():
    service = create_graph_service(sketch_id="capture-sketch", capture_only=True)

    assert isinstance(service.repository, CaptureGraphRepository)
    assert service.repository.export_captures()["candidate_operations"] == 0


def test_capture_factory_remains_fenced_in_a_forensic_case():
    """Capture mode does not bypass the legacy GraphService boundary."""
    with forensic_case_scope("def46-forensic-case"):
        with pytest.raises(LegacyExecutionFenced) as raised:
            create_graph_service(sketch_id="capture-sketch", capture_only=True)

    assert raised.value.code == "legacy_execution_forensic_case_fenced"
