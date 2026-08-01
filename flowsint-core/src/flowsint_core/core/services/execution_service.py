"""Transactional persistence for durable structured enricher executions."""
from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..execution import (
    EvidenceEnvelope,
    InputOutcome,
    OutcomeStatus,
    RedactedDiagnostic,
    StructuredExecutionResult,
)
from ..models import EvidenceEnvelopeRecord, FlowRun, StepRun
from ..projection.contracts import ProjectionBinding
from ..projection.repository import ProjectionRepository
from ..repositories.execution_repository import ExecutionRepository
from .base import BaseService


FINAL_RUN_STATUSES = frozenset({"completed", "partial", "failed", "hold"})
DEFAULT_RUN_LEASE_DURATION = timedelta(minutes=15)


@dataclass(frozen=True)
class RunLease:
    """Capability proving ownership of one active flow-run attempt."""

    run_id: UUID
    lease_owner: str
    attempt: int



@dataclass(frozen=True)
class PersistedStructuredResult:
    """Committed execution state and graph outbox rows created with its evidence."""

    run: FlowRun
    projection_job_ids: tuple[UUID, ...]

class RunLeaseLost(RuntimeError):
    """Raised when a worker no longer owns an active flow-run lease."""



def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _json_value(value: Any) -> Any:
    """Serialize mapped outputs without turning unknown values into text."""
    if isinstance(value, BaseModel):
        return _json_value(value.model_dump(mode="json"))
    if isinstance(value, Enum):
        return _json_value(value.value)
    if isinstance(value, datetime):
        if value.tzinfo is not None and value.utcoffset() is not None:
            value = value.astimezone(timezone.utc)
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    raise TypeError(f"Mapped output type {type(value).__name__} is not JSON-safe")


