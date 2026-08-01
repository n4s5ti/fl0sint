"""Transactional persistence for durable structured enricher executions."""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..execution import (
    EvidenceEnvelope,
    InputOutcome,
    OutcomeStatus,
    RedactedDiagnostic,
    StructuredExecutionResult,
)
from ..models import EvidenceEnvelopeRecord, FlowRun, StepRun
from ..repositories.execution_repository import ExecutionRepository
from .base import BaseService


FINAL_RUN_STATUSES = frozenset({"completed", "partial", "failed", "hold"})


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
        owner_id: UUID | None,
        idempotency_key: str,
        input_digest: str,
        input_count: int,
        flow_id: UUID | None = None,
        sketch_id: UUID | None = None,
        run_id: UUID | None = None,
    ) -> tuple[FlowRun, bool]:
        """Create a run once per owner/key, rejecting mismatched replay input."""
        existing = self._repo.get_by_owner_and_idempotency_key(
            owner_id, idempotency_key
        )
        if existing is not None:
            if existing.input_digest != input_digest or existing.input_count != input_count:
                raise ValueError("Idempotency key cannot be reused for different inputs")
            return existing, False

        run = FlowRun(
            owner_id=owner_id,
            flow_id=flow_id,
            sketch_id=sketch_id,
            idempotency_key=idempotency_key,
            input_digest=input_digest,
            input_count=input_count,
        )
        if run_id is not None:
            run.id = run_id
        self._repo.add(run)
        self._commit()
        return run, True

    def begin_or_resume_run(self, run: FlowRun) -> FlowRun:
        """Transition a non-final run to running and advance its attempt."""
        run.attempt += 1
        run.status = "running"
        run.safe_error_code = None
        run.safe_error_diagnostic = None
        run.completed_at = None
        run.started_at = _utcnow()
        self._commit()
        return run

    def get_step(self, run: FlowRun, step_key: str) -> StepRun | None:
        """Return the stable step state for a run, if it has been started."""
        return self._repo.get_step(run.id, step_key)

    def begin_or_resume_step(
        self, run: FlowRun, step_key: str, input_count: int
    ) -> StepRun:
        """Create or resume a stable step while preserving its checkpoint."""
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

    def update_run_checkpoint(self, run: FlowRun, checkpoint: dict) -> FlowRun:
        run.checkpoint = _json_value(checkpoint)
        self._commit()
        return run

    def update_step_checkpoint(self, step: StepRun, checkpoint: dict) -> StepRun:
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
        step: StepRun,
        result: StructuredExecutionResult,
    ) -> FlowRun:
        """Persist every input outcome without overwriting immutable evidence."""
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
                self._repo.add(
                    self._record_for_outcome(
                        run=run,
                        step=step,
                        attempt=step.attempt,
                        input_index=input_index,
                        evidence_index=evidence_index,
                        outcome=outcome,
                        envelope=envelope,
                        source=result.enricher_name,
                    )
                )

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
        run.completed_at = _utcnow()
        run.checkpoint = {
            **run.checkpoint,
            "last_step_key": step.step_key,
            "last_step_attempt": step.attempt,
            "completed_input_count": len(result.outcomes),
        }
        self._commit()
        return run

    def fail_run(self, run: FlowRun, diagnostic: RedactedDiagnostic) -> FlowRun:
        """Store only an approved redacted diagnostic for a top-level failure."""
        run.status = "failed"
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
            request_url_pattern=envelope.request_url_pattern,
            artifact_sha256=envelope.artifact_sha256,
            artifact_reference=envelope.artifact_reference,
            observed_at=envelope.observed_at,
            source_rights=envelope.source_rights,
            schema_version=envelope.schema_version,
            parser_version=envelope.parser_version,
            confidence=envelope.confidence,
            verification_state=envelope.verification_state,
            supersedes_id=prior.id,
        )
        self._repo.add(record)
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
                if record.request_url_pattern is not None
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
            request_url_pattern=(
                envelope.request_url_pattern if envelope is not None else None
            ),
            artifact_sha256=envelope.artifact_sha256 if envelope is not None else None,
            artifact_reference=(
                envelope.artifact_reference if envelope is not None else None
            ),
            observed_at=envelope.observed_at if envelope is not None else None,
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
        observed_at = record.observed_at or record.created_at
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            observed_at = observed_at.replace(tzinfo=timezone.utc)
        return EvidenceEnvelope(
            input_ref=record.input_ref,
            request_url_pattern=record.request_url_pattern,
            artifact_sha256=record.artifact_sha256,
            artifact_reference=record.artifact_reference,
            source_rights=record.source_rights or "unspecified",
            schema_version=record.schema_version or "unknown",
            parser_version=record.parser_version or "unknown",
            confidence=record.confidence if record.confidence is not None else 0.0,
            verification_state=record.verification_state or "unverified",
            observed_at=observed_at,
        )


def create_execution_service(db: Session) -> ExecutionService:
    """Create the durable execution service for a database session."""
    return ExecutionService(db=db, execution_repo=ExecutionRepository(db))
