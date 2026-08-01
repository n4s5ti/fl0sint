"""Task-level persistence contract for database template execution."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy.orm import sessionmaker

from flowsint_core.core.execution import (
    EvidenceEnvelope,
    InputOutcome,
    OutcomeStatus,
    RedactedDiagnostic,
    StructuredExecutionResult,
    canonical_input_hash,
)
from flowsint_core.core.models import EvidenceEnvelopeRecord, FlowRun, Profile, Scan, StepRun
from flowsint_core.core.enums import EventLevel
from flowsint_core.core.services.execution_service import create_execution_service
from flowsint_core.tasks import enricher as task_module


class _StructuredTemplateEnricher:
    instances = 0
    executions = 0

    @staticmethod
    def get_params_schema_for_template(template):
        return []

    def __init__(self, **kwargs):
        type(self).instances += 1

    async def execute_structured(self, values):
        type(self).executions += 1
        input_ref = canonical_input_hash(values[0])
        return StructuredExecutionResult(
            enricher_name="safe-template",
            outcomes=(
                InputOutcome(
                    input_ref=input_ref,
                    status=OutcomeStatus.SUCCESS,
                    outputs=({"address": "203.0.113.10"},),
                    evidence=(
                        EvidenceEnvelope(
                            input_ref=input_ref,
                            request_url_pattern="https://api.example.test/{{address}}",
                            artifact_sha256="b" * 64,
                            artifact_reference="artifact://safe-reference",
                            source_rights="public",
                            schema_version="v1",
                            parser_version="v1",
                            confidence=1.0,
                            verification_state="verified",
                            event_at=datetime(2026, 7, 31, tzinfo=timezone.utc),
                            retrieved_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
                            ingested_at=datetime(2026, 8, 1, 0, 0, 1, tzinfo=timezone.utc),
                        ),
                    ),
                ),
            ),
        )


class _HoldingTemplateEnricher:
    @staticmethod
    def get_params_schema_for_template(template):
        return []

    def __init__(self, **kwargs):
        pass

    async def execute_structured(self, values):
        input_ref = canonical_input_hash(values[0])
        return StructuredExecutionResult(
            enricher_name="safe-template",
            outcomes=(
                InputOutcome(
                    input_ref=input_ref,
                    status=OutcomeStatus.HOLD,
                    diagnostic=RedactedDiagnostic(
                        code="source_rights_unspecified",
                        safe_message="Source rights must be specified.",
                        retryable=False,
                    ),
                    evidence=(
                        EvidenceEnvelope(
                            input_ref=input_ref,
                            request_url_pattern="https://api.example.test/{{address}}",
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

def test_template_task_reuses_idempotency_key_without_duplicate_evidence(
    db_session, monkeypatch
):
    owner = Profile(email="task@example.test", hashed_password="hash")
    db_session.add(owner)
    db_session.commit()
    _StructuredTemplateEnricher.instances = 0
    _StructuredTemplateEnricher.executions = 0
    owner_id = str(owner.id)

    task_session = sessionmaker(bind=db_session.get_bind())
    monkeypatch.setattr(task_module, "SessionLocal", task_session)
    monkeypatch.setattr(task_module, "Template", lambda **content: SimpleNamespace())
    monkeypatch.setattr(task_module, "TemplateEnricher", _StructuredTemplateEnricher)
    monkeypatch.setattr(
        task_module,
        "create_vault_service",
        lambda session: SimpleNamespace(for_user=lambda owner_id: None),
    )
    monkeypatch.setattr(
        task_module,
        "create_enricher_template_service",
        lambda session: SimpleNamespace(
            find_by_name=lambda template_name, owner_id: SimpleNamespace(content={})
        ),
    )

    values = [{"address": "203.0.113.10"}]
    first = task_module.run_template_enricher.apply(
        args=("safe-template", values, None, owner_id),
        kwargs={"idempotency_key": "replay-safe-key"},
        task_id=str(uuid4()),
    )
    second = task_module.run_template_enricher.apply(
        args=("safe-template", values, None, owner_id),
        kwargs={"idempotency_key": "replay-safe-key"},
        task_id=str(uuid4()),
    )

    db_session.expire_all()

    assert first.successful()
    assert second.successful()
    assert second.get()["result"] == first.get()["result"]
    assert db_session.query(FlowRun).count() == 1
    assert db_session.query(StepRun).one().attempt == 1
    assert db_session.query(EvidenceEnvelopeRecord).count() == 1
    assert _StructuredTemplateEnricher.instances == 1
    assert _StructuredTemplateEnricher.executions == 1
    assert db_session.query(Scan).count() == 2


def test_active_template_duplicate_attaches_until_expired_lease_recovers(
    db_session, monkeypatch
):
    owner = Profile(email="active-task@example.test", hashed_password="hash")
    db_session.add(owner)
    db_session.commit()
    owner_id = str(owner.id)
    values = [{"address": "203.0.113.10"}]
    idempotency_key = "active-template-key"
    execution_service = create_execution_service(db_session)
    flow_run, created = execution_service.create_or_reuse_run(
        owner_id=owner.id,
        idempotency_key=idempotency_key,
        input_digest=canonical_input_hash(values),
        input_count=len(values),
    )
    assert created
    assert execution_service.claim_run(flow_run, "active-worker") is not None

    _StructuredTemplateEnricher.instances = 0
    _StructuredTemplateEnricher.executions = 0
    task_session = sessionmaker(bind=db_session.get_bind())
    monkeypatch.setattr(task_module, "SessionLocal", task_session)
    monkeypatch.setattr(task_module, "Template", lambda **content: SimpleNamespace())
    monkeypatch.setattr(task_module, "TemplateEnricher", _StructuredTemplateEnricher)
    monkeypatch.setattr(
        task_module,
        "create_vault_service",
        lambda session: SimpleNamespace(for_user=lambda owner_id: None),
    )
    monkeypatch.setattr(
        task_module,
        "create_enricher_template_service",
        lambda session: SimpleNamespace(
            find_by_name=lambda template_name, owner_id: SimpleNamespace(content={})
        ),
    )

    active_task_id = uuid4()
    attached = task_module.run_template_enricher.apply(
        args=("safe-template", values, None, owner_id),
        kwargs={"idempotency_key": idempotency_key},
        task_id=str(active_task_id),
    )

    assert attached.successful()
    assert attached.get()["result"] == {
        "status": "in_progress",
        "flow_run_id": str(flow_run.id),
        "flow_run_reference": f"flow_run:{flow_run.id}",
    }
    assert _StructuredTemplateEnricher.instances == 0
    assert _StructuredTemplateEnricher.executions == 0
    assert db_session.query(StepRun).count() == 0
    assert db_session.query(EvidenceEnvelopeRecord).count() == 0
    active_scan = db_session.get(Scan, active_task_id)
    assert active_scan.status is EventLevel.COMPLETED
    assert active_scan.details["flow_run_id"] == str(flow_run.id)

    db_session.refresh(flow_run)
    flow_run.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db_session.commit()

    recovered = task_module.run_template_enricher.apply(
        args=("safe-template", values, None, owner_id),
        kwargs={"idempotency_key": idempotency_key},
        task_id=str(uuid4()),
    )
    db_session.expire_all()


    assert recovered.successful()
    assert _StructuredTemplateEnricher.instances == 1
    assert _StructuredTemplateEnricher.executions == 1
    recovered_run = db_session.query(FlowRun).one()
    assert recovered_run.attempt == 2
    assert recovered_run.lease_owner is None
    assert recovered_run.lease_expires_at is None
    assert db_session.query(StepRun).one().attempt == 1
    assert db_session.query(EvidenceEnvelopeRecord).count() == 1
    assert db_session.query(Scan).count() == 2


def test_template_task_hold_omits_outputs_from_evidence_and_scan_details(
    db_session, monkeypatch
):
    owner = Profile(email="hold-task@example.test", hashed_password="hash")
    db_session.add(owner)
    db_session.commit()
    owner_id = str(owner.id)
    task_session = sessionmaker(bind=db_session.get_bind())
    monkeypatch.setattr(task_module, "SessionLocal", task_session)
    monkeypatch.setattr(task_module, "Template", lambda **content: SimpleNamespace())
    monkeypatch.setattr(task_module, "TemplateEnricher", _HoldingTemplateEnricher)
    monkeypatch.setattr(
        task_module,
        "create_vault_service",
        lambda session: SimpleNamespace(for_user=lambda owner_id: None),
    )
    monkeypatch.setattr(
        task_module,
        "create_enricher_template_service",
        lambda session: SimpleNamespace(
            find_by_name=lambda template_name, owner_id: SimpleNamespace(content={})
        ),
    )

    task_id = uuid4()
    task_result = task_module.run_template_enricher.apply(
        args=("safe-template", [{"address": "203.0.113.10"}], None, owner_id),
        task_id=str(task_id),
    )

    assert task_result.successful()
    assert task_result.get()["result"]["outcomes"][0]["outputs"] == []
    db_session.expire_all()
    assert db_session.query(EvidenceEnvelopeRecord).one().mapped_outputs == []
    assert db_session.get(Scan, task_id).details["outcomes"][0]["outputs"] == []
