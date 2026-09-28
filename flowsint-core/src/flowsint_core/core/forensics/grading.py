"""B9/OBS-1970 -- LeadGen evidence maturity and Admiralty grading over forensic claims.

Contract layer porting LeadGen evidence maturity (E0-E3) and Admiralty
grading (A-F source reliability, 1-6 information credibility, analytic
confidence) over the forensic source-card (OBS-1965) and observation
(OBS-1968) layers. Records are frozen, append-only grade assessments;
every gate (ceiling, corroboration, scope-scale compatibility) is a pure
function. The four scales are four separate vocabularies and nothing
here converts between them (AC-216: E3 never implies A1 and A1 never
implies E3). Grades cite what was inspected (AC-213), and E3
corroboration requires explicit pairwise independence evidence
(AC-215, AC-217). There is deliberately no opportunity/prospect scope:
nothing above SOURCE/REPORT/CLAIM/ASSESSMENT can carry a grade.
Exception text never carries raw rationale bodies (redaction
discipline) -- only stable codes and ledger identifiers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from hashlib import sha256
from typing import Sequence
from uuid import UUID

from .observations import ContradictionState, ObservedClaim
from .source_cards import (
    ArtifactLocatorMode,
    ArtifactLocatorPolicy,
    IndependenceAssessment,
    SourceCard,
    SourceCardFamily,
    SourceIndependenceError,
    SourceReference,
    require_distinct_sources,
)

GRADING_CONTRACT_VERSION = "v1"
RUBRIC_VERSION_LEADGEN_ADMIRALTY_V1 = "leadgen-admiralty/v1"

_HEX64 = re.compile(r"^[0-9a-f]{64}$")

ASSESSMENT_ERROR_CODES = frozenset(
    {
        "grade_invalid_scale_value",
        "grade_scope_scale_mismatch",
        "grade_missing_citation",
        "grade_invalid_withheld_reason",
        "grade_e3_missing_independence_basis",
        "grade_exceeds_ceiling",
        "grade_claim_scope_mismatch",
        "grade_insufficient_independence",
    }
)

WITHHELD_REASON_CODES = frozenset(
    {
        "grade_withheld_unknown_rights",
        "grade_withheld_missing_locator",
        "grade_withheld_unresolved_contradiction",
        "grade_withheld_insufficient_independence",
        "grade_withheld_missing_source",
    }
)

CEILING_REASON_CODES = frozenset(
    {
        "grade_ceiling_missing_source",
        "grade_ceiling_missing_locator",
        "grade_ceiling_unknown_rights",
        "grade_ceiling_unresolved_contradiction",
    }
)

_CAPPING_CONTRADICTION_STATES = frozenset(
    {
        ContradictionState.UNRESOLVED,
        ContradictionState.AMBIGUOUS,
    }
)


# ── Scales ────────────────────────────────────────────────────────────────────


class EvidenceMaturity(StrEnum):
    """LeadGen evidence maturity of a scoped claim (E0-E3)."""

    E0 = "e0"
    E1 = "e1"
    E2 = "e2"
    E3 = "e3"


class SourceReliability(StrEnum):
    """Admiralty source reliability (A-F); F = cannot be judged."""

    A = "a"
    B = "b"
    C = "c"
    D = "d"
    E = "e"
    F = "f"


class InformationCredibility(StrEnum):
    """Admiralty information credibility (1-6); 6 = cannot be judged."""

    ONE = "1"
    TWO = "2"
    THREE = "3"
    FOUR = "4"
    FIVE = "5"
    SIX = "6"


class AnalyticConfidence(StrEnum):
    """Analyst confidence attached to a graded assessment."""

    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"


class GradeKind(StrEnum):
    """Kind of a grade; scope-scale compatibility follows kind."""

    EVIDENCE_MATURITY = "evidence_maturity"
    SOURCE_RELIABILITY = "source_reliability"
    INFORMATION_CREDIBILITY = "information_credibility"
    ANALYTIC_CONFIDENCE = "analytic_confidence"


class GradeScopeKind(StrEnum):
    """Target of a grade; deliberately no opportunity/prospect scope."""

    SOURCE = "source"
    REPORT = "report"
    CLAIM = "claim"
    ASSESSMENT = "assessment"


_MATURITY_RANK = {
    EvidenceMaturity.E0: 0,
    EvidenceMaturity.E1: 1,
    EvidenceMaturity.E2: 2,
    EvidenceMaturity.E3: 3,
}

_SCALE_FOR_KIND = {
    GradeKind.EVIDENCE_MATURITY: EvidenceMaturity,
    GradeKind.SOURCE_RELIABILITY: SourceReliability,
    GradeKind.INFORMATION_CREDIBILITY: InformationCredibility,
    GradeKind.ANALYTIC_CONFIDENCE: AnalyticConfidence,
}

_ALLOWED_SCOPES_FOR_KIND = {
    GradeKind.SOURCE_RELIABILITY: frozenset({GradeScopeKind.SOURCE}),
    GradeKind.EVIDENCE_MATURITY: frozenset({GradeScopeKind.CLAIM}),
    GradeKind.INFORMATION_CREDIBILITY: frozenset(
        {GradeScopeKind.REPORT, GradeScopeKind.CLAIM}
    ),
    GradeKind.ANALYTIC_CONFIDENCE: frozenset({GradeScopeKind.ASSESSMENT}),
}


# ── Errors ────────────────────────────────────────────────────────────────────


class GradeAssessmentError(RuntimeError):
    """A safe, stable grade-assessment failure with a diagnostic code.

    Messages carry codes and ledger identifiers only -- never raw
    rationale bodies.
    """

    def __init__(self, code: str, message: str) -> None:
        if code not in ASSESSMENT_ERROR_CODES:
            raise ValueError(f"unknown grade-assessment code: {code!r}")
        super().__init__(message)
        self.code = code
        self.safe_message = message


# ── Records ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SourceCardVersionRef:
    """A cited source card version."""

    card_id: UUID
    version: int

    def __post_init__(self) -> None:
        _require_uuid(self.card_id, "card_id")
        if not isinstance(self.version, int) or self.version < 1:
            raise ValueError("version must be >= 1")


@dataclass(frozen=True)
class GradeScopeRef:
    """Identity of the graded target, per GradeScopeKind.

    claim_key is REQUIRED iff kind is CLAIM; source_card_version is
    REQUIRED iff kind is SOURCE; otherwise each must be None.
    """

    kind: GradeScopeKind
    target_id: UUID
    claim_key: str | None
    source_card_version: int | None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, GradeScopeKind):
            raise TypeError("kind must be a GradeScopeKind")
        _require_uuid(self.target_id, "target_id")
        if self.kind is GradeScopeKind.CLAIM:
            if self.source_card_version is not None:
                raise ValueError("source_card_version must be None for CLAIM scope")
            _require_hex64(self.claim_key, "claim_key")
        else:
            if self.claim_key is not None:
                raise ValueError("claim_key must be None for non-CLAIM scope")
        if self.kind is GradeScopeKind.SOURCE:
            if (
                not isinstance(self.source_card_version, int)
                or self.source_card_version < 1
            ):
                raise ValueError("source_card_version must be >= 1 for SOURCE scope")
        elif self.source_card_version is not None:
            raise ValueError("source_card_version must be None for non-SOURCE scope")


@dataclass(frozen=True)
class GradeAssessment:
    """An append-only grade record over one graded target.

    Exactly one of grade_value or withheld_reason_code is set; withheld
    records still cite what was inspected (AC-213). Grades supersede only
    grades, mirroring the append-only correction discipline of the
    observation layer. The four scales never merge into a combined score
    (AC-216).
    """

    id: UUID
    grade_key: str
    kind: GradeKind
    scope: GradeScopeRef
    grade_value: str | None
    withheld_reason_code: str | None
    as_of: datetime
    rubric_version: str
    evidence_envelope_ids: frozenset[UUID]
    source_card_refs: frozenset[SourceCardVersionRef]
    independence_assessment_ids: frozenset[UUID]
    rationale: str
    graded_by_subject: str
    recorded_at: datetime
    supersedes_id: UUID | None = None
    supersedes_reason: str | None = None

    def __post_init__(self) -> None:
        _require_uuid(self.id, "id")
        _require_hex64(self.grade_key, "grade_key")
        if not isinstance(self.kind, GradeKind):
            raise TypeError("kind must be a GradeKind")
        if not isinstance(self.scope, GradeScopeRef):
            raise TypeError("scope must be a GradeScopeRef")
        _require_aware(self.as_of, "as_of")
        _require_nonempty_text(self.rubric_version, "rubric_version")
        _require_nonempty_text(self.rationale, "rationale")
        _require_nonempty_text(self.graded_by_subject, "graded_by_subject")
        _require_aware(self.recorded_at, "recorded_at")
        _require_uuid_optional(self.supersedes_id, "supersedes_id")
        if (self.grade_value is None) == (self.withheld_reason_code is None):
            raise ValueError(
                "exactly one of grade_value or withheld_reason_code must be set"
            )
        _require_scope_scale_compatible(self.kind, self.scope.kind)
        if self.grade_value is not None:
            _require_scale_member(self.kind, self.grade_value)
        elif self.withheld_reason_code not in WITHHELD_REASON_CODES:
            raise GradeAssessmentError(
                "grade_invalid_withheld_reason",
                "withheld_reason_code is not a known withheld reason",
            )
        if (
            self.kind is GradeKind.EVIDENCE_MATURITY
            and self.grade_value == EvidenceMaturity.E3.value
            and not self.independence_assessment_ids
        ):
            raise GradeAssessmentError(
                "grade_e3_missing_independence_basis",
                "E3 requires explicit independence assessments (AC-213)",
            )
        _validate_citation_sets(self)
        if self.scope.kind is GradeScopeKind.SOURCE:
            if not any(
                ref.card_id == self.scope.target_id
                and ref.version == self.scope.source_card_version
                for ref in self.source_card_refs
            ):
                raise ValueError(
                    "source-scoped grade must cite the graded source card version"
                )
        _require_supersession(
            self.id, self.supersedes_id, self.supersedes_reason, "grade"
        )
        computed = build_grade_key(
            self.scope.kind, self.scope.target_id, self.kind, self.recorded_at
        )
        if self.grade_key != computed:
            raise ValueError("grade_key does not match recomputation")


# ── Key Builder ───────────────────────────────────────────────────────────────


def build_grade_key(
    scope_kind: GradeScopeKind,
    target_id: UUID,
    kind: GradeKind,
    recorded_at: datetime,
) -> str:
    """Stable content-addressed key for a grade record."""
    if not isinstance(scope_kind, GradeScopeKind):
        raise TypeError("scope_kind must be a GradeScopeKind")
    _require_uuid(target_id, "target_id")
    if not isinstance(kind, GradeKind):
        raise TypeError("kind must be a GradeKind")
    return _hash_parts(
        "obs1970/grade/v1",
        scope_kind.value,
        str(target_id),
        kind.value,
        _utc_iso(recorded_at),
    )


# ── Maturity Rank ─────────────────────────────────────────────────────────────


def maturity_rank(maturity: EvidenceMaturity) -> int:
    """Ordinal rank of an evidence maturity (E0=0 .. E3=3)."""
    if not isinstance(maturity, EvidenceMaturity):
        raise TypeError("maturity must be an EvidenceMaturity")
    return _MATURITY_RANK[maturity]


# ── Ceiling Gate (AC-214) ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class MaturityCeiling:
    """Highest claim maturity currently derivable from the evidence record."""

    ceiling: EvidenceMaturity
    reason_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.ceiling, EvidenceMaturity):
            raise TypeError("ceiling must be an EvidenceMaturity")
        cleaned = tuple(sorted(set(self.reason_codes)))
        object.__setattr__(self, "reason_codes", cleaned)
        if self.ceiling is EvidenceMaturity.E3 and cleaned:
            raise ValueError("an E3 ceiling must have empty reason codes")
        if self.ceiling is not EvidenceMaturity.E3 and not cleaned:
            raise ValueError("a ceiling below E3 must carry at least one reason code")


def derive_maturity_ceiling(
    *,
    source_reference: SourceReference | None,
    locator_policy: ArtifactLocatorPolicy | None,
    rights: frozenset[str] | None,
    contradiction_states: Sequence[ContradictionState],
) -> MaturityCeiling:
    """Derive the highest maturity a claim may carry from evidence-record state.

    The ceiling starts at E3 and caps to E1 as soon as any one of the
    four structural conditions holds: no reconstructable source, no
    reconstructable locator, unknown rights, or an unresolved/ambiguous
    contradiction. RESOLVED_* contradiction states never cap.
    """
    reasons: list[str] = []
    if source_reference is None or source_reference.missing_source_reason is not None:
        reasons.append("grade_ceiling_missing_source")
    if locator_policy is None or locator_policy.mode is ArtifactLocatorMode.OPEN:
        reasons.append("grade_ceiling_missing_locator")
    if rights is None or not rights:
        reasons.append("grade_ceiling_unknown_rights")
    if any(state in _CAPPING_CONTRADICTION_STATES for state in contradiction_states):
        reasons.append("grade_ceiling_unresolved_contradiction")
    if reasons:
        return MaturityCeiling(EvidenceMaturity.E1, tuple(reasons))
    return MaturityCeiling(EvidenceMaturity.E3, ())


def require_maturity_within_ceiling(
    requested: EvidenceMaturity,
    ceiling: MaturityCeiling,
) -> None:
    """Reject a requested maturity above the derived ceiling (AC-214)."""
    if not isinstance(requested, EvidenceMaturity):
        raise TypeError("requested must be an EvidenceMaturity")
    if not isinstance(ceiling, MaturityCeiling):
        raise TypeError("ceiling must be a MaturityCeiling")
    if maturity_rank(requested) > maturity_rank(ceiling.ceiling):
        reasons = ", ".join(ceiling.reason_codes) or "no ceiling reasons recorded"
        raise GradeAssessmentError(
            "grade_exceeds_ceiling",
            f"requested maturity {requested.value} exceeds ceiling "
            f"{ceiling.ceiling.value}: {reasons}",
        )


# ── Corroboration Gate (AC-215, AC-217) ──────────────────────────────────────


@dataclass(frozen=True)
class CorroborationResult:
    """Outcome of the corroboration gate over one scoped claim."""

    claim_key: str
    independent_family_count: int
    family_groups: tuple[frozenset[UUID], ...]
    qualifying_assessment_ids: frozenset[UUID]


def require_corroborated_claim(
    claim_key: str,
    supports: Sequence[tuple[ObservedClaim, SourceCard]],
    memberships: Sequence[SourceCardFamily],
    independence: Sequence[IndependenceAssessment],
    *,
    as_of: datetime,
) -> CorroborationResult:
    """Require >=2 independent source families supporting the scoped claim.

    Independence is established ONLY by explicit pairwise
    IndependenceAssessment evidence validated at as_of (AC-215); absence
    of an assessment is never treated as independence, and UNKNOWN
    relationships stay unknown. Cards sharing any active family merge
    into one group; the count is the largest set of groups that are
    pairwise qualified through such assessments.
    """
    _require_hex64(claim_key, "claim_key")
    _require_aware(as_of, "as_of")

    for claim, _card in supports:
        if claim.claim_key != claim_key:
            raise GradeAssessmentError(
                "grade_claim_scope_mismatch",
                "a supporting claim does not match the graded claim_key",
            )

    # Dedupe supports by card_id, keeping first occurrence order.
    deduped: dict[UUID, tuple[ObservedClaim, SourceCard]] = {}
    for claim, card in supports:
        deduped.setdefault(card.card_id, (claim, card))
    distinct = list(deduped.values())
    cards_by_id = {card.card_id: card for _claim, card in distinct}

    # Union-find over distinct support cards via active family memberships.
    parent = {card.card_id: card.card_id for _claim, card in distinct}

    def _find(card_id: UUID) -> UUID:
        while parent[card_id] != card_id:
            parent[card_id] = parent[parent[card_id]]
            card_id = parent[card_id]
        return card_id

    def _union(left: UUID, right: UUID) -> None:
        root_left, root_right = _find(left), _find(right)
        if root_left != root_right:
            parent[root_right] = root_left

    active = [
        membership
        for membership in memberships
        if membership.joined_at <= as_of
        and (membership.removed_at is None or membership.removed_at > as_of)
    ]
    by_family: dict[UUID, list[UUID]] = {}
    for membership in active:
        if membership.card_id in parent:
            by_family.setdefault(membership.family_id, []).append(membership.card_id)
    for family_cards in by_family.values():
        if len(family_cards) < 2:
            continue
        first = family_cards[0]
        for other in family_cards[1:]:
            _union(first, other)

    groups_by_root: dict[UUID, set[UUID]] = {}
    for card_id in parent:
        groups_by_root.setdefault(_find(card_id), set()).add(card_id)
    groups = [frozenset(cards) for cards in groups_by_root.values()]
    groups.sort(key=lambda group: min(str(card_id) for card_id in group))

    group_count = len(groups)
    if group_count < 2:
        reason = (
            "all supporting sources fall within a single family group"
            if len(distinct) > 1
            else "only a single supporting source was cited"
        )
        raise GradeAssessmentError(
            "grade_insufficient_independence",
            f"fewer than two independent source families: {reason}",
        )

    edge_ok = [[False] * group_count for _ in range(group_count)]
    edge_ids: dict[tuple[int, int], UUID] = {}
    pair_reasons: dict[tuple[int, int], list[str]] = {}
    for index_a in range(group_count):
        for index_b in range(index_a + 1, group_count):
            qualified, assessment_id, reasons = _assess_group_pair(
                groups[index_a], groups[index_b], independence,
                cards_by_id=cards_by_id, as_of=as_of,
            )
            pair_reasons[(index_a, index_b)] = reasons
            if qualified:
                edge_ok[index_a][index_b] = True
                edge_ok[index_b][index_a] = True
                edge_ids[(index_a, index_b)] = assessment_id

    # Maximum clique over group nodes (exact search; tiny group counts).
    best_size = 1
    best_assessment_ids: frozenset[UUID] = frozenset()
    for mask in range(1, 1 << group_count):
        size = mask.bit_count()
        if size <= best_size or not _is_clique(mask, edge_ok, group_count):
            continue
        best_size = size
        ids: set[UUID] = set()
        for index_a in range(group_count):
            if not (mask >> index_a) & 1:
                continue
            for index_b in range(index_a + 1, group_count):
                if (mask >> index_b) & 1 and (index_a, index_b) in edge_ids:
                    ids.add(edge_ids[(index_a, index_b)])
        best_assessment_ids = frozenset(ids)

    if best_size < 2:
        reasons = _collapse_reasons(pair_reasons, group_count)
        raise GradeAssessmentError(
            "grade_insufficient_independence",
            f"fewer than two independent source families: {reasons}",
        )

    return CorroborationResult(
        claim_key=claim_key,
        independent_family_count=best_size,
        family_groups=tuple(groups),
        qualifying_assessment_ids=best_assessment_ids,
    )


# ── Display/Query Rules (AC-216) ─────────────────────────────────────────────


@dataclass(frozen=True)
class GradeSummary:
    """Latest live grade per scale for one scope identity; scales never merge."""

    scope_kind: GradeScopeKind
    target_id: UUID
    maturity: GradeAssessment | None = None
    reliability: GradeAssessment | None = None
    credibility: GradeAssessment | None = None
    confidence: GradeAssessment | None = None
    limitations: tuple[str, ...] = ()


def summarize_grades(
    records: Sequence[GradeAssessment],
    *,
    scope_kind: GradeScopeKind,
    target_id: UUID,
) -> GradeSummary:
    """Pick the latest live grade per kind for one scope identity.

    A record is live unless another record IN THE INPUT supersedes it;
    among live records the latest (recorded_at, id as str) wins. There is
    no combined score: each scale lands in its own field, and withheld
    reason codes of the four selected records form the sorted limitations.
    """
    if not isinstance(scope_kind, GradeScopeKind):
        raise TypeError("scope_kind must be a GradeScopeKind")
    _require_uuid(target_id, "target_id")
    matching = [
        record
        for record in records
        if record.scope.kind is scope_kind and record.scope.target_id == target_id
    ]
    live = [
        record
        for record in matching
        if not any(other.supersedes_id == record.id for other in matching)
    ]

    def latest(kind: GradeKind) -> GradeAssessment | None:
        candidates = [record for record in live if record.kind is kind]
        if not candidates:
            return None
        return max(candidates, key=lambda record: (record.recorded_at, str(record.id)))

    maturity = latest(GradeKind.EVIDENCE_MATURITY)
    reliability = latest(GradeKind.SOURCE_RELIABILITY)
    credibility = latest(GradeKind.INFORMATION_CREDIBILITY)
    confidence = latest(GradeKind.ANALYTIC_CONFIDENCE)
    selected = [
        record
        for record in (maturity, reliability, credibility, confidence)
        if record is not None
    ]
    limitations = tuple(
        sorted(
            {
                record.withheld_reason_code
                for record in selected
                if record.withheld_reason_code is not None
            }
        )
    )
    return GradeSummary(
        scope_kind=scope_kind,
        target_id=target_id,
        maturity=maturity,
        reliability=reliability,
        credibility=credibility,
        confidence=confidence,
        limitations=limitations,
    )


# ── Module-Private Helpers ────────────────────────────────────────────────────


def _require_scope_scale_compatible(kind: GradeKind, scope_kind: GradeScopeKind) -> None:
    if scope_kind not in _ALLOWED_SCOPES_FOR_KIND[kind]:
        raise GradeAssessmentError(
            "grade_scope_scale_mismatch",
            f"{kind.value} grades cannot target {scope_kind.value} scope",
        )


def _require_scale_member(kind: GradeKind, grade_value: str) -> None:
    scale = _SCALE_FOR_KIND[kind]
    try:
        scale(grade_value)
    except (ValueError, TypeError):
        raise GradeAssessmentError(
            "grade_invalid_scale_value",
            f"{grade_value!r} is not a valid value for {kind.value}",
        ) from None


def _validate_citation_sets(grade: GradeAssessment) -> None:
    if not isinstance(grade.evidence_envelope_ids, frozenset):
        raise TypeError("evidence_envelope_ids must be a frozenset[UUID]")
    if not isinstance(grade.source_card_refs, frozenset):
        raise TypeError("source_card_refs must be a frozenset[SourceCardVersionRef]")
    if not isinstance(grade.independence_assessment_ids, frozenset):
        raise TypeError("independence_assessment_ids must be a frozenset[UUID]")
    if not grade.evidence_envelope_ids or not grade.source_card_refs:
        raise GradeAssessmentError(
            "grade_missing_citation",
            "grade must cite at least one evidence envelope and one source card",
        )
    for envelope_id in grade.evidence_envelope_ids:
        if not isinstance(envelope_id, UUID):
            raise TypeError("evidence_envelope_ids must contain only UUIDs")
    for ref in grade.source_card_refs:
        if not isinstance(ref, SourceCardVersionRef):
            raise TypeError("source_card_refs must contain only SourceCardVersionRef")
    for assessment_id in grade.independence_assessment_ids:
        if not isinstance(assessment_id, UUID):
            raise TypeError("independence_assessment_ids must contain only UUIDs")


def _assess_group_pair(
    group_a: frozenset[UUID],
    group_b: frozenset[UUID],
    independence: Sequence[IndependenceAssessment],
    *,
    cards_by_id: dict[UUID, SourceCard],
    as_of: datetime,
) -> tuple[bool, UUID | None, list[str]]:
    """Qualify one cross-group pair via explicit independence evidence.

    Any card pair between the groups qualifies when SOME matching
    assessment validates at as_of; otherwise the failure reasons are
    collected deterministically for the safe_message.
    """
    ordered_a = sorted(group_a, key=str)
    ordered_b = sorted(group_b, key=str)
    reasons: list[str] = []
    seen_reasons: set[str] = set()
    saw_assessment = False

    def record(reason: str) -> None:
        if reason not in seen_reasons:
            seen_reasons.add(reason)
            reasons.append(reason)

    for card_a in ordered_a:
        for card_b in ordered_b:
            for assessment in independence:
                if {assessment.card_a_id, assessment.card_b_id} != {card_a, card_b}:
                    continue
                saw_assessment = True
                if assessment.card_a_id == card_a:
                    left, right = cards_by_id[card_a], cards_by_id[card_b]
                else:
                    left, right = cards_by_id[card_b], cards_by_id[card_a]
                try:
                    require_distinct_sources(
                        left, right, assessment, evaluated_at=as_of
                    )
                except SourceIndependenceError as error:
                    record(error.reason)
                    continue
                return True, assessment.assessment_id, reasons
    if not saw_assessment:
        record("no independence assessment between the source groups")
    return False, None, reasons


def _is_clique(mask: int, edge_ok: list[list[bool]], group_count: int) -> bool:
    members = [index for index in range(group_count) if (mask >> index) & 1]
    for position, index_a in enumerate(members):
        for index_b in members[position + 1 :]:
            if not edge_ok[index_a][index_b]:
                return False
    return True


def _collapse_reasons(
    pair_reasons: dict[tuple[int, int], list[str]], group_count: int
) -> str:
    seen: set[str] = set()
    merged: list[str] = []
    for index_a in range(group_count):
        for index_b in range(index_a + 1, group_count):
            for reason in pair_reasons.get((index_a, index_b), ()):
                if reason not in seen:
                    seen.add(reason)
                    merged.append(reason)
    if merged:
        return "; ".join(merged)
    return "no qualifying cross-group independence assessment"


def _hash_parts(domain: str, *parts: str) -> str:
    return sha256((domain + "\0" + "\0".join(parts)).encode()).hexdigest()


def _utc_iso(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _require_hex64(value: object, name: str) -> None:
    if not isinstance(value, str) or not _HEX64.fullmatch(value):
        raise ValueError(f"{name} must be a 64-char hex string")


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
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise TypeError(f"{name} must be timezone-aware")


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
