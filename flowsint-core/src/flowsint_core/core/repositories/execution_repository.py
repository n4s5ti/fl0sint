"""Persistence queries for durable execution records."""
from datetime import datetime
from typing import List, Optional
from uuid import UUID

from sqlalchemy import func, or_, update

from ..models import EvidenceEnvelopeRecord, FlowRun, StepRun
from .base import BaseRepository


class ExecutionRepository(BaseRepository[FlowRun]):
    """Repository for flow runs, step state, and immutable evidence."""

    model = FlowRun

    def get_by_owner_and_idempotency_key(
        self, owner_id: UUID, idempotency_key: str
    ) -> Optional[FlowRun]:
        return (
            self._db.query(FlowRun)
            .filter(
                FlowRun.owner_id == owner_id,
                FlowRun.idempotency_key == idempotency_key,
            )
            .first()
        )

    def claim_run(
        self,
        run_id: UUID,
        *,
        lease_owner: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> bool:
        """Atomically acquire an available lease for a non-final run."""
        result = self._db.execute(
            update(FlowRun)
            .execution_options(synchronize_session=False)
            .where(
                FlowRun.id == run_id,
                FlowRun.status.in_(("pending", "running")),
                or_(
                    FlowRun.lease_expires_at.is_(None),
                    FlowRun.lease_expires_at <= now,
                ),
            )
            .values(
                status="running",
                attempt=FlowRun.attempt + 1,
                lease_owner=lease_owner,
                lease_expires_at=lease_expires_at,
                safe_error_code=None,
                safe_error_diagnostic=None,
                completed_at=None,
                started_at=now,
            )
        )
        return result.rowcount == 1

    def get_step(self, flow_run_id: UUID, step_key: str) -> Optional[StepRun]:
        return (
            self._db.query(StepRun)
            .filter(
                StepRun.flow_run_id == flow_run_id,
                StepRun.step_key == step_key,
            )
            .first()
        )

    def get_evidence(
        self,
        step_run_id: UUID,
        attempt: int,
        input_index: int,
        evidence_index: int,
    ) -> Optional[EvidenceEnvelopeRecord]:
        return (
            self._db.query(EvidenceEnvelopeRecord)
            .filter(
                EvidenceEnvelopeRecord.step_run_id == step_run_id,
                EvidenceEnvelopeRecord.attempt == attempt,
                EvidenceEnvelopeRecord.input_index == input_index,
                EvidenceEnvelopeRecord.evidence_index == evidence_index,
            )
            .first()
        )

    def list_evidence_for_attempt(
        self, step_run_id: UUID, attempt: int
    ) -> List[EvidenceEnvelopeRecord]:
        return (
            self._db.query(EvidenceEnvelopeRecord)
            .filter(
                EvidenceEnvelopeRecord.step_run_id == step_run_id,
                EvidenceEnvelopeRecord.attempt == attempt,
            )
            .order_by(
                EvidenceEnvelopeRecord.input_index,
                EvidenceEnvelopeRecord.evidence_index,
            )
            .all()
        )

    def next_evidence_index(
        self, step_run_id: UUID, attempt: int, input_index: int
    ) -> int:
        existing_max = (
            self._db.query(func.max(EvidenceEnvelopeRecord.evidence_index))
            .filter(
                EvidenceEnvelopeRecord.step_run_id == step_run_id,
                EvidenceEnvelopeRecord.attempt == attempt,
                EvidenceEnvelopeRecord.input_index == input_index,
            )
            .scalar()
        )
        return 0 if existing_max is None else int(existing_max) + 1
