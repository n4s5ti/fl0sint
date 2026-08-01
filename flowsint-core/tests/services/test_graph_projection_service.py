"""Deterministic contracts for durable approved graph projection (not run here)."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
from uuid import uuid4

import pytest

from flowsint_core.core.execution import (
    EvidenceEnvelope,
    InputOutcome,
    OutcomeStatus,
    StructuredExecutionResult,
)
from flowsint_core.core.models import (
    EvidenceEnvelopeRecord,
    FlowRun,
    GraphProjectionJob,
    Profile,
    StepRun,
)
from flowsint_core.core.projection.contracts import (
    ApprovedProjectionProfile,
    ApprovedProjectionRegistry,
    Cardinality,
    EntityRule,
    ObservationRule,
    ProjectionKind,
    RelationshipRule,
)
from flowsint_core.core.projection.graph_repository import (
    ProjectionGraphRepository,
    ProjectionBatch,
    ProjectionGraphUnavailable,
)
from flowsint_core.core.projection.repository import ProjectionRepository
from flowsint_core.core.projection.service import GraphProjectionService
from flowsint_core.core.services.execution_service import ExecutionService
from flowsint_core.templates.types import TemplateGraphProjectionRef


class RecordingGraphDriver:
    """In-memory graph-driver seam that preserves the actual generated write batch."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.failure: Exception | None = None

    def execute_write(self, query: str, parameters: dict | None = None) -> None:
        if self.failure is not None:
            failure = self.failure
            self.failure = None
            raise failure
        self.calls.append((query, deepcopy(parameters or {})))


def _profile(profile_id: str = "parcel_owner", source_id: str = "county_a"):
    return ApprovedProjectionProfile(
        profile_id=profile_id,
        revision=1,
        source_id=source_id,
        entities=(
            EntityRule(
                alias="parcel",
                kind=ProjectionKind.PARCEL,
                items_pointer="/parcels",
                source_record_id_pointer="/id",
                effective_from_pointer="/effective_from",
                effective_to_pointer="/effective_to",
                observations=(
                    ObservationRule("address", "/address", "string"),
                ),
                cardinality=Cardinality(minimum=1, maximum=2),
            ),
            EntityRule(
                alias="party",
                kind=ProjectionKind.PARTY,
                items_pointer="/parties",
                source_record_id_pointer="/id",
                effective_from_pointer="/effective_from",
                effective_to_pointer=None,
                observations=(ObservationRule("name", "/name", "string"),),
                cardinality=Cardinality(minimum=1, maximum=2),
            ),
        ),
        relationships=(
            RelationshipRule(
                rule_id="owns_record",
                semantic="owns",
                items_pointer="/ownership",
                from_alias="party",
                from_source_record_id_pointer="/party_id",
                to_alias="parcel",
                to_source_record_id_pointer="/parcel_id",
                effective_from_pointer="/effective_from",
                effective_to_pointer="/effective_to",
                cardinality=Cardinality(minimum=1, maximum=2),
            ),
        ),
    )


def _output(address: str = "1 Main Street", parcel_id: str = "parcel-1") -> dict:
    return {
        "parcels": [
            {
                "id": parcel_id,
                "address": address,
                "effective_from": "2026-01-01",
                "effective_to": None,
            }
        ],
        "parties": [
            {
                "id": "party-1",
                "name": "Owner",
                "effective_from": "2026-01-01",
            }
        ],
        "ownership": [
            {
                "party_id": "party-1",
                "parcel_id": parcel_id,
                "effective_from": "2026-01-01",
                "effective_to": None,
            }
        ],
    }


