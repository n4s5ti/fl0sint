"""B9/OBS-1970 -- Domain-contract tests for the LeadGen/Admiralty grading layer.

Covers the four scale vocabularies and maturity ranking (AC-216), the
GradeScopeRef/GradeAssessment validators, build_grade_key determinism and
domain distinctness, the maturity ceiling gate (AC-214), the corroboration
gate (AC-215/AC-217: same scoped claim, active family merging, explicit
pairwise independence only), and per-scale grade summarization with no
cross-scale conversion anywhere.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from flowsint_core.core.forensics.grading import (
    ASSESSMENT_ERROR_CODES,
    CEILING_REASON_CODES,
    GRADING_CONTRACT_VERSION,
    RUBRIC_VERSION_LEADGEN_ADMIRALTY_V1,
    WITHHELD_REASON_CODES,
    AnalyticConfidence,
    CorroborationResult,
    EvidenceMaturity,
    GradeAssessment,
    GradeAssessmentError,
    GradeKind,
    GradeScopeKind,
    GradeScopeRef,
    GradeSummary,
    InformationCredibility,
    MaturityCeiling,
    SourceCardVersionRef,
    SourceReliability,
    build_grade_key,
    derive_maturity_ceiling,
    maturity_rank,
    require_corroborated_claim,
    require_maturity_within_ceiling,
    summarize_grades,
)
from flowsint_core.core.forensics.observations import ContradictionState, ObservedClaim
from flowsint_core.core.forensics.source_cards import (
    ArtifactLocatorMode,
    ArtifactLocatorPolicy,
    CollectionMethod,
    IndependenceAssessment,
    IndependenceRelationship,
    SourceCard,
    SourceCardFamily,
    SourceReference,
)

NOW = datetime(2026, 8, 9, 12, 0, 0, tzinfo=timezone.utc)
LATER = NOW.replace(minute=1)
FUTURE = NOW + timedelta(days=30)
HEX = frozenset("0123456789abcdef")

SUBJECT = "e" * 64
CLAIM_KEY = "c" * 64
CLAIM_KEY_2 = "d" * 64

CARD_A = UUID("00000000-0000-4000-8000-000000000001")
CARD_B = UUID("00000000-0000-4000-8000-000000000002")
CARD_C = UUID("00000000-0000-4000-8000-000000000003")
CLAIM_ID_A = UUID("00000000-0000-4000-8000-000000000011")
CLAIM_ID_B = UUID("00000000-0000-4000-8000-000000000012")
REPORT_ID = UUID("00000000-0000-4000-8000-000000000021")
ASSESSMENT_ID = UUID("00000000-0000-4000-8000-000000000031")
ENVELOPE_ID = UUID("00000000-0000-4000-8000-0000000000ff")
FAMILY_ID = UUID("00000000-0000-4000-8000-0000000000aa")
FAMILY_2_ID = UUID("00000000-0000-4000-8000-0000000000ab")
GRADE_1_ID = UUID("00000000-0000-4000-8000-0000000000e1")
GRADE_2_ID = UUID("00000000-0000-4000-8000-0000000000e2")

RAW_RATIONALE_PAYLOAD = "RAW_RATIONALE_PAYLOAD_MARKER"

DEFAULT_GRADE_VALUE = {
    GradeKind.EVIDENCE_MATURITY: EvidenceMaturity.E1.value,
    GradeKind.SOURCE_RELIABILITY: SourceReliability.B.value,
    GradeKind.INFORMATION_CREDIBILITY: InformationCredibility.THREE.value,
    GradeKind.ANALYTIC_CONFIDENCE: AnalyticConfidence.MODERATE.value,
}

DEFAULT_SCOPE_KIND = {
    GradeKind.EVIDENCE_MATURITY: GradeScopeKind.CLAIM,
    GradeKind.SOURCE_RELIABILITY: GradeScopeKind.SOURCE,
    GradeKind.INFORMATION_CREDIBILITY: GradeScopeKind.REPORT,
    GradeKind.ANALYTIC_CONFIDENCE: GradeScopeKind.ASSESSMENT,
}


def canonical_json(value: dict) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def digest(value_json: str) -> str:
    return hashlib.sha256(value_json.encode()).hexdigest()


# ── Fixture Factories ─────────────────────────────────────────────────────────


def _source_card(*, card_id: UUID | None = None, version: int = 1) -> SourceCard:
    return SourceCard(
        card_id=card_id or uuid4(),
        version=version,
        card_revision_of=None,
        label="example.com",
        publisher_origin="Example Corp",
        collection_method=CollectionMethod.CONNECTOR,
        jurisdiction=None,
        scope=None,
        rights=frozenset({"permitted"}),
        reliability_context=None,
        artifact_locator_policy=ArtifactLocatorPolicy(
            mode=ArtifactLocatorMode.EXACT, expression="https://example.com",
        ),
        scope_effective_from=NOW,
        scope_effective_until=None,
        created_at=NOW,
        created_by_subject="subject:test-operator",
    )


def _claim(
    *,
    card_id: UUID = CARD_A,
    claim_key: str = CLAIM_KEY,
    id_: UUID | None = None,
) -> ObservedClaim:
    value_json = canonical_json({"owner": "alice"})
    return ObservedClaim(
        id=id_ or uuid4(),
        claim_key=claim_key,
        subject_entity_key=SUBJECT,
        predicate="owns_asset",
        observed_value_json=value_json,
        value_digest=digest(value_json),
        observed_at=NOW,
        evidence_envelope_id=ENVELOPE_ID,
        source_card_id=str(card_id),
        source_card_version=1,
        recorded_at=NOW,
    )


def _membership(
    *,
    card_id: UUID,
    family_id: UUID = FAMILY_ID,
    joined_at: datetime = NOW,
    removed_at: datetime | None = None,
) -> SourceCardFamily:
    return SourceCardFamily(
        card_id=card_id,
        family_id=family_id,
        family_version=1,
        joined_at=joined_at,
        removed_at=removed_at,
    )


def _independence(
    card_a: UUID,
    card_b: UUID,
    *,
    relationship: IndependenceRelationship = IndependenceRelationship.INDEPENDENT,
    effective_from: datetime = NOW,
    effective_until: datetime | None = None,
) -> IndependenceAssessment:
    refs = (
        frozenset({ENVELOPE_ID})
        if relationship is not IndependenceRelationship.UNKNOWN
        else frozenset()
    )
    return IndependenceAssessment(
        assessment_id=uuid4(),
        card_a_id=card_a,
        card_b_id=card_b,
        relationship=relationship,
        evidence_rationale="distinct domains, different registrars",
        evidence_references=refs,
        effective_from=effective_from,
        effective_until=effective_until,
        assessed_by_subject="subject:test-analyst",
        assessed_at=NOW,
    )


def _source_reference(*, with_card: bool = True) -> SourceReference:
    if with_card:
        return SourceReference(
            envelope_id=ENVELOPE_ID,
            source_card_id=CARD_A,
            source_card_version=1,
            missing_source_reason=None,
            referenced_at=NOW,
            referenced_by_subject="subject:test-operator",
        )
    return SourceReference(
        envelope_id=ENVELOPE_ID,
        source_card_id=None,
        source_card_version=None,
        missing_source_reason="no source card available for this connector",
        referenced_at=NOW,
        referenced_by_subject="subject:test-operator",
    )


def _scope(
    *,
    kind: GradeScopeKind,
    target_id: UUID,
    claim_key: str | None = None,
    source_card_version: int | None = None,
) -> GradeScopeRef:
    return GradeScopeRef(
        kind=kind,
        target_id=target_id,
        claim_key=claim_key,
        source_card_version=source_card_version,
    )


def _grade(
    *,
    id_: UUID | None = None,
    kind: GradeKind = GradeKind.EVIDENCE_MATURITY,
    scope: GradeScopeRef | None = None,
    grade_value: str | None = None,
    withheld_reason_code: str | None = None,
    as_of: datetime = NOW,
    rubric_version: str = RUBRIC_VERSION_LEADGEN_ADMIRALTY_V1,
    evidence_envelope_ids: frozenset[UUID] | None = None,
    source_card_refs: frozenset[SourceCardVersionRef] | None = None,
    independence_assessment_ids: frozenset[UUID] = frozenset(),
    rationale: str = "graded per contract rubric",
    graded_by_subject: str = "subject:test-grader",
    recorded_at: datetime = NOW,
    supersedes_id: UUID | None = None,
    supersedes_reason: str | None = None,
) -> GradeAssessment:
    if scope is None:
        scope_kind = DEFAULT_SCOPE_KIND[kind]
        if scope_kind is GradeScopeKind.SOURCE:
            scope = _scope(kind=scope_kind, target_id=CARD_A, source_card_version=1)
        elif scope_kind is GradeScopeKind.CLAIM:
            scope = _scope(kind=scope_kind, target_id=CLAIM_ID_A, claim_key=CLAIM_KEY)
        else:
            scope = _scope(kind=scope_kind, target_id=uuid4())
    if grade_value is None and withheld_reason_code is None:
        grade_value = DEFAULT_GRADE_VALUE[kind]
    if evidence_envelope_ids is None:
        evidence_envelope_ids = frozenset({ENVELOPE_ID})
    if source_card_refs is None:
        if scope.kind is GradeScopeKind.SOURCE:
            source_card_refs = frozenset(
                {
                    SourceCardVersionRef(
                        card_id=scope.target_id, version=scope.source_card_version
                    )
                }
            )
        else:
            source_card_refs = frozenset({SourceCardVersionRef(card_id=CARD_A, version=1)})
    return GradeAssessment(
        id=id_ or uuid4(),
        grade_key=build_grade_key(scope.kind, scope.target_id, kind, recorded_at),
        kind=kind,
        scope=scope,
        grade_value=grade_value,
        withheld_reason_code=withheld_reason_code,
        as_of=as_of,
        rubric_version=rubric_version,
        evidence_envelope_ids=evidence_envelope_ids,
        source_card_refs=source_card_refs,
        independence_assessment_ids=independence_assessment_ids,
        rationale=rationale,
        graded_by_subject=graded_by_subject,
        recorded_at=recorded_at,
        supersedes_id=supersedes_id,
        supersedes_reason=supersedes_reason,
    )


# ── enum contract ───────────────────────────────────────────────────────────


def test_evidence_maturity_values_and_rank_ordering() -> None:
    assert {member.value for member in EvidenceMaturity} == {"e0", "e1", "e2", "e3"}
    ranks = [maturity_rank(member) for member in EvidenceMaturity]
    assert ranks == [0, 1, 2, 3]
    assert maturity_rank(EvidenceMaturity.E0) == 0
    assert maturity_rank(EvidenceMaturity.E3) == 3
    assert maturity_rank(EvidenceMaturity.E2) > maturity_rank(EvidenceMaturity.E1)


def test_maturity_rank_rejects_non_member() -> None:
    with pytest.raises(TypeError, match="EvidenceMaturity"):
        maturity_rank("e1")  # type: ignore[arg-type]


def test_source_reliability_values_are_stable_contract_strings() -> None:
    assert {member.value for member in SourceReliability} == {
        "a", "b", "c", "d", "e", "f",
    }


def test_information_credibility_values_are_stable_contract_strings() -> None:
    assert {member.value for member in InformationCredibility} == {
        "1", "2", "3", "4", "5", "6",
    }


def test_analytic_confidence_values_are_stable_contract_strings() -> None:
    assert {member.value for member in AnalyticConfidence} == {
        "low", "moderate", "high",
    }


def test_grade_kind_values_are_stable_contract_strings() -> None:
    assert {member.value for member in GradeKind} == {
        "evidence_maturity",
        "source_reliability",
        "information_credibility",
        "analytic_confidence",
    }


def test_grade_scope_kind_values_are_stable_contract_strings() -> None:
    assert {member.value for member in GradeScopeKind} == {
        "source", "report", "claim", "assessment",
    }


def test_contract_version_constants_are_stable() -> None:
    assert GRADING_CONTRACT_VERSION == "v1"
    assert RUBRIC_VERSION_LEADGEN_ADMIRALTY_V1 == "leadgen-admiralty/v1"
    assert WITHHELD_REASON_CODES == frozenset(
        {
            "grade_withheld_unknown_rights",
            "grade_withheld_missing_locator",
            "grade_withheld_unresolved_contradiction",
            "grade_withheld_insufficient_independence",
            "grade_withheld_missing_source",
        }
    )
    assert CEILING_REASON_CODES == frozenset(
        {
            "grade_ceiling_missing_source",
            "grade_ceiling_missing_locator",
            "grade_ceiling_unknown_rights",
            "grade_ceiling_unresolved_contradiction",
        }
    )


# ── GradeScopeRef validation ─────────────────────────────────────────────────


def test_claim_scope_requires_claim_key() -> None:
    with pytest.raises(ValueError, match="claim_key"):
        _scope(kind=GradeScopeKind.CLAIM, target_id=CLAIM_ID_A)
    with pytest.raises(ValueError, match="64-char hex"):
        _scope(kind=GradeScopeKind.CLAIM, target_id=CLAIM_ID_A, claim_key="z" * 64)
    with pytest.raises(ValueError, match="64-char hex"):
        _scope(kind=GradeScopeKind.CLAIM, target_id=CLAIM_ID_A, claim_key="a" * 63)


def test_claim_scope_rejects_source_card_version() -> None:
    with pytest.raises(ValueError, match="source_card_version must be None for CLAIM"):
        _scope(
            kind=GradeScopeKind.CLAIM,
            target_id=CLAIM_ID_A,
            claim_key=CLAIM_KEY,
            source_card_version=1,
        )


def test_source_scope_requires_source_card_version() -> None:
    with pytest.raises(ValueError, match="source_card_version must be >= 1"):
        _scope(kind=GradeScopeKind.SOURCE, target_id=CARD_A)
    with pytest.raises(ValueError, match="source_card_version must be >= 1"):
        _scope(kind=GradeScopeKind.SOURCE, target_id=CARD_A, source_card_version=0)


def test_source_scope_rejects_claim_key() -> None:
    with pytest.raises(ValueError, match="claim_key must be None for non-CLAIM"):
        _scope(
            kind=GradeScopeKind.SOURCE,
            target_id=CARD_A,
            source_card_version=1,
            claim_key=CLAIM_KEY,
        )


@pytest.mark.parametrize("kind", [GradeScopeKind.REPORT, GradeScopeKind.ASSESSMENT])
def test_report_and_assessment_scopes_reject_both_optional_fields(kind: GradeScopeKind) -> None:
    with pytest.raises(ValueError, match="claim_key must be None for non-CLAIM"):
        _scope(kind=kind, target_id=uuid4(), claim_key=CLAIM_KEY)
    with pytest.raises(ValueError, match="source_card_version must be None for non-SOURCE"):
        _scope(kind=kind, target_id=uuid4(), source_card_version=1)


def test_scope_rejects_non_uuid_target_and_non_kind() -> None:
    with pytest.raises(TypeError, match="target_id"):
        _scope(kind=GradeScopeKind.REPORT, target_id="not-a-uuid")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="kind"):
        GradeScopeRef(  # type: ignore[arg-type]
            kind="claim", target_id=CLAIM_ID_A, claim_key=CLAIM_KEY,
            source_card_version=None,
        )


def test_valid_scopes_are_constructed() -> None:
    source = _scope(kind=GradeScopeKind.SOURCE, target_id=CARD_A, source_card_version=2)
    claim = _scope(kind=GradeScopeKind.CLAIM, target_id=CLAIM_ID_A, claim_key=CLAIM_KEY)
    report = _scope(kind=GradeScopeKind.REPORT, target_id=REPORT_ID)
    assessment = _scope(kind=GradeScopeKind.ASSESSMENT, target_id=ASSESSMENT_ID)
    assert (source.kind, source.source_card_version) == (GradeScopeKind.SOURCE, 2)
    assert (claim.kind, claim.claim_key) == (GradeScopeKind.CLAIM, CLAIM_KEY)
    assert (report.kind, report.claim_key, report.source_card_version) == (
        GradeScopeKind.REPORT, None, None,
    )
    assert assessment.kind is GradeScopeKind.ASSESSMENT


def test_source_card_version_ref_validation() -> None:
    ref = SourceCardVersionRef(card_id=CARD_A, version=1)
    assert ref.version == 1
    with pytest.raises(ValueError, match="version must be >= 1"):
        SourceCardVersionRef(card_id=CARD_A, version=0)
    with pytest.raises(TypeError, match="card_id"):
        SourceCardVersionRef(card_id="x", version=1)  # type: ignore[arg-type]


# ── build_grade_key ──────────────────────────────────────────────────────────


def test_grade_key_is_deterministic_hex64() -> None:
    kwargs = {
        "scope_kind": GradeScopeKind.CLAIM,
        "target_id": CLAIM_ID_A,
        "kind": GradeKind.EVIDENCE_MATURITY,
        "recorded_at": NOW,
    }
    key = build_grade_key(**kwargs)
    assert key == build_grade_key(**kwargs)
    assert len(key) == 64
    assert all(char in HEX for char in key)


@pytest.mark.parametrize(
    "change",
    [
        {"scope_kind": GradeScopeKind.REPORT},
        {"target_id": CLAIM_ID_B},
        {"kind": GradeKind.INFORMATION_CREDIBILITY},
        {"recorded_at": NOW.replace(minute=1)},
    ],
)
def test_grade_key_is_sensitive_to_every_part(change: dict) -> None:
    base = {
        "scope_kind": GradeScopeKind.CLAIM,
        "target_id": CLAIM_ID_A,
        "kind": GradeKind.EVIDENCE_MATURITY,
        "recorded_at": NOW,
    }
    assert build_grade_key(**base) != build_grade_key(**{**base, **change})


def test_grade_key_domain_is_distinct() -> None:
    scope_kind = GradeScopeKind.CLAIM
    target_id = CLAIM_ID_A
    kind = GradeKind.EVIDENCE_MATURITY
    key = build_grade_key(scope_kind, target_id, kind, NOW)
    other_domain = hashlib.sha256(
        (
            "obs1970/grade/v2" + "\0" + "\0".join(
                [
                    scope_kind.value,
                    str(target_id),
                    kind.value,
                    NOW.astimezone(timezone.utc).isoformat(),
                ]
            )
        ).encode()
    ).hexdigest()
    assert key != other_domain


# ── GradeAssessment validation ───────────────────────────────────────────────


def test_valid_grade_records_for_every_kind_at_allowed_scopes() -> None:
    maturity = _grade(kind=GradeKind.EVIDENCE_MATURITY, grade_value="e2")
    reliability = _grade(kind=GradeKind.SOURCE_RELIABILITY, grade_value="b")
    credibility_report = _grade(kind=GradeKind.INFORMATION_CREDIBILITY, grade_value="3")
    credibility_claim = _grade(
        kind=GradeKind.INFORMATION_CREDIBILITY,
        scope=_scope(kind=GradeScopeKind.CLAIM, target_id=CLAIM_ID_A, claim_key=CLAIM_KEY),
        grade_value="4",
    )
    confidence = _grade(kind=GradeKind.ANALYTIC_CONFIDENCE, grade_value="high")
    assert maturity.grade_value == "e2"
    assert reliability.grade_value == "b"
    assert credibility_report.grade_value == "3"
    assert credibility_claim.grade_value == "4"
    assert confidence.grade_value == "high"


def test_e3_grade_is_valid_with_independence_basis() -> None:
    assessment_id = uuid4()
    grade = _grade(
        kind=GradeKind.EVIDENCE_MATURITY,
        grade_value="e3",
        independence_assessment_ids=frozenset({assessment_id}),
    )
    assert grade.independence_assessment_ids == frozenset({assessment_id})


def test_withheld_grade_is_valid_with_citations() -> None:
    grade = _grade(withheld_reason_code="grade_withheld_unknown_rights")
    assert grade.grade_value is None
    assert grade.withheld_reason_code == "grade_withheld_unknown_rights"


def test_grade_value_withheld_exclusivity() -> None:
    base = _grade()
    with pytest.raises(ValueError, match="exactly one"):
        replace(base, grade_value=None, withheld_reason_code=None)
    with pytest.raises(ValueError, match="exactly one"):
        replace(
            base,
            grade_value="e1",
            withheld_reason_code="grade_withheld_missing_source",
        )


@pytest.mark.parametrize(
    "kind",
    list(GradeKind),
)
def test_invalid_scale_value_per_kind(kind: GradeKind) -> None:
    with pytest.raises(GradeAssessmentError) as exc:
        _grade(kind=kind, grade_value="bogus")
    assert exc.value.code == "grade_invalid_scale_value"


def test_wrong_scale_value_is_rejected() -> None:
    with pytest.raises(GradeAssessmentError) as exc:
        _grade(kind=GradeKind.ANALYTIC_CONFIDENCE, grade_value="e1")
    assert exc.value.code == "grade_invalid_scale_value"
    with pytest.raises(GradeAssessmentError) as exc:
        _grade(kind=GradeKind.EVIDENCE_MATURITY, grade_value=42)  # type: ignore[arg-type]
    assert exc.value.code == "grade_invalid_scale_value"


def test_invalid_withheld_reason_code_is_rejected() -> None:
    with pytest.raises(GradeAssessmentError) as exc:
        _grade(withheld_reason_code="not_a_known_reason")
    assert exc.value.code == "grade_invalid_withheld_reason"


@pytest.mark.parametrize(
    ("kind", "scope_kind"),
    [
        (GradeKind.SOURCE_RELIABILITY, GradeScopeKind.REPORT),
        (GradeKind.SOURCE_RELIABILITY, GradeScopeKind.CLAIM),
        (GradeKind.SOURCE_RELIABILITY, GradeScopeKind.ASSESSMENT),
        (GradeKind.EVIDENCE_MATURITY, GradeScopeKind.SOURCE),
        (GradeKind.EVIDENCE_MATURITY, GradeScopeKind.REPORT),
        (GradeKind.EVIDENCE_MATURITY, GradeScopeKind.ASSESSMENT),
        (GradeKind.INFORMATION_CREDIBILITY, GradeScopeKind.SOURCE),
        (GradeKind.INFORMATION_CREDIBILITY, GradeScopeKind.ASSESSMENT),
        (GradeKind.ANALYTIC_CONFIDENCE, GradeScopeKind.SOURCE),
        (GradeKind.ANALYTIC_CONFIDENCE, GradeScopeKind.REPORT),
        (GradeKind.ANALYTIC_CONFIDENCE, GradeScopeKind.CLAIM),
    ],
)
def test_scope_scale_mismatch_for_every_forbidden_pairing(
    kind: GradeKind, scope_kind: GradeScopeKind
) -> None:
    if scope_kind is GradeScopeKind.SOURCE:
        scope = _scope(kind=scope_kind, target_id=CARD_A, source_card_version=1)
    elif scope_kind is GradeScopeKind.CLAIM:
        scope = _scope(kind=scope_kind, target_id=CLAIM_ID_A, claim_key=CLAIM_KEY)
    else:
        scope = _scope(kind=scope_kind, target_id=uuid4())
    with pytest.raises(GradeAssessmentError) as exc:
        _grade(kind=kind, scope=scope)
    assert exc.value.code == "grade_scope_scale_mismatch"


def test_empty_citations_rejected_for_both_sets() -> None:
    with pytest.raises(GradeAssessmentError) as exc:
        replace(_grade(), evidence_envelope_ids=frozenset())
    assert exc.value.code == "grade_missing_citation"
    with pytest.raises(GradeAssessmentError) as exc:
        replace(_grade(), source_card_refs=frozenset())
    assert exc.value.code == "grade_missing_citation"


def test_e3_without_independence_basis_is_rejected() -> None:
    with pytest.raises(GradeAssessmentError) as exc:
        _grade(kind=GradeKind.EVIDENCE_MATURITY, grade_value="e3")
    assert exc.value.code == "grade_e3_missing_independence_basis"


def test_grade_key_recomputation_mismatch_is_rejected() -> None:
    with pytest.raises(ValueError, match="does not match"):
        replace(_grade(), grade_key="a" * 64)
    with pytest.raises(ValueError, match="64-char hex"):
        replace(_grade(), grade_key="z" * 64)


def test_source_scoped_grade_must_cite_graded_source_version() -> None:
    base = _grade(kind=GradeKind.SOURCE_RELIABILITY)
    with pytest.raises(ValueError, match="must cite the graded source card"):
        replace(
            base,
            source_card_refs=frozenset({SourceCardVersionRef(card_id=CARD_B, version=1)}),
        )


def test_grade_rejects_naive_datetimes() -> None:
    with pytest.raises(TypeError, match="timezone-aware"):
        replace(_grade(), as_of=datetime(2026, 8, 9, 12, 0, 0))
    with pytest.raises(TypeError, match="timezone-aware"):
        replace(_grade(), recorded_at=datetime(2026, 8, 9, 12, 0, 0))


def test_grade_supersession_rules() -> None:
    with pytest.raises(ValueError, match="cannot supersede itself"):
        _grade(id_=GRADE_1_ID, supersedes_id=GRADE_1_ID, supersedes_reason="oops")
    with pytest.raises(ValueError, match="supersedes_reason is required"):
        _grade(supersedes_id=GRADE_1_ID)
    with pytest.raises(ValueError, match="meaningless"):
        _grade(supersedes_reason="corrected")
    correction = _grade(supersedes_id=GRADE_1_ID, supersedes_reason="corrected")
    assert correction.supersedes_id == GRADE_1_ID
    assert correction.supersedes_reason == "corrected"


def test_grade_rejects_empty_rationale_and_rubric() -> None:
    with pytest.raises(ValueError, match="rationale"):
        replace(_grade(), rationale="  ")
    with pytest.raises(ValueError, match="rubric_version"):
        replace(_grade(), rubric_version="")


def test_error_class_carries_stable_code_and_safe_message() -> None:
    error = GradeAssessmentError("grade_exceeds_ceiling", "above ceiling")
    assert error.code == "grade_exceeds_ceiling"
    assert error.safe_message == "above ceiling"
    assert isinstance(error, RuntimeError)


def test_domain_errors_never_embed_raw_rationale() -> None:
    with pytest.raises(GradeAssessmentError) as exc:
        _grade(
            kind=GradeKind.EVIDENCE_MATURITY,
            grade_value="bogus",
            rationale=RAW_RATIONALE_PAYLOAD,
        )
    assert RAW_RATIONALE_PAYLOAD not in str(exc.value)
    with pytest.raises(ValueError) as exc:
        replace(_grade(rationale=RAW_RATIONALE_PAYLOAD), grade_key="a" * 64)
    assert RAW_RATIONALE_PAYLOAD not in str(exc.value)


# ── derive_maturity_ceiling (AC-214) ─────────────────────────────────────────


def test_clean_record_derives_e3_ceiling() -> None:
    ceiling = derive_maturity_ceiling(
        source_reference=_source_reference(),
        locator_policy=ArtifactLocatorPolicy(
            mode=ArtifactLocatorMode.EXACT, expression="https://example.com"
        ),
        rights=frozenset({"permitted"}),
        contradiction_states=[],
    )
    assert ceiling.ceiling is EvidenceMaturity.E3
    assert ceiling.reason_codes == ()


def test_missing_source_reference_caps_to_e1() -> None:
    for source_reference in (None, _source_reference(with_card=False)):
        ceiling = derive_maturity_ceiling(
            source_reference=source_reference,
            locator_policy=ArtifactLocatorPolicy(
                mode=ArtifactLocatorMode.EXACT, expression="https://example.com"
            ),
            rights=frozenset({"permitted"}),
            contradiction_states=[],
        )
        assert ceiling.ceiling is EvidenceMaturity.E1
        assert ceiling.reason_codes == ("grade_ceiling_missing_source",)


def test_missing_locator_policy_caps_to_e1() -> None:
    for locator_policy in (
        None,
        ArtifactLocatorPolicy(mode=ArtifactLocatorMode.OPEN, expression=None),
    ):
        ceiling = derive_maturity_ceiling(
            source_reference=_source_reference(),
            locator_policy=locator_policy,
            rights=frozenset({"permitted"}),
            contradiction_states=[],
        )
        assert ceiling.ceiling is EvidenceMaturity.E1
        assert ceiling.reason_codes == ("grade_ceiling_missing_locator",)


@pytest.mark.parametrize(
    "mode",
    [ArtifactLocatorMode.EXACT, ArtifactLocatorMode.PREFIX, ArtifactLocatorMode.REGEX],
)
def test_reconstructable_locator_modes_never_cap(mode: ArtifactLocatorMode) -> None:
    ceiling = derive_maturity_ceiling(
        source_reference=_source_reference(),
        locator_policy=ArtifactLocatorPolicy(
            mode=mode, expression="https://example.com/path"
        ),
        rights=frozenset({"permitted"}),
        contradiction_states=[],
    )
    assert ceiling.ceiling is EvidenceMaturity.E3
    assert ceiling.reason_codes == ()


def test_missing_or_empty_rights_caps_to_e1() -> None:
    for rights in (None, frozenset()):
        ceiling = derive_maturity_ceiling(
            source_reference=_source_reference(),
            locator_policy=ArtifactLocatorPolicy(
                mode=ArtifactLocatorMode.EXACT, expression="https://example.com"
            ),
            rights=rights,
            contradiction_states=[],
        )
        assert ceiling.ceiling is EvidenceMaturity.E1
        assert ceiling.reason_codes == ("grade_ceiling_unknown_rights",)


@pytest.mark.parametrize(
    "state",
    [ContradictionState.UNRESOLVED, ContradictionState.AMBIGUOUS],
)
def test_unresolved_and_ambiguous_contradictions_cap(state: ContradictionState) -> None:
    ceiling = derive_maturity_ceiling(
        source_reference=_source_reference(),
        locator_policy=ArtifactLocatorPolicy(
            mode=ArtifactLocatorMode.EXACT, expression="https://example.com"
        ),
        rights=frozenset({"permitted"}),
        contradiction_states=[state],
    )
    assert ceiling.ceiling is EvidenceMaturity.E1
    assert ceiling.reason_codes == ("grade_ceiling_unresolved_contradiction",)


@pytest.mark.parametrize(
    "state",
    [ContradictionState.RESOLVED_COMPATIBLE, ContradictionState.RESOLVED_UPHELD],
)
def test_resolved_contradiction_states_never_cap(state: ContradictionState) -> None:
    ceiling = derive_maturity_ceiling(
        source_reference=_source_reference(),
        locator_policy=ArtifactLocatorPolicy(
            mode=ArtifactLocatorMode.EXACT, expression="https://example.com"
        ),
        rights=frozenset({"permitted"}),
        contradiction_states=[state],
    )
    assert ceiling.ceiling is EvidenceMaturity.E3
    assert ceiling.reason_codes == ()


def test_combined_conditions_produce_sorted_deduped_reasons() -> None:
    ceiling = derive_maturity_ceiling(
        source_reference=None,
        locator_policy=ArtifactLocatorPolicy(
            mode=ArtifactLocatorMode.OPEN, expression=None
        ),
        rights=frozenset(),
        contradiction_states=[
            ContradictionState.UNRESOLVED,
            ContradictionState.AMBIGUOUS,
        ],
    )
    assert ceiling.ceiling is EvidenceMaturity.E1
    assert ceiling.reason_codes == (
        "grade_ceiling_missing_locator",
        "grade_ceiling_missing_source",
        "grade_ceiling_unknown_rights",
        "grade_ceiling_unresolved_contradiction",
    )


def test_ceiling_constructor_normalizes_reason_codes() -> None:
    normalized = MaturityCeiling(EvidenceMaturity.E1, ("b", "a", "a"))
    assert normalized.reason_codes == ("a", "b")
    with pytest.raises(ValueError, match="empty reason codes"):
        MaturityCeiling(EvidenceMaturity.E3, ("grade_ceiling_missing_source",))
    with pytest.raises(ValueError, match="at least one reason code"):
        MaturityCeiling(EvidenceMaturity.E1, ())


# ── require_maturity_within_ceiling (AC-214) ─────────────────────────────────


def test_equal_or_lower_rank_passes() -> None:
    ceiling_e1 = MaturityCeiling(EvidenceMaturity.E1, ("grade_ceiling_missing_source",))
    require_maturity_within_ceiling(EvidenceMaturity.E1, ceiling_e1)
    require_maturity_within_ceiling(EvidenceMaturity.E0, ceiling_e1)
    ceiling_e3 = MaturityCeiling(EvidenceMaturity.E3, ())
    require_maturity_within_ceiling(EvidenceMaturity.E3, ceiling_e3)
    require_maturity_within_ceiling(EvidenceMaturity.E0, ceiling_e3)


def test_above_ceiling_fails_with_code_and_reasons() -> None:
    ceiling = MaturityCeiling(
        EvidenceMaturity.E1, ("grade_ceiling_missing_source",)
    )
    with pytest.raises(GradeAssessmentError) as exc:
        require_maturity_within_ceiling(EvidenceMaturity.E2, ceiling)
    assert exc.value.code == "grade_exceeds_ceiling"
    assert "grade_ceiling_missing_source" in exc.value.safe_message
    with pytest.raises(GradeAssessmentError) as exc:
        require_maturity_within_ceiling(EvidenceMaturity.E3, ceiling)
    assert exc.value.code == "grade_exceeds_ceiling"


# ── require_corroborated_claim (AC-215, AC-217) ─────────────────────────────


def test_two_independent_singletons_qualify() -> None:
    card_a, card_b = _source_card(card_id=CARD_A), _source_card(card_id=CARD_B)
    claim_a, claim_b = _claim(card_id=CARD_A), _claim(card_id=CARD_B)
    assessment = _independence(CARD_A, CARD_B)
    result = require_corroborated_claim(
        CLAIM_KEY,
        [(claim_a, card_a), (claim_b, card_b)],
        [],
        [assessment],
        as_of=NOW,
    )
    assert isinstance(result, CorroborationResult)
    assert result.claim_key == CLAIM_KEY
    assert result.independent_family_count == 2
    assert result.family_groups == (frozenset({CARD_A}), frozenset({CARD_B}))
    assert result.qualifying_assessment_ids == frozenset({assessment.assessment_id})


def test_same_family_collapses_to_insufficient_independence() -> None:
    card_a, card_b = _source_card(card_id=CARD_A), _source_card(card_id=CARD_B)
    claim_a, claim_b = _claim(card_id=CARD_A), _claim(card_id=CARD_B)
    assessment = _independence(CARD_A, CARD_B)
    memberships = [
        _membership(card_id=CARD_A, family_id=FAMILY_ID),
        _membership(card_id=CARD_B, family_id=FAMILY_ID),
    ]
    with pytest.raises(GradeAssessmentError) as exc:
        require_corroborated_claim(
            CLAIM_KEY,
            [(claim_a, card_a), (claim_b, card_b)],
            memberships,
            [assessment],
            as_of=NOW,
        )
    assert exc.value.code == "grade_insufficient_independence"
    assert "single family" in exc.value.safe_message


def test_shared_family_merges_transitively_across_two_families() -> None:
    card_a, card_b, card_c = (
        _source_card(card_id=CARD_A),
        _source_card(card_id=CARD_B),
        _source_card(card_id=CARD_C),
    )
    claims = [_claim(card_id=CARD_A), _claim(card_id=CARD_B), _claim(card_id=CARD_C)]
    memberships = [
        _membership(card_id=CARD_A, family_id=FAMILY_ID),
        _membership(card_id=CARD_B, family_id=FAMILY_ID),
        _membership(card_id=CARD_B, family_id=FAMILY_2_ID),
        _membership(card_id=CARD_C, family_id=FAMILY_2_ID),
    ]
    with pytest.raises(GradeAssessmentError) as exc:
        require_corroborated_claim(
            CLAIM_KEY,
            list(zip(claims, [card_a, card_b, card_c])),
            memberships,
            [],
            as_of=NOW,
        )
    assert exc.value.code == "grade_insufficient_independence"


def test_family_merge_with_singleton_group_qualifies() -> None:
    card_a, card_b, card_c = (
        _source_card(card_id=CARD_A),
        _source_card(card_id=CARD_B),
        _source_card(card_id=CARD_C),
    )
    claims = [_claim(card_id=CARD_A), _claim(card_id=CARD_B), _claim(card_id=CARD_C)]
    memberships = [
        _membership(card_id=CARD_A, family_id=FAMILY_ID),
        _membership(card_id=CARD_B, family_id=FAMILY_ID),
    ]
    assessment = _independence(CARD_A, CARD_C)
    result = require_corroborated_claim(
        CLAIM_KEY,
        list(zip(claims, [card_a, card_b, card_c])),
        memberships,
        [assessment],
        as_of=NOW,
    )
    assert result.independent_family_count == 2
    assert result.family_groups == (
        frozenset({CARD_A, CARD_B}),
        frozenset({CARD_C}),
    )
    assert result.qualifying_assessment_ids == frozenset({assessment.assessment_id})


@pytest.mark.parametrize(
    "relationship",
    [
        IndependenceRelationship.UNKNOWN,
        IndependenceRelationship.RELATED,
        IndependenceRelationship.COMMON_UPSTREAM,
    ],
)
def test_non_independent_relationships_fail(relationship: IndependenceRelationship) -> None:
    card_a, card_b = _source_card(card_id=CARD_A), _source_card(card_id=CARD_B)
    claims = [_claim(card_id=CARD_A), _claim(card_id=CARD_B)]
    assessment = _independence(CARD_A, CARD_B, relationship=relationship)
    with pytest.raises(GradeAssessmentError) as exc:
        require_corroborated_claim(
            CLAIM_KEY, list(zip(claims, [card_a, card_b])), [], [assessment],
            as_of=NOW,
        )
    assert exc.value.code == "grade_insufficient_independence"
    assert relationship.name in exc.value.safe_message


def test_expired_assessment_fails() -> None:
    card_a, card_b = _source_card(card_id=CARD_A), _source_card(card_id=CARD_B)
    claims = [_claim(card_id=CARD_A), _claim(card_id=CARD_B)]
    assessment = _independence(
        CARD_A,
        CARD_B,
        effective_from=NOW - timedelta(days=30),
        effective_until=NOW - timedelta(days=1),
    )
    with pytest.raises(GradeAssessmentError) as exc:
        require_corroborated_claim(
            CLAIM_KEY, list(zip(claims, [card_a, card_b])), [], [assessment],
            as_of=NOW,
        )
    assert exc.value.code == "grade_insufficient_independence"
    assert "expired" in exc.value.safe_message


def test_not_yet_effective_assessment_fails() -> None:
    card_a, card_b = _source_card(card_id=CARD_A), _source_card(card_id=CARD_B)
    claims = [_claim(card_id=CARD_A), _claim(card_id=CARD_B)]
    assessment = _independence(CARD_A, CARD_B, effective_from=FUTURE)
    with pytest.raises(GradeAssessmentError) as exc:
        require_corroborated_claim(
            CLAIM_KEY, list(zip(claims, [card_a, card_b])), [], [assessment],
            as_of=NOW,
        )
    assert exc.value.code == "grade_insufficient_independence"
    assert "not yet effective" in exc.value.safe_message


def test_missing_assessment_between_groups_fails() -> None:
    card_a, card_b = _source_card(card_id=CARD_A), _source_card(card_id=CARD_B)
    claims = [_claim(card_id=CARD_A), _claim(card_id=CARD_B)]
    with pytest.raises(GradeAssessmentError) as exc:
        require_corroborated_claim(
            CLAIM_KEY, list(zip(claims, [card_a, card_b])), [], [], as_of=NOW
        )
    assert exc.value.code == "grade_insufficient_independence"
    assert "no independence assessment" in exc.value.safe_message


def test_wrong_claim_key_raises_claim_scope_mismatch() -> None:
    card_a, card_b = _source_card(card_id=CARD_A), _source_card(card_id=CARD_B)
    claims = [_claim(card_id=CARD_A, claim_key=CLAIM_KEY_2), _claim(card_id=CARD_B)]
    with pytest.raises(GradeAssessmentError) as exc:
        require_corroborated_claim(
            CLAIM_KEY, list(zip(claims, [card_a, card_b])), [], [], as_of=NOW
        )
    assert exc.value.code == "grade_claim_scope_mismatch"


def test_membership_removed_before_as_of_does_not_merge() -> None:
    card_a, card_b = _source_card(card_id=CARD_A), _source_card(card_id=CARD_B)
    claims = [_claim(card_id=CARD_A), _claim(card_id=CARD_B)]
    assessment = _independence(CARD_A, CARD_B)
    memberships = [
        _membership(
            card_id=CARD_A,
            family_id=FAMILY_ID,
            joined_at=NOW - timedelta(days=2),
            removed_at=NOW - timedelta(days=1),
        ),
        _membership(
            card_id=CARD_B,
            family_id=FAMILY_ID,
            joined_at=NOW - timedelta(days=2),
            removed_at=NOW - timedelta(days=1),
        ),
    ]
    result = require_corroborated_claim(
        CLAIM_KEY, list(zip(claims, [card_a, card_b])), memberships, [assessment],
        as_of=NOW,
    )
    assert result.independent_family_count == 2
    assert result.family_groups == (frozenset({CARD_A}), frozenset({CARD_B}))


def test_membership_joined_after_as_of_does_not_merge() -> None:
    card_a, card_b = _source_card(card_id=CARD_A), _source_card(card_id=CARD_B)
    claims = [_claim(card_id=CARD_A), _claim(card_id=CARD_B)]
    assessment = _independence(CARD_A, CARD_B)
    memberships = [
        _membership(card_id=CARD_A, family_id=FAMILY_ID, joined_at=FUTURE),
        _membership(card_id=CARD_B, family_id=FAMILY_ID, joined_at=FUTURE),
    ]
    result = require_corroborated_claim(
        CLAIM_KEY, list(zip(claims, [card_a, card_b])), memberships, [assessment],
        as_of=NOW,
    )
    assert result.independent_family_count == 2
    assert result.family_groups == (frozenset({CARD_A}), frozenset({CARD_B}))


def test_three_groups_with_two_pairwise_qualified_counts_two() -> None:
    card_a, card_b, card_c = (
        _source_card(card_id=CARD_A),
        _source_card(card_id=CARD_B),
        _source_card(card_id=CARD_C),
    )
    claims = [_claim(card_id=CARD_A), _claim(card_id=CARD_B), _claim(card_id=CARD_C)]
    ab = _independence(CARD_A, CARD_B)
    ac = _independence(CARD_A, CARD_C, relationship=IndependenceRelationship.RELATED)
    result = require_corroborated_claim(
        CLAIM_KEY,
        list(zip(claims, [card_a, card_b, card_c])),
        [],
        [ab, ac],
        as_of=NOW,
    )
    assert result.independent_family_count == 2
    assert result.family_groups == (
        frozenset({CARD_A}),
        frozenset({CARD_B}),
        frozenset({CARD_C}),
    )
    assert result.qualifying_assessment_ids == frozenset({ab.assessment_id})


def test_supports_deduped_by_card_id() -> None:
    card_a, card_b = _source_card(card_id=CARD_A), _source_card(card_id=CARD_B)
    claim_a, claim_a_dup, claim_b = (
        _claim(card_id=CARD_A),
        _claim(card_id=CARD_A, id_=CLAIM_ID_B),
        _claim(card_id=CARD_B),
    )
    assessment = _independence(CARD_A, CARD_B)
    result = require_corroborated_claim(
        CLAIM_KEY,
        [(claim_a, card_a), (claim_a_dup, card_a), (claim_b, card_b)],
        [],
        [assessment],
        as_of=NOW,
    )
    assert result.independent_family_count == 2
    assert result.family_groups == (frozenset({CARD_A}), frozenset({CARD_B}))


def test_corroboration_result_is_deterministic() -> None:
    card_a, card_b = _source_card(card_id=CARD_A), _source_card(card_id=CARD_B)
    claim_a, claim_b = _claim(card_id=CARD_A), _claim(card_id=CARD_B)
    assessment = _independence(CARD_A, CARD_B)
    forward = require_corroborated_claim(
        CLAIM_KEY, [(claim_a, card_a), (claim_b, card_b)], [], [assessment],
        as_of=NOW,
    )
    reversed_supports = require_corroborated_claim(
        CLAIM_KEY, [(claim_b, card_b), (claim_a, card_a)], [], [assessment],
        as_of=NOW,
    )
    assert forward == reversed_supports
    assert forward.family_groups == (
        frozenset({CARD_A}),
        frozenset({CARD_B}),
    )


def test_corroboration_rejects_invalid_claim_key() -> None:
    with pytest.raises(ValueError, match="64-char hex"):
        require_corroborated_claim("z" * 64, [], [], [], as_of=NOW)


# ── summarize_grades (AC-216) ────────────────────────────────────────────────


def test_empty_input_summary_has_all_none() -> None:
    summary = summarize_grades(
        [], scope_kind=GradeScopeKind.CLAIM, target_id=CLAIM_ID_A
    )
    assert summary == GradeSummary(
        scope_kind=GradeScopeKind.CLAIM, target_id=CLAIM_ID_A
    )
    assert summary.maturity is None
    assert summary.reliability is None
    assert summary.credibility is None
    assert summary.confidence is None
    assert summary.limitations == ()


def test_latest_live_record_wins_per_kind() -> None:
    earlier = _grade(kind=GradeKind.EVIDENCE_MATURITY, recorded_at=NOW, grade_value="e1")
    later = _grade(
        kind=GradeKind.EVIDENCE_MATURITY, recorded_at=LATER, grade_value="e2"
    )
    summary = summarize_grades(
        [earlier, later], scope_kind=GradeScopeKind.CLAIM, target_id=CLAIM_ID_A
    )
    assert summary.maturity is later
    assert summary.maturity.grade_value == "e2"


def test_superseded_record_is_excluded() -> None:
    older = _grade(
        id_=GRADE_1_ID,
        kind=GradeKind.EVIDENCE_MATURITY,
        recorded_at=NOW,
        grade_value="e1",
    )
    newer = _grade(
        id_=GRADE_2_ID,
        kind=GradeKind.EVIDENCE_MATURITY,
        recorded_at=LATER,
        grade_value="e2",
        supersedes_id=GRADE_1_ID,
        supersedes_reason="corrected",
    )
    summary = summarize_grades(
        [older, newer], scope_kind=GradeScopeKind.CLAIM, target_id=CLAIM_ID_A
    )
    assert summary.maturity is newer
    alone = summarize_grades(
        [newer], scope_kind=GradeScopeKind.CLAIM, target_id=CLAIM_ID_A
    )
    assert alone.maturity is newer


def test_four_scales_land_in_four_separate_fields() -> None:
    claim_scope = _scope(
        kind=GradeScopeKind.CLAIM, target_id=CLAIM_ID_A, claim_key=CLAIM_KEY
    )
    source_scope = _scope(
        kind=GradeScopeKind.SOURCE, target_id=CARD_A, source_card_version=1
    )
    report_scope = _scope(kind=GradeScopeKind.REPORT, target_id=REPORT_ID)
    assessment_scope = _scope(kind=GradeScopeKind.ASSESSMENT, target_id=ASSESSMENT_ID)
    maturity = _grade(kind=GradeKind.EVIDENCE_MATURITY, scope=claim_scope, grade_value="e2")
    reliability = _grade(kind=GradeKind.SOURCE_RELIABILITY, scope=source_scope, grade_value="b")
    credibility = _grade(kind=GradeKind.INFORMATION_CREDIBILITY, scope=report_scope, grade_value="3")
    confidence = _grade(kind=GradeKind.ANALYTIC_CONFIDENCE, scope=assessment_scope, grade_value="high")
    records = [maturity, reliability, credibility, confidence]

    claim_summary = summarize_grades(
        records, scope_kind=GradeScopeKind.CLAIM, target_id=CLAIM_ID_A
    )
    assert claim_summary.maturity is maturity
    assert claim_summary.reliability is None
    assert claim_summary.credibility is None
    assert claim_summary.confidence is None

    source_summary = summarize_grades(
        records, scope_kind=GradeScopeKind.SOURCE, target_id=CARD_A
    )
    assert source_summary.reliability is reliability
    assert source_summary.maturity is None

    report_summary = summarize_grades(
        records, scope_kind=GradeScopeKind.REPORT, target_id=REPORT_ID
    )
    assert report_summary.credibility is credibility

    assessment_summary = summarize_grades(
        records, scope_kind=GradeScopeKind.ASSESSMENT, target_id=ASSESSMENT_ID
    )
    assert assessment_summary.confidence is confidence

    # Same scope identity can carry maturity and credibility side by side;
    # the two scales stay in separate fields with their own values.
    credibility_claim = _grade(
        kind=GradeKind.INFORMATION_CREDIBILITY, scope=claim_scope, grade_value="4"
    )
    both = summarize_grades(
        [maturity, credibility_claim],
        scope_kind=GradeScopeKind.CLAIM,
        target_id=CLAIM_ID_A,
    )
    assert both.maturity is maturity
    assert both.credibility is credibility_claim
    assert both.maturity.grade_value == "e2"
    assert both.credibility.grade_value == "4"


def test_limitations_are_sorted_withheld_codes_of_selected_records() -> None:
    withheld_maturity = _grade(
        kind=GradeKind.EVIDENCE_MATURITY,
        recorded_at=LATER,
        withheld_reason_code="grade_withheld_unknown_rights",
    )
    withheld_credibility = _grade(
        kind=GradeKind.INFORMATION_CREDIBILITY,
        scope=_scope(kind=GradeScopeKind.CLAIM, target_id=CLAIM_ID_A, claim_key=CLAIM_KEY),
        withheld_reason_code="grade_withheld_missing_locator",
    )
    summary = summarize_grades(
        [withheld_maturity, withheld_credibility],
        scope_kind=GradeScopeKind.CLAIM,
        target_id=CLAIM_ID_A,
    )
    assert summary.limitations == (
        "grade_withheld_missing_locator",
        "grade_withheld_unknown_rights",
    )


def test_superseded_withheld_record_contributes_no_limitation() -> None:
    older = _grade(
        id_=GRADE_1_ID,
        kind=GradeKind.EVIDENCE_MATURITY,
        recorded_at=NOW,
        withheld_reason_code="grade_withheld_unknown_rights",
    )
    newer = _grade(
        id_=GRADE_2_ID,
        kind=GradeKind.EVIDENCE_MATURITY,
        recorded_at=LATER,
        withheld_reason_code="grade_withheld_missing_source",
        supersedes_id=GRADE_1_ID,
        supersedes_reason="re-graded",
    )
    summary = summarize_grades(
        [older, newer], scope_kind=GradeScopeKind.CLAIM, target_id=CLAIM_ID_A
    )
    assert summary.limitations == ("grade_withheld_missing_source",)


def test_summary_filters_by_scope_identity() -> None:
    foreign = _grade(
        kind=GradeKind.EVIDENCE_MATURITY,
        scope=_scope(kind=GradeScopeKind.CLAIM, target_id=CLAIM_ID_B, claim_key=CLAIM_KEY),
    )
    summary = summarize_grades(
        [foreign], scope_kind=GradeScopeKind.CLAIM, target_id=CLAIM_ID_A
    )
    assert summary.maturity is None
    assert summary.limitations == ()


# ── Documented Key Recipe (spec §4.3) ─────────────────────────────────────────


def test_build_grade_key_matches_documented_recipe() -> None:
    """The §4.3 NUL-joined `Z`-normalized recipe is the runtime key, byte for byte."""
    key = build_grade_key(
        GradeScopeKind.CLAIM,
        UUID("12345678-1234-5678-1234-567812345678"),
        GradeKind.EVIDENCE_MATURITY,
        datetime(2026, 8, 9, 12, 34, 56, tzinfo=timezone.utc),
    )
    assert key == "b5a28e21d946582cb973356275426f7d94d7a239736122ee9afbe1b2df4ce0c3"


def test_build_grade_key_normalizes_nonzero_offset_to_utc_z() -> None:
    utc_moment = datetime(2026, 8, 9, 12, 34, 56, tzinfo=timezone.utc)
    offset_moment = utc_moment.astimezone(timezone(timedelta(hours=5, minutes=30)))
    target = UUID("12345678-1234-5678-1234-567812345678")
    assert build_grade_key(
        GradeScopeKind.CLAIM, target, GradeKind.EVIDENCE_MATURITY, offset_moment
    ) == build_grade_key(
        GradeScopeKind.CLAIM, target, GradeKind.EVIDENCE_MATURITY, utc_moment
    )


# ── Stable-Code Registry (spec §8.1) ──────────────────────────────────────────


def test_assessment_error_code_registry_is_exact() -> None:
    assert ASSESSMENT_ERROR_CODES == frozenset(
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


def test_grade_assessment_error_rejects_unregistered_code() -> None:
    with pytest.raises(ValueError):
        GradeAssessmentError("grade_unregistered_code", "safe message")


# ── Claim Mutation Paths (B7 supersession × grading) ──────────────────────────


def _corrected_claim(
    original: ObservedClaim, *, reason: str = "transcription corrected"
) -> ObservedClaim:
    value_json = canonical_json({"owner": "alice-corrected"})
    return ObservedClaim(
        id=uuid4(),
        claim_key=original.claim_key,
        subject_entity_key=original.subject_entity_key,
        predicate=original.predicate,
        observed_value_json=value_json,
        value_digest=digest(value_json),
        observed_at=original.observed_at,
        evidence_envelope_id=original.evidence_envelope_id,
        source_card_id=original.source_card_id,
        source_card_version=original.source_card_version,
        recorded_at=LATER,
        supersedes_id=original.id,
        supersedes_reason=reason,
    )


def test_claim_correction_chain_is_append_only() -> None:
    original = _claim(id_=CLAIM_ID_A)
    snapshot = replace(original)
    corrected = _corrected_claim(original)
    assert corrected.is_correction
    assert corrected.supersedes_id == original.id
    assert not original.is_correction
    assert original == snapshot
    with pytest.raises(FrozenInstanceError):
        original.observed_value_json = corrected.observed_value_json  # type: ignore[misc]


def test_claim_supersession_reason_iff_link() -> None:
    original = _claim(id_=CLAIM_ID_A)
    with pytest.raises(ValueError):
        _corrected_claim(original, reason="")
    with pytest.raises(ValueError):
        replace(_claim(), supersedes_reason="reason without a superseded claim")


def test_claim_cannot_supersede_itself() -> None:
    claim = _claim(id_=CLAIM_ID_A)
    with pytest.raises(ValueError):
        replace(claim, supersedes_id=claim.id, supersedes_reason="self")


def test_correction_chain_does_not_inflate_corroboration() -> None:
    """Original plus its correction is still one card: dedup keeps AC-215 honest."""
    card_a, card_b = _source_card(card_id=CARD_A), _source_card(card_id=CARD_B)
    original = _claim(card_id=CARD_A, id_=CLAIM_ID_A)
    corrected = _corrected_claim(original)
    independent = _claim(card_id=CARD_B, id_=CLAIM_ID_B)
    assessment = _independence(CARD_A, CARD_B)
    result = require_corroborated_claim(
        CLAIM_KEY,
        [(original, card_a), (corrected, card_a), (independent, card_b)],
        [],
        [assessment],
        as_of=NOW,
    )
    assert result.independent_family_count == 2
    assert result.family_groups == (frozenset({CARD_A}), frozenset({CARD_B}))


def test_corrected_claim_with_new_key_fails_original_scope() -> None:
    """A correction that changes the proposition cannot support the old claim key."""
    card_a = _source_card(card_id=CARD_A)
    rekeyed = replace(_corrected_claim(_claim(id_=CLAIM_ID_A)), claim_key=CLAIM_KEY_2)
    with pytest.raises(GradeAssessmentError) as exc:
        require_corroborated_claim(CLAIM_KEY, [(rekeyed, card_a)], [], [], as_of=NOW)
    assert exc.value.code == "grade_claim_scope_mismatch"
