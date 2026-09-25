"""Behavior contracts for the legacy execution fence and reingestion boundary."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, event

from flowsint_core.core.forensics import (
    ForensicLedgerSession,
    LegacyExecutionFenced,
    LegacyExecutionMode,
    LegacyPathDisposition,
    StructuredLegacyReingestionDenied,
    StructuredLegacyReingestionRequest,
    admit_structured_legacy_evidence,
    forensic_case_scope,
    FORENSIC_DATA_CLASS_POLICY_VERSION,
    ForensicDataClass,
    ForensicPolicyDecision,
    PolicyAccessRequest,
    PolicyState,
    ResearchOperation,
    ReviewerAuthority,
    authorize_collection,
    create_forensic_ledger_session_factory,
    require_legacy_graph_access,
)
from flowsint_core.core.graph import GraphService, create_graph_service
from flowsint_core.core.models import Base, EvidenceEnvelopeRecord


class InstrumentedGraphRepository:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def get_sketch_graph(self, sketch_id: str) -> dict[str, list[object]]:
        self.calls.append("get_sketch_graph")
        return {"nodes": [], "edges": []}

    def update_nodes_positions(self, positions: list[object], sketch_id: str) -> int:
        self.calls.append("update_nodes_positions")
        return len(positions)


@pytest.fixture
def ledger_session() -> ForensicLedgerSession:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = create_forensic_ledger_session_factory(engine)()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def _stored_evidence(**overrides: object) -> EvidenceEnvelopeRecord:
    now = datetime.now(timezone.utc)
    values: dict[str, object] = {
        "id": uuid4(),
        "flow_run_id": uuid4(),
        "step_run_id": uuid4(),
        "attempt": 0,
        "input_index": 0,
        "evidence_index": 0,
        "input_ref": "legacy-reingestion-test",
        "status": "success",
        "retryable": False,
        "mapped_outputs": [],
        "source": "connector:test-destination:test-endpoint",
        "artifact_sha256": "a" * 64,
        "artifact_reference": f"body:sha256:{'a' * 64}",
        "retrieved_at": now,
        "ingested_at": now,
        "source_rights": "permitted",
        "destination_id": "test-destination",
        "endpoint_id": "test-endpoint",
        "policy_version": "1",
        "capability": "enrich.read",
    }
    values.update(overrides)
    return EvidenceEnvelopeRecord(**values)


def _reingestion_request(
    evidence_envelope_id: UUID,
    *,
    source: str = "connector:test-destination:test-endpoint",
    source_rights: frozenset[str] = frozenset({"permitted"}),
) -> StructuredLegacyReingestionRequest:
    now = datetime.now(timezone.utc)
    policy_request = PolicyAccessRequest(
        case_reference="case-1964",
        taxonomy_version=FORENSIC_DATA_CLASS_POLICY_VERSION,
        data_class=ForensicDataClass.PUBLIC,
        operation=ResearchOperation.COLLECTION,
        evidence_envelope_id=evidence_envelope_id,
        source=source,
        source_rights=source_rights,
        subject_id="subject:test-operator",
        requested_at=now,
    )
    decision = ForensicPolicyDecision(
        case_reference=policy_request.case_reference,
        taxonomy_version=FORENSIC_DATA_CLASS_POLICY_VERSION,
        data_class=ForensicDataClass.PUBLIC,
        operations=frozenset({ResearchOperation.COLLECTION}),
        source_scope=frozenset({source}),
        source_rights=source_rights,
        effective_at=now.replace(year=now.year - 1),
        expires_at=now.replace(year=now.year + 1),
        reviewer_authorities=frozenset({ReviewerAuthority.CASE_OWNER}),
        rationale="admit structured legacy evidence",
        state=PolicyState.ALLOW,
    )
    return StructuredLegacyReingestionRequest(
        evidence_envelope_id=evidence_envelope_id,
        case_reference=policy_request.case_reference,
        data_class=policy_request.data_class,
        source_rights=source_rights,
        subject_id="subject:test-operator",
        collection_grant=authorize_collection([decision], policy_request),
    )

def test_forensic_graph_construction_fences_before_repository_or_sql_access(
    ledger_session: ForensicLedgerSession,
) -> None:
    repository = InstrumentedGraphRepository()
    statements: list[str] = []
    event.listen(
        ledger_session.get_bind(),
        "before_cursor_execute",
        lambda *args: statements.append(args[2]),
    )

    with pytest.raises(LegacyExecutionFenced) as direct_error:
        GraphService(
            sketch_id="case-1",
            repository=repository,
            mode=LegacyExecutionMode.FORENSIC_CASE,
            case_reference="case-1",
        )

    assert direct_error.value.code == "legacy_execution_forensic_case_fenced"
    assert direct_error.value.decision.disposition is LegacyPathDisposition.FENCED
    assert direct_error.value.decision.operation == "graph_service_construction"
    assert repository.calls == []
    assert statements == []


def test_forensic_graph_factory_fences_before_neo4j_repository_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    constructed = False

    def fail_if_constructed() -> object:
        nonlocal constructed
        constructed = True
        raise AssertionError("factory touched Neo4j")

    monkeypatch.setattr(
        "flowsint_core.core.graph.service.Neo4jGraphRepository", fail_if_constructed
    )

    with pytest.raises(LegacyExecutionFenced) as factory_error:
        create_graph_service(
            "case-2",
            mode=LegacyExecutionMode.FORENSIC_CASE,
            case_reference="case-2",
        )

    assert factory_error.value.code == "legacy_execution_forensic_case_fenced"
    assert (
        factory_error.value.decision.operation == "graph_service_factory_construction"
    )
    assert constructed is False


@pytest.mark.parametrize("operation", ["graph_read", "graph_write"])
def test_forensic_graph_read_and_write_requests_are_fenced(operation: str) -> None:
    with pytest.raises(LegacyExecutionFenced) as error:
        require_legacy_graph_access(
            LegacyExecutionMode.FORENSIC_CASE,
            operation,
            case_reference="case-3",
        )

    assert error.value.code == "legacy_execution_forensic_case_fenced"
    assert error.value.decision.operation == operation
    assert error.value.decision.allowed is False


def test_legacy_canvas_default_remains_operational() -> None:
    repository = InstrumentedGraphRepository()

    service = GraphService(sketch_id="legacy-sketch", repository=repository)
    graph = service.get_sketch_graph()

    assert graph.nodes == []
    assert graph.edges == []
    assert repository.calls == ["get_sketch_graph"]


@pytest.mark.parametrize("case_reference", ["", "   ", None])
def test_case_scope_requires_nonempty_reference(case_reference: str | None) -> None:
    with pytest.raises(ValueError, match="case_reference is required"):
        with forensic_case_scope(case_reference):  # type: ignore[arg-type]
            pass


def test_precreated_graph_service_fences_reads_and_writes_inside_case_scope() -> None:
    repository = InstrumentedGraphRepository()
    service = GraphService(sketch_id="legacy-sketch", repository=repository)

    with forensic_case_scope("case-4"):
        with pytest.raises(LegacyExecutionFenced) as read_error:
            service.get_sketch_graph()
        with pytest.raises(LegacyExecutionFenced) as write_error:
            service.update_nodes_positions([])

    assert read_error.value.decision.operation == "graph_service_get_sketch_graph"
    assert (
        write_error.value.decision.operation == "graph_service_update_nodes_positions"
    )
    assert repository.calls == []


def test_case_scope_cannot_be_bypassed_and_resets_nested_context() -> None:
    service = GraphService(
        sketch_id="legacy-sketch", repository=InstrumentedGraphRepository()
    )

    with forensic_case_scope("outer-case"):
        with pytest.raises(LegacyExecutionFenced) as outer_error:
            service.get_sketch_graph()
        with forensic_case_scope("inner-case"):
            with pytest.raises(LegacyExecutionFenced) as inner_error:
                service.get_sketch_graph()
        with pytest.raises(LegacyExecutionFenced) as restored_outer_error:
            require_legacy_graph_access(
                LegacyExecutionMode.LEGACY_CANVAS,
                "explicit_legacy_override",
            )

    assert outer_error.value.decision.case_reference == "outer-case"
    assert inner_error.value.decision.case_reference == "inner-case"
    assert restored_outer_error.value.decision.case_reference == "outer-case"
    assert service.get_sketch_graph().nodes == []


def test_case_scope_fences_factory_before_neo4j_for_explicit_legacy_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    constructed = False

    def fail_if_constructed() -> object:
        nonlocal constructed
        constructed = True
        raise AssertionError("factory touched Neo4j")

    monkeypatch.setattr(
        "flowsint_core.core.graph.service.Neo4jGraphRepository", fail_if_constructed
    )

    with forensic_case_scope("case-5"):
        with pytest.raises(LegacyExecutionFenced) as error:
            create_graph_service(
                "flow-or-sketch", mode=LegacyExecutionMode.LEGACY_CANVAS
            )

    assert error.value.decision.case_reference == "case-5"
    assert constructed is False


@pytest.mark.parametrize(
    ("overrides", "expected_code"),
    [
        ({"source": ""}, "structured_legacy_reingestion_provenance_missing"),
        ({"source_rights": None}, "structured_legacy_reingestion_rights_missing"),
        ({"artifact_sha256": None}, "structured_legacy_reingestion_artifact_missing"),
        (
            {"artifact_sha256": "not-a-digest"},
            "structured_legacy_reingestion_artifact_invalid",
        ),
        (
            {"artifact_reference": None},
            "structured_legacy_reingestion_artifact_missing",
        ),
    ],
)
def test_reingestion_rejects_incomplete_stored_evidence(
    ledger_session: ForensicLedgerSession,
    overrides: dict[str, object],
    expected_code: str,
) -> None:
    evidence = _stored_evidence(**overrides)
    ledger_session.add(evidence)
    ledger_session.commit()

    with pytest.raises(StructuredLegacyReingestionDenied) as error:
        admit_structured_legacy_evidence(
            ledger_session,
            _reingestion_request(evidence.id),
        )

    assert error.value.code == expected_code


@pytest.mark.parametrize(
    "overrides",
    [
        {"source": "neo4j:element-id"},
        {"source": "connector:test-destination:other-endpoint"},
        {"capability": "enrich.write"},
        {"policy_version": None},
        {"destination_id": None},
        {"endpoint_id": None},
    ],
)
def test_reingestion_rejects_noncanonical_provenance_without_sql_writes(
    ledger_session: ForensicLedgerSession,
    overrides: dict[str, object],
) -> None:
    evidence = _stored_evidence(**overrides)
    ledger_session.add(evidence)
    ledger_session.commit()
    evidence_envelope_id = evidence.id
    statements: list[str] = []
    event.listen(
        ledger_session.get_bind(),
        "before_cursor_execute",
        lambda *args: statements.append(args[2]),
    )

    with pytest.raises(StructuredLegacyReingestionDenied) as error:
        admit_structured_legacy_evidence(
            ledger_session,
            _reingestion_request(evidence_envelope_id),
        )

    assert error.value.code == "structured_legacy_reingestion_provenance_invalid"
    assert not [
        statement
        for statement in statements
        if statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
    ]


@pytest.mark.parametrize(
    ("artifact_sha256", "artifact_reference"),
    [
        ("a" * 64, "neo4j:element-id"),
        ("a" * 64, "canvas:node"),
        ("a" * 64, f"body:sha256:{'b' * 64}"),
        ("A" * 64, f"body:sha256:{'A' * 64}"),
        ("a" * 64, f"body:sha256:{'A' * 64}"),
        ("a" * 64, "artifact://legacy-evidence"),
    ],
)
def test_reingestion_rejects_noncanonical_artifact_locator_without_sql_writes(
    ledger_session: ForensicLedgerSession,
    artifact_sha256: str,
    artifact_reference: str,
) -> None:
    evidence = _stored_evidence(
        artifact_sha256=artifact_sha256,
        artifact_reference=artifact_reference,
    )
    ledger_session.add(evidence)
    ledger_session.commit()
    statements: list[str] = []
    event.listen(
        ledger_session.get_bind(),
        "before_cursor_execute",
        lambda *args: statements.append(args[2]),
    )

    with pytest.raises(StructuredLegacyReingestionDenied) as error:
        admit_structured_legacy_evidence(
            ledger_session,
            _reingestion_request(evidence.id),
        )

    assert error.value.code == "structured_legacy_reingestion_artifact_invalid"
    assert evidence.artifact_sha256 == artifact_sha256
    assert evidence.artifact_reference == artifact_reference
    assert not [
        statement
        for statement in statements
        if statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
    ]


@pytest.mark.parametrize(
    "evidence_envelope_id", [pytest.param(uuid4(), id="missing")]
)
def test_reingestion_rejects_graph_only_or_missing_ledger_evidence(
    ledger_session: ForensicLedgerSession,
    evidence_envelope_id: UUID,
) -> None:
    graph_only_fact = {"graph_node_id": "neo4j:legacy:1"}
    request = _reingestion_request(evidence_envelope_id)

    with pytest.raises(StructuredLegacyReingestionDenied) as error:
        admit_structured_legacy_evidence(ledger_session, request)

    assert error.value.code == "structured_legacy_reingestion_evidence_unavailable"
    assert "neo4j" not in error.value.safe_message
    assert graph_only_fact["graph_node_id"] == "neo4j:legacy:1"


def test_reingestion_rejects_unpersisted_evidence_without_sql_writes(
    ledger_session: ForensicLedgerSession,
) -> None:
    evidence = _stored_evidence()
    ledger_session.add(evidence)
    statements: list[str] = []
    event.listen(
        ledger_session.get_bind(),
        "before_cursor_execute",
        lambda *args: statements.append(args[2]),
    )

    with pytest.raises(StructuredLegacyReingestionDenied) as error:
        admit_structured_legacy_evidence(
            ledger_session,
            _reingestion_request(evidence.id),
        )

    assert error.value.code == "structured_legacy_reingestion_evidence_unavailable"
    assert not [
        statement
        for statement in statements
        if statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
    ]


def test_reingestion_preserves_pending_evidence_mutation_without_sql_writes(
    ledger_session: ForensicLedgerSession,
) -> None:
    evidence = _stored_evidence()
    ledger_session.add(evidence)
    ledger_session.commit()
    evidence_envelope_id = evidence.id
    evidence.source_rights = "pending-rights-change"
    statements: list[str] = []
    event.listen(
        ledger_session.get_bind(),
        "before_cursor_execute",
        lambda *args: statements.append(args[2]),
    )

    with pytest.raises(StructuredLegacyReingestionDenied) as error:
        admit_structured_legacy_evidence(
            ledger_session,
            _reingestion_request(evidence_envelope_id),
        )

    assert error.value.code == "structured_legacy_reingestion_evidence_unavailable"
    assert evidence.source_rights == "pending-rights-change"
    assert not [
        statement
        for statement in statements
        if statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
    ]


def test_reingestion_preserves_pending_evidence_deletion_without_sql_writes(
    ledger_session: ForensicLedgerSession,
) -> None:
    evidence = _stored_evidence()
    ledger_session.add(evidence)
    ledger_session.commit()
    evidence_envelope_id = evidence.id
    ledger_session.delete(evidence)
    statements: list[str] = []
    event.listen(
        ledger_session.get_bind(),
        "before_cursor_execute",
        lambda *args: statements.append(args[2]),
    )

    with pytest.raises(StructuredLegacyReingestionDenied) as error:
        admit_structured_legacy_evidence(
            ledger_session,
            _reingestion_request(evidence_envelope_id),
        )

    assert error.value.code == "structured_legacy_reingestion_evidence_unavailable"
    assert evidence in ledger_session.deleted
    assert not [
        statement
        for statement in statements
        if statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
    ]


def test_reingestion_returns_only_immutable_ledger_uuid_without_sql_writes(
    ledger_session: ForensicLedgerSession,
) -> None:
    evidence = _stored_evidence()
    ledger_session.add(evidence)
    ledger_session.commit()
    statements: list[str] = []
    event.listen(
        ledger_session.get_bind(),
        "before_cursor_execute",
        lambda *args: statements.append(args[2]),
    )

    admission = admit_structured_legacy_evidence(
        ledger_session,
        _reingestion_request(evidence.id),
    )

    assert admission.evidence_envelope_id == evidence.id
    assert set(admission.__dict__) == {"evidence_envelope_id"}
    assert not [
        statement
        for statement in statements
        if statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
    ]
    with pytest.raises(FrozenInstanceError):
        admission.evidence_envelope_id = uuid4()  # type: ignore[misc]


def test_reingestion_denies_an_unregistered_grant_before_any_sql_lookup(
    ledger_session: ForensicLedgerSession,
) -> None:
    request = _reingestion_request(uuid4())
    statements: list[str] = []
    event.listen(
        ledger_session.get_bind(),
        "before_cursor_execute",
        lambda *args: statements.append(args[2]),
    )

    with pytest.raises(StructuredLegacyReingestionDenied) as error:
        admit_structured_legacy_evidence(
            ledger_session,
            replace(request, collection_grant=replace(request.collection_grant)),
        )

    assert error.value.code == "forensic_grant_invalid_hold"
    assert statements == []


@pytest.mark.parametrize(
    ("overrides", "expected_code"),
    [
        ({"source_rights": "unspecified"}, "forensic_grant_invalid_hold"),
        (
            {
                "source": "connector:other-destination:other-endpoint",
                "destination_id": "other-destination",
                "endpoint_id": "other-endpoint",
            },
            "forensic_grant_invalid_hold",
        ),
    ],
)
def test_reingestion_rechecks_persisted_source_and_rights_against_collection_grant(
    ledger_session: ForensicLedgerSession,
    overrides: dict[str, object],
    expected_code: str,
) -> None:
    evidence = _stored_evidence(**overrides)
    ledger_session.add(evidence)
    ledger_session.commit()

    with pytest.raises(StructuredLegacyReingestionDenied) as error:
        admit_structured_legacy_evidence(
            ledger_session,
            _reingestion_request(evidence.id),
        )

    assert error.value.code == expected_code
    assert "other-destination" not in error.value.safe_message


def test_reingestion_expired_grant_denies_before_any_sql(
    ledger_session: ForensicLedgerSession,
) -> None:
    """An expired collection grant is denied through admission before SQL."""
    from unittest.mock import patch

    evidence = _stored_evidence()
    ledger_session.add(evidence)
    ledger_session.commit()
    now = datetime.now(timezone.utc)
    policy_request = PolicyAccessRequest(
        case_reference="case-1964",
        taxonomy_version=FORENSIC_DATA_CLASS_POLICY_VERSION,
        data_class=ForensicDataClass.PUBLIC,
        operation=ResearchOperation.COLLECTION,
        evidence_envelope_id=evidence.id,
        source="connector:test-destination:test-endpoint",
        source_rights=frozenset({"permitted"}),
        subject_id="subject:test-operator",
        requested_at=now,
    )
    quick_decision = ForensicPolicyDecision(
        case_reference=policy_request.case_reference,
        taxonomy_version=FORENSIC_DATA_CLASS_POLICY_VERSION,
        data_class=ForensicDataClass.PUBLIC,
        operations=frozenset({ResearchOperation.COLLECTION}),
        source_scope=frozenset({"connector:test-destination:test-endpoint"}),
        source_rights=frozenset({"permitted"}),
        effective_at=now - timedelta(hours=1),
        expires_at=now + timedelta(minutes=1),
        reviewer_authorities=frozenset({ReviewerAuthority.CASE_OWNER}),
        rationale="short-lived grant",
        state=PolicyState.ALLOW,
    )
    grant = authorize_collection([quick_decision], policy_request)

    future = now + timedelta(minutes=5)
    statements: list[str] = []
    event.listen(
        ledger_session.get_bind(),
        "before_cursor_execute",
        lambda *args: statements.append(args[2]),
    )

    # Patch the fencing module's datetime so admission uses a future clock
    with patch("flowsint_core.core.forensics.fencing.datetime") as mock_dt:
        mock_dt.now.return_value = future

        expired_request = replace(
            _reingestion_request(evidence.id),
            collection_grant=grant,
        )
        with pytest.raises(StructuredLegacyReingestionDenied) as error:
            admit_structured_legacy_evidence(ledger_session, expired_request)

    assert error.value.code == "forensic_grant_expired_hold"
    assert statements == []