class ExecutionService(BaseService):
    """Owns transactions for runs, resumable steps, and immutable evidence."""

    def __init__(
        self, db: Session, execution_repo: ExecutionRepository | None = None, **kwargs
    ):
        super().__init__(db, **kwargs)
        self._repo = execution_repo or ExecutionRepository(db)

    def create_or_reuse_run(
        self,
        *,
        owner_id: UUID,
        idempotency_key: str,
        input_digest: str,
        input_count: int,
        operation_digest: str | None = None,
        flow_id: UUID | None = None,
        sketch_id: UUID | None = None,
        run_id: UUID | None = None,
    ) -> tuple[FlowRun, bool]:
        """Create a run once per owner/key, rejecting mismatched replay input."""
        operation_digest = (
            input_digest if operation_digest is None else operation_digest
        )
        existing = self._repo.get_by_owner_and_idempotency_key(
            owner_id, idempotency_key
        )
        if existing is not None:
            if (
                existing.input_digest != input_digest
                or existing.input_count != input_count
                or existing.operation_digest != operation_digest
            ):
                raise ValueError(
                    "Idempotency key cannot be reused for a different operation"
                )
            return existing, False

        run = FlowRun(
            owner_id=owner_id,
            flow_id=flow_id,
            sketch_id=sketch_id,
            idempotency_key=idempotency_key,
            input_digest=input_digest,
            operation_digest=operation_digest,
            input_count=input_count,
        )
        if run_id is not None:
            run.id = run_id

        try:
            with self._db.begin_nested():
                self._repo.add(run)
                self._db.flush()
        except IntegrityError:
            existing = self._repo.get_by_owner_and_idempotency_key(
                owner_id, idempotency_key
            )
            if existing is None:
                raise
            if (
                existing.input_digest != input_digest
                or existing.input_count != input_count
                or existing.operation_digest != operation_digest
            ):
                raise ValueError(
                    "Idempotency key cannot be reused for a different operation"
                )
            return existing, False

        self._commit()
        return run, True

    def claim_run(
        self,
        run: FlowRun,
        lease_owner: str,
        lease_duration: timedelta = DEFAULT_RUN_LEASE_DURATION,
    ) -> RunLease | None:
        """Atomically claim a pending or expired run lease."""
        now = _utcnow()
        if not self._repo.claim_run(
            run.id,
            lease_owner=lease_owner,
            now=now,
            lease_expires_at=now + lease_duration,
        ):
            return None
        self._commit()
        self._db.refresh(run)
        return RunLease(run_id=run.id, lease_owner=lease_owner, attempt=run.attempt)

    def get_step(self, run: FlowRun, step_key: str) -> StepRun | None:
        """Return the stable step state for a run, if it has been started."""
        return self._repo.get_step(run.id, step_key)

    def _fence_run(self, run: FlowRun, lease: RunLease) -> None:
        """Assert the caller still owns an unexpired lease within this transaction."""
        if lease.run_id != run.id or not self._repo.fence_run(
            run.id,
            lease_owner=lease.lease_owner,
            attempt=lease.attempt,
            now=_utcnow(),
        ):
            self._rollback()
            raise RunLeaseLost(f"Lease lost for flow run {run.id}")

    def begin_or_resume_step(
        self, run: FlowRun, lease: RunLease, step_key: str, input_count: int
    ) -> StepRun:
        """Create or resume a stable step while preserving its checkpoint."""
        self._fence_run(run, lease)
        step = self._repo.get_step(run.id, step_key)
        if step is None:
            step = StepRun(flow_run_id=run.id, step_key=step_key, input_count=input_count)
            self._repo.add(step)
        else:
            step.input_count = input_count

        step.attempt = (step.attempt or 0) + 1
        step.status = "running"
        step.retryable = False
        step.success_count = 0
        step.failure_count = 0
        step.hold_count = 0
        step.output_count = 0
        step.completed_at = None
        step.started_at = _utcnow()
        self._commit()
        return step

    def update_run_checkpoint(
        self, run: FlowRun, lease: RunLease, checkpoint: dict
    ) -> FlowRun:
        self._fence_run(run, lease)
        run.checkpoint = _json_value(checkpoint)
        self._commit()
        return run

    def update_step_checkpoint(
        self, run: FlowRun, lease: RunLease, step: StepRun, checkpoint: dict
    ) -> StepRun:
        self._fence_run(run, lease)
        step.checkpoint = _json_value(checkpoint)
        self._commit()
        return step

    @staticmethod
    def aggregate_status(outcomes: tuple[InputOutcome, ...]) -> str:
        statuses = {outcome.status for outcome in outcomes}
        if not outcomes or statuses == {OutcomeStatus.SUCCESS}:
            return "completed"
        if OutcomeStatus.SUCCESS in statuses:
            return "partial"
        if OutcomeStatus.HOLD in statuses:
            return "hold"
        return "failed"

    def persist_structured_result(
        self,
        run: FlowRun,
        lease: RunLease,
        step: StepRun,
        result: StructuredExecutionResult,
        *,
        projection_binding: ProjectionBinding | None = None,
    ) -> PersistedStructuredResult:
        """Persist immutable evidence and matching projection jobs in one transaction."""
        projection_repo = (
            ProjectionRepository(self._db) if projection_binding is not None else None
        )
        projection_job_ids: list[UUID] = []
        self._fence_run(run, lease)
        if len(result.outcomes) != run.input_count:
            raise ValueError("Structured result must contain every original input")
        if step.flow_run_id != run.id:
            raise ValueError("Step does not belong to the flow run")

        for input_index, outcome in enumerate(result.outcomes):
            envelopes: tuple[EvidenceEnvelope | None, ...] = (
                outcome.evidence or (None,)
            )
            for evidence_index, envelope in enumerate(envelopes):
                if envelope is not None and envelope.input_ref != outcome.input_ref:
                    raise ValueError("Evidence input reference must match its outcome")
                if (
                    self._repo.get_evidence(
                        step.id, step.attempt, input_index, evidence_index
                    )
                    is not None
                ):
                    continue
                record = self._record_for_outcome(
                    run=run,
                    step=step,
                    attempt=step.attempt,
                    input_index=input_index,
                    evidence_index=evidence_index,
                    outcome=outcome,
                    envelope=envelope,
                    source=result.enricher_name,
                )
                self._repo.add(record)
                if (
                    projection_repo is not None
                    and projection_binding is not None
                    and outcome.status is OutcomeStatus.SUCCESS
                ):
                    self._db.flush()
                    job, created = projection_repo.enqueue(record, projection_binding)
                    if created:
                        projection_job_ids.append(job.id)

        aggregate_status = self.aggregate_status(result.outcomes)
        step.success_count = sum(
            outcome.status is OutcomeStatus.SUCCESS for outcome in result.outcomes
        )
        step.failure_count = sum(
            outcome.status is OutcomeStatus.FAILURE for outcome in result.outcomes
        )
        step.hold_count = sum(
            outcome.status is OutcomeStatus.HOLD for outcome in result.outcomes
        )
        step.output_count = sum(len(outcome.outputs) for outcome in result.outcomes)
        step.retryable = any(
            outcome.diagnostic is not None and outcome.diagnostic.retryable
            for outcome in result.outcomes
        )
        step.status = aggregate_status
        step.completed_at = _utcnow()
        step.checkpoint = {
            **step.checkpoint,
            "attempt": step.attempt,
            "completed_input_count": len(result.outcomes),
        }

        run.status = aggregate_status
        run.lease_owner = None
        run.lease_expires_at = None
        run.completed_at = _utcnow()
        run.checkpoint = {
            **run.checkpoint,
            "last_step_key": step.step_key,
            "last_step_attempt": step.attempt,
            "completed_input_count": len(result.outcomes),
        }
        self._commit()
        return PersistedStructuredResult(
            run=run,
            projection_job_ids=tuple(projection_job_ids),
        )

    def fail_run(
        self, run: FlowRun, lease: RunLease, diagnostic: RedactedDiagnostic
    ) -> FlowRun:
        """Store only an approved redacted diagnostic for a top-level failure."""
        self._fence_run(run, lease)
        run.status = "failed"
        run.lease_owner = None
        run.lease_expires_at = None
        run.safe_error_code = diagnostic.code
        run.safe_error_diagnostic = diagnostic.model_dump(mode="json")
        run.completed_at = _utcnow()
        self._commit()
        return run

    def append_superseding_evidence(
        self,
        prior: EvidenceEnvelopeRecord,
        envelope: EvidenceEnvelope,
        *,
        source: str | None = None,
        projection_binding: ProjectionBinding | None = None,
    ) -> EvidenceEnvelopeRecord:
        """Supersede evidence by appending a new immutable row."""
        if envelope.input_ref != prior.input_ref:
            raise ValueError("Superseding evidence must retain the original input reference")
        record = EvidenceEnvelopeRecord(
            flow_run_id=prior.flow_run_id,
            step_run_id=prior.step_run_id,
            attempt=prior.attempt,
            input_index=prior.input_index,
            evidence_index=self._repo.next_evidence_index(
                prior.step_run_id, prior.attempt, prior.input_index
            ),
            input_ref=prior.input_ref,
            status=prior.status,
            retryable=prior.retryable,
            mapped_outputs=prior.mapped_outputs,
            diagnostic=prior.diagnostic,
            source=source or prior.source,
            destination_id=envelope.destination_id,
            endpoint_id=envelope.endpoint_id,
            capability=envelope.capability,
            policy_version=envelope.policy_version,
            artifact_sha256=envelope.artifact_sha256,
            artifact_reference=envelope.artifact_reference,
            event_at=envelope.event_at,
            retrieved_at=envelope.retrieved_at,
            ingested_at=envelope.ingested_at,
            source_rights=envelope.source_rights,
            schema_version=envelope.schema_version,
            parser_version=envelope.parser_version,
            confidence=envelope.confidence,
            verification_state=envelope.verification_state,
            supersedes_id=prior.id,
        )
        self._repo.add(record)
        if projection_binding is not None and record.status == OutcomeStatus.SUCCESS.value:
            self._db.flush()
            ProjectionRepository(self._db).enqueue(record, projection_binding)
        self._commit()
        return record

    def reconstruct_structured_result(
        self, step: StepRun
    ) -> StructuredExecutionResult:
        """Reload the active evidence rows into the structured execution contract."""
        records = self._repo.list_evidence_for_attempt(step.id, step.attempt)
        superseded_ids = {
            record.supersedes_id for record in records if record.supersedes_id is not None
        }
        active_records = [record for record in records if record.id not in superseded_ids]
        grouped: dict[int, list[EvidenceEnvelopeRecord]] = {}
        for record in active_records:
            grouped.setdefault(record.input_index, []).append(record)

        outcomes = []
        for input_index in sorted(grouped):
            outcome_records = grouped[input_index]
            first = outcome_records[0]
            evidence = tuple(
                self._envelope_from_record(record)
                for record in outcome_records
                if record.destination_id is not None
            )
            diagnostic = (
                RedactedDiagnostic.model_validate(first.diagnostic)
                if first.diagnostic is not None
                else None
            )
            outcomes.append(
                InputOutcome(
                    input_ref=first.input_ref,
                    status=OutcomeStatus(first.status),
                    outputs=tuple(first.mapped_outputs),
                    diagnostic=diagnostic,
                    evidence=evidence,
                )
            )
        source = active_records[0].source if active_records else step.step_key
        return StructuredExecutionResult(enricher_name=source, outcomes=tuple(outcomes))

    @staticmethod
    def _record_for_outcome(
        *,
        run: FlowRun,
        step: StepRun,
        attempt: int,
        input_index: int,
        evidence_index: int,
        outcome: InputOutcome,
        envelope: EvidenceEnvelope | None,
        source: str,
    ) -> EvidenceEnvelopeRecord:
        return EvidenceEnvelopeRecord(
            flow_run_id=run.id,
            step_run_id=step.id,
            attempt=attempt,
            input_index=input_index,
            evidence_index=evidence_index,
            input_ref=outcome.input_ref,
            status=outcome.status.value,
            retryable=bool(outcome.diagnostic and outcome.diagnostic.retryable),
            mapped_outputs=[_json_value(output) for output in outcome.outputs],
            diagnostic=(
                outcome.diagnostic.model_dump(mode="json")
                if outcome.diagnostic is not None
                else None
            ),
            source=source,
            destination_id=envelope.destination_id if envelope is not None else None,
            endpoint_id=envelope.endpoint_id if envelope is not None else None,
            capability=envelope.capability if envelope is not None else None,
            policy_version=envelope.policy_version if envelope is not None else None,
            artifact_sha256=envelope.artifact_sha256 if envelope is not None else None,
            artifact_reference=(
                envelope.artifact_reference if envelope is not None else None
            ),
            event_at=envelope.event_at if envelope is not None else None,
            retrieved_at=envelope.retrieved_at if envelope is not None else _utcnow(),
            ingested_at=envelope.ingested_at if envelope is not None else _utcnow(),
            source_rights=envelope.source_rights if envelope is not None else None,
            schema_version=envelope.schema_version if envelope is not None else None,
            parser_version=envelope.parser_version if envelope is not None else None,
            confidence=envelope.confidence if envelope is not None else None,
            verification_state=(
                envelope.verification_state if envelope is not None else None
            ),
        )

    @staticmethod
    def _envelope_from_record(record: EvidenceEnvelopeRecord) -> EvidenceEnvelope:
        def utc_timestamp(value: datetime) -> datetime:
            if value.tzinfo is None or value.utcoffset() is None:
                return value.replace(tzinfo=timezone.utc)
            return value.astimezone(timezone.utc)

        return EvidenceEnvelope(
            input_ref=record.input_ref,
            destination_id=record.destination_id or "unknown",
            endpoint_id=record.endpoint_id or "unknown",
            capability=record.capability or "enrich.read",
            policy_version=record.policy_version or "unknown",
            artifact_sha256=record.artifact_sha256,
            artifact_reference=record.artifact_reference,
            source_rights=record.source_rights or "unspecified",
            schema_version=record.schema_version or "unknown",
            parser_version=record.parser_version or "unknown",
            confidence=record.confidence if record.confidence is not None else 0.0,
            verification_state=record.verification_state or "unverified",
            event_at=(
                utc_timestamp(record.event_at) if record.event_at is not None else None
            ),
            retrieved_at=utc_timestamp(record.retrieved_at),
            ingested_at=utc_timestamp(record.ingested_at),
        )


def create_execution_service(db: Session) -> ExecutionService:
    """Create the durable execution service for a database session."""
    return ExecutionService(db=db, execution_repo=ExecutionRepository(db))
