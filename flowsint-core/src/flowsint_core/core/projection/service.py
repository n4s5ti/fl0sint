"""Lease-safe projection of persisted evidence into the dedicated graph repository."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import json
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from .contracts import (
    ApprovedProjectionProfile,
    ApprovedProjectionRegistry,
    EntityRule,
    ProjectionBinding,
    ProjectionError,
    ProjectionValidationError,
    RelationshipRule,
    canonical_source_record_id,
    resolve_pointer,
    stable_claim_key,
    stable_entity_key,
    stable_entity_assertion_key,
    stable_observation_key,
    validate_scalar,
)
from .graph_repository import (
    ProjectionBatch,
    ProjectionClaimOperation,
    ProjectionEntityAssertionOperation,
    ProjectionEntityOperation,
    ProjectionGraphError,
    ProjectionGraphUnavailable,
    ProjectionGraphRepository,
    ProjectionObservationOperation,
)
from .repository import PersistedProjectionInput, ProjectionLease, ProjectionRepository


DEFAULT_PROJECTION_LEASE_DURATION = timedelta(minutes=15)
MAX_PROJECTION_ATTEMPTS = 5
_RETRY_DELAYS = (timedelta(seconds=30), timedelta(minutes=2), timedelta(minutes=10), timedelta(minutes=30), timedelta(hours=2))


@dataclass(frozen=True)
class ProjectionResult:
    job_id: UUID
    status: str
    attempt: int | None = None


class GraphProjectionService:
    """Claims durable jobs, validates persisted evidence, then commits deterministic MERGEs."""

    def __init__(
        self,
        db: Session,
        registry: ApprovedProjectionRegistry,
        graph_repository: ProjectionGraphRepository | None = None,
        repository: ProjectionRepository | None = None,
    ) -> None:
        self._db = db
        self._registry = registry
        self._graph_repository = graph_repository
        self._repository = repository or ProjectionRepository(db)

    def project(
        self,
        job_id: UUID,
        worker_id: str,
        lease_duration: timedelta = DEFAULT_PROJECTION_LEASE_DURATION,
    ) -> ProjectionResult:
        """Project one job; all evidence/profile validation completes before graph I/O."""
        job = self._repository.get_job(job_id)
        if job is None:
            return ProjectionResult(job_id=job_id, status="missing")
        now = _utcnow()
        if not self._repository.claim(
            job_id,
            lease_owner=worker_id,
            now=now,
            lease_expires_at=now + lease_duration,
        ):
            self._db.rollback()
            return ProjectionResult(job_id=job_id, status="not_claimed")
        self._db.commit()
        claimed = self._repository.get_job(job_id)
        if claimed is None:
            return ProjectionResult(job_id=job_id, status="missing")
        lease = ProjectionLease(
            job_id=job_id,
            lease_owner=worker_id,
            attempt=claimed.attempt,
        )

        try:
            persisted = self._repository.hydrate(job_id)
            profile = self._validated_profile(persisted)
            batch = self._build_batch(persisted, profile)
            if not self._repository.fence(lease, _utcnow()):
                self._db.rollback()
                return ProjectionResult(
                    job_id=job_id, status="lease_lost", attempt=lease.attempt
                )
            graph_repository = self._graph_repository
            if graph_repository is None:
                try:
                    graph_repository = ProjectionGraphRepository()
                except Exception as error:
                    raise ProjectionGraphUnavailable(
                        "Projection graph initialization unavailable"
                    ) from error
                self._graph_repository = graph_repository
            graph_repository.write_projection(batch)
        except ProjectionError as error:
            return self._fail(lease, error.code, error.safe_message, retryable=False)
        except TimeoutError:
            return self._fail(
                lease,
                "projection_timeout",
                "The projection graph timed out",
                retryable=True,
            )
        except ProjectionGraphError:
            return self._fail(
                lease,
                "projection_graph_unavailable",
                "The projection graph is temporarily unavailable",
                retryable=True,
            )
        except Exception:
            return self._fail(
                lease,
                "projection_graph_transient",
                "The projection graph could not complete the projection",
                retryable=True,
            )

        now = _utcnow()
        if not self._repository.mark_succeeded(lease, now):
            self._db.rollback()
            return ProjectionResult(job_id=job_id, status="lease_lost", attempt=lease.attempt)
        self._db.commit()
        return ProjectionResult(job_id=job_id, status="succeeded", attempt=lease.attempt)

    def sweep(self, worker_id: str, limit: int = 100) -> list[ProjectionResult]:
        """Claim each due durable job; callers may use this from Celery Beat recovery."""
        job_ids = self._repository.due_job_ids(_utcnow(), limit)
        self._db.rollback()
        return [self.project(job_id, worker_id) for job_id in job_ids]

    def _validated_profile(
        self, persisted: PersistedProjectionInput
    ) -> ApprovedProjectionProfile:
        binding = ProjectionBinding(
            profile_id=persisted.profile_id,
            revision=persisted.profile_revision,
            digest=persisted.profile_digest,
            snapshot=persisted.profile_snapshot,
        )
        return self._registry.validate_binding(binding)

    def _build_batch(
        self,
        persisted: PersistedProjectionInput,
        profile: ApprovedProjectionProfile,
    ) -> ProjectionBatch:
        entity_operations: dict[str, ProjectionEntityOperation] = {}
        entity_keys: dict[tuple[str, str], str] = {}
        entity_assertions: list[ProjectionEntityAssertionOperation] = []
        observations: list[ProjectionObservationOperation] = []
        provenance_json = json.dumps(
            persisted.provenance,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )

        for output_index, output in enumerate(persisted.mapped_outputs):
            for rule in profile.entities:
                items = _items(output, rule.items_pointer)
                rule.cardinality.validate(len(items))
                for item_index, item in enumerate(items):
                    item = _object(item)
                    source_record_id = canonical_source_record_id(
                        resolve_pointer(item, rule.source_record_id_pointer)
                    )
                    effective_from, effective_to = _effective_dates(
                        item,
                        rule.effective_from_pointer,
                        rule.effective_to_pointer,
                    )
                    entity_key = stable_entity_key(
                        profile.source_id, rule.kind, source_record_id
                    )
                    entity_operations.setdefault(
                        entity_key,
                        ProjectionEntityOperation(
                            projection_key=entity_key,
                            source_id=profile.source_id,
                            entity_kind=rule.kind,
                            source_record_id=source_record_id,
                            ingested_at=persisted.ingested_at.isoformat(),
                        ),
                    )
                    entity_keys[(rule.alias, source_record_id)] = entity_key
                    entity_assertions.append(
                        ProjectionEntityAssertionOperation(
                            projection_key=stable_entity_assertion_key(
                                profile.profile_id,
                                profile.revision,
                                str(persisted.evidence_record_id),
                                output_index,
                                rule.alias,
                                item_index,
                                entity_key,
                            ),
                            entity_key=entity_key,
                            flow_run_id=str(persisted.flow_run_id),
                            step_run_id=str(persisted.step_run_id),
                            evidence_record_id=str(persisted.evidence_record_id),
                            profile_id=profile.profile_id,
                            profile_revision=profile.revision,
                            output_index=output_index,
                            entity_alias=rule.alias,
                            item_index=item_index,
                            provenance_json=provenance_json,
                        )
                    )
                    for observation in rule.observations:
                        value = validate_scalar(
                            resolve_pointer(item, observation.value_pointer),
                            observation.value_type,
                        )
                        observations.append(
                            ProjectionObservationOperation(
                                projection_key=stable_observation_key(
                                    profile.profile_id,
                                    profile.revision,
                                    str(persisted.evidence_record_id),
                                    output_index,
                                    rule.alias,
                                    item_index,
                                    observation.field_name,
                                ),
                                entity_key=entity_key,
                                field_name=observation.field_name,
                                value=value,
                                value_type=observation.value_type,
                                effective_from=effective_from,
                                effective_to=effective_to,
                                flow_run_id=str(persisted.flow_run_id),
                                step_run_id=str(persisted.step_run_id),
                                evidence_record_id=str(persisted.evidence_record_id),
                                profile_id=profile.profile_id,
                                profile_revision=profile.revision,
                                output_index=output_index,
                                entity_alias=rule.alias,
                                item_index=item_index,
                                provenance_json=provenance_json,
                            )
                        )

        claims: list[ProjectionClaimOperation] = []
        for output_index, output in enumerate(persisted.mapped_outputs):
            for rule in profile.relationships:
                items = _items(output, rule.items_pointer)
                rule.cardinality.validate(len(items))
                for item_index, item in enumerate(items):
                    item = _object(item)
                    from_source_record_id = canonical_source_record_id(
                        resolve_pointer(item, rule.from_source_record_id_pointer)
                    )
                    to_source_record_id = canonical_source_record_id(
                        resolve_pointer(item, rule.to_source_record_id_pointer)
                    )
                    from_key = entity_keys.get((rule.from_alias, from_source_record_id))
                    to_key = entity_keys.get((rule.to_alias, to_source_record_id))
                    if from_key is None or to_key is None:
                        raise ProjectionValidationError(
                            "projection_endpoint_missing",
                            "A projection relationship endpoint is not present in persisted evidence",
                        )
                    effective_from, effective_to = _effective_dates(
                        item,
                        rule.effective_from_pointer,
                        rule.effective_to_pointer,
                    )
                    claims.append(
                        ProjectionClaimOperation(
                            projection_key=stable_claim_key(
                                profile.profile_id,
                                profile.revision,
                                str(persisted.evidence_record_id),
                                output_index,
                                rule.rule_id,
                                rule.semantic,
                                item_index,
                            ),
                            from_entity_key=from_key,
                            to_entity_key=to_key,
                            semantic=rule.semantic,
                            relationship_rule_id=rule.rule_id,
                            effective_from=effective_from,
                            effective_to=effective_to,
                            flow_run_id=str(persisted.flow_run_id),
                            step_run_id=str(persisted.step_run_id),
                            evidence_record_id=str(persisted.evidence_record_id),
                            profile_id=profile.profile_id,
                            profile_revision=profile.revision,
                            output_index=output_index,
                            relationship_item_index=item_index,
                            provenance_json=provenance_json,
                        )
                    )

        return ProjectionBatch(
            entities=tuple(entity_operations.values()),
            entity_assertions=tuple(entity_assertions),
            observations=tuple(observations),
            claims=tuple(claims),
        )

    def _fail(
        self,
        lease: ProjectionLease,
        code: str,
        safe_message: str,
        *,
        retryable: bool,
    ) -> ProjectionResult:
        self._db.rollback()
        should_retry = retryable and lease.attempt < MAX_PROJECTION_ATTEMPTS
        now = _utcnow()
        next_attempt_at = (
            now + _RETRY_DELAYS[min(lease.attempt - 1, len(_RETRY_DELAYS) - 1)]
            if should_retry
            else None
        )
        if not self._repository.mark_retry_or_failed(
            lease,
            now=now,
            code=code,
            safe_message=safe_message,
            retryable=should_retry,
            next_attempt_at=next_attempt_at,
        ):
            self._db.rollback()
            return ProjectionResult(job_id=lease.job_id, status="lease_lost", attempt=lease.attempt)
        self._db.commit()
        return ProjectionResult(
            job_id=lease.job_id,
            status="retry" if should_retry else "failed",
            attempt=lease.attempt,
        )


def _items(output: dict[str, Any], pointer: str) -> list[Any]:
    value = resolve_pointer(output, pointer)
    if not isinstance(value, list):
        raise ProjectionValidationError(
            "projection_cardinality_invalid",
            "An approved projection collection is not an array",
        )
    return value


def _object(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProjectionValidationError(
            "projection_evidence_invalid",
            "An approved projection collection item is not an object",
        )
    return value


def _effective_dates(
    item: dict[str, Any],
    effective_from_pointer: str,
    effective_to_pointer: str | None,
) -> tuple[str, str | None]:
    effective_from = _iso_date(resolve_pointer(item, effective_from_pointer))
    raw_effective_to = (
        resolve_pointer(item, effective_to_pointer)
        if effective_to_pointer is not None
        else None
    )
    effective_to = (
        _iso_date(raw_effective_to) if raw_effective_to is not None else None
    )
    if effective_to is not None and effective_from > effective_to:
        raise ProjectionValidationError(
            "projection_effective_date_invalid",
            "Projection effective dates are invalid",
        )
    return effective_from, effective_to


def _iso_date(value: Any) -> str:
    if not isinstance(value, str):
        raise ProjectionValidationError(
            "projection_effective_date_invalid",
            "Projection effective dates are invalid",
        )
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as error:
        raise ProjectionValidationError(
            "projection_effective_date_invalid",
            "Projection effective dates are invalid",
        ) from error


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)
