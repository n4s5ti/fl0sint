"""Task-level persistence contract for database template execution."""
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy.orm import sessionmaker

from flowsint_core.core.execution import (
    EvidenceEnvelope,
    InputOutcome,
    OutcomeStatus,
    StructuredExecutionResult,
    canonical_input_hash,
)
from flowsint_core.core.models import EvidenceEnvelopeRecord, FlowRun, Profile, Scan, StepRun
from flowsint_core.tasks import enricher as task_module


class _StructuredTemplateEnricher:
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
                            observed_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
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
    assert db_session.query(Scan).count() == 2
