"""Acceptance contracts for forensic authority and SQLite lineage."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from flowsint_core.core.forensics import (
    AUTHORITY_CONTRACT_VERSION,
    FORENSIC_AUTHORITY,
    MIGRATION_DECISIONS,
    AuthorityRole,
    AuthoritySystem,
    ForensicLedgerSession,
    LedgerLineageError,
    MigrationDisposition,
    ProjectionEvidenceReference,
    require_ledger_evidence,
    create_forensic_ledger_session_factory,
)
from flowsint_core.core.models import Base, EvidenceEnvelopeRecord


@pytest.fixture
def db_session() -> ForensicLedgerSession:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = create_forensic_ledger_session_factory(engine)()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def _evidence_record() -> EvidenceEnvelopeRecord:
    now = datetime.now(timezone.utc)
    return EvidenceEnvelopeRecord(
        id=uuid4(),
        flow_run_id=uuid4(),
        step_run_id=uuid4(),
        attempt=0,
        input_index=0,
        evidence_index=0,
        input_ref="forensic-authority-test",
        status="success",
        retryable=False,
        mapped_outputs=[],
        source="test",
        retrieved_at=now,
        ingested_at=now,
    )


def test_ac_101_sqlite_is_sole_authority_and_ladybugdb_is_rebuildable() -> None:
    assert FORENSIC_AUTHORITY.version == AUTHORITY_CONTRACT_VERSION == "v1"
    assert set(FORENSIC_AUTHORITY.rules) == set(AuthoritySystem)
    assert (
        FORENSIC_AUTHORITY[AuthoritySystem.SQLITE_LEDGER].role
        is AuthorityRole.FORENSIC_AUTHORITY
    )
    assert (
        FORENSIC_AUTHORITY[AuthoritySystem.LADYBUGDB_PROJECTION].role
        is AuthorityRole.REBUILDABLE_EVIDENCE_KEYED_READ_MODEL
    )


def test_authority_matrix_covers_supporting_systems_immutably() -> None:
    assert (
        FORENSIC_AUTHORITY[AuthoritySystem.ARTIFACT_STORE].role
        is AuthorityRole.EVIDENCE_PAYLOAD_STORE
    )
    assert (
        FORENSIC_AUTHORITY[AuthoritySystem.DERIVED_VIEWS].role
        is AuthorityRole.REBUILDABLE_DERIVED_VIEW
    )
    with pytest.raises(TypeError):
        FORENSIC_AUTHORITY.rules[AuthoritySystem.SQLITE_LEDGER] = (  # type: ignore[index]
            FORENSIC_AUTHORITY[AuthoritySystem.SQLITE_LEDGER]
        )


def test_migration_decisions_are_complete_and_immutable() -> None:
    assert {
        issue_id: decision.disposition
        for issue_id, decision in MIGRATION_DECISIONS.items()
    } == {
        "OBS-1782": MigrationDisposition.PRESERVE_STRUCTURED_SQLITE_EVIDENCE,
        "OBS-1787": MigrationDisposition.REBUILD_FROM_SQLITE_WITHOUT_NEO4J_AUTHORITY,
        "OBS-1788": MigrationDisposition.PRESERVE_REGISTRY_EGRESS_BOUNDARY,
    }
    with pytest.raises(TypeError):
        MIGRATION_DECISIONS["OBS-1782"] = MIGRATION_DECISIONS["OBS-1782"]  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        MIGRATION_DECISIONS["OBS-1782"].rationale = "changed"  # type: ignore[misc]


def test_ac_102_ledger_lookup_survives_projection_reversal(
    db_session: ForensicLedgerSession,
) -> None:
    evidence = _evidence_record()
    db_session.add(evidence)
    db_session.flush()
    projected_fact = ProjectionEvidenceReference(evidence_envelope_id=evidence.id)
    fake_projection = {"evidence_envelope_id": str(evidence.id)}

    fake_projection.clear()
    fake_projection["corrupted"] = True

    resolved = require_ledger_evidence(db_session, projected_fact.evidence_envelope_id)

    assert resolved.id == evidence.id
    with pytest.raises(FrozenInstanceError):
        projected_fact.evidence_envelope_id = uuid4()  # type: ignore[misc]


def test_ac_103_neo4j_runtime_is_excluded_from_ladybugdb_projection() -> None:
    assert (
        AuthoritySystem.LEGACY_NEO4J_CANVAS is not AuthoritySystem.LADYBUGDB_PROJECTION
    )
    assert (
        FORENSIC_AUTHORITY[AuthoritySystem.LEGACY_NEO4J_CANVAS].role
        is AuthorityRole.EXCLUDED_FROM_FORENSIC_TARGET
    )
    assert (
        FORENSIC_AUTHORITY[AuthoritySystem.LADYBUGDB_PROJECTION].role
        is AuthorityRole.REBUILDABLE_EVIDENCE_KEYED_READ_MODEL
    )


@pytest.mark.parametrize(
    ("evidence_envelope_id", "expected_code"),
    [
        (None, "forensic_evidence_key_missing"),
        (uuid4(), "forensic_evidence_not_found"),
    ],
)
def test_ledger_guard_rejects_graph_only_or_missing_sqlite_evidence(
    db_session: ForensicLedgerSession,
    evidence_envelope_id: UUID | None,
    expected_code: str,
) -> None:
    graph_only_fact = {"graph_node_id": "ladybug:fact:1"}
    key = evidence_envelope_id or graph_only_fact.get("evidence_envelope_id")

    with pytest.raises(LedgerLineageError) as error:
        require_ledger_evidence(db_session, key)

    assert error.value.code == expected_code
    assert "ladybug" not in error.value.safe_message
    assert "fact:1" not in error.value.safe_message


def test_ledger_guard_rejects_postgresql_before_io() -> None:
    engine = create_engine("postgresql+psycopg2://localhost/forensic_authority")
    with pytest.raises(LedgerLineageError) as factory_error:
        create_forensic_ledger_session_factory(engine)
    session = ForensicLedgerSession(bind=engine)
    try:
        with pytest.raises(LedgerLineageError) as error:
            require_ledger_evidence(session, uuid4())
    finally:
        session.close()
        engine.dispose()

    assert error.value.code == "forensic_ledger_backend_not_sqlite"
    assert factory_error.value.code == "forensic_ledger_backend_not_sqlite"


def test_factory_rejects_sqlite_connection() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    connection = engine.connect()
    try:
        with pytest.raises(LedgerLineageError) as error:
            create_forensic_ledger_session_factory(connection)
    finally:
        connection.close()
        engine.dispose()

    assert error.value.code == "forensic_ledger_backend_not_sqlite"


def test_ledger_guard_rejects_generic_sqlite_session_before_query() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    try:
        evidence = _evidence_record()
        session.add(evidence)
        session.flush()
        statements: list[str] = []

        def record_statement(
            connection, cursor, statement, parameters, context, executemany
        ) -> None:
            statements.append(statement)

        event.listen(engine, "before_cursor_execute", record_statement)
        with pytest.raises(LedgerLineageError) as error:
            require_ledger_evidence(session, evidence.id)
    finally:
        event.remove(engine, "before_cursor_execute", record_statement)
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()

    assert error.value.code == "forensic_ledger_backend_not_sqlite"
    assert statements == []


def test_ledger_guard_rejects_direct_subclass_sqlite_session_before_query() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = ForensicLedgerSession(bind=engine)
    try:
        evidence = _evidence_record()
        session.add(evidence)
        session.flush()
        statements: list[str] = []

        def record_statement(
            connection, cursor, statement, parameters, context, executemany
        ) -> None:
            statements.append(statement)

        event.listen(engine, "before_cursor_execute", record_statement)
        with pytest.raises(LedgerLineageError) as error:
            require_ledger_evidence(session, evidence.id)
    finally:
        event.remove(engine, "before_cursor_execute", record_statement)
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()

    assert error.value.code == "forensic_ledger_backend_not_sqlite"
    assert statements == []


def test_ledger_guard_rejects_copied_factory_session_state_before_query() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    trusted_session = create_forensic_ledger_session_factory(engine)()
    session = ForensicLedgerSession(bind=engine)
    session.__dict__.update(trusted_session.__dict__)
    try:
        evidence = _evidence_record()
        session.add(evidence)
        session.flush()
        statements: list[str] = []

        def record_statement(
            connection, cursor, statement, parameters, context, executemany
        ) -> None:
            statements.append(statement)

        event.listen(engine, "before_cursor_execute", record_statement)
        with pytest.raises(LedgerLineageError) as error:
            require_ledger_evidence(session, evidence.id)
    finally:
        event.remove(engine, "before_cursor_execute", record_statement)
        session.close()
        trusted_session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()

    assert error.value.code == "forensic_ledger_backend_not_sqlite"
    assert statements == []


def test_ledger_guard_rejects_rebound_factory_session_before_query() -> None:
    source_engine = create_engine("sqlite+pysqlite:///:memory:")
    rebound_engine = create_engine("sqlite+pysqlite:///:memory:")
    session = create_forensic_ledger_session_factory(source_engine)()
    statements: list[str] = []

    def record_statement(
        connection, cursor, statement, parameters, context, executemany
    ) -> None:
        statements.append(statement)

    event.listen(rebound_engine, "before_cursor_execute", record_statement)
    try:
        session.bind = rebound_engine
        with pytest.raises(LedgerLineageError) as error:
            require_ledger_evidence(session, uuid4())
    finally:
        event.remove(rebound_engine, "before_cursor_execute", record_statement)
        session.close()
        source_engine.dispose()
        rebound_engine.dispose()

    assert error.value.code == "forensic_ledger_backend_not_sqlite"
    assert statements == []


def test_ledger_guard_rejects_mapper_bound_attacker_before_query() -> None:
    source_engine = create_engine("sqlite+pysqlite:///:memory:")
    attacker_engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(source_engine)
    Base.metadata.create_all(attacker_engine)
    source_writer = Session(source_engine)
    attacker_writer = Session(attacker_engine)
    source_record = _evidence_record()
    source_writer.add(source_record)
    source_writer.flush()
    source_record_id = source_record.id
    attacker_record = _evidence_record()
    attacker_record.id = source_record_id
    attacker_writer.add(attacker_record)
    source_writer.commit()
    attacker_writer.commit()
    source_writer.close()
    attacker_writer.close()
    session = create_forensic_ledger_session_factory(source_engine)()
    statements: list[str] = []

    def record_statement(
        connection, cursor, statement, parameters, context, executemany
    ) -> None:
        statements.append(statement)

    event.listen(attacker_engine, "before_cursor_execute", record_statement)
    try:
        session.bind_mapper(EvidenceEnvelopeRecord, attacker_engine)
        with pytest.raises(LedgerLineageError) as error:
            require_ledger_evidence(session, source_record_id)
    finally:
        event.remove(attacker_engine, "before_cursor_execute", record_statement)
        session.close()
        Base.metadata.drop_all(source_engine)
        Base.metadata.drop_all(attacker_engine)
        source_engine.dispose()
        attacker_engine.dispose()

    assert error.value.code == "forensic_ledger_backend_not_sqlite"
    assert statements == []


def test_ledger_guard_rejects_table_bound_attacker_before_query() -> None:
    source_engine = create_engine("sqlite+pysqlite:///:memory:")
    attacker_engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(source_engine)
    Base.metadata.create_all(attacker_engine)
    source_writer = Session(source_engine)
    attacker_writer = Session(attacker_engine)
    source_record = _evidence_record()
    source_writer.add(source_record)
    source_writer.flush()
    source_record_id = source_record.id
    attacker_record = _evidence_record()
    attacker_record.id = source_record_id
    attacker_writer.add(attacker_record)
    source_writer.commit()
    attacker_writer.commit()
    source_writer.close()
    attacker_writer.close()
    session = create_forensic_ledger_session_factory(source_engine)()
    statements: list[str] = []

    def record_statement(
        connection, cursor, statement, parameters, context, executemany
    ) -> None:
        statements.append(statement)

    event.listen(attacker_engine, "before_cursor_execute", record_statement)
    try:
        session.bind_table(EvidenceEnvelopeRecord.__table__, attacker_engine)
        with pytest.raises(LedgerLineageError) as error:
            require_ledger_evidence(session, source_record_id)
    finally:
        event.remove(attacker_engine, "before_cursor_execute", record_statement)
        session.close()
        Base.metadata.drop_all(source_engine)
        Base.metadata.drop_all(attacker_engine)
        source_engine.dispose()
        attacker_engine.dispose()

    assert error.value.code == "forensic_ledger_backend_not_sqlite"
    assert statements == []


def test_ledger_guard_rejects_unbound_session() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    session = create_forensic_ledger_session_factory(engine)()
    try:
        session.bind = None
        with pytest.raises(LedgerLineageError) as error:
            require_ledger_evidence(session, uuid4())
    finally:
        session.close()
        engine.dispose()

    assert error.value.code == "forensic_ledger_backend_not_sqlite"


def test_ledger_guard_rejects_missing_session() -> None:
    with pytest.raises(LedgerLineageError) as error:
        require_ledger_evidence(None, uuid4())

    assert error.value.code == "forensic_ledger_backend_not_sqlite"
