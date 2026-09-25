"""B4/OBS-1968 -- Append-only forensic observation claims, relations, assessments.

Domain model for the forensic ledger's claim layer. Claims are immutable
observed-value records; relations link claims into contradiction pairs;
assessments record claim dispositions and contradiction-resolution states.
Corrections are append-only superseding records. Nothing here adjudicates
identity or merges claims; projection eligibility is decided per claim
(AC-211, AC-212). Values are preserved verbatim as canonical JSON; exception
text never carries raw observed values or rationale bodies (redaction
discipline) -- only stable codes and ledger identifiers.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from hashlib import sha256
from typing import Sequence
from uuid import UUID

OBSERVATIONS_CONTRACT_VERSION = "v1"

_SAFE_PREDICATE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")

_HOLD_WITHDRAWN = "forensic_claim_withdrawn_hold"
_HOLD_SUPERSEDED = "forensic_claim_superseded_hold"
_HOLD_INSUFFICIENT_SUPPORT = "forensic_claim_insufficient_support_hold"

_ERROR_MISSING_EVIDENCE = "forensic_claim_missing_evidence"
_ERROR_SUPERSESSION_FORK = "forensic_claim_supersession_fork"
_ERROR_RELATION_SELF = "forensic_claim_relation_self"
_ERROR_RELATION_DUPLICATE = "forensic_claim_relation_duplicate"
_ERROR_MISSING_RATIONALE = "forensic_claim_missing_rationale"
_ERROR_ASSESSMENT_SCOPE_MISMATCH = "forensic_claim_assessment_scope_mismatch"
_ERROR_INVALID_KEY = "forensic_claim_invalid_key"


class ForensicObservationError(RuntimeError):
    """A safe, stable forensic-observation failure with a diagnostic code.

    Messages carry codes and ledger identifiers only -- never raw observed
    values or rationale bodies.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.safe_message = message


class ClaimRelationKind(StrEnum):
    """Semantic kind of a relation between two claims."""

    CONTRADICTS = "contradicts"
    COMPATIBLE = "compatible"
    DUPLICATE_REPORT = "duplicate_report"


class ClaimAssessmentKind(StrEnum):
    """Assessment kinds; scope follows kind.

    withdrawn and insufficient_support are claim-scoped; unresolved,
    ambiguous, resolved_compatible, and resolved_upheld are
    relation-scoped (they resolve a contradiction relation).
    """

    WITHDRAWN = "withdrawn"
    INSUFFICIENT_SUPPORT = "insufficient_support"
    UNRESOLVED = "unresolved"
    AMBIGUOUS = "ambiguous"
    RESOLVED_COMPATIBLE = "resolved_compatible"
    RESOLVED_UPHELD = "resolved_upheld"


class ContradictionState(StrEnum):
    """Projection-facing state of a contradiction relation."""

    UNRESOLVED = "unresolved"
    AMBIGUOUS = "ambiguous"
    RESOLVED_COMPATIBLE = "resolved_compatible"
    RESOLVED_UPHELD = "resolved_upheld"


_CLAIM_SCOPED_ASSESSMENT_KINDS = frozenset(
    {
        ClaimAssessmentKind.WITHDRAWN,
        ClaimAssessmentKind.INSUFFICIENT_SUPPORT,
    }
)

_ASSESSMENT_STATE_MAP = {
    ClaimAssessmentKind.UNRESOLVED: ContradictionState.UNRESOLVED,
    ClaimAssessmentKind.AMBIGUOUS: ContradictionState.AMBIGUOUS,
    ClaimAssessmentKind.RESOLVED_COMPATIBLE: ContradictionState.RESOLVED_COMPATIBLE,
    ClaimAssessmentKind.RESOLVED_UPHELD: ContradictionState.RESOLVED_UPHELD,
}


