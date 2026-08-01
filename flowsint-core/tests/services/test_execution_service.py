"""SQLite contracts for durable structured execution persistence."""
from datetime import datetime, timezone

import pytest

from flowsint_core.core.execution import (
    EvidenceEnvelope,
    InputOutcome,
    OutcomeStatus,
    RedactedDiagnostic,
    StructuredExecutionResult,
    canonical_input_hash,
)
from flowsint_core.core.models import EvidenceEnvelopeRecord, Profile
from flowsint_core.core.services.execution_service import create_execution_service


def _diagnostic(code: str = "request_failed", retryable: bool = False):
    return RedactedDiagnostic(
        code=code,
        safe_message="The request could not be completed",
        retryable=retryable,
    )


def _evidence(input_ref: str, *, reference: str = "artifact://safe-reference"):
    return EvidenceEnvelope(
        input_ref=input_ref,
        request_url_pattern="https://api.example.test/lookup/{{value}}",
        artifact_sha256="a" * 64,
        artifact_reference=reference,
        source_rights="public",
        schema_version="v1",
        parser_version="v1",
        confidence=0.9,
        verification_state="verified",
        observed_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
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
    service.begin_or_resume_run(run)
    step = service.begin_or_resume_step(run, "template-lookup", len(values))
    return service, run, step, owner


def test_create_or_reuse_and_resume_preserves_checkpoints(db_session):
    values = [{"value": "one"}]
    service, run, step, owner = _started_run(db_session, values)

    service.update_run_checkpoint(run, {"next_step": "template-lookup"})
    service.update_step_checkpoint(step, {"next_input_index": 1})
    same_run, created = service.create_or_reuse_run(
        owner_id=owner.id,
        idempotency_key="template-task-key",
        input_digest=canonical_input_hash(values),
        input_count=len(values),
    )

    assert not created
    assert same_run.id == run.id
    service.begin_or_resume_run(same_run)
    resumed_step = service.begin_or_resume_step(same_run, "template-lookup", 1)
    db_session.expire_all()

    assert db_session.get(type(run), run.id).attempt == 2
    assert db_session.get(type(run), run.id).checkpoint == {
        "next_step": "template-lookup"
    }
    assert resumed_step.attempt == 2
    assert resumed_step.checkpoint == {"next_input_index": 1}


def test_persists_sibling_outcomes_and_one_to_many_output_grouping(db_session):
    values = [{"value": "one"}, {"value": "two"}]
    service, run, step, _ = _started_run(db_session, values)
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

    service.persist_structured_result(run, step, result)
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

    reloaded = service.reconstruct_structured_result(step)
    assert reloaded.model_dump(mode="json") == result.model_dump(mode="json")


def test_hold_is_aggregated_and_duplicate_replay_does_not_duplicate_evidence(db_session):
    values = [{"value": "one"}]
    service, run, step, _ = _started_run(db_session, values)
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
                        request_url_pattern="https://api.example.test/{{value}}",
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

    service.persist_structured_result(run, step, result)
    service.persist_structured_result(run, step, result)

    assert run.status == "hold"
    assert step.hold_count == 1
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
    service, run, step, _ = _started_run(db_session, values)
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
    service.persist_structured_result(run, step, result)
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
