"""Stage 3 replay coverage for registry-backed connector template tasks."""

import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier, Lock, get_ident
from types import SimpleNamespace
from uuid import NAMESPACE_URL, uuid4, uuid5

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session as OrmSession
from sqlalchemy.orm import sessionmaker

from flowsint_core.core.connector_egress import connector_template_digest
from flowsint_core.core.enums import EventLevel
from flowsint_core.core.execution import (
    EvidenceEnvelope,
    InputOutcome,
    OutcomeStatus,
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
from flowsint_core.core.services.execution_service import create_execution_service
from flowsint_core.tasks import enricher as task_module
from flowsint_core.templates.types import Template


def connector_template() -> Template:
    return Template.model_validate(
        {
            "name": "approved-connector",
            "description": "test",
            "category": "Location",
            "version": 1.0,
            "input": {"type": "Location", "key": "address"},
            "connector": {
                "destination_id": "approved_directory",
                "endpoint_id": "lookup",
                "capability": "enrich.read",
            },
            "output": {"type": "Location"},
            "evidence": {"source_rights": "test-only"},
        }
    )


class StructuredConnectorEnricher:
    instances = 0
    executions = 0

    def __init__(self, **_kwargs):
        type(self).instances += 1
        self.endpoint = SimpleNamespace(
            destination_id="approved_directory",
            endpoint_id="lookup",
            capability="enrich.read",
            policy_version="1",
        )
        self.vault = None

    async def execute_structured(self, values):
        type(self).executions += 1
        input_ref = canonical_input_hash(values[0])
        return StructuredExecutionResult(
            enricher_name="connector:approved_directory:lookup",
            outcomes=(
                InputOutcome(
                    input_ref=input_ref,
                    status=OutcomeStatus.SUCCESS,
                    outputs=({"private": "mapped output"},),
                    evidence=(
                        EvidenceEnvelope(
                            input_ref=input_ref,
                            destination_id="approved_directory",
                            endpoint_id="lookup",
                            capability="enrich.read",
                            policy_version="1",
                            artifact_sha256="b" * 64,
                            artifact_reference="body:sha256:" + "b" * 64,
                            source_rights="test-only",
                            schema_version="v1",
                            parser_version="v1",
                            confidence=1.0,
                            verification_state="verified",
                            event_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
                        ),
                    ),
                ),
            ),
        )


def patch_connector_task(monkeypatch, db_session, template):
    task_session = sessionmaker(bind=db_session.get_bind())
    monkeypatch.setattr(task_module, "SessionLocal", task_session)
    monkeypatch.setattr(task_module, "TemplateEnricher", StructuredConnectorEnricher)
    monkeypatch.setattr(
        task_module,
        "create_vault_service",
        lambda _session: SimpleNamespace(for_user=lambda _owner_id: None),
    )
    monkeypatch.setattr(
        task_module,
        "create_enricher_template_service",
        lambda _session: SimpleNamespace(
            get_template=lambda _template_id, _owner_id: SimpleNamespace(
                content=template.model_dump(mode="json")
            )
        ),
    )


def run_task(owner_id, template, values, task_id, idempotency_key, template_id=None):
    template_digest = connector_template_digest(template)
    template_id = template_id or uuid5(
        NAMESPACE_URL, f"flowsint:connector-template:{template_digest}"
    )
    return task_module.run_connector_template.apply(
        args=(
            str(template_id),
            template_digest,
            values,
            None,
            owner_id,
        ),
        kwargs={"idempotency_key": idempotency_key},
        task_id=str(task_id),
    )


def test_connector_task_reuses_durable_result_and_redacts_scan(
    db_session, monkeypatch
):
    owner = Profile(email="connector-task@example.test", hashed_password="hash")
    db_session.add(owner)
    db_session.commit()
    template = connector_template()
    values = [{"address": "private input"}]
    StructuredConnectorEnricher.instances = 0
    StructuredConnectorEnricher.executions = 0
    patch_connector_task(monkeypatch, db_session, template)

    first_task_id = uuid4()
    first = run_task(
        str(owner.id), template, values, first_task_id, "connector-replay-key"
    )
    second = run_task(
        str(owner.id), template, values, uuid4(), "connector-replay-key"
    )

    assert first.successful()
    assert second.successful()
    assert first.get()["result"] == second.get()["result"]
    assert StructuredConnectorEnricher.instances == 1
    assert StructuredConnectorEnricher.executions == 1
    assert db_session.query(FlowRun).count() == 1
    assert db_session.query(StepRun).one().attempt == 1
    assert db_session.query(EvidenceEnvelopeRecord).count() == 1
    assert db_session.get(Scan, first_task_id).status is EventLevel.COMPLETED
    scan_text = str(db_session.get(Scan, first_task_id).details)
    assert "private input" not in scan_text
    assert "mapped output" not in scan_text
    assert "https://" not in scan_text


def test_changed_template_replay_preserves_completed_canonical_scan(
    db_session, monkeypatch
):
    owner = Profile(email="connector-mismatch@example.test", hashed_password="hash")
    db_session.add(owner)
    db_session.commit()
    template = connector_template()
    changed_template = template.model_copy(
        update={"description": "changed connector template"}
    )
    values = [{"address": "private input"}]
    template_id = uuid4()
    StructuredConnectorEnricher.instances = 0
    StructuredConnectorEnricher.executions = 0
    patch_connector_task(monkeypatch, db_session, template)

    first_task_id = uuid4()
    first = run_task(
        str(owner.id),
        template,
        values,
        first_task_id,
        "connector-template-mismatch",
        template_id,
    )
    assert first.successful()
    original_scan = db_session.get(Scan, first_task_id)
    assert original_scan is not None
    original_details = original_scan.details

    patch_connector_task(monkeypatch, db_session, changed_template)
    replay = run_task(
        str(owner.id),
        changed_template,
        values,
        uuid4(),
        "connector-template-mismatch",
        template_id,
    )

    assert replay.failed()
    with pytest.raises(RuntimeError, match="Connector execution could not be completed"):
        replay.get()

    db_session.expire_all()
    canonical_scan = db_session.get(Scan, first_task_id)
    assert canonical_scan is not None
    assert canonical_scan.status is EventLevel.COMPLETED
    assert canonical_scan.error is None
    assert canonical_scan.details == original_details
    assert db_session.query(FlowRun).count() == 1
    assert StructuredConnectorEnricher.instances == 1
    assert StructuredConnectorEnricher.executions == 1

def test_connector_task_attaches_then_recovers_expired_lease(db_session, monkeypatch):
    owner = Profile(email="connector-lease@example.test", hashed_password="hash")
    db_session.add(owner)
    db_session.commit()
    template = connector_template()
    values = [{"address": "private input"}]
    execution_service = create_execution_service(db_session)
    template_digest = connector_template_digest(template)
    template_id = uuid5(
        NAMESPACE_URL, f"flowsint:connector-template:{template_digest}"
    )
    flow_run, created = execution_service.create_or_reuse_run(
        owner_id=owner.id,
        idempotency_key="connector-active-key",
        input_digest=canonical_input_hash(values),
        input_count=len(values),
        operation_digest=hashlib.sha256(
            f"{template_id}:{template_digest}".encode("utf-8")
        ).hexdigest(),
    )
    assert created
    assert execution_service.claim_run(flow_run, "active-worker") is not None
    patch_connector_task(monkeypatch, db_session, template)

    attached = run_task(
        str(owner.id), template, values, uuid4(), "connector-active-key"
    )
    assert attached.successful()
    assert attached.get()["result"]["status"] == "in_progress"
    assert db_session.query(StepRun).count() == 0

    db_session.refresh(flow_run)
    flow_run.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db_session.commit()
    recovered = run_task(
        str(owner.id), template, values, uuid4(), "connector-active-key"
    )

    assert recovered.successful()
    assert db_session.query(FlowRun).one().attempt == 2
    assert db_session.query(EvidenceEnvelopeRecord).count() == 1
    assert db_session.query(Scan).count() == 1


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
        f"sqlite:///{tmp_path / 'concurrent-connector-replay.db'}",
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
        assert session.get(Scan, canonical_scan_id).status is EventLevel.PENDING
    engine.dispose()