@dataclass(frozen=True)
class ObservedClaim:
    """An immutable observed-value claim anchored to a SQLite evidence envelope."""

    id: UUID
    claim_key: str
    subject_entity_key: str
    predicate: str
    observed_value_json: str
    value_digest: str
    observed_at: datetime
    evidence_envelope_id: UUID
    source_card_id: str
    source_card_version: int
    recorded_at: datetime
    case_reference: str | None = None
    object_entity_key: str | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    source_record_id: UUID | None = None
    supersedes_id: UUID | None = None
    supersedes_reason: str | None = None

    def __post_init__(self) -> None:
        _require_uuid(self.id, "id")
        _require_hex64(self.claim_key, "claim_key")
        _require_hex64(self.subject_entity_key, "subject_entity_key")
        if self.object_entity_key is not None:
            _require_hex64(self.object_entity_key, "object_entity_key")
        _require_safe_predicate(self.predicate)
        _require_canonical_json(self.observed_value_json)
        _require_hex64(self.value_digest, "value_digest")
        _require_digest_match(self.observed_value_json, self.value_digest)
        _require_aware(self.observed_at, "observed_at")
        _require_aware(self.recorded_at, "recorded_at")
        _require_aware_optional(self.valid_from, "valid_from")
        _require_aware_optional(self.valid_to, "valid_to")
        if (
            self.valid_from is not None
            and self.valid_to is not None
            and self.valid_to < self.valid_from
        ):
            raise ValueError("valid_to must not precede valid_from")
        _require_uuid(self.evidence_envelope_id, "evidence_envelope_id")
        _require_nonempty_text(self.source_card_id, "source_card_id")
        if not isinstance(self.source_card_version, int) or self.source_card_version < 1:
            raise ValueError("source_card_version must be >= 1")
        _require_uuid_optional(self.source_record_id, "source_record_id")
        _require_supersession(
            self.id, self.supersedes_id, self.supersedes_reason, "claim"
        )

    @property
    def is_correction(self) -> bool:
        return self.supersedes_id is not None


@dataclass(frozen=True)
class ClaimRelation:
    """A relation between two claims, stored in normalized pair order."""

    id: UUID
    relation_key: str
    claim_id: UUID
    related_claim_id: UUID
    kind: ClaimRelationKind
    rationale: str
    evidence_envelope_id: UUID
    recorded_at: datetime
    supersedes_id: UUID | None = None
    supersedes_reason: str | None = None

    def __post_init__(self) -> None:
        _require_uuid(self.id, "id")
        _require_hex64(self.relation_key, "relation_key")
        _require_uuid(self.claim_id, "claim_id")
        _require_uuid(self.related_claim_id, "related_claim_id")
        # Normalize pair order: lexicographically smaller UUID hex first.
        first, second = sorted((str(self.claim_id), str(self.related_claim_id)))
        object.__setattr__(self, "claim_id", UUID(first))
        object.__setattr__(self, "related_claim_id", UUID(second))
        if self.claim_id == self.related_claim_id:
            raise ValueError("a relation cannot link a claim to itself")
        if not isinstance(self.kind, ClaimRelationKind):
            raise TypeError("kind must be a ClaimRelationKind")
        _require_nonempty_text(self.rationale, "rationale")
        _require_uuid(self.evidence_envelope_id, "evidence_envelope_id")
        _require_aware(self.recorded_at, "recorded_at")
        _require_supersession(
            self.id, self.supersedes_id, self.supersedes_reason, "relation"
        )

    @property
    def is_correction(self) -> bool:
        return self.supersedes_id is not None


@dataclass(frozen=True)
class ClaimAssessment:
    """An assessment of a claim disposition or a contradiction relation.

    Exactly one scope is set: claim_id for claim-scoped kinds, relation_id
    for relation-scoped kinds. Assessments supersede only assessments, so
    there is structurally no path to supersede a claim (AC-211).
    """

    id: UUID
    assessment_key: str
    kind: ClaimAssessmentKind
    rationale: str
    evidence_envelope_id: UUID
    recorded_at: datetime
    claim_id: UUID | None = None
    relation_id: UUID | None = None
    supersedes_id: UUID | None = None
    supersedes_reason: str | None = None

    def __post_init__(self) -> None:
        _require_uuid(self.id, "id")
        _require_hex64(self.assessment_key, "assessment_key")
        if not isinstance(self.kind, ClaimAssessmentKind):
            raise TypeError("kind must be a ClaimAssessmentKind")
        _require_nonempty_text(self.rationale, "rationale")
        _require_uuid(self.evidence_envelope_id, "evidence_envelope_id")
        _require_aware(self.recorded_at, "recorded_at")
        _require_uuid_optional(self.claim_id, "claim_id")
        _require_uuid_optional(self.relation_id, "relation_id")
        if (self.claim_id is None) == (self.relation_id is None):
            raise ValueError("exactly one of claim_id or relation_id must be set")
        if self.kind in _CLAIM_SCOPED_ASSESSMENT_KINDS:
            if self.claim_id is None:
                raise ValueError(f"{self.kind.value} assessments are claim-scoped")
        elif self.relation_id is None:
            raise ValueError(f"{self.kind.value} assessments are relation-scoped")
        _require_supersession(
            self.id, self.supersedes_id, self.supersedes_reason, "assessment"
        )

    @property
    def is_correction(self) -> bool:
        return self.supersedes_id is not None