def _persisted_evidence(db_session, output: dict) -> EvidenceEnvelopeRecord:
    now = datetime.now(timezone.utc)
    owner = Profile(
        email=f"projection-{uuid4()}@example.test",
        hashed_password="hash",
    )
    db_session.add(owner)
    db_session.flush()
    run = FlowRun(
        owner_id=owner.id,
        idempotency_key=str(uuid4()),
        input_digest="a" * 64,
        input_count=1,
        status="completed",
    )
    db_session.add(run)
    db_session.flush()
    step = StepRun(
        flow_run_id=run.id,
        step_key="connector-template:test",
        attempt=1,
        status="completed",
        input_count=1,
    )
    db_session.add(step)
    db_session.flush()
    record = EvidenceEnvelopeRecord(
        flow_run_id=run.id,
        step_run_id=step.id,
        attempt=1,
        input_index=0,
        evidence_index=0,
        input_ref="b" * 64,
        status="success",
        retryable=False,
        mapped_outputs=[output],
        diagnostic=None,
        source="connector-template:test",
        destination_id="county_records",
        endpoint_id="parcel_lookup",
        capability="enrich.read",
        policy_version="1",
        artifact_sha256="c" * 64,
        artifact_reference="artifact://safe-evidence",
        event_at=None,
        retrieved_at=now,
        ingested_at=now,
        source_rights="official-record",
        schema_version="1",
        parser_version="1",
        confidence=0.8,
        verification_state="unverified",
    )
    db_session.add(record)
    db_session.commit()
    return record


def _enqueue(db_session, record: EvidenceEnvelopeRecord, profile):
    registry = ApprovedProjectionRegistry((profile,))
    binding = registry.resolve(
        TemplateGraphProjectionRef(profile_id=profile.profile_id, revision=profile.revision)
    )
    assert binding is not None
    job, created = ProjectionRepository(db_session).enqueue(record, binding)
    assert created
    db_session.commit()
    return job, registry


def _service(db_session, registry, driver, repository=None):
    return GraphProjectionService(
        db_session,
        registry,
        ProjectionGraphRepository(driver),
        repository,
    )


def test_projection_identity_constraints_back_each_immutable_merge():
    migration = (
        Path(__file__).parents[3] / "neo4j-migrations" / "005_projection_identity.cypher"
    ).read_text()
    driver = RecordingGraphDriver()
    ProjectionGraphRepository(driver).write_projection(
        ProjectionBatch(entities=(), entity_assertions=(), observations=(), claims=())
    )
    query = driver.calls[0][0]

    for label, variable, item in (
        ("ProjectionEntity", "e", "entity"),
        ("ProjectionEntityAssertion", "a", "assertion"),
        ("ProjectionObservation", "o", "observation"),
        ("ProjectionClaim", "c", "claim"),
    ):
        assert (
            f"FOR (node:{label}) REQUIRE node.projection_key IS UNIQUE;" in migration
        )
        assert (
            f"MERGE ({variable}:{label} {{projection_key: {item}.projection_key}})"
            in query
        )
    assert "CREATE CONSTRAINT" not in query


def test_distinct_sources_keep_the_same_source_record_id_separate(db_session):
    first_record = _persisted_evidence(db_session, _output())
    second_record = _persisted_evidence(db_session, _output())
    first_profile = _profile("county_a_parcel_owner", "county_a")
    second_profile = _profile("county_b_parcel_owner", "county_b")
    first_job, first_registry = _enqueue(db_session, first_record, first_profile)
    second_job, second_registry = _enqueue(db_session, second_record, second_profile)
    driver = RecordingGraphDriver()

    assert _service(db_session, first_registry, driver).project(
        first_job.id, "worker-a"
    ).status == "succeeded"
    assert _service(db_session, second_registry, driver).project(
        second_job.id, "worker-b"
    ).status == "succeeded"

    first_entity = driver.calls[0][1]["entities"][0]
    second_entity = driver.calls[1][1]["entities"][0]
    assert first_entity["source_record_id"] == second_entity["source_record_id"] == "parcel-1"
    assert first_entity["projection_key"] != second_entity["projection_key"]


