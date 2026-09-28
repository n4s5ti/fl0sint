"""B4/OBS-1968 -- Domain-contract tests for the append-only forensic observations layer.

Covers the ObservedClaim/ClaimRelation/ClaimAssessment validators, the
content-addressed key builders (determinism, cross-domain distinctness,
per-part sensitivity), contradiction-state derivation, per-claim projection
eligibility (AC-211/AC-212), and error redaction discipline.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest

from flowsint_core.core.forensics.observations import (
    ClaimAssessment,
    ClaimAssessmentKind,
    ClaimRelation,
    ClaimRelationKind,
    ContradictionState,
    ForensicObservationError,
    ObservedClaim,
    ProjectionEligibility,
    build_assessment_key,
    build_claim_key,
    build_relation_key,
    canonical_observed_value,
    derive_contradiction_state,
    evaluate_projection_eligibility,
)

NOW = datetime(2026, 8, 8, 12, 0, 0, tzinfo=timezone.utc)
SUBJECT = "e" * 64
SOURCE_CARD_ID = "card-obs1968"
SOURCE_CARD_VERSION = 1

CLAIM_A_ID = UUID("00000000-0000-4000-8000-000000000001")
CLAIM_B_ID = UUID("00000000-0000-4000-8000-000000000002")
CLAIM_C_ID = UUID("00000000-0000-4000-8000-000000000003")
RELATION_ID = UUID("00000000-0000-4000-8000-000000000010")
RELATION_2_ID = UUID("00000000-0000-4000-8000-000000000011")
ENVELOPE_ID = UUID("00000000-0000-4000-8000-0000000000ff")

RAW_VALUE_PAYLOAD = "RAW_OBSERVED_VALUE_PAYLOAD_MARKER"
RAW_RATIONALE_PAYLOAD = "RAW_RATIONALE_PAYLOAD_MARKER"

HEX = frozenset("0123456789abcdef")


def canonical_json(value: dict) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def digest(value_json: str) -> str:
    return hashlib.sha256(value_json.encode()).hexdigest()


def make_claim(
    *,
    id_: UUID | None = None,
    subject: str = SUBJECT,
    predicate: str = "owns_asset",
    value: dict | None = None,
    observed_at: datetime = NOW,
    recorded_at: datetime = NOW,
    source_card_id: str = SOURCE_CARD_ID,
    source_card_version: int = SOURCE_CARD_VERSION,
    valid_from: datetime | None = None,
    valid_to: datetime | None = None,
    supersedes_id: UUID | None = None,
    supersedes_reason: str | None = None,
) -> ObservedClaim:
    value_json = canonical_json(value if value is not None else {"owner": "alice"})
    return ObservedClaim(
        id=id_ or uuid4(),
        claim_key=build_claim_key(
            subject, predicate, digest(value_json), observed_at,
            source_card_id, source_card_version,
        ),
        subject_entity_key=subject,
        predicate=predicate,
        observed_value_json=value_json,
        value_digest=digest(value_json),
        observed_at=observed_at,
        evidence_envelope_id=ENVELOPE_ID,
        source_card_id=source_card_id,
        source_card_version=source_card_version,
        recorded_at=recorded_at,
        valid_from=valid_from,
        valid_to=valid_to,
        supersedes_id=supersedes_id,
        supersedes_reason=supersedes_reason,
    )


def make_relation(
    *,
    id_: UUID | None = None,
    claim_id: UUID = CLAIM_A_ID,
    related_claim_id: UUID = CLAIM_B_ID,
    kind: ClaimRelationKind = ClaimRelationKind.CONTRADICTS,
    rationale: str = "same asset attributed to two different owners",
    recorded_at: datetime = NOW,
    supersedes_id: UUID | None = None,
    supersedes_reason: str | None = None,
) -> ClaimRelation:
    return ClaimRelation(
        id=id_ or uuid4(),
        relation_key=build_relation_key(claim_id, related_claim_id, kind),
        claim_id=claim_id,
        related_claim_id=related_claim_id,
        kind=kind,
        rationale=rationale,
        evidence_envelope_id=ENVELOPE_ID,
        recorded_at=recorded_at,
        supersedes_id=supersedes_id,
        supersedes_reason=supersedes_reason,
    )


def make_assessment(
    *,
    id_: UUID | None = None,
    kind: ClaimAssessmentKind,
    claim_id: UUID | None = None,
    relation_id: UUID | None = None,
    recorded_at: datetime = NOW,
    rationale: str = "recorded assessment rationale",
    supersedes_id: UUID | None = None,
    supersedes_reason: str | None = None,
) -> ClaimAssessment:
    scope_id = relation_id if relation_id is not None else claim_id
    return ClaimAssessment(
        id=id_ or uuid4(),
        assessment_key=build_assessment_key(scope_id, kind, recorded_at),
        kind=kind,
        rationale=rationale,
        evidence_envelope_id=ENVELOPE_ID,
        recorded_at=recorded_at,
        claim_id=claim_id,
        relation_id=relation_id,
        supersedes_id=supersedes_id,
        supersedes_reason=supersedes_reason,
    )


# ── enum contract ───────────────────────────────────────────────────────────


def test_claim_relation_kinds_are_stable_contract_strings() -> None:
    assert {kind.value for kind in ClaimRelationKind} == {
        "contradicts",
        "compatible",
        "duplicate_report",
    }


def test_claim_assessment_kinds_are_stable_contract_strings() -> None:
    assert {kind.value for kind in ClaimAssessmentKind} == {
        "withdrawn",
        "insufficient_support",
        "unresolved",
        "ambiguous",
        "resolved_compatible",
        "resolved_upheld",
    }


def test_contradiction_states_are_stable_contract_strings() -> None:
    assert {state.value for state in ContradictionState} == {
        "unresolved",
        "ambiguous",
        "resolved_compatible",
        "resolved_upheld",
    }


def test_contract_version_is_stable() -> None:
    from flowsint_core.core.forensics.observations import (
        OBSERVATIONS_CONTRACT_VERSION,
    )

    assert OBSERVATIONS_CONTRACT_VERSION == "v1"


# ── key builders ────────────────────────────────────────────────────────────


def test_claim_key_is_deterministic_hex64() -> None:
    kwargs = {
        "subject_entity_key": SUBJECT,
        "predicate": "owns_asset",
        "value_digest": "a" * 64,
        "observed_at": NOW,
        "source_card_id": SOURCE_CARD_ID,
        "source_card_version": SOURCE_CARD_VERSION,
    }
    key = build_claim_key(**kwargs)
    assert key == build_claim_key(**kwargs)
    assert len(key) == 64
    assert all(char in HEX for char in key)


@pytest.mark.parametrize(
    "change",
    [
        {"subject_entity_key": "f" * 64},
        {"predicate": "owes_money"},
        {"value_digest": "b" * 64},
        {"observed_at": NOW.replace(minute=1)},
        {"source_card_id": "card-other"},
        {"source_card_version": 2},
    ],
)
def test_claim_key_is_sensitive_to_every_part(change: dict) -> None:
    base = {
        "subject_entity_key": SUBJECT,
        "predicate": "owns_asset",
        "value_digest": "a" * 64,
        "observed_at": NOW,
        "source_card_id": SOURCE_CARD_ID,
        "source_card_version": SOURCE_CARD_VERSION,
    }
    assert build_claim_key(**base) != build_claim_key(**{**base, **change})


def test_relation_key_is_deterministic_and_symmetric() -> None:
    forward = build_relation_key(CLAIM_A_ID, CLAIM_B_ID, ClaimRelationKind.CONTRADICTS)
    assert forward == build_relation_key(
        CLAIM_A_ID, CLAIM_B_ID, ClaimRelationKind.CONTRADICTS
    )
    assert forward == build_relation_key(
        CLAIM_B_ID, CLAIM_A_ID, ClaimRelationKind.CONTRADICTS
    )
    assert len(forward) == 64
    assert all(char in HEX for char in forward)


@pytest.mark.parametrize(
    "change",
    [
        {"claim_id": CLAIM_C_ID},
        {"related_claim_id": CLAIM_C_ID},
        {"kind": ClaimRelationKind.COMPATIBLE},
    ],
)
def test_relation_key_is_sensitive_to_every_part(change: dict) -> None:
    base = {
        "claim_id": CLAIM_A_ID,
        "related_claim_id": CLAIM_B_ID,
        "kind": ClaimRelationKind.CONTRADICTS,
    }
    assert build_relation_key(**base) != build_relation_key(**{**base, **change})


def test_assessment_key_is_deterministic_hex64() -> None:
    key = build_assessment_key(CLAIM_A_ID, ClaimAssessmentKind.WITHDRAWN, NOW)
    assert key == build_assessment_key(CLAIM_A_ID, ClaimAssessmentKind.WITHDRAWN, NOW)
    assert len(key) == 64
    assert all(char in HEX for char in key)


@pytest.mark.parametrize(
    "change",
    [
        {"scope_id": CLAIM_B_ID},
        {"kind": ClaimAssessmentKind.INSUFFICIENT_SUPPORT},
        {"recorded_at": NOW.replace(minute=2)},
    ],
)
def test_assessment_key_is_sensitive_to_every_part(change: dict) -> None:
    base = {
        "scope_id": CLAIM_A_ID,
        "kind": ClaimAssessmentKind.WITHDRAWN,
        "recorded_at": NOW,
    }
    assert build_assessment_key(**base) != build_assessment_key(**{**base, **change})


def test_key_domains_are_distinct() -> None:
    claim_key = build_claim_key(
        SUBJECT, "owns_asset", "a" * 64, NOW, SOURCE_CARD_ID, SOURCE_CARD_VERSION
    )
    relation_key = build_relation_key(
        CLAIM_A_ID, CLAIM_B_ID, ClaimRelationKind.CONTRADICTS
    )
    assessment_key = build_assessment_key(CLAIM_A_ID, ClaimAssessmentKind.WITHDRAWN, NOW)
    assert len({claim_key, relation_key, assessment_key}) == 3
    for key in (claim_key, relation_key, assessment_key):
        assert len(key) == 64
        assert all(char in HEX for char in key)


# ── ObservedClaim validation ────────────────────────────────────────────────


def test_claim_rejects_naive_datetimes() -> None:
    with pytest.raises((TypeError, ValueError), match="timezone-aware"):
        make_claim(observed_at=datetime(2026, 8, 8, 12, 0, 0))
    with pytest.raises(TypeError, match="timezone-aware"):
        make_claim(recorded_at=datetime(2026, 8, 8, 12, 0, 0))
    with pytest.raises(TypeError, match="timezone-aware"):
        make_claim(valid_from=datetime(2026, 8, 8, 11, 0, 0), valid_to=NOW)
    with pytest.raises(TypeError, match="timezone-aware"):
        make_claim(valid_from=NOW, valid_to=datetime(2026, 8, 8, 13, 0, 0))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("claim_key", "z" * 64),
        ("claim_key", "a" * 63),
        ("subject_entity_key", "z" * 64),
        ("object_entity_key", "z" * 64),
        ("value_digest", "z" * 64),
    ],
)
def test_claim_rejects_non_hex_or_wrong_length_keys(field: str, value: str) -> None:
    with pytest.raises(ValueError, match="64-char hex"):
        replace(make_claim(), **{field: value})


def test_claim_rejects_digest_not_matching_observed_value() -> None:
    with pytest.raises(ValueError, match="does not match sha256"):
        replace(make_claim(), value_digest="a" * 64)


@pytest.mark.parametrize(
    "predicate",
    ["OwnsAsset", "1st_claim", "has space", "a-b", "a" * 65, ""],
)
def test_claim_rejects_bad_predicates(predicate: str) -> None:
    with pytest.raises(ValueError, match="predicate"):
        replace(make_claim(), predicate=predicate)


def test_claim_rejects_invalid_json_and_empty_value() -> None:
    with pytest.raises(ValueError, match="valid JSON"):
        replace(make_claim(), observed_value_json="{not json")
    with pytest.raises(ValueError, match="non-empty JSON"):
        replace(make_claim(), observed_value_json="")


@pytest.mark.parametrize(
    "value_json",
    [
        '{"owner": "alice"}',
        '{"b":1,"a":2}',
        '{"owner":"\u00e9"}',
    ],
)
def test_claim_rejects_non_canonical_json(value_json: str) -> None:
    digest = hashlib.sha256(value_json.encode()).hexdigest()
    with pytest.raises(ValueError, match="canonical JSON"):
        replace(make_claim(), observed_value_json=value_json, value_digest=digest)


def test_canonical_observed_value_round_trips() -> None:
    canonical = canonical_observed_value({"b": 1, "a": "é"})
    assert canonical == '{"a":"\\u00e9","b":1}'
    claim = replace(
        make_claim(),
        observed_value_json=canonical,
        value_digest=hashlib.sha256(canonical.encode()).hexdigest(),
    )
    assert claim.observed_value_json == canonical


def test_claim_rejects_inverted_temporal_scope() -> None:
    with pytest.raises(ValueError, match="valid_to must not precede valid_from"):
        make_claim(valid_from=NOW, valid_to=NOW.replace(hour=11))


def test_claim_rejects_invalid_source_card_version() -> None:
    with pytest.raises(ValueError, match="source_card_version"):
        replace(make_claim(), source_card_version=0)


def test_supersedes_reason_is_required_iff_supersedes_id() -> None:
    with pytest.raises(ValueError, match="supersedes_reason is required"):
        make_claim(supersedes_id=CLAIM_B_ID)
    with pytest.raises(ValueError, match="meaningless"):
        make_claim(supersedes_reason="corrected value")
    with pytest.raises(ValueError, match="cannot supersede itself"):
        make_claim(
            id_=CLAIM_A_ID, supersedes_id=CLAIM_A_ID, supersedes_reason="oops"
        )


def test_claim_correction_flag_tracks_supersession() -> None:
    assert make_claim().is_correction is False
    correction = make_claim(supersedes_id=CLAIM_B_ID, supersedes_reason="corrected")
    assert correction.is_correction is True


# ── ClaimRelation validation ────────────────────────────────────────────────


def test_relation_rejects_self_pair() -> None:
    with pytest.raises(ValueError, match="itself"):
        make_relation(claim_id=CLAIM_A_ID, related_claim_id=CLAIM_A_ID)


@pytest.mark.parametrize("rationale", ["", "   ", "\t\n"])
def test_relation_rejects_empty_or_whitespace_rationale(rationale: str) -> None:
    with pytest.raises(ValueError, match="rationale"):
        make_relation(rationale=rationale)


def test_relation_normalizes_pair_order_and_key() -> None:
    forward = make_relation(id_=RELATION_ID)
    backward = make_relation(id_=RELATION_2_ID, claim_id=CLAIM_B_ID, related_claim_id=CLAIM_A_ID)
    assert (forward.claim_id, forward.related_claim_id) == (CLAIM_A_ID, CLAIM_B_ID)
    assert (backward.claim_id, backward.related_claim_id) == (CLAIM_A_ID, CLAIM_B_ID)
    assert forward.relation_key == backward.relation_key
    assert forward.relation_key == build_relation_key(
        CLAIM_A_ID, CLAIM_B_ID, ClaimRelationKind.CONTRADICTS
    )


def test_relation_rejects_non_kind() -> None:
    with pytest.raises(TypeError, match="kind"):
        ClaimRelation(
            id=uuid4(),
            relation_key="a" * 64,
            claim_id=CLAIM_A_ID,
            related_claim_id=CLAIM_B_ID,
            kind="contradicts",  # type: ignore[arg-type]
            rationale="x",
            evidence_envelope_id=ENVELOPE_ID,
            recorded_at=NOW,
        )


def test_relation_rejects_naive_recorded_at() -> None:
    with pytest.raises(TypeError, match="timezone-aware"):
        make_relation(recorded_at=datetime(2026, 8, 8, 12, 0, 0))


def test_relation_rejects_superseding_without_reason() -> None:
    with pytest.raises(ValueError, match="supersedes_reason is required"):
        make_relation(supersedes_id=RELATION_ID)


# ── ClaimAssessment validation ──────────────────────────────────────────────


def test_assessment_requires_exactly_one_scope() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        make_assessment(kind=ClaimAssessmentKind.UNRESOLVED, claim_id=None, relation_id=None)
    with pytest.raises(ValueError, match="exactly one"):
        make_assessment(
            kind=ClaimAssessmentKind.UNRESOLVED,
            claim_id=CLAIM_A_ID,
            relation_id=RELATION_ID,
        )


@pytest.mark.parametrize(
    "kind",
    [ClaimAssessmentKind.WITHDRAWN, ClaimAssessmentKind.INSUFFICIENT_SUPPORT],
)
def test_claim_scoped_kind_rejects_relation_scope(kind: ClaimAssessmentKind) -> None:
    with pytest.raises(ValueError, match="claim-scoped"):
        make_assessment(kind=kind, relation_id=RELATION_ID)


@pytest.mark.parametrize(
    "kind",
    [
        ClaimAssessmentKind.UNRESOLVED,
        ClaimAssessmentKind.AMBIGUOUS,
        ClaimAssessmentKind.RESOLVED_COMPATIBLE,
        ClaimAssessmentKind.RESOLVED_UPHELD,
    ],
)
def test_relation_scoped_kind_rejects_claim_scope(kind: ClaimAssessmentKind) -> None:
    with pytest.raises(ValueError, match="relation-scoped"):
        make_assessment(kind=kind, claim_id=CLAIM_A_ID)


def test_assessment_rejects_non_kind() -> None:
    with pytest.raises(TypeError, match="kind"):
        ClaimAssessment(
            id=uuid4(),
            assessment_key="a" * 64,
            kind="withdrawn",  # type: ignore[arg-type]
            rationale="x",
            evidence_envelope_id=ENVELOPE_ID,
            claim_id=CLAIM_A_ID,
            recorded_at=NOW,
        )


def test_assessment_rejects_empty_rationale_and_naive_recorded_at() -> None:
    with pytest.raises(ValueError, match="rationale"):
        make_assessment(kind=ClaimAssessmentKind.WITHDRAWN, claim_id=CLAIM_A_ID, rationale="")
    with pytest.raises((TypeError, ValueError), match="timezone-aware"):
        make_assessment(
            kind=ClaimAssessmentKind.WITHDRAWN,
            claim_id=CLAIM_A_ID,
            recorded_at=datetime(2026, 8, 8, 12, 0, 0),
        )


# ── derive_contradiction_state ──────────────────────────────────────────────


def test_contradiction_without_assessment_is_unresolved() -> None:
    relation = make_relation(id_=RELATION_ID)
    assert derive_contradiction_state(relation, []) is ContradictionState.UNRESOLVED


def test_non_contradicts_relation_is_never_resolved() -> None:
    relation = make_relation(id_=RELATION_ID, kind=ClaimRelationKind.COMPATIBLE)
    assessment = make_assessment(
        kind=ClaimAssessmentKind.RESOLVED_COMPATIBLE, relation_id=RELATION_ID
    )
    assert derive_contradiction_state(relation, [assessment]) is ContradictionState.UNRESOLVED


def test_latest_non_superseded_assessment_wins() -> None:
    relation = make_relation(id_=RELATION_ID)
    earlier = make_assessment(
        kind=ClaimAssessmentKind.UNRESOLVED, relation_id=RELATION_ID, recorded_at=NOW
    )
    later = make_assessment(
        kind=ClaimAssessmentKind.RESOLVED_COMPATIBLE,
        relation_id=RELATION_ID,
        recorded_at=NOW.replace(minute=1),
    )
    assert (
        derive_contradiction_state(relation, [earlier, later])
        is ContradictionState.RESOLVED_COMPATIBLE
    )


def test_superseded_assessment_is_ignored_even_when_later() -> None:
    relation = make_relation(id_=RELATION_ID)
    live = make_assessment(
        kind=ClaimAssessmentKind.UNRESOLVED, relation_id=RELATION_ID, recorded_at=NOW
    )
    superseded = make_assessment(
        kind=ClaimAssessmentKind.RESOLVED_UPHELD,
        relation_id=RELATION_ID,
        recorded_at=NOW.replace(minute=9),
        supersedes_id=RELATION_2_ID,
        supersedes_reason="overtaken by a later assessment",
    )
    assert (
        derive_contradiction_state(relation, [live, superseded])
        is ContradictionState.UNRESOLVED
    )


def test_assessments_for_other_relations_do_not_resolve_this_relation() -> None:
    relation = make_relation(id_=RELATION_ID)
    other = make_assessment(
        kind=ClaimAssessmentKind.RESOLVED_UPHELD,
        relation_id=RELATION_2_ID,
        recorded_at=NOW.replace(minute=1),
    )
    assert derive_contradiction_state(relation, [other]) is ContradictionState.UNRESOLVED


# ── evaluate_projection_eligibility ─────────────────────────────────────────


def test_no_relations_or_assessments_means_eligible_without_hold_or_state() -> None:
    claim = make_claim(id_=CLAIM_A_ID)
    result = evaluate_projection_eligibility(
        claim, superseded=False, assessments=[], relations_with_states=[]
    )
    assert result == ProjectionEligibility(
        claim.id, True, None, None, claim.claim_key
    )


def test_hold_precedence_withdrawn_over_superseded_over_insufficient_support() -> None:
    claim = make_claim(id_=CLAIM_A_ID)
    withdrawn = make_assessment(
        kind=ClaimAssessmentKind.WITHDRAWN, claim_id=claim.id, recorded_at=NOW
    )
    insufficient = make_assessment(
        kind=ClaimAssessmentKind.INSUFFICIENT_SUPPORT,
        claim_id=claim.id,
        recorded_at=NOW.replace(minute=1),
    )
    both = evaluate_projection_eligibility(
        claim, superseded=False, assessments=[withdrawn, insufficient],
        relations_with_states=[],
    )
    assert (both.eligible, both.hold_code) == (False, "forensic_claim_withdrawn_hold")
    superseded_beats_insufficient = evaluate_projection_eligibility(
        claim, superseded=True, assessments=[insufficient], relations_with_states=[]
    )
    assert (
        superseded_beats_insufficient.eligible,
        superseded_beats_insufficient.hold_code,
    ) == (False, "forensic_claim_superseded_hold")
    withdrawn_beats_superseded = evaluate_projection_eligibility(
        claim, superseded=True, assessments=[withdrawn], relations_with_states=[]
    )
    assert (
        withdrawn_beats_superseded.eligible,
        withdrawn_beats_superseded.hold_code,
    ) == (False, "forensic_claim_withdrawn_hold")
    alone = evaluate_projection_eligibility(
        claim, superseded=False, assessments=[insufficient], relations_with_states=[]
    )
    assert (alone.eligible, alone.hold_code) == (
        False,
        "forensic_claim_insufficient_support_hold",
    )


def test_superseded_withdrawn_assessment_does_not_hold_claim() -> None:
    claim = make_claim(id_=CLAIM_A_ID)
    superseded = make_assessment(
        kind=ClaimAssessmentKind.WITHDRAWN,
        claim_id=claim.id,
        supersedes_id=RELATION_ID,
        supersedes_reason="reassessed",
    )
    result = evaluate_projection_eligibility(
        claim, superseded=False, assessments=[superseded], relations_with_states=[]
    )
    assert (result.eligible, result.hold_code, result.contradiction_state) == (
        True, None, None,
    )


def test_other_claims_assessments_do_not_hold_this_claim() -> None:
    claim = make_claim(id_=CLAIM_A_ID)
    other_withdrawn = make_assessment(
        kind=ClaimAssessmentKind.WITHDRAWN, claim_id=CLAIM_B_ID
    )
    result = evaluate_projection_eligibility(
        claim, superseded=False, assessments=[other_withdrawn], relations_with_states=[]
    )
    assert result.eligible is True
    assert result.hold_code is None


def test_ac_212_unresolved_contradiction_holds_neither_side() -> None:
    claim_a = make_claim(id_=CLAIM_A_ID)
    claim_b = make_claim(id_=CLAIM_B_ID)
    relation = make_relation(id_=RELATION_ID)
    results = [
        evaluate_projection_eligibility(
            claim, superseded=False, assessments=[],
            relations_with_states=[(relation, ContradictionState.UNRESOLVED)],
        )
        for claim in (claim_a, claim_b)
    ]
    assert [(r.eligible, r.hold_code, r.contradiction_state) for r in results] == [
        (True, None, ContradictionState.UNRESOLVED),
        (True, None, ContradictionState.UNRESOLVED),
    ]


def test_resolved_contradiction_state_is_carried_while_eligible() -> None:
    claim = make_claim(id_=CLAIM_A_ID)
    relation = make_relation(id_=RELATION_ID)
    result = evaluate_projection_eligibility(
        claim, superseded=False, assessments=[],
        relations_with_states=[(relation, ContradictionState.RESOLVED_COMPATIBLE)],
    )
    assert (result.eligible, result.hold_code, result.contradiction_state) == (
        True, None, ContradictionState.RESOLVED_COMPATIBLE,
    )


def test_latest_live_contradiction_relation_wins() -> None:
    claim = make_claim(id_=CLAIM_A_ID)
    earlier = make_relation(id_=RELATION_ID, recorded_at=NOW)
    later = make_relation(id_=RELATION_2_ID, recorded_at=NOW.replace(minute=1))
    result = evaluate_projection_eligibility(
        claim, superseded=False, assessments=[],
        relations_with_states=[
            (earlier, ContradictionState.UNRESOLVED),
            (later, ContradictionState.RESOLVED_UPHELD),
        ],
    )
    assert result.contradiction_state is ContradictionState.RESOLVED_UPHELD


def test_superseded_contradiction_relation_does_not_affect_state() -> None:
    claim = make_claim(id_=CLAIM_A_ID)
    superseded = make_relation(
        id_=RELATION_2_ID,
        recorded_at=NOW.replace(minute=1),
        supersedes_id=RELATION_ID,
        supersedes_reason="overtaken by a correcting relation",
    )
    result = evaluate_projection_eligibility(
        claim, superseded=False, assessments=[],
        relations_with_states=[(superseded, ContradictionState.RESOLVED_UPHELD)],
    )
    assert result.contradiction_state is None


# ── ForensicObservationError ────────────────────────────────────────────────


def test_error_carries_stable_code_and_safe_message() -> None:
    error = ForensicObservationError(
        "forensic_claim_missing_evidence", "evidence envelope missing"
    )
    assert error.code == "forensic_claim_missing_evidence"
    assert error.safe_message == "evidence envelope missing"
    assert isinstance(error, RuntimeError)


def test_domain_errors_never_embed_raw_values_or_rationale() -> None:
    with pytest.raises(ValueError) as claim_error:
        replace(make_claim(value={"owner": RAW_VALUE_PAYLOAD}), claim_key="z" * 64)
    assert RAW_VALUE_PAYLOAD not in str(claim_error.value)
    with pytest.raises(ValueError) as relation_error:
        make_relation(
            rationale=RAW_RATIONALE_PAYLOAD,
            claim_id=CLAIM_A_ID,
            related_claim_id=CLAIM_A_ID,
        )
    assert RAW_RATIONALE_PAYLOAD not in str(relation_error.value)