@dataclass(frozen=True)
class ProjectionEligibility:
    """Per-claim projection decision; one claim is never merged with another."""

    claim_id: UUID
    eligible: bool
    hold_code: str | None
    contradiction_state: ContradictionState | None
    claim_key: str


def canonical_observed_value(value: object) -> str:
    """Serialize a JSON-serializable value to the ledger's canonical form.

    Canonical form is the only accepted encoding for
    ``ObservedClaim.observed_value_json``: sorted keys, compact separators,
    ascii-escaped. One value has exactly one canonical byte sequence, so
    ``value_digest`` and ``claim_key`` are stable per value.
    """
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def build_claim_key(
    subject_entity_key: str,
    predicate: str,
    value_digest: str,
    observed_at: datetime,
    source_card_id: str,
    source_card_version: int,
) -> str:
    """Stable content-addressed key for a claim record."""
    return _hash_parts(
        "obs1968/claim/v1",
        subject_entity_key,
        predicate,
        value_digest,
        _utc_iso(observed_at),
        source_card_id,
        str(source_card_version),
    )


def build_relation_key(
    claim_id: UUID,
    related_claim_id: UUID,
    kind: ClaimRelationKind,
) -> str:
    """Stable key over the normalized claim pair and relation kind."""
    first, second = sorted((str(claim_id), str(related_claim_id)))
    return _hash_parts("obs1968/relation/v1", first, second, kind.value)


def build_assessment_key(
    scope_id: UUID,
    kind: ClaimAssessmentKind,
    recorded_at: datetime,
) -> str:
    """Stable key over the scope target, assessment kind, and recorded time.

    The scope tag (claim vs relation) follows deterministically from kind.
    """
    scope_tag = "claim" if kind in _CLAIM_SCOPED_ASSESSMENT_KINDS else "relation"
    return _hash_parts(
        "obs1968/assessment/v1",
        scope_tag,
        str(scope_id),
        kind.value,
        _utc_iso(recorded_at),
    )


def derive_contradiction_state(
    relation: ClaimRelation,
    assessments: Sequence[ClaimAssessment],
) -> ContradictionState:
    """Resolve a contradiction relation to its current projection state.

    A contradicts relation with no live relation-scoped resolving
    assessment is UNRESOLVED; otherwise the latest non-superseded
    relation-scoped resolving assessment wins. Non-contradicts relations
    have no contradiction to resolve and default to UNRESOLVED.
    """
    if relation.kind is not ClaimRelationKind.CONTRADICTS:
        return ContradictionState.UNRESOLVED
    resolving = [
        assessment
        for assessment in assessments
        if assessment.relation_id == relation.id
        and assessment.supersedes_id is None
        and assessment.kind in _ASSESSMENT_STATE_MAP
    ]
    if not resolving:
        return ContradictionState.UNRESOLVED
    latest = max(resolving, key=lambda a: (a.recorded_at, a.id))
    return _ASSESSMENT_STATE_MAP[latest.kind]


