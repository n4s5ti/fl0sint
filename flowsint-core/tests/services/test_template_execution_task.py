"""Task-level persistence contract for database template execution."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from threading import Barrier, Lock, get_ident
from uuid import uuid4

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session as OrmSession
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
    FlowRun,
    Profile,
    Scan,
    StepRun,
)
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

class _InterleavingTemplateEnricher:
    attempts = 0
    recover = None

    @staticmethod
    def get_params_schema_for_template(template):
        return []

    def __init__(self, **kwargs):
        pass

    async def execute_structured(self, values):
        type(self).attempts += 1
        worker = f"attempt-{type(self).attempts}"
        if type(self).attempts == 1:
            await asyncio.to_thread(type(self).recover)
        input_ref = canonical_input_hash(values[0])
        return StructuredExecutionResult(
            enricher_name="safe-template",
            outcomes=(
                InputOutcome(
                    input_ref=input_ref,
                    status=OutcomeStatus.SUCCESS,
                    outputs=({"worker": worker},),
                    evidence=(
                        EvidenceEnvelope(
                            input_ref=input_ref,
                            request_url_pattern="https://api.example.test/{{address}}",
                            artifact_sha256="c" * 64,
                            artifact_reference=f"artifact://{worker}",
                            source_rights="public",
                            schema_version="v1",
                            parser_version="v1",
                            confidence=1.0,
                            verification_state="verified",
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
    first_task_id = uuid4()
    second_task_id = uuid4()
    first = task_module.run_template_enricher.apply(
        args=("safe-template", values, None, owner_id),
        kwargs={"idempotency_key": "replay-safe-key"},
        task_id=str(first_task_id),
    )
    second = task_module.run_template_enricher.apply(
        args=("safe-template", values, None, owner_id),
        kwargs={"idempotency_key": "replay-safe-key"},
        task_id=str(second_task_id),
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
    assert db_session.query(Scan).count() == 1
    assert db_session.get(Scan, first_task_id) is not None
    assert db_session.get(Scan, second_task_id) is None


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
    assert db_session.get(Scan, active_task_id) is None

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
    assert db_session.query(Scan).count() == 1


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


def test_template_task_fences_stale_attempt_after_same_task_id_recovery(
    tmp_path, monkeypatch
):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'stale-template-task.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    task_session = sessionmaker(bind=engine, expire_on_commit=False)
    db_session = task_session()
    owner = Profile(email="stale-task@example.test", hashed_password="hash")
    db_session.add(owner)
    db_session.commit()
    owner_id = str(owner.id)
    task_id = uuid4()
    values = [{"address": "203.0.113.10"}]
    monkeypatch.setattr(task_module, "SessionLocal", task_session)
    monkeypatch.setattr(task_module, "Template", lambda **content: SimpleNamespace())
    monkeypatch.setattr(
        task_module, "TemplateEnricher", _InterleavingTemplateEnricher
    )
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

    recovered = {}

    def recover_expired_attempt():
        recovery_session = task_session()
        try:
            flow_run = recovery_session.get(FlowRun, task_id)
            flow_run.lease_expires_at = datetime.now(timezone.utc) - timedelta(
                seconds=1
            )
            recovery_session.commit()
        finally:
            recovery_session.close()
        recovered["result"] = task_module.run_template_enricher.apply(
            args=("safe-template", values, None, owner_id),
            task_id=str(task_id),
        )

    _InterleavingTemplateEnricher.attempts = 0
    _InterleavingTemplateEnricher.recover = recover_expired_attempt
    stale = task_module.run_template_enricher.apply(
        args=("safe-template", values, None, owner_id),
        task_id=str(task_id),
    )

    assert stale.successful()
    assert recovered["result"].successful()
    assert stale.get()["result"] == recovered["result"].get()["result"]
    assert _InterleavingTemplateEnricher.attempts == 2
    db_session.expire_all()
    flow_run = db_session.get(FlowRun, task_id)
    step_run = db_session.query(StepRun).one()
    evidence = db_session.query(EvidenceEnvelopeRecord).one()
    scan = db_session.get(Scan, task_id)
    canonical_result = recovered["result"].get()["result"]

    assert flow_run.attempt == 2
    assert step_run.attempt == 2
    assert evidence.attempt == 2
    assert evidence.artifact_reference == "artifact://attempt-2"
    assert db_session.query(EvidenceEnvelopeRecord).count() == 1
    assert db_session.query(Scan).count() == 1
    assert scan.status is EventLevel.COMPLETED
    assert scan.details == canonical_result
    assert canonical_result["outcomes"][0]["outputs"] == [{"worker": "attempt-2"}]
    db_session.close()
    engine.dispose()


def test_final_replay_repairs_pending_canonical_scan(db_session, monkeypatch):
    owner = Profile(email="replay-repair@example.test", hashed_password="hash")
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
    _StructuredTemplateEnricher.instances = 0
    _StructuredTemplateEnricher.executions = 0
    values = [{"address": "203.0.113.10"}]
    canonical_scan_id = uuid4()
    replay_scan_id = uuid4()

    first = task_module.run_template_enricher.apply(
        args=("safe-template", values, None, owner_id),
        kwargs={"idempotency_key": "repair-final-scan"},
        task_id=str(canonical_scan_id),
    )
    canonical_result = first.get()["result"]
    db_session.expire_all()
    canonical_scan = db_session.get(Scan, canonical_scan_id)
    canonical_scan.status = EventLevel.PENDING
    canonical_scan.error = None
    canonical_scan.details = None
    db_session.commit()

    replay = task_module.run_template_enricher.apply(
        args=("safe-template", values, None, owner_id),
        kwargs={"idempotency_key": "repair-final-scan"},
        task_id=str(replay_scan_id),
    )
    db_session.expire_all()

    assert first.successful()
    assert replay.successful()
    assert replay.get()["result"] == canonical_result
    repaired_scan = db_session.get(Scan, canonical_scan_id)
    assert repaired_scan.status is EventLevel.COMPLETED
    assert repaired_scan.error is None
    assert repaired_scan.details == canonical_result
    assert db_session.get(Scan, replay_scan_id) is None
    assert _StructuredTemplateEnricher.executions == 1


def test_failed_replay_repairs_missing_canonical_scan(db_session, monkeypatch):
    owner = Profile(email="failed-repair@example.test", hashed_password="hash")
    db_session.add(owner)
    db_session.commit()
    owner_id = str(owner.id)
    values = [{"address": "203.0.113.10"}]
    canonical_scan_id = uuid4()
    replay_scan_id = uuid4()
    execution_service = create_execution_service(db_session)
    flow_run, created = execution_service.create_or_reuse_run(
        owner_id=owner.id,
        idempotency_key="repair-failed-scan",
        input_digest=canonical_input_hash(values),
        input_count=len(values),
        run_id=canonical_scan_id,
    )
    assert created
    lease = execution_service.claim_run(flow_run, str(canonical_scan_id))
    assert lease is not None
    diagnostic = RedactedDiagnostic(
        code="template_execution_failed",
        safe_message="Template execution could not be completed",
        retryable=False,
    )
    execution_service.fail_run(flow_run, lease, diagnostic)
    assert db_session.get(Scan, canonical_scan_id) is None

    task_session = sessionmaker(bind=db_session.get_bind())
    monkeypatch.setattr(task_module, "SessionLocal", task_session)
    monkeypatch.setattr(task_module, "Template", lambda **content: SimpleNamespace())
    monkeypatch.setattr(task_module, "TemplateEnricher", _StructuredTemplateEnricher)

    replay = task_module.run_template_enricher.apply(
        args=("safe-template", values, None, owner_id),
        kwargs={"idempotency_key": "repair-failed-scan"},
        task_id=str(replay_scan_id),
    )
    db_session.expire_all()

    assert replay.successful()
    assert replay.get()["result"] == {
        "status": "failed",
        "flow_run_id": str(flow_run.id),
        "flow_run_reference": f"flow_run:{flow_run.id}",
        "error": diagnostic.safe_message,
    }
    repaired_scan = db_session.get(Scan, canonical_scan_id)
    assert repaired_scan.status is EventLevel.FAILED
    assert repaired_scan.error == diagnostic.safe_message
    assert repaired_scan.details == replay.get()["result"]
    assert db_session.get(Scan, replay_scan_id) is None


def test_atomic_scan_insert_converges_concurrent_replays(tmp_path):
    class InsertBarrierSession(OrmSession):
        barrier = Barrier(2)
        seen_threads = set()
        seen_lock = Lock()

        def execute(self, statement, *args, **kwargs):
            should_wait = False
            if getattr(statement, "table", None) is Scan.__table__:
                thread_id = get_ident()
                with type(self).seen_lock:
                    if thread_id not in type(self).seen_threads:
                        type(self).seen_threads.add(thread_id)
                        should_wait = True
            if should_wait:
                type(self).barrier.wait(timeout=10)
            return super().execute(statement, *args, **kwargs)

    engine = create_engine(
        f"sqlite:///{tmp_path / 'concurrent-replay.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )

    @event.listens_for(engine, "connect")
    def configure_sqlite(connection, _record):
        cursor = connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=10000")
        cursor.close()

    Base.metadata.create_all(engine)
    task_session = sessionmaker(
        bind=engine, class_=InsertBarrierSession, expire_on_commit=False
    )
    canonical_scan_id = uuid4()

    def create_canonical_scan(_index):
        with task_session() as session:
            scan = task_module._get_or_create_scan(
                session, canonical_scan_id, sketch_id=None
            )
            session.commit()
            return scan.id

    with ThreadPoolExecutor(max_workers=2) as executor:
        scan_ids = list(executor.map(create_canonical_scan, range(2)))

    with task_session() as session:
        assert scan_ids == [canonical_scan_id, canonical_scan_id]
        assert session.query(Scan).count() == 1
        canonical_scan = session.get(Scan, canonical_scan_id)
        assert canonical_scan.status is EventLevel.PENDING
        assert canonical_scan.sketch_id is None
    engine.dispose()