def test_profile_identity_and_relationship_rule_ids_namespace_facts(db_session):
    base_profile = _profile()
    primary_rule = base_profile.relationships[0]
    secondary_rule = replace(primary_rule, rule_id="owns_record_secondary")
    profile = replace(
        base_profile,
        relationships=(primary_rule, secondary_rule),
    )
    profile_id_variant = replace(profile, profile_id="parcel_owner_alt")
    revision_variant = replace(profile, revision=2)
    changed_rule = replace(primary_rule, rule_id="owns_record_rekeyed")
    assert [rule["rule_id"] for rule in profile.snapshot()["relationships"]] == [
        "owns_record",
        "owns_record_secondary",
    ]
    assert profile.digest() != replace(
        profile, relationships=(changed_rule, secondary_rule)
    ).digest()

    record = _persisted_evidence(db_session, _output())
    job, registry = _enqueue(db_session, record, profile)
    profile_id_job, profile_id_registry = _enqueue(
        db_session, record, profile_id_variant
    )
    revision_job, revision_registry = _enqueue(
        db_session, record, revision_variant
    )
    driver = RecordingGraphDriver()

    assert _service(db_session, registry, driver).project(job.id, "worker-a").status == "succeeded"
    assert _service(db_session, profile_id_registry, driver).project(
        profile_id_job.id, "worker-b"
    ).status == "succeeded"
    assert _service(db_session, revision_registry, driver).project(
        revision_job.id, "worker-c"
    ).status == "succeeded"

    primary = driver.calls[0][1]
    assert primary["observations"][0]["projection_key"] != driver.calls[1][1][
        "observations"
    ][0]["projection_key"]
    assert primary["observations"][0]["projection_key"] != driver.calls[2][1][
        "observations"
    ][0]["projection_key"]
    primary_claim_keys = {claim["projection_key"] for claim in primary["claims"]}
    assert len(primary_claim_keys) == 2
    assert primary_claim_keys.isdisjoint(
        {claim["projection_key"] for claim in driver.calls[1][1]["claims"]}
    )
    assert primary_claim_keys.isdisjoint(
        {claim["projection_key"] for claim in driver.calls[2][1]["claims"]}
    )
    assert {
        claim["relationship_rule_id"] for claim in primary["claims"]
    } == {"owns_record", "owns_record_secondary"}
    assert "c.relationship_rule_id = claim.relationship_rule_id" in driver.calls[0][0]


def test_projection_profile_rejects_unsafe_or_duplicate_relationship_rule_ids():
    profile = _profile()
    with pytest.raises(ValueError):
        replace(profile.relationships[0], rule_id="not-safe")
    with pytest.raises(ValueError, match="Relationship rule IDs must be unique"):
        replace(
            profile,
            relationships=(profile.relationships[0], profile.relationships[0]),
        )


def test_conflicting_values_preserve_immutable_observations_and_provenance(db_session):
    profile = _profile()
    first_record = _persisted_evidence(db_session, _output("1 Main Street"))
    second_record = _persisted_evidence(db_session, _output("2 Main Street"))
    first_job, registry = _enqueue(db_session, first_record, profile)
    second_job, _ = _enqueue(db_session, second_record, profile)
    driver = RecordingGraphDriver()
    service = _service(db_session, registry, driver)

    assert service.project(first_job.id, "worker-a").status == "succeeded"
    assert service.project(second_job.id, "worker-b").status == "succeeded"

    first_observation = driver.calls[0][1]["observations"][0]
    second_observation = driver.calls[1][1]["observations"][0]
    claim = driver.calls[0][1]["claims"][0]
    assert first_observation["entity_key"] == second_observation["entity_key"]
    assert first_observation["projection_key"] != second_observation["projection_key"]
    assert {first_observation["value"], second_observation["value"]} == {
        "1 Main Street",
        "2 Main Street",
    }
    for fact in (first_observation, claim):
        assert fact["flow_run_id"]
        assert fact["step_run_id"]
        assert fact["evidence_record_id"]
        assert fact["profile_id"] == profile.profile_id
        assert fact["profile_revision"] == profile.revision
        assert fact["output_index"] == 0
        assert '"destination_id":"county_records"' in fact["provenance_json"]



