"""Durable outbox persistence for approved graph projection."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import EvidenceEnvelopeRecord, FlowRun, GraphProjectionJob, StepRun
from .contracts import ProjectionBinding, ProjectionValidationError


@dataclass(frozen=True)
class ProjectionLease:
    job_id: UUID
    lease_owner: str
    attempt: int


@dataclass(frozen=True)
class PersistedProjectionInput:
    """Only durable evidence and persisted provenance supplied to the graph layer."""

    job_id: UUID
    evidence_record_id: UUID
    flow_run_id: UUID
    step_run_id: UUID
    profile_id: str
    profile_revision: int
    profile_digest: str
    profile_snapshot: dict[str, Any]
    mapped_outputs: tuple[dict[str, Any], ...]
    provenance: dict[str, Any]
    ingested_at: datetime


class ProjectionRepository:
    """SQL outbox and lease operations; it never reaches the graph driver."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def enqueue(
        self, evidence_record: EvidenceEnvelopeRecord, binding: ProjectionBinding
    ) -> tuple[GraphProjectionJob, bool]:
        """Insert one deterministic job while sharing the caller's transaction."""
        existing = self._job_for_binding(evidence_record.id, binding)
        if existing is not None:
            return existing, False
        job = GraphProjectionJob(
            evidence_envelope_id=evidence_record.id,
            profile_id=binding.profile_id,
            profile_revision=binding.revision,
            profile_digest=binding.digest,
            profile_snapshot=binding.snapshot,
            source_attempt=evidence_record.attempt,
        )
        try:
            with self._db.begin_nested():
                self._db.add(job)
                self._db.flush()
        except IntegrityError:
            existing = self._job_for_binding(evidence_record.id, binding)
            if existing is None:
                raise
            return existing, False
        return job, True

    def get_job(self, job_id: UUID) -> GraphProjectionJob | None:
        return self._db.get(GraphProjectionJob, job_id)

    def claim(
        self,
        job_id: UUID,
        *,
        lease_owner: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> bool:
        """Claim a due or expired job with an attempt/lease write fence."""
        due_pending = and_(
            GraphProjectionJob.status.in_(("pending", "retry")),
            or_(
                GraphProjectionJob.next_attempt_at.is_(None),
                GraphProjectionJob.next_attempt_at <= now,
            ),
        )
        expired_running = and_(
            GraphProjectionJob.status == "running",
            GraphProjectionJob.lease_expires_at <= now,
        )
        result = self._db.execute(
            update(GraphProjectionJob)
            .execution_options(synchronize_session=False)
            .where(
                GraphProjectionJob.id == job_id,
                or_(due_pending, expired_running),
            )
            .values(
                status="running",
                attempt=GraphProjectionJob.attempt + 1,
                lease_owner=lease_owner,
                lease_expires_at=lease_expires_at,
                started_at=now,
                completed_at=None,
            )
        )
        return result.rowcount == 1

    def fence(self, lease: ProjectionLease, now: datetime) -> bool:
        """Assert that a worker still owns an unexpired projection lease."""
        result = self._db.execute(
            update(GraphProjectionJob)
            .execution_options(synchronize_session=False)
            .where(
                GraphProjectionJob.id == lease.job_id,
                GraphProjectionJob.status == "running",
                GraphProjectionJob.lease_owner == lease.lease_owner,
                GraphProjectionJob.attempt == lease.attempt,
                GraphProjectionJob.lease_expires_at > now,
            )
            .values(lease_expires_at=GraphProjectionJob.lease_expires_at)
        )
        return result.rowcount == 1

    def mark_succeeded(self, lease: ProjectionLease, now: datetime) -> bool:
        result = self._db.execute(
            update(GraphProjectionJob)
            .execution_options(synchronize_session=False)
            .where(
                GraphProjectionJob.id == lease.job_id,
                GraphProjectionJob.status == "running",
                GraphProjectionJob.lease_owner == lease.lease_owner,
                GraphProjectionJob.attempt == lease.attempt,
                GraphProjectionJob.lease_expires_at > now,
            )
            .values(
                status="succeeded",
                lease_owner=None,
                lease_expires_at=None,
                next_attempt_at=None,
                safe_error_code=None,
                safe_error_diagnostic=None,
                completed_at=now,
            )
        )
        return result.rowcount == 1

    def mark_retry_or_failed(
        self,
        lease: ProjectionLease,
        *,
        now: datetime,
        code: str,
        safe_message: str,
        retryable: bool,
        next_attempt_at: datetime | None,
    ) -> bool:
        """Finalize a lease with a redacted, structured diagnostic only."""
        if retryable and next_attempt_at is None:
            raise ValueError("Retryable projection failures need a next attempt time")
        status = "retry" if retryable else "failed"
        result = self._db.execute(
            update(GraphProjectionJob)
            .execution_options(synchronize_session=False)
            .where(
                GraphProjectionJob.id == lease.job_id,
                GraphProjectionJob.status == "running",
                GraphProjectionJob.lease_owner == lease.lease_owner,
                GraphProjectionJob.attempt == lease.attempt,
                GraphProjectionJob.lease_expires_at > now,
            )
            .values(
                status=status,
                lease_owner=None,
                lease_expires_at=None,
                next_attempt_at=next_attempt_at,
                safe_error_code=code,
                safe_error_diagnostic={
                    "code": code,
                    "safe_message": safe_message,
                    "retryable": retryable,
                    "attempt": lease.attempt,
                },
                completed_at=now if not retryable else None,
            )
        )
        return result.rowcount == 1

    def due_job_ids(self, now: datetime, limit: int) -> list[UUID]:
        due_pending = and_(
            GraphProjectionJob.status.in_(("pending", "retry")),
            or_(
                GraphProjectionJob.next_attempt_at.is_(None),
                GraphProjectionJob.next_attempt_at <= now,
            ),
        )
        expired_running = and_(
            GraphProjectionJob.status == "running",
            GraphProjectionJob.lease_expires_at <= now,
        )
        return list(
            self._db.scalars(
                select(GraphProjectionJob.id)
                .where(or_(due_pending, expired_running))
                .order_by(GraphProjectionJob.created_at, GraphProjectionJob.id)
                .limit(limit)
            )
        )

    def hydrate(self, job_id: UUID) -> PersistedProjectionInput:
        """Join the job to immutable successful evidence and durable provenance."""
        row = self._db.execute(
            select(GraphProjectionJob, EvidenceEnvelopeRecord, StepRun, FlowRun)
            .join(
                EvidenceEnvelopeRecord,
                GraphProjectionJob.evidence_envelope_id == EvidenceEnvelopeRecord.id,
            )
            .join(StepRun, EvidenceEnvelopeRecord.step_run_id == StepRun.id)
            .join(FlowRun, EvidenceEnvelopeRecord.flow_run_id == FlowRun.id)
            .where(GraphProjectionJob.id == job_id)
        ).one_or_none()
        if row is None:
            raise ProjectionValidationError(
                "projection_evidence_invalid",
                "Projection evidence could not be loaded",
            )
        job, record, step, run = row
        if (
            record.step_run_id != step.id
            or record.flow_run_id != run.id
            or step.flow_run_id != run.id
            or record.attempt != job.source_attempt
            or record.attempt != step.attempt
            or record.status != "success"
            or record.diagnostic is not None
            or not isinstance(record.mapped_outputs, list)
            or not isinstance(job.profile_snapshot, dict)
        ):
            raise ProjectionValidationError(
                "projection_evidence_invalid",
                "Projection evidence is not a successful durable record",
            )
        if any(not isinstance(output, dict) for output in record.mapped_outputs):
            raise ProjectionValidationError(
                "projection_evidence_invalid",
                "Projection evidence outputs are not structured JSON objects",
            )
        return PersistedProjectionInput(
            job_id=job.id,
            evidence_record_id=record.id,
            flow_run_id=run.id,
            step_run_id=step.id,
            profile_id=job.profile_id,
            profile_revision=job.profile_revision,
            profile_digest=job.profile_digest,
            profile_snapshot=job.profile_snapshot,
            mapped_outputs=tuple(record.mapped_outputs),
            provenance=_persisted_provenance(record),
            ingested_at=record.ingested_at,
        )

    def _job_for_binding(
        self, evidence_record_id: UUID, binding: ProjectionBinding
    ) -> GraphProjectionJob | None:
        return self._db.scalar(
            select(GraphProjectionJob).where(
                GraphProjectionJob.evidence_envelope_id == evidence_record_id,
                GraphProjectionJob.profile_id == binding.profile_id,
                GraphProjectionJob.profile_revision == binding.revision,
            )
        )


def _persisted_provenance(record: EvidenceEnvelopeRecord) -> dict[str, Any]:
    """Project only bounded provenance fields, never raw request/response material."""
    provenance: dict[str, Any] = {
        "source": record.source,
        "artifact_sha256": record.artifact_sha256,
        "artifact_reference": record.artifact_reference,
        "event_at": record.event_at.isoformat() if record.event_at is not None else None,
        "retrieved_at": record.retrieved_at.isoformat(),
        "ingested_at": record.ingested_at.isoformat(),
        "source_rights": record.source_rights,
        "schema_version": record.schema_version,
        "parser_version": record.parser_version,
        "confidence": record.confidence,
        "verification_state": record.verification_state,
    }
    for field in ("destination_id", "endpoint_id", "capability", "policy_version"):
        value = getattr(record, field, None)
        if value is not None:
            provenance[field] = value
    return provenance
