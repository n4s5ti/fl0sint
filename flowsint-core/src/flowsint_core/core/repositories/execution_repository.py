"""Persistence queries for durable execution records."""
from typing import List, Optional
from uuid import UUID

from sqlalchemy import func

from ..models import EvidenceEnvelopeRecord, FlowRun, StepRun
from .base import BaseRepository


class ExecutionRepository(BaseRepository[FlowRun]):
    """Repository for flow runs, step state, and immutable evidence."""

    model = FlowRun

    def get_by_owner_and_idempotency_key(
        self, owner_id: UUID | None, idempotency_key: str
    ) -> Optional[FlowRun]:
        query = self._db.query(FlowRun).filter(
            FlowRun.idempotency_key == idempotency_key
        )
        if owner_id is None:
            query = query.filter(FlowRun.owner_id.is_(None))
        else:
            query = query.filter(FlowRun.owner_id == owner_id)
        return query.first()

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
