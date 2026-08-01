"""SQLite contracts for durable structured execution persistence."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from flowsint_core.core.execution import (
    EvidenceEnvelope,
    InputOutcome,
    OutcomeStatus,
    RedactedDiagnostic,
    StructuredExecutionResult,
    canonical_input_hash,
)
from flowsint_core.core.models import (
    Base,
    EvidenceEnvelopeRecord,
    Flow,
    FlowRun,
    Profile,
    Sketch,
    StepRun,
)
from flowsint_core.core.services.execution_service import (
    RunLeaseLost,
    create_execution_service,
)

def _diagnostic(code: str = "request_failed", retryable: bool = False):
    return RedactedDiagnostic(
        code=code,
        safe_message="The request could not be completed",
        retryable=retryable,
    )


def _evidence(input_ref: str, *, reference: str = "artifact://safe-reference"):
    return EvidenceEnvelope(
        input_ref=input_ref,
        destination_id="approved_directory",
        endpoint_id="lookup",
        capability="enrich.read",
        policy_version="1",
        artifact_sha256="a" * 64,
        artifact_reference=reference,
        source_rights="public",
        schema_version="v1",
        parser_version="v1",
        confidence=0.9,
        verification_state="verified",
        event_at=datetime(2026, 7, 31, tzinfo=timezone.utc),
        retrieved_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        ingested_at=datetime(2026, 8, 1, 0, 0, 1, tzinfo=timezone.utc),
    )


def _started_run(db_session, values):
    owner = Profile(email="execution@example.test", hashed_password="hash")
    db_session.add(owner)
    db_session.commit()
    service = create_execution_service(db_session)
    run, created = service.create_or_reuse_run(
        owner_id=owner.id,
        idempotency_key="template-task-key",
        input_digest=canonical_input_hash(values),
        input_count=len(values),
    )
    assert created
    lease = service.claim_run(run, "started-run")
    assert lease is not None
    step = service.begin_or_resume_step(run, lease, "template-lookup", len(values))
    return service, run, lease, step, owner


def test_expired_lease_resume_preserves_checkpoints(db_session):
    values = [{"value": "one"}]
    service, run, lease, step, owner = _started_run(db_session, values)

    service.update_run_checkpoint(run, lease, {"next_step": "template-lookup"})
    service.update_step_checkpoint(run, lease, step, {"next_input_index": 1})
    run.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db_session.commit()
    same_run, created = service.create_or_reuse_run(
        owner_id=owner.id,
        idempotency_key="template-task-key",
        input_digest=canonical_input_hash(values),
        input_count=len(values),
    )

    assert not created
    assert same_run.id == run.id
    recovered_lease = service.claim_run(same_run, "recovery-run")
    assert recovered_lease is not None
    resumed_step = service.begin_or_resume_step(
        same_run, recovered_lease, "template-lookup", 1
    )
    db_session.expire_all()

    assert db_session.get(type(run), run.id).attempt == 2
    assert db_session.get(type(run), run.id).checkpoint == {
        "next_step": "template-lookup"
    }
    assert resumed_step.attempt == 2
    assert resumed_step.checkpoint == {"next_input_index": 1}


def test_active_lease_blocks_second_service_until_expiry(db_session):
    values = [{"value": "one"}]
    owner = Profile(email="leases@example.test", hashed_password="hash")
    db_session.add(owner)
    db_session.commit()
    first_service = create_execution_service(db_session)
    run, created = first_service.create_or_reuse_run(
        owner_id=owner.id,
        idempotency_key="lease-key",
        input_digest=canonical_input_hash(values),
        input_count=len(values),
    )
    assert created
    first_lease = first_service.claim_run(run, "worker-one")
    assert first_lease is not None

    second_session = sessionmaker(bind=db_session.get_bind())()
    try:
        second_service = create_execution_service(second_session)
        duplicate, created = second_service.create_or_reuse_run(
            owner_id=owner.id,
            idempotency_key="lease-key",
            input_digest=canonical_input_hash(values),
            input_count=len(values),
        )

        assert not created
        assert second_service.claim_run(duplicate, "worker-two") is None

        db_session.refresh(run)
        run.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db_session.commit()

        recovered = second_service.claim_run(duplicate, "worker-two")
        assert recovered is not None
        assert recovered.attempt == 2
        assert recovered.lease_owner == "worker-two"
    finally:
        second_session.close()


def test_idempotency_replay_rejects_different_routing_context(db_session):
    owner = Profile(email="routing@example.test", hashed_password="hash")
    first_flow = Flow(name="first flow")
    second_flow = Flow(name="second flow")
    first_sketch = Sketch(title="first", description="first", owner_id=owner.id)
    second_sketch = Sketch(title="second", description="second", owner_id=owner.id)
    db_session.add_all(
        (owner, first_flow, second_flow, first_sketch, second_sketch)
    )
    db_session.commit()
    service = create_execution_service(db_session)
    input_digest = canonical_input_hash([{"value": "one"}])
    run, created = service.create_or_reuse_run(
        owner_id=owner.id,
        idempotency_key="routing-key",
        input_digest=input_digest,
        input_count=1,
        operation_digest="a" * 64,
        flow_id=first_flow.id,
        sketch_id=first_sketch.id,
    )

    assert created
    with pytest.raises(ValueError, match="different operation"):
        service.create_or_reuse_run(
            owner_id=owner.id,
            idempotency_key="routing-key",
            input_digest=input_digest,
            input_count=1,
            operation_digest="a" * 64,
            flow_id=second_flow.id,
            sketch_id=first_sketch.id,
        )
    with pytest.raises(ValueError, match="different operation"):
        service.create_or_reuse_run(
            owner_id=owner.id,
            idempotency_key="routing-key",
            input_digest=input_digest,
            input_count=1,
            operation_digest="a" * 64,
            flow_id=first_flow.id,
            sketch_id=second_sketch.id,
        )

    assert db_session.query(FlowRun).one().id == run.id


def test_persists_sibling_outcomes_and_one_to_many_output_grouping(db_session):
    values = [{"value": "one"}, {"value": "two"}]
    service, run, lease, step, _ = _started_run(db_session, values)
    first_ref = canonical_input_hash(values[0])
    second_ref = canonical_input_hash(values[1])
    result = StructuredExecutionResult(
        enricher_name="template-lookup",
        outcomes=(
            InputOutcome(
                input_ref=first_ref,
                status=OutcomeStatus.SUCCESS,
                outputs=({"name": "first"}, {"name": "second"}),
                evidence=(_evidence(first_ref),),
            ),
            InputOutcome(
                input_ref=second_ref,
                status=OutcomeStatus.FAILURE,
                diagnostic=_diagnostic(retryable=True),
            ),
        ),
    )

    service.persist_structured_result(run, lease, step, result)
    db_session.expire_all()

    records = (
        db_session.query(EvidenceEnvelopeRecord)
        .order_by(EvidenceEnvelopeRecord.input_index)
        .all()
    )
    assert len(records) == 2
    assert records[0].mapped_outputs == [{"name": "first"}, {"name": "second"}]
    assert records[1].mapped_outputs == []
    assert records[1].diagnostic == _diagnostic(retryable=True).model_dump()
    assert step.success_count == 1
    assert step.failure_count == 1
    assert step.output_count == 2
    assert run.status == "partial"
    assert run.lease_owner is None
    assert run.lease_expires_at is None

    reloaded = service.reconstruct_structured_result(step)
    assert reloaded.model_dump(mode="json") == result.model_dump(mode="json")


def test_failure_releases_run_lease(db_session):
    values = [{"value": "one"}]
    service, run, lease, _, _ = _started_run(db_session, values)

    service.fail_run(run, lease, _diagnostic())

    assert run.status == "failed"
    assert run.lease_owner is None
    assert run.lease_expires_at is None


def test_hold_is_aggregated_and_duplicate_replay_does_not_duplicate_evidence(db_session):
    values = [{"value": "one"}]
    service, run, lease, step, _ = _started_run(db_session, values)
    input_ref = canonical_input_hash(values[0])
    result = StructuredExecutionResult(
        enricher_name="template-lookup",
        outcomes=(
            InputOutcome(
                input_ref=input_ref,
                status=OutcomeStatus.HOLD,
                diagnostic=_diagnostic("rights_unspecified"),
                evidence=(
                    EvidenceEnvelope(
                        input_ref=input_ref,
                        destination_id="approved_directory",
                        endpoint_id="lookup",
                        capability="enrich.read",
                        policy_version="1",
                        source_rights="unspecified",
                        schema_version="v1",
                        parser_version="v1",
                        confidence=0.0,
                        verification_state="unverified",
                    ),
                ),
            ),
        ),
    )

    service.persist_structured_result(run, lease, step, result)
    with pytest.raises(RunLeaseLost):
        service.persist_structured_result(run, lease, step, result)

    assert run.status == "hold"
    assert step.hold_count == 1
    assert result.outcomes[0].outputs == ()
    assert (
        db_session.query(EvidenceEnvelopeRecord).one().mapped_outputs == []
    )
    assert service.reconstruct_structured_result(step).outcomes[0].outputs == ()
    assert db_session.query(EvidenceEnvelopeRecord).count() == 1
    assert service.aggregate_status(result.outcomes) == "hold"
    assert service.aggregate_status(
        (
            result.outcomes[0],
            InputOutcome(
                input_ref=canonical_input_hash({"value": "ok"}),
                status=OutcomeStatus.SUCCESS,
            ),
        )
    ) == "partial"
    assert service.aggregate_status(
        (
            InputOutcome(
                input_ref=canonical_input_hash({"value": "failed"}),
                status=OutcomeStatus.FAILURE,
                diagnostic=_diagnostic(),
            ),
        )
    ) == "failed"


def test_evidence_is_append_only_and_supersession_adds_a_new_row(db_session):
    values = [{"value": "one"}]
    service, run, lease, step, _ = _started_run(db_session, values)
    input_ref = canonical_input_hash(values[0])
    result = StructuredExecutionResult(
        enricher_name="template-lookup",
        outcomes=(
            InputOutcome(
                input_ref=input_ref,
                status=OutcomeStatus.SUCCESS,
                evidence=(_evidence(input_ref, reference="artifact://original"),),
            ),
        ),
    )
    service.persist_structured_result(run, lease, step, result)
    original = db_session.query(EvidenceEnvelopeRecord).one()

    original.source = "mutated"
    with pytest.raises(TypeError, match="append-only"):
        db_session.commit()
    db_session.rollback()

    original = db_session.get(EvidenceEnvelopeRecord, original.id)
    with pytest.raises(TypeError, match="append-only"):
        db_session.delete(original)
        db_session.commit()
    db_session.rollback()

    replacement = service.append_superseding_evidence(
        db_session.get(EvidenceEnvelopeRecord, original.id),
        _evidence(input_ref, reference="artifact://replacement"),
    )
    db_session.expire_all()

    records = db_session.query(EvidenceEnvelopeRecord).order_by(
        EvidenceEnvelopeRecord.evidence_index
    ).all()
    assert len(records) == 2
    assert records[0].artifact_reference == "artifact://original"
    assert replacement.supersedes_id == original.id
    assert records[1].artifact_reference == "artifact://replacement"


def test_stale_worker_mutators_are_fenced_across_file_backed_sessions(tmp_path):
    database_path = tmp_path / "lease-fencing.sqlite"
    engine = create_engine(f"sqlite:///{database_path}")

    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    seed_session = sessions()
    worker_a_session = sessions()
    worker_b_session = sessions()
    try:
        owner = Profile(email="lease-fencing@example.test", hashed_password="hash")
        seed_session.add(owner)
        seed_session.commit()
        seed_service = create_execution_service(seed_session)
        seeded_run, created = seed_service.create_or_reuse_run(
            owner_id=owner.id,
            idempotency_key="lease-fencing-key",
            input_digest=canonical_input_hash([{"value": "one"}]),
            input_count=1,
        )
        assert created
        run_id = seeded_run.id
        seed_session.close()

        worker_a = create_execution_service(worker_a_session)
        run_a = worker_a_session.get(FlowRun, run_id)
        lease_a = worker_a.claim_run(run_a, "worker-a")
        assert lease_a is not None
        assert lease_a.attempt == 1

        expiry_session = sessions()
        try:
            expired_run = expiry_session.get(FlowRun, run_id)
            expired_run.lease_expires_at = datetime.now(timezone.utc) - timedelta(
                seconds=1
            )
            expiry_session.commit()
        finally:
            expiry_session.close()

        worker_b = create_execution_service(worker_b_session)
        run_b = worker_b_session.get(FlowRun, run_id)
        lease_b = worker_b.claim_run(run_b, "worker-b")
        assert lease_b is not None
        assert lease_b.attempt == 2

        def persisted_state():
            audit_session = sessions()
            try:
                current_run = audit_session.get(FlowRun, run_id)
                current_step = audit_session.query(StepRun).filter_by(
                    flow_run_id=run_id, step_key="template-lookup"
                ).one_or_none()
                return {
                    "run": {
                        "attempt": current_run.attempt,
                        "checkpoint": current_run.checkpoint,
                        "completed_at": current_run.completed_at,
                        "lease_expires_at": current_run.lease_expires_at,
                        "lease_owner": current_run.lease_owner,
                        "safe_error_code": current_run.safe_error_code,
                        "safe_error_diagnostic": current_run.safe_error_diagnostic,
                        "started_at": current_run.started_at,
                        "status": current_run.status,
                        "updated_at": current_run.updated_at,
                    },
                    "step": (
                        None
                        if current_step is None
                        else {
                            "attempt": current_step.attempt,
                            "checkpoint": current_step.checkpoint,
                            "completed_at": current_step.completed_at,
                            "failure_count": current_step.failure_count,
                            "hold_count": current_step.hold_count,
                            "input_count": current_step.input_count,
                            "output_count": current_step.output_count,
                            "retryable": current_step.retryable,
                            "started_at": current_step.started_at,
                            "status": current_step.status,
                            "success_count": current_step.success_count,
                            "updated_at": current_step.updated_at,
                        }
                    ),
                    "evidence_count": audit_session.query(EvidenceEnvelopeRecord).count(),
                }
            finally:
                audit_session.close()

        before_stale_begin = persisted_state()
        with pytest.raises(RunLeaseLost):
            worker_a.begin_or_resume_step(
                run_a, lease_a, "template-lookup", input_count=1
            )
        assert persisted_state() == before_stale_begin

        step_b = worker_b.begin_or_resume_step(
            run_b, lease_b, "template-lookup", input_count=1
        )
        step_a = worker_a_session.get(StepRun, step_b.id)
        before_stale_checkpoint = persisted_state()
        with pytest.raises(RunLeaseLost):
            worker_a.update_run_checkpoint(run_a, lease_a, {"stale": "run"})
        assert persisted_state() == before_stale_checkpoint
        with pytest.raises(RunLeaseLost):
            worker_a.update_step_checkpoint(
                run_a, lease_a, step_a, {"stale": "step"}
            )
        assert persisted_state() == before_stale_checkpoint

        input_ref = canonical_input_hash({"value": "one"})
        result = StructuredExecutionResult(
            enricher_name="template-lookup",
            outcomes=(
                InputOutcome(
                    input_ref=input_ref,
                    status=OutcomeStatus.SUCCESS,
                    outputs=({"value": "current-worker"},),
                    evidence=(_evidence(input_ref),),
                ),
            ),
        )
        before_stale_persist = persisted_state()
        with pytest.raises(RunLeaseLost):
            worker_a.persist_structured_result(run_a, lease_a, step_a, result)
        assert persisted_state() == before_stale_persist

        before_stale_failure = persisted_state()
        with pytest.raises(RunLeaseLost):
            worker_a.fail_run(run_a, lease_a, _diagnostic("stale_worker"))
        assert persisted_state() == before_stale_failure

        worker_b.persist_structured_result(run_b, lease_b, step_b, result)
        completed_state = persisted_state()
        assert completed_state["run"]["status"] == "completed"
        assert completed_state["run"]["lease_owner"] is None
        assert completed_state["run"]["lease_expires_at"] is None
        assert completed_state["step"]["status"] == "completed"
        assert completed_state["evidence_count"] == 1
    finally:
        seed_session.close()
        worker_a_session.close()
        worker_b_session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()
