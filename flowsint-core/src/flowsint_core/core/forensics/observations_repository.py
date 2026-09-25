"""B4/OBS-1968 -- SQLite-anchored forensic claim, relation, assessment repository.

Persists append-only observation records into the authoritative SQLite
ledger. Every insert pre-validates references and scope shape so failures
surface as ForensicObservationError with stable codes before SQL
constraint errors. All loads are deterministic (ordered by recorded_at,
then id) and never collapse contradictory claims (AC-211).
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from flowsint_core.core.forensics.observations import (
    ClaimAssessment,
    ClaimAssessmentKind,
    ClaimRelation,
    ClaimRelationKind,
    ContradictionState,
    ForensicObservationError,
    ObservedClaim,
    ProjectionEligibility,
    derive_contradiction_state,
    evaluate_projection_eligibility,
)
from flowsint_core.core.models import (
    EvidenceEnvelopeRecord,
    ForensicClaimAssessmentRecord,
    ForensicClaimRecord,
    ForensicClaimRelationRecord,
)

_ERROR_MISSING_EVIDENCE = "forensic_claim_missing_evidence"
_ERROR_SUPERSESSION_FORK = "forensic_claim_supersession_fork"
_ERROR_RELATION_SELF = "forensic_claim_relation_self"
_ERROR_RELATION_DUPLICATE = "forensic_claim_relation_duplicate"
_ERROR_MISSING_RATIONALE = "forensic_claim_missing_rationale"
_ERROR_ASSESSMENT_SCOPE_MISMATCH = "forensic_claim_assessment_scope_mismatch"
_ERROR_INVALID_KEY = "forensic_claim_invalid_key"


def insert_claim_record(
    session: Session,
    claim: ObservedClaim,
) -> ForensicClaimRecord:
    """Insert one immutable claim record, verifying evidence and fork safety."""
    _require_valid_key(claim.claim_key)
    if not _evidence_exists(session, claim.evidence_envelope_id):
        raise ForensicObservationError(
            _ERROR_MISSING_EVIDENCE,
            f"evidence envelope {claim.evidence_envelope_id} is missing for claim {claim.id}",
        )
    if claim.supersedes_id is not None:
        if not claim.supersedes_reason:
            raise ForensicObservationError(
                _ERROR_MISSING_RATIONALE,
                f"claim {claim.id} supersedes {claim.supersedes_id} without a supersedes_reason",
            )
        if not _claim_exists(session, claim.supersedes_id):
            raise ForensicObservationError(
                _ERROR_INVALID_KEY,
                f"claim {claim.id} supersedes claim {claim.supersedes_id} which does not exist",
            )
        if _fork_exists(session, ForensicClaimRecord, claim.supersedes_id):
            raise ForensicObservationError(
                _ERROR_SUPERSESSION_FORK,
                f"claim {claim.supersedes_id} is already superseded by another claim",
            )
    record = ForensicClaimRecord(
        id=claim.id,
        claim_key=claim.claim_key,
        case_reference=claim.case_reference,
        subject_entity_key=claim.subject_entity_key,
        predicate=claim.predicate,
        object_entity_key=claim.object_entity_key,
        observed_value_json=claim.observed_value_json,
        value_digest=claim.value_digest,
        observed_at=claim.observed_at,
        valid_from=claim.valid_from,
        valid_to=claim.valid_to,
        source_card_id=claim.source_card_id,
        source_card_version=claim.source_card_version,
        source_record_id=claim.source_record_id,
        evidence_envelope_id=claim.evidence_envelope_id,
        supersedes_id=claim.supersedes_id,
        supersedes_reason=claim.supersedes_reason,
        recorded_at=claim.recorded_at,
    )
    session.add(record)
    session.flush()
    return record


def insert_claim_relation_record(
    session: Session,
    relation: ClaimRelation,
) -> ForensicClaimRelationRecord:
    """Insert one normalized claim-pair relation with full reference checks."""
    _require_valid_key(relation.relation_key)
    if relation.claim_id == relation.related_claim_id:
        raise ForensicObservationError(
            _ERROR_RELATION_SELF,
            f"relation {relation.id} links claim {relation.claim_id} to itself",
        )
    if not relation.rationale or not relation.rationale.strip():
        raise ForensicObservationError(
            _ERROR_MISSING_RATIONALE,
            f"relation {relation.id} requires a non-empty rationale",
        )
    if not _claim_exists(session, relation.claim_id) or not _claim_exists(
        session, relation.related_claim_id
    ):
        raise ForensicObservationError(
            _ERROR_INVALID_KEY,
            f"relation {relation.id} references a claim that does not exist",
        )
    if _duplicate_relation_exists(session, relation):
        raise ForensicObservationError(
            _ERROR_RELATION_DUPLICATE,
            f"relation {relation.id} duplicates existing {relation.kind.value} pair "
            f"({relation.claim_id}, {relation.related_claim_id})",
        )
    if not _evidence_exists(session, relation.evidence_envelope_id):
        raise ForensicObservationError(
            _ERROR_MISSING_EVIDENCE,
            f"evidence envelope {relation.evidence_envelope_id} is missing for relation {relation.id}",
        )
    if relation.supersedes_id is not None:
        if not relation.supersedes_reason:
            raise ForensicObservationError(
                _ERROR_MISSING_RATIONALE,
                f"relation {relation.id} supersedes {relation.supersedes_id} without a supersedes_reason",
            )
        if not _relation_exists(session, relation.supersedes_id):
            raise ForensicObservationError(
                _ERROR_INVALID_KEY,
                f"relation {relation.id} supersedes relation {relation.supersedes_id} which does not exist",
            )
        if _fork_exists(session, ForensicClaimRelationRecord, relation.supersedes_id):
            raise ForensicObservationError(
                _ERROR_SUPERSESSION_FORK,
                f"relation {relation.supersedes_id} is already superseded by another relation",
            )
    record = ForensicClaimRelationRecord(
        id=relation.id,
        relation_key=relation.relation_key,
        claim_id=relation.claim_id,
        related_claim_id=relation.related_claim_id,
        kind=relation.kind.value,
        rationale=relation.rationale,
        evidence_envelope_id=relation.evidence_envelope_id,
        supersedes_id=relation.supersedes_id,
        supersedes_reason=relation.supersedes_reason,
        recorded_at=relation.recorded_at,
    )
    session.add(record)
    session.flush()
    return record


def insert_claim_assessment_record(
    session: Session,
    assessment: ClaimAssessment,
) -> ForensicClaimAssessmentRecord:
    """Insert one assessment, enforcing claim/relation scope shape and targets."""
    _require_valid_key(assessment.assessment_key)
    if not _scope_is_valid(assessment):
        raise ForensicObservationError(
            _ERROR_ASSESSMENT_SCOPE_MISMATCH,
            f"assessment {assessment.id} scope does not match kind {assessment.kind.value}",
        )
    if assessment.claim_id is not None and not _claim_exists(session, assessment.claim_id):
        raise ForensicObservationError(
            _ERROR_INVALID_KEY,
            f"assessment {assessment.id} references claim {assessment.claim_id} which does not exist",
        )
    if assessment.relation_id is not None and not _relation_exists(
        session, assessment.relation_id
    ):
        raise ForensicObservationError(
            _ERROR_INVALID_KEY,
            f"assessment {assessment.id} references relation {assessment.relation_id} which does not exist",
        )
    if not assessment.rationale or not assessment.rationale.strip():
        raise ForensicObservationError(
            _ERROR_MISSING_RATIONALE,
            f"assessment {assessment.id} requires a non-empty rationale",
        )
    if not _evidence_exists(session, assessment.evidence_envelope_id):
        raise ForensicObservationError(
            _ERROR_MISSING_EVIDENCE,
            f"evidence envelope {assessment.evidence_envelope_id} is missing for assessment {assessment.id}",
        )
    if assessment.supersedes_id is not None:
        if not assessment.supersedes_reason:
            raise ForensicObservationError(
                _ERROR_MISSING_RATIONALE,
                f"assessment {assessment.id} supersedes {assessment.supersedes_id} without a supersedes_reason",
            )
        if not _assessment_exists(session, assessment.supersedes_id):
            raise ForensicObservationError(
                _ERROR_INVALID_KEY,
                f"assessment {assessment.id} supersedes assessment {assessment.supersedes_id} which does not exist",
            )
        if _fork_exists(session, ForensicClaimAssessmentRecord, assessment.supersedes_id):
            raise ForensicObservationError(
                _ERROR_SUPERSESSION_FORK,
                f"assessment {assessment.supersedes_id} is already superseded by another assessment",
            )
    record = ForensicClaimAssessmentRecord(
        id=assessment.id,
        assessment_key=assessment.assessment_key,
        claim_id=assessment.claim_id,
        relation_id=assessment.relation_id,
        kind=assessment.kind.value,
        rationale=assessment.rationale,
        evidence_envelope_id=assessment.evidence_envelope_id,
        supersedes_id=assessment.supersedes_id,
        supersedes_reason=assessment.supersedes_reason,
        recorded_at=assessment.recorded_at,
    )
    session.add(record)
    session.flush()
    return record


def load_claims_for_subject(
    session: Session,
    subject_entity_key: str,
) -> list[ForensicClaimRecord]:
    """Load every claim row for a subject, including superseded and withdrawn.

    Contradictory claims remain simultaneously queryable (AC-211); the
    ledger never collapses them. Ordered by recorded_at, then id.
    """
    return list(
        session.execute(
            select(ForensicClaimRecord)
            .where(ForensicClaimRecord.subject_entity_key == subject_entity_key)
            .order_by(
                ForensicClaimRecord.recorded_at.asc(),
                ForensicClaimRecord.id.asc(),
            )
        ).scalars().all()
    )


def load_contradiction_state(
    session: Session,
    relation_id: UUID,
) -> ContradictionState:
    """Derive the contradiction state of one relation from its live assessments."""
    relation_row = session.get(ForensicClaimRelationRecord, relation_id)
    if relation_row is None:
        raise ForensicObservationError(
            _ERROR_INVALID_KEY,
            f"no relation record exists for relation_id={relation_id}",
        )
    assessment_rows = list(
        session.execute(
            select(ForensicClaimAssessmentRecord)
            .where(
                ForensicClaimAssessmentRecord.relation_id == relation_id,
                ForensicClaimAssessmentRecord.supersedes_id.is_(None),
            )
            .order_by(
                ForensicClaimAssessmentRecord.recorded_at.asc(),
                ForensicClaimAssessmentRecord.id.asc(),
            )
        ).scalars().all()
    )
    return derive_contradiction_state(
        _relation_from_row(relation_row),
        [_assessment_from_row(row) for row in assessment_rows],
    )


def load_subject_contradictions(
    session: Session,
    subject_entity_key: str,
) -> list[tuple[ForensicClaimRelationRecord, ContradictionState]]:
    """Load live contradicts relations of a subject's claims with their states.

    Superseded relations are excluded: a correcting relation carries the
    current truth. Ordered by recorded_at, then id.
    """
    claim_rows = load_claims_for_subject(session, subject_entity_key)
    if not claim_rows:
        return []
    claim_ids = [row.id for row in claim_rows]
    relation_rows = list(
        session.execute(
            select(ForensicClaimRelationRecord)
            .where(
                ForensicClaimRelationRecord.supersedes_id.is_(None),
                ForensicClaimRelationRecord.kind == ClaimRelationKind.CONTRADICTS.value,
                or_(
                    ForensicClaimRelationRecord.claim_id.in_(claim_ids),
                    ForensicClaimRelationRecord.related_claim_id.in_(claim_ids),
                ),
            )
            .order_by(
                ForensicClaimRelationRecord.recorded_at.asc(),
                ForensicClaimRelationRecord.id.asc(),
            )
        ).scalars().all()
    )
    if not relation_rows:
        return []
    relation_ids = [row.id for row in relation_rows]
    assessment_rows = list(
        session.execute(
            select(ForensicClaimAssessmentRecord)
            .where(
                ForensicClaimAssessmentRecord.relation_id.in_(relation_ids),
                ForensicClaimAssessmentRecord.supersedes_id.is_(None),
            )
            .order_by(
                ForensicClaimAssessmentRecord.recorded_at.asc(),
                ForensicClaimAssessmentRecord.id.asc(),
            )
        ).scalars().all()
    )
    assessments_by_relation: dict[UUID, list[ClaimAssessment]] = {}
    for row in assessment_rows:
        assessments_by_relation.setdefault(row.relation_id, []).append(
            _assessment_from_row(row)
        )
    return [
        (
            relation_row,
            derive_contradiction_state(
                _relation_from_row(relation_row),
                assessments_by_relation.get(relation_row.id, []),
            ),
        )
        for relation_row in relation_rows
    ]


def evaluate_subject_projection_eligibility(
    session: Session,
    subject_entity_key: str,
) -> tuple[ProjectionEligibility, ...]:
    """Per-claim projection eligibility for a subject, never collapsed.

    Returns one ProjectionEligibility per claim row (superseded and
    withdrawn claims included), in load order.
    """
    claim_rows = load_claims_for_subject(session, subject_entity_key)
    if not claim_rows:
        return ()
    claim_ids = [row.id for row in claim_rows]
    # A claim is superseded when another claim row references it; the
    # referencing row is the live correction and stays eligible.
    superseded_ids = {
        row.supersedes_id for row in claim_rows if row.supersedes_id is not None
    }
    assessment_rows = list(
        session.execute(
            select(ForensicClaimAssessmentRecord)
            .where(
                ForensicClaimAssessmentRecord.claim_id.in_(claim_ids),
                ForensicClaimAssessmentRecord.supersedes_id.is_(None),
            )
            .order_by(
                ForensicClaimAssessmentRecord.recorded_at.asc(),
                ForensicClaimAssessmentRecord.id.asc(),
            )
        ).scalars().all()
    )
    assessments_by_claim: dict[UUID, list[ClaimAssessment]] = {}
    for row in assessment_rows:
        assessments_by_claim.setdefault(row.claim_id, []).append(
            _assessment_from_row(row)
        )
    relations_with_states = load_subject_contradictions(session, subject_entity_key)
    return tuple(
        evaluate_projection_eligibility(
            _claim_from_row(row),
            superseded=row.id in superseded_ids,
            assessments=assessments_by_claim.get(row.id, []),
            relations_with_states=[
                (_relation_from_row(relation_row), state)
                for relation_row, state in relations_with_states
                if row.id in (relation_row.claim_id, relation_row.related_claim_id)
            ],
        )
        for row in claim_rows
    )


# ── private helpers ─────────────────────────────────────────────────────────


def _require_valid_key(key: str) -> None:
    if not isinstance(key, str) or len(key) != 64 or any(
        char not in "0123456789abcdef" for char in key
    ):
        raise ForensicObservationError(
            _ERROR_INVALID_KEY, "ledger key must be a 64-char lowercase hex string"
        )


def _evidence_exists(session: Session, evidence_envelope_id: UUID) -> bool:
    return session.get(EvidenceEnvelopeRecord, evidence_envelope_id) is not None


def _claim_exists(session: Session, claim_id: UUID) -> bool:
    return session.get(ForensicClaimRecord, claim_id) is not None


def _relation_exists(session: Session, relation_id: UUID) -> bool:
    return session.get(ForensicClaimRelationRecord, relation_id) is not None


def _assessment_exists(session: Session, assessment_id: UUID) -> bool:
    return session.get(ForensicClaimAssessmentRecord, assessment_id) is not None


def _fork_exists(session: Session, model, supersedes_id: UUID) -> bool:
    return (
        session.execute(
            select(model.id).where(model.supersedes_id == supersedes_id)
        ).first()
        is not None
    )


def _duplicate_relation_exists(session: Session, relation: ClaimRelation) -> bool:
    return (
        session.execute(
            select(ForensicClaimRelationRecord.id).where(
                ForensicClaimRelationRecord.claim_id == relation.claim_id,
                ForensicClaimRelationRecord.related_claim_id == relation.related_claim_id,
                ForensicClaimRelationRecord.kind == relation.kind.value,
            )
        ).first()
        is not None
    )


def _scope_is_valid(assessment: ClaimAssessment) -> bool:
    if (assessment.claim_id is None) == (assessment.relation_id is None):
        return False
    if assessment.kind in {
        ClaimAssessmentKind.WITHDRAWN,
        ClaimAssessmentKind.INSUFFICIENT_SUPPORT,
    }:
        return assessment.claim_id is not None
    return assessment.relation_id is not None


def _claim_from_row(row: ForensicClaimRecord) -> ObservedClaim:
    return ObservedClaim(
        id=row.id,
        claim_key=row.claim_key,
        case_reference=row.case_reference,
        subject_entity_key=row.subject_entity_key,
        predicate=row.predicate,
        object_entity_key=row.object_entity_key,
        observed_value_json=row.observed_value_json,
        value_digest=row.value_digest,
        observed_at=_aware(row.observed_at),
        valid_from=_aware_optional(row.valid_from),
        valid_to=_aware_optional(row.valid_to),
        source_card_id=row.source_card_id,
        source_card_version=row.source_card_version,
        source_record_id=row.source_record_id,
        evidence_envelope_id=row.evidence_envelope_id,
        supersedes_id=row.supersedes_id,
        supersedes_reason=row.supersedes_reason,
        recorded_at=_aware(row.recorded_at),
    )


def _relation_from_row(row: ForensicClaimRelationRecord) -> ClaimRelation:
    return ClaimRelation(
        id=row.id,
        relation_key=row.relation_key,
        claim_id=row.claim_id,
        related_claim_id=row.related_claim_id,
        kind=ClaimRelationKind(row.kind),
        rationale=row.rationale,
        evidence_envelope_id=row.evidence_envelope_id,
        supersedes_id=row.supersedes_id,
        supersedes_reason=row.supersedes_reason,
        recorded_at=_aware(row.recorded_at),
    )


def _assessment_from_row(row: ForensicClaimAssessmentRecord) -> ClaimAssessment:
    return ClaimAssessment(
        id=row.id,
        assessment_key=row.assessment_key,
        kind=ClaimAssessmentKind(row.kind),
        rationale=row.rationale,
        evidence_envelope_id=row.evidence_envelope_id,
        claim_id=row.claim_id,
        relation_id=row.relation_id,
        supersedes_id=row.supersedes_id,
        supersedes_reason=row.supersedes_reason,
        recorded_at=_aware(row.recorded_at),
    )


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _aware_optional(value: datetime | None) -> datetime | None:
    return None if value is None else _aware(value)