def test_entity_assertion_traces_entity_without_scalar_observations(db_session):
    profile = ApprovedProjectionProfile(
        profile_id="parcel_identity_only",
        revision=1,
        source_id="county_a",
        entities=(
            EntityRule(
                alias="parcel",
                kind=ProjectionKind.PARCEL,
                items_pointer="/parcels",
                source_record_id_pointer="/id",
                effective_from_pointer="/effective_from",
                effective_to_pointer="/effective_to",
                observations=(),
                cardinality=Cardinality(minimum=1, maximum=1),
            ),
        ),
    )
    record = _persisted_evidence(db_session, _output())
    job, registry = _enqueue(db_session, record, profile)
    driver = RecordingGraphDriver()

    assert _service(db_session, registry, driver).project(
        job.id, "worker"
    ).status == "succeeded"

    parameters = driver.calls[0][1]
    assert parameters["observations"] == []
    assertion = parameters["entity_assertions"][0]
    assert assertion["entity_key"] == parameters["entities"][0]["projection_key"]
    assert assertion["flow_run_id"]
    assert assertion["step_run_id"]
    assert assertion["evidence_record_id"] == str(record.id)
    assert assertion["profile_id"] == profile.profile_id
    assert assertion["profile_revision"] == profile.revision
    assert assertion["output_index"] == 0
    assert assertion["entity_alias"] == "parcel"
    assert assertion["item_index"] == 0
    assert '"destination_id":"county_records"' in assertion["provenance_json"]
    assert "MERGE (a)-[:ASSERTS_ENTITY]->(e)" in driver.calls[0][0]

def test_invalid_cardinality_fails_before_repository_graph_write(db_session):
    profile = _profile()
    invalid_output = _output()
    invalid_output["parcels"].append(
        {
            "id": "parcel-2",
            "address": "2 Main Street",
            "effective_from": "2026-01-01",
            "effective_to": None,
        }
    )
    invalid_output["parcels"].append(
        {
            "id": "parcel-3",
            "address": "3 Main Street",
            "effective_from": "2026-01-01",
            "effective_to": None,
        }
    )
    record = _persisted_evidence(db_session, invalid_output)
    job, registry = _enqueue(db_session, record, profile)
    driver = RecordingGraphDriver()

    result = _service(db_session, registry, driver).project(job.id, "worker")

    assert result.status == "failed"
    assert driver.calls == []
    failed_job = db_session.get(GraphProjectionJob, job.id)
    assert failed_job is not None
    assert failed_job.safe_error_code == "projection_cardinality_invalid"


def test_transient_failure_retries_with_redacted_diagnostic(db_session):
    record = _persisted_evidence(db_session, _output())
    job, registry = _enqueue(db_session, record, _profile())
    driver = RecordingGraphDriver()
    driver.failure = ProjectionGraphUnavailable("driver detail must not persist")
    service = _service(db_session, registry, driver)

    assert service.project(job.id, "worker").status == "retry"
    retry_job = db_session.get(GraphProjectionJob, job.id)
    assert retry_job is not None
    assert retry_job.safe_error_code == "projection_graph_unavailable"
    assert retry_job.safe_error_diagnostic == {
        "code": "projection_graph_unavailable",
        "safe_message": "The projection graph is temporarily unavailable",
        "retryable": True,
        "attempt": 1,
    }


def test_failed_lease_fence_returns_lease_lost_without_graph_io(db_session):
    class FenceRejectingRepository(ProjectionRepository):
        def fence(self, lease, now):
            return False

    record = _persisted_evidence(db_session, _output())
    job, registry = _enqueue(db_session, record, _profile())
    driver = RecordingGraphDriver()

    result = _service(
        db_session,
        registry,
        driver,
        FenceRejectingRepository(db_session),
    ).project(job.id, "worker")

    assert result.status == "lease_lost"
    assert driver.calls == []


def test_lease_fence_remains_open_through_graph_write(db_session):
    events: list[str] = []

    class FencedRepository(ProjectionRepository):
        def fence(self, lease, now):
            fenced = super().fence(lease, now)
            if fenced:
                events.append("fence")
            return fenced

    class TransactionCheckingDriver(RecordingGraphDriver):
        def execute_write(self, query: str, parameters: dict | None = None) -> None:
            assert events == ["fence"]
            assert db_session.in_transaction()
            events.append("write")
            super().execute_write(query, parameters)

    record = _persisted_evidence(db_session, _output())
    job, registry = _enqueue(db_session, record, _profile())
    driver = TransactionCheckingDriver()

    assert _service(
        db_session,
        registry,
        driver,
        FencedRepository(db_session),
    ).project(job.id, "worker").status == "succeeded"
    assert events == ["fence", "write"]