def evaluate_projection_eligibility(
    claim: ObservedClaim,
    *,
    superseded: bool,
    assessments: Sequence[ClaimAssessment],
    relations_with_states: Sequence[tuple[ClaimRelation, ContradictionState]],
) -> ProjectionEligibility:
    """Decide projection eligibility for one claim.

    Holds (in precedence order): withdrawn, superseded,
    insufficient_support. An unresolved contradiction does not hold either
    side -- both claims project with contradiction_state=UNRESOLVED
    (AC-212); this function evaluates one claim at a time and never
    returns a merged or pick-one result.
    """
    live_claim_assessments = [
        assessment
        for assessment in assessments
        if assessment.claim_id == claim.id and assessment.supersedes_id is None
    ]
    if any(
        assessment.kind is ClaimAssessmentKind.WITHDRAWN
        for assessment in live_claim_assessments
    ):
        return ProjectionEligibility(
            claim.id, False, _HOLD_WITHDRAWN, None, claim.claim_key
        )
    if superseded:
        return ProjectionEligibility(
            claim.id, False, _HOLD_SUPERSEDED, None, claim.claim_key
        )
    if any(
        assessment.kind is ClaimAssessmentKind.INSUFFICIENT_SUPPORT
        for assessment in live_claim_assessments
    ):
        return ProjectionEligibility(
            claim.id, False, _HOLD_INSUFFICIENT_SUPPORT, None, claim.claim_key
        )
    contradiction_state = _claim_contradiction_state(claim.id, relations_with_states)
    return ProjectionEligibility(
        claim.id, True, None, contradiction_state, claim.claim_key
    )


def _claim_contradiction_state(
    claim_id: UUID,
    relations_with_states: Sequence[tuple[ClaimRelation, ContradictionState]],
) -> ContradictionState | None:
    """State of the most recently recorded live contradiction involving the claim."""
    relevant = [
        (relation, state)
        for relation, state in relations_with_states
        if relation.kind is ClaimRelationKind.CONTRADICTS
        and relation.supersedes_id is None
        and claim_id in (relation.claim_id, relation.related_claim_id)
    ]
    if not relevant:
        return None
    latest = max(relevant, key=lambda pair: (pair[0].recorded_at, pair[0].id))
    return latest[1]


def _hash_parts(domain: str, *parts: str) -> str:
    return sha256((domain + "\0" + "\0".join(parts)).encode()).hexdigest()


def _utc_iso(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat()


def _require_hex64(value: object, name: str) -> None:
    if not isinstance(value, str) or not _HEX64.fullmatch(value):
        raise ValueError(f"{name} must be a 64-char hex string")


def _require_safe_predicate(value: object) -> None:
    if not isinstance(value, str) or not _SAFE_PREDICATE.fullmatch(value):
        raise ValueError("predicate must match ^[a-z][a-z0-9_]{0,63}$")


def _require_canonical_json(value: object) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError("observed_value_json must be a non-empty JSON string")
    try:
        parsed = json.loads(value)
    except (ValueError, TypeError) as error:
        raise ValueError("observed_value_json must be valid JSON") from error
    if value != canonical_observed_value(parsed):
        raise ValueError(
            "observed_value_json must be canonical JSON"
            " (sorted keys, compact separators, ascii-escaped)"
        )


def _require_digest_match(value_json: str, digest: str) -> None:
    computed = sha256(value_json.encode()).hexdigest()
    if digest != computed:
        raise ValueError(
            "value_digest does not match sha256 of observed_value_json"
        )


def _require_nonempty_text(value: object, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} is required")


def _require_uuid(value: object, name: str) -> None:
    if not isinstance(value, UUID):
        raise TypeError(f"{name} must be a UUID")


def _require_uuid_optional(value: object, name: str) -> None:
    if value is not None and not isinstance(value, UUID):
        raise TypeError(f"{name} must be a UUID or None")


def _require_aware(value: datetime, name: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise TypeError(f"{name} must be timezone-aware")


def _require_aware_optional(value: datetime | None, name: str) -> None:
    if value is not None:
        _require_aware(value, name)


def _require_supersession(
    record_id: UUID,
    supersedes_id: UUID | None,
    supersedes_reason: str | None,
    kind: str,
) -> None:
    if record_id == supersedes_id:
        raise ValueError(f"{kind} cannot supersede itself")
    if supersedes_id is not None and not supersedes_reason:
        raise ValueError(f"supersedes_reason is required when superseding a {kind}")
    if supersedes_reason and supersedes_id is None:
        raise ValueError(
            f"supersedes_reason is meaningless without a superseded {kind}"
        )
