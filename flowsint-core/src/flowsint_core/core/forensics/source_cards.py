"""Forensic source-card authority contract (B4 / OBS-1965).

Defines versioned source cards, source families, independence
assessments, and upstream edges for the SQLite forensic ledger.

Design invariants:
- Source cards and families are immutable and versioned.
- Independence is explicit, evidence-backed, never inferred.
- All models are SQLite-row shapes with forensic audit provenance.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from uuid import UUID, uuid4


# ── Enums ────────────────────────────────────────────────────────────────────


class CollectionMethod(StrEnum):
    CONNECTOR = "connector"
    MANUAL_ENTRY = "manual_entry"
    DISCOVERED = "discovered"
    IMPORTED = "imported"
    UNKNOWN = "unknown"


class ArtifactLocatorMode(StrEnum):
    EXACT = "exact"
    PREFIX = "prefix"
    REGEX = "regex"
    OPEN = "open"


class IndependenceRelationship(StrEnum):
    INDEPENDENT = "independent"
    RELATED = "related"
    COMMON_UPSTREAM = "common_upstream"
    UNKNOWN = "unknown"


# ── Value Objects ─────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True, weakref_slot=True)
class ArtifactLocatorPolicy:
    mode: ArtifactLocatorMode
    expression: str | None  # required for EXACT/PREFIX/REGEX; must be None for OPEN

    def __post_init__(self) -> None:
        if self.mode is ArtifactLocatorMode.OPEN:
            if self.expression is not None:
                raise ValueError("OPEN policy must have expression=None")
        else:
            if self.expression is None:
                raise ValueError(f"{self.mode.value} policy requires an expression")
            _require_nonempty_text(self.expression, "expression")


# ── Source Card ───────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True, weakref_slot=True)
class SourceCard:
    card_id: UUID
    version: int
    card_revision_of: UUID | None
    label: str
    publisher_origin: str
    collection_method: CollectionMethod
    jurisdiction: str | None
    scope: str | None              # subject-matter/content scope
    rights: frozenset[str]
    reliability_context: str | None
    artifact_locator_policy: ArtifactLocatorPolicy
    scope_effective_from: datetime
    scope_effective_until: datetime | None
    created_at: datetime
    created_by_subject: str

    def __post_init__(self) -> None:
        _require_aware_datetime(self.scope_effective_from, "scope_effective_from")
        if self.scope_effective_until is not None:
            _require_aware_datetime(self.scope_effective_until, "scope_effective_until")
            if self.scope_effective_until <= self.scope_effective_from:
                raise ValueError("scope_effective_until must be after scope_effective_from")
        _require_aware_datetime(self.created_at, "created_at")
        if self.version < 1:
            raise ValueError("version must be >= 1")
        if self.card_revision_of is not None and self.version == 1:
            raise ValueError("v1 cannot have card_revision_of")
        if self.card_revision_of is None and self.version > 1:
            raise ValueError(f"version {self.version} must have card_revision_of")
        _require_nonempty_text(self.label, "label")
        _require_nonempty_text(self.publisher_origin, "publisher_origin")
        _require_text_set(self.rights, "rights")
        _require_nonempty_text(self.created_by_subject, "created_by_subject")


# ── Source Family ─────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True, weakref_slot=True)
class SourceFamily:
    family_id: UUID
    version: int
    family_revision_of: UUID | None
    label: str
    rationale: str
    scope_effective_from: datetime
    scope_effective_until: datetime | None
    created_at: datetime
    created_by_subject: str

    def __post_init__(self) -> None:
        _require_nonempty_text(self.label, "label")
        _require_nonempty_text(self.rationale, "rationale")
        _require_aware_datetime(self.scope_effective_from, "scope_effective_from")
        if self.scope_effective_until is not None:
            _require_aware_datetime(self.scope_effective_until, "scope_effective_until")
            if self.scope_effective_until <= self.scope_effective_from:
                raise ValueError("scope_effective_until must be after scope_effective_from")
        _require_aware_datetime(self.created_at, "created_at")
        _require_nonempty_text(self.created_by_subject, "created_by_subject")
        if self.version < 1:
            raise ValueError("version must be >= 1")
        if self.family_revision_of is not None and self.version == 1:
            raise ValueError("v1 cannot have family_revision_of")
        if self.family_revision_of is None and self.version > 1:
            raise ValueError(f"version {self.version} must have family_revision_of")


@dataclass(frozen=True, slots=True, weakref_slot=True)
class SourceCardFamily:
    card_id: UUID
    family_id: UUID
    family_version: int
    joined_at: datetime
    removed_at: datetime | None

    def __post_init__(self) -> None:
        _require_aware_datetime(self.joined_at, "joined_at")
        if self.family_version < 1:
            raise ValueError("family_version must be >= 1")
        if self.removed_at is not None:
            _require_aware_datetime(self.removed_at, "removed_at")
            if self.removed_at <= self.joined_at:
                raise ValueError("removed_at must be after joined_at")


# ── Independence Assessment ──────────────────────────────────────────────────


@dataclass(frozen=True, slots=True, weakref_slot=True)
class IndependenceAssessment:
    assessment_id: UUID
    card_a_id: UUID
    card_b_id: UUID
    relationship: IndependenceRelationship
    evidence_rationale: str
    evidence_references: frozenset[UUID]  # supporting evidence envelope ids
    effective_from: datetime
    effective_until: datetime | None
    assessed_by_subject: str
    assessed_at: datetime

    def __post_init__(self) -> None:
        if self.card_a_id == self.card_b_id:
            raise ValueError("cannot assess a source against itself")
        _require_nonempty_text(self.evidence_rationale, "evidence_rationale")
        _validate_evidence_references(
            self.evidence_references,
            allow_empty=(self.relationship is IndependenceRelationship.UNKNOWN),
            field="evidence_references",
        )
        _require_nonempty_text(self.assessed_by_subject, "assessed_by_subject")
        _require_aware_datetime(self.effective_from, "effective_from")
        if self.effective_until is not None:
            _require_aware_datetime(self.effective_until, "effective_until")
            if self.effective_until <= self.effective_from:
                raise ValueError("effective_until must be after effective_from")
        _require_aware_datetime(self.assessed_at, "assessed_at")


# ── Source Upstream ───────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True, weakref_slot=True)
class SourceUpstream:
    upstream_edge_id: UUID
    upstream_card_id: UUID
    downstream_card_id: UUID
    evidence_rationale: str
    evidence_references: frozenset[UUID]  # supporting evidence envelope ids
    effective_from: datetime
    effective_until: datetime | None
    assessed_by_subject: str
    assessed_at: datetime

    def __post_init__(self) -> None:
        if self.upstream_card_id == self.downstream_card_id:
            raise ValueError("a source cannot be upstream of itself")
        _require_nonempty_text(self.evidence_rationale, "evidence_rationale")
        _validate_evidence_references(
            self.evidence_references,
            allow_empty=False,  # positive upstream claim always requires evidence
            field="evidence_references",
        )
        _require_nonempty_text(self.assessed_by_subject, "assessed_by_subject")
        _require_aware_datetime(self.effective_from, "effective_from")
        if self.effective_until is not None:
            _require_aware_datetime(self.effective_until, "effective_until")
            if self.effective_until <= self.effective_from:
                raise ValueError("effective_until must be after effective_from")
        _require_aware_datetime(self.assessed_at, "assessed_at")


# ── Source Reference ──────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True, weakref_slot=True)
class SourceReference:
    envelope_id: UUID
    source_card_id: UUID | None
    source_card_version: int | None
    missing_source_reason: str | None
    referenced_at: datetime
    referenced_by_subject: str

    def __post_init__(self) -> None:
        has_card = self.source_card_id is not None
        has_reason = self.missing_source_reason is not None
        if has_card == has_reason:
            raise ValueError(
                "exactly one of source_card_id or missing_source_reason must be set"
            )
        if has_card:
            if self.source_card_version is None or self.source_card_version < 1:
                raise ValueError("source_card_version must be >= 1 when card is set")
        else:
            if self.source_card_version is not None:
                raise ValueError("source_card_version must be None when card is absent")
            _require_nonempty_text(self.missing_source_reason, "missing_source_reason")
        _require_nonempty_text(self.referenced_by_subject, "referenced_by_subject")
        _require_aware_datetime(self.referenced_at, "referenced_at")


# ── Helpers ───────────────────────────────────────────────────────────────────


def _require_nonempty_text(value: str, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise TypeError(f"{field} must be a non-empty string")


def _require_text_set(value: frozenset[str], field: str) -> None:
    if not isinstance(value, frozenset):
        raise TypeError(f"{field} must be a frozenset[str]")
    for item in value:
        if not isinstance(item, str):
            raise TypeError(f"{field} must contain only strings, got {type(item)}")


def _require_aware_datetime(dt: datetime, field: str) -> None:
    if not isinstance(dt, datetime) or dt.tzinfo is None:
        raise TypeError(f"{field} must be a timezone-aware datetime")


# ── Revision Factories ────────────────────────────────────────────────────────


_UNSET = object()


def revise_source_card(
    current: SourceCard,
    *,
    label: str | None = None,
    publisher_origin: str | None = None,
    collection_method: CollectionMethod | None = None,
    jurisdiction: str | None | object = _UNSET,
    scope: str | None | object = _UNSET,
    rights: frozenset[str] | None = None,
    reliability_context: str | None | object = _UNSET,
    artifact_locator_policy: ArtifactLocatorPolicy | None = None,
    revised_by_subject: str | None = None,
) -> tuple[SourceCard, SourceCard]:
    if revised_by_subject is None:
        raise ValueError("revised_by_subject is required")
    now = datetime.now(timezone.utc)

    closed_prior = SourceCard(
        card_id=current.card_id,
        version=current.version,
        card_revision_of=current.card_revision_of,
        label=current.label,
        publisher_origin=current.publisher_origin,
        collection_method=current.collection_method,
        jurisdiction=current.jurisdiction,
        scope=current.scope,
        rights=current.rights,
        reliability_context=current.reliability_context,
        artifact_locator_policy=current.artifact_locator_policy,
        scope_effective_from=current.scope_effective_from,
        scope_effective_until=now,
        created_at=current.created_at,
        created_by_subject=current.created_by_subject,
    )

    resolved_jurisdiction: str | None = (
        current.jurisdiction if jurisdiction is _UNSET else jurisdiction
    )
    resolved_scope: str | None = (
        current.scope if scope is _UNSET else scope
    )
    resolved_reliability: str | None = (
        current.reliability_context if reliability_context is _UNSET else reliability_context
    )

    return closed_prior, SourceCard(
        card_id=uuid4(),
        version=current.version + 1,
        card_revision_of=current.card_id,
        label=label if label is not None else current.label,
        publisher_origin=publisher_origin if publisher_origin is not None else current.publisher_origin,
        collection_method=collection_method if collection_method is not None else current.collection_method,
        jurisdiction=resolved_jurisdiction,
        scope=resolved_scope,
        rights=rights if rights is not None else current.rights,
        reliability_context=resolved_reliability,
        artifact_locator_policy=(
            artifact_locator_policy
            if artifact_locator_policy is not None
            else current.artifact_locator_policy
        ),
        scope_effective_from=now,
        scope_effective_until=None,
        created_at=now,
        created_by_subject=revised_by_subject,
    )


def revise_source_family(
    current: SourceFamily,
    *,
    label: str | None = None,
    rationale: str | None = None,
    revised_by_subject: str | None = None,
) -> tuple[SourceFamily, SourceFamily]:
    if revised_by_subject is None:
        raise ValueError("revised_by_subject is required")
    now = datetime.now(timezone.utc)
    closed_prior = SourceFamily(
        family_id=current.family_id,
        version=current.version,
        family_revision_of=current.family_revision_of,
        label=current.label,
        rationale=current.rationale,
        scope_effective_from=current.scope_effective_from,
        scope_effective_until=now,
        created_at=current.created_at,
        created_by_subject=current.created_by_subject,
    )
    return closed_prior, SourceFamily(
        family_id=uuid4(),
        version=current.version + 1,
        family_revision_of=current.family_id,
        label=label if label is not None else current.label,
        rationale=rationale if rationale is not None else current.rationale,
        scope_effective_from=now,
        scope_effective_until=None,
        created_at=now,
        created_by_subject=revised_by_subject,
    )


# ── Authority: Independence Rules ─────────────────────────────────────────────


def require_distinct_sources(
    card_a: SourceCard,
    card_b: SourceCard,
    assessment: IndependenceAssessment,
    *,
    evaluated_at: datetime,
) -> None:
    _require_aware_datetime(evaluated_at, "evaluated_at")
    if card_a.card_id == card_b.card_id:
        raise SourceIndependenceError(
            card_a.card_id, card_b.card_id, assessment.assessment_id,
            "same source cannot corroborate itself",
        )
    if assessment.card_a_id != card_a.card_id or assessment.card_b_id != card_b.card_id:
        raise SourceIndependenceError(
            card_a.card_id, card_b.card_id, assessment.assessment_id,
            "assessment does not reference the provided cards",
        )
    if assessment.effective_from > evaluated_at:
        raise SourceIndependenceError(
            card_a.card_id, card_b.card_id, assessment.assessment_id,
            "assessment is not yet effective",
        )
    if assessment.effective_until is not None and assessment.effective_until <= evaluated_at:
        raise SourceIndependenceError(
            card_a.card_id, card_b.card_id, assessment.assessment_id,
            "assessment has expired",
        )
    if assessment.relationship is IndependenceRelationship.UNKNOWN:
        raise SourceIndependenceError(
            card_a.card_id, card_b.card_id, assessment.assessment_id,
            "UNKNOWN relationship cannot be treated as independent",
        )
    if assessment.relationship in (
        IndependenceRelationship.RELATED,
        IndependenceRelationship.COMMON_UPSTREAM,
    ):
        raise SourceIndependenceError(
            card_a.card_id, card_b.card_id, assessment.assessment_id,
            f"sources are {assessment.relationship.name}, not independent",
        )

class SourceIndependenceError(ValueError):
    def __init__(
        self,
        card_a_id: UUID,
        card_b_id: UUID,
        assessment_id: UUID,
        reason: str,
    ) -> None:
        self.card_a_id = card_a_id
        self.card_b_id = card_b_id
        self.assessment_id = assessment_id
        self.reason = reason
        super().__init__(
            f"independence violation between {card_a_id} and {card_b_id}: {reason}"
        )


def _validate_evidence_references(
    refs: object,
    *,
    allow_empty: bool,
    field: str,
) -> None:
    if not isinstance(refs, frozenset):
        raise TypeError(f"{field} must be a frozenset[UUID]")
    for item in refs:
        if not isinstance(item, UUID):
            raise TypeError(f"{field} must contain only UUIDs, got {type(item)}")
    if not allow_empty and not refs:
        raise ValueError(f"{field} must not be empty")