def test_default_graph_repository_initialization_retries_safely(
    db_session, monkeypatch
):
    class MissingGraphRepository:
        def __init__(self) -> None:
            raise ValueError("missing graph configuration")

    monkeypatch.setattr(
        "flowsint_core.core.projection.service.ProjectionGraphRepository",
        MissingGraphRepository,
    )
    record = _persisted_evidence(db_session, _output())
    job, registry = _enqueue(db_session, record, _profile())

    result = GraphProjectionService(db_session, registry).project(job.id, "worker")

    assert result.status == "retry"
    retry_job = db_session.get(GraphProjectionJob, job.id)
    assert retry_job is not None
    assert retry_job.safe_error_code == "projection_graph_unavailable"
    assert retry_job.safe_error_diagnostic == {
        "code": "projection_graph_unavailable",
        "safe_message": "The projection graph is temporarily unavailable",
        "retryable": True,
        "attempt": 1,
    }


def test_graph_success_then_sql_finalize_crash_replays_identical_merge_operations(db_session):
    record = _persisted_evidence(db_session, _output())
    job, registry = _enqueue(db_session, record, _profile())
    driver = RecordingGraphDriver()

    class CrashingFinalizeRepository(ProjectionRepository):
        def mark_succeeded(self, lease, now):
            raise RuntimeError("simulated SQL-finalize crash")

    with pytest.raises(RuntimeError, match="simulated SQL-finalize crash"):
        _service(
            db_session,
            registry,
            driver,
            CrashingFinalizeRepository(db_session),
        ).project(job.id, "worker-a")

    crashed_job = db_session.get(GraphProjectionJob, job.id)
    assert crashed_job is not None
    crashed_job.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db_session.commit()

    assert _service(db_session, registry, driver).project(job.id, "worker-b").status == "succeeded"
    assert len(driver.calls) == 2
    assert driver.calls[0][1] == driver.calls[1][1]


def test_evidence_persistence_commits_projection_job_in_the_same_transaction(db_session):
    profile = _profile()
    registry = ApprovedProjectionRegistry((profile,))
    binding = registry.resolve(TemplateGraphProjectionRef(profile_id=profile.profile_id, revision=1))
    assert binding is not None
    owner = Profile(email="outbox@example.test", hashed_password="hash")
    db_session.add(owner)
    db_session.commit()
    service = ExecutionService(db_session)
    run, _ = service.create_or_reuse_run(
        owner_id=owner.id,
        idempotency_key="projection-outbox",
        input_digest="d" * 64,
        input_count=1,
    )
    lease = service.claim_run(run, "worker")
    assert lease is not None
    step = service.begin_or_resume_step(run, lease, "connector-template:test", 1)
    evidence = EvidenceEnvelope(
        input_ref="e" * 64,
        destination_id="county_records",
        endpoint_id="parcel_lookup",
        capability="enrich.read",
        policy_version="1",
        artifact_sha256="f" * 64,
        artifact_reference="artifact://safe-evidence",
        source_rights="official-record",
        schema_version="1",
        parser_version="1",
        confidence=0.8,
        verification_state="unverified",
    )
    result = StructuredExecutionResult(
        enricher_name="connector-template:test",
        outcomes=(
            InputOutcome(
                input_ref="e" * 64,
                status=OutcomeStatus.SUCCESS,
                outputs=(_output(),),
                evidence=(evidence,),
            ),
        ),
    )

    persisted = service.persist_structured_result(
        run,
        lease,
        step,
        result,
        projection_binding=binding,
    )

    assert persisted.run.id == run.id
    assert len(persisted.projection_job_ids) == 1
    job = db_session.get(GraphProjectionJob, persisted.projection_job_ids[0])
    assert job is not None
    assert job.evidence_envelope_id
    assert job.status == "pending"


def test_projection_migration_anchors_to_committed_stage3_revision():
    migration_path = (
        Path(__file__).parents[3]
        / "flowsint-api"
        / "alembic"
        / "versions"
        / "d4e5f6a7b8c9_add_graph_projection_jobs.py"
    )
    spec = importlib.util.spec_from_file_location("projection_migration", migration_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.revision == "d4e5f6a7b8c9"
    assert module.down_revision == "c3f4e5d6a7b8"
