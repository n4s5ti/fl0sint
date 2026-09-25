"""B4/OBS-1968 -- SQLite repository tests for the append-only forensic observations layer.

Each test builds the evidence-envelope FK chain once (profile -> flow_run ->
step_run -> evidence envelope) and exercises insert pre-validation, AC-210
preservation, AC-211 queryability, AC-212 eligibility, append-only
enforcement, and raw CHECK-constraint rejection.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, event, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from flowsint_core.core.forensics.observations import (
    ClaimAssessment,
    ClaimAssessmentKind,
    ClaimRelation,
    ClaimRelationKind,
    ContradictionState,
    ForensicObservationError,
    ObservedClaim,
    build_assessment_key,
    build_claim_key,
    build_relation_key,
)
from flowsint_core.core.forensics.observations_repository import (
    evaluate_subject_projection_eligibility,
    insert_claim_assessment_record,
    insert_claim_record,
    insert_claim_relation_record,
    load_claims_for_subject,
    load_contradiction_state,
    load_subject_contradictions,
)
from flowsint_core.core.models import (
    Base,
    EvidenceEnvelopeRecord,
    FlowRun,
    ForensicClaimAssessmentRecord,
    ForensicClaimRecord,
    ForensicClaimRelationRecord,
    Profile,
    StepRun,
)

NOW = datetime(2026, 8, 8, 12, 0, 0, tzinfo=timezone.utc)
RAW_VALUE_PAYLOAD = "RAW_OBSERVED_VALUE_PAYLOAD_MARKER"
RAW_RATIONALE_PAYLOAD = "RAW_RATIONALE_PAYLOAD_MARKER"


@pytest.fixture
def engine():
    eng = create_engine("sqlite://", echo=False)
    event.listen(eng, "connect", lambda conn, _: conn.execute("PRAGMA foreign_keys=ON"))
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture
def session(engine):
    SessionLocal = sessionmaker(bind=engine)
    with SessionLocal() as sess:
        yield sess
        sess.rollback()


def _canonical_json(value: dict) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _digest(value_json: str) -> str:
    return hashlib.sha256(value_json.encode()).hexdigest()


def _utc(value: datetime | None) -> datetime | None:
    """SQLite round-trips tz-aware datetimes as naive; normalize like the repo."""
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _raw_claim(
    *,
    id_: UUID,
    claim_key: str,
    subject: str,
    predicate: str,
    value_json: str,
    observed_at: datetime,
    evidence_envelope_id: UUID,
    recorded_at: datetime,
    supersedes_id: UUID | None = None,
    supersedes_reason: str | None = None,
) -> ObservedClaim:
    """Build a claim that bypasses domain validation (repo defense-in-depth paths)."""
    claim = object.__new__(ObservedClaim)
    for name, value in {
        "id": id_,
        "claim_key": claim_key,
        "case_reference": None,
        "subject_entity_key": subject,
        "predicate": predicate,
        "object_entity_key": None,
        "observed_value_json": value_json,
        "value_digest": _digest(value_json),
        "observed_at": observed_at,
        "valid_from": None,
        "valid_to": None,
        "source_card_id": "card-obs1968",
        "source_card_version": 1,
        "source_record_id": None,
        "evidence_envelope_id": evidence_envelope_id,
        "supersedes_id": supersedes_id,
        "supersedes_reason": supersedes_reason,
        "recorded_at": recorded_at,
    }.items():
        object.__setattr__(claim, name, value)
    return claim


def _raw_relation(
    *,
    id_: UUID,
    relation_key: str,
    claim_id: UUID,
    related_claim_id: UUID,
    kind: ClaimRelationKind,
    rationale: str,
    evidence_envelope_id: UUID,
    recorded_at: datetime,
) -> ClaimRelation:
    """Build a relation that bypasses domain validation (repo defense-in-depth paths)."""
    relation = object.__new__(ClaimRelation)
    for name, value in {
        "id": id_,
        "relation_key": relation_key,
        "claim_id": claim_id,
        "related_claim_id": related_claim_id,
        "kind": kind,
        "rationale": rationale,
        "evidence_envelope_id": evidence_envelope_id,
        "supersedes_id": None,
        "supersedes_reason": None,
        "recorded_at": recorded_at,
    }.items():
        object.__setattr__(relation, name, value)
    return relation


def _raw_assessment(
    *,
    id_: UUID,
    assessment_key: str,
    kind: ClaimAssessmentKind,
    rationale: str,
    evidence_envelope_id: UUID,
    recorded_at: datetime,
    claim_id: UUID | None = None,
    relation_id: UUID | None = None,
) -> ClaimAssessment:
    """Build an assessment that bypasses domain validation (repo defense-in-depth paths)."""
    assessment = object.__new__(ClaimAssessment)
    for name, value in {
        "id": id_,
        "assessment_key": assessment_key,
        "kind": kind,
        "rationale": rationale,
        "evidence_envelope_id": evidence_envelope_id,
        "recorded_at": recorded_at,
        "claim_id": claim_id,
        "relation_id": relation_id,
        "supersedes_id": None,
        "supersedes_reason": None,
    }.items():
        object.__setattr__(assessment, name, value)
    return assessment


@pytest.fixture
def envelope(session):
    """Evidence-envelope FK chain: profile -> flow_run -> step_run -> envelope."""
    owner_id = uuid4()
    profile = Profile(id=owner_id, email=f"owner-{owner_id}@obs1968.test")
    for col in Profile.__table__.columns:
        if col.name.startswith("hashed_"):
            setattr(profile, col.name, "x" * 64)
    session.add(profile)

    flow_run_id = uuid4()
    session.add(
        FlowRun(
            id=flow_run_id,
            owner_id=owner_id,
            idempotency_key=f"obs1968-repo-{uuid4()}",
            input_digest="a" * 64,
            operation_digest="a" * 64,
        )
    )
    step_run_id = uuid4()
    session.add(
        StepRun(id=step_run_id, flow_run_id=flow_run_id, step_key="obs1968-repo")
    )
    env_id = uuid4()
    session.add(
        EvidenceEnvelopeRecord(
            id=env_id,
            flow_run_id=flow_run_id,
            step_run_id=step_run_id,
            attempt=0,
            input_index=0,
            evidence_index=0,
            input_ref="obs1968-repo",
            status="success",
            retryable=False,
            mapped_outputs=[],
            source="test",
            retrieved_at=NOW,
            ingested_at=NOW,
        )
    )
    session.flush()

    subject = "e" * 64
    source_card_id = "card-obs1968"

    def make_claim(
        predicate: str = "owns_asset",
        value: dict | None = None,
        observed_at: datetime = NOW,
        recorded_at: datetime = NOW,
        supersedes_id: UUID | None = None,
        supersedes_reason: str | None = None,
        *,
        id_: UUID | None = None,
        valid_from: datetime | None = None,
        valid_to: datetime | None = None,
        source_card_id: str = source_card_id,
        source_card_version: int = 1,
    ) -> ObservedClaim:
        value_json = _canonical_json(value if value is not None else {"owner": "alice"})
        return ObservedClaim(
            id=id_ or uuid4(),
            claim_key=build_claim_key(
                subject, predicate, _digest(value_json), observed_at,
                source_card_id, source_card_version,
            ),
            subject_entity_key=subject,
            predicate=predicate,
            observed_value_json=value_json,
            value_digest=_digest(value_json),
            observed_at=observed_at,
            evidence_envelope_id=env_id,
            source_card_id=source_card_id,
            source_card_version=source_card_version,
            recorded_at=recorded_at,
            valid_from=valid_from,
            valid_to=valid_to,
            supersedes_id=supersedes_id,
            supersedes_reason=supersedes_reason,
        )

    return SimpleNamespace(
        session=session,
        env_id=env_id,
        subject=subject,
        source_card_id=source_card_id,
        make_claim=make_claim,
    )


@pytest.fixture
def six_claims(session, envelope):
    """Six claims with distinct recorded_at minutes: the six-claim scenario."""
    a = envelope.make_claim(
        value={"asset": "parcel-1", "owner": "alice"}, recorded_at=NOW.replace(minute=0)
    )
    b = envelope.make_claim(
        value={"asset": "parcel-1", "owner": "bob"},
        observed_at=NOW.replace(minute=1),
        recorded_at=NOW.replace(minute=1),
    )
    c = envelope.make_claim(
        predicate="withdrawn_claim",
        value={"note": "false lead"},
        recorded_at=NOW.replace(minute=2),
    )
    d = envelope.make_claim(
        predicate="superseded_target",
        value={"note": "old value"},
        recorded_at=NOW.replace(minute=3),
    )
    e = envelope.make_claim(
        predicate="superseded_target",
        value={"note": "corrected value"},
        recorded_at=NOW.replace(minute=4),
        supersedes_id=d.id,
        supersedes_reason="corrected observed value",
    )
    f = envelope.make_claim(
        predicate="thin_support",
        value={"note": "single source"},
        recorded_at=NOW.replace(minute=5),
    )
    for claim in (a, b, c, d, e, f):
        insert_claim_record(session, claim)
    session.commit()
    return SimpleNamespace(
        session=session,
        subject=envelope.subject,
        env_id=envelope.env_id,
        a=a, b=b, c=c, d=d, e=e, f=f,
    )


@pytest.fixture
def ledger(six_claims):
    """Six-claim scenario plus the contradicts relation and its assessments."""
    session = six_claims.session
    env_id = six_claims.env_id

    relation = ClaimRelation(
        id=uuid4(),
        relation_key=build_relation_key(
            six_claims.a.id, six_claims.b.id, ClaimRelationKind.CONTRADICTS
        ),
        claim_id=six_claims.a.id,
        related_claim_id=six_claims.b.id,
        kind=ClaimRelationKind.CONTRADICTS,
        rationale="same asset attributed to two different owners",
        evidence_envelope_id=env_id,
        recorded_at=NOW.replace(minute=6),
    )
    insert_claim_relation_record(session, relation)

    unresolved = ClaimAssessment(
        id=uuid4(),
        assessment_key=build_assessment_key(
            relation.id, ClaimAssessmentKind.UNRESOLVED, NOW.replace(minute=7)
        ),
        kind=ClaimAssessmentKind.UNRESOLVED,
        rationale="both sources carry equal weight pending further evidence",
        evidence_envelope_id=env_id,
        relation_id=relation.id,
        recorded_at=NOW.replace(minute=7),
    )
    insert_claim_assessment_record(session, unresolved)

    withdrawn = ClaimAssessment(
        id=uuid4(),
        assessment_key=build_assessment_key(
            six_claims.c.id, ClaimAssessmentKind.WITHDRAWN, NOW.replace(minute=8)
        ),
        kind=ClaimAssessmentKind.WITHDRAWN,
        rationale="claim retracted by source",
        evidence_envelope_id=env_id,
        claim_id=six_claims.c.id,
        recorded_at=NOW.replace(minute=8),
    )
    insert_claim_assessment_record(session, withdrawn)

    insufficient = ClaimAssessment(
        id=uuid4(),
        assessment_key=build_assessment_key(
            six_claims.f.id, ClaimAssessmentKind.INSUFFICIENT_SUPPORT,
            NOW.replace(minute=9),
        ),
        kind=ClaimAssessmentKind.INSUFFICIENT_SUPPORT,
        rationale="only one low-confidence source",
        evidence_envelope_id=env_id,
        claim_id=six_claims.f.id,
        recorded_at=NOW.replace(minute=9),
    )
    insert_claim_assessment_record(session, insufficient)
    session.commit()

    return SimpleNamespace(
        session=session,
        subject=six_claims.subject,
        env_id=env_id,
        a=six_claims.a, b=six_claims.b, c=six_claims.c,
        d=six_claims.d, e=six_claims.e, f=six_claims.f,
        relation=relation,
        unresolved=unresolved, withdrawn=withdrawn, insufficient=insufficient,
    )


# ── claim inserts ───────────────────────────────────────────────────────────


class TestInsertClaimRecord:
    def test_ac_210_preserves_value_temporal_scope_and_source_card_version(
        self, session, envelope
    ) -> None:
        value = {
            "owner": "alice",
            "unicode": "\U0001F3E1",
            "nested": {"coords": [47.6, -122.3], "tags": ["a", "b"], "none": None},
        }
        value_json = _canonical_json(value)
        claim = envelope.make_claim(
            predicate="owns_asset",
            value=value,
            observed_at=NOW.replace(hour=9, minute=15),
            recorded_at=NOW.replace(hour=10),
            valid_from=NOW.replace(hour=8),
            valid_to=NOW.replace(hour=23),
            source_card_id="card-v7",
            source_card_version=7,
        )
        insert_claim_record(session, claim)
        session.commit()

        row = session.get(ForensicClaimRecord, claim.id)
        assert row is not None
        assert row.observed_value_json == value_json
        assert len(row.observed_value_json) == len(value_json)
        assert row.value_digest == _digest(value_json)
        assert _utc(row.observed_at) == claim.observed_at
        assert _utc(row.valid_from) == claim.valid_from
        assert _utc(row.valid_to) == claim.valid_to
        assert row.source_card_id == "card-v7"
        assert row.source_card_version == 7
        assert row.subject_entity_key == envelope.subject
        assert row.predicate == "owns_asset"
        assert row.evidence_envelope_id == envelope.env_id
        assert row.claim_key == claim.claim_key
        assert row.recorded_at.replace(tzinfo=timezone.utc) == claim.recorded_at

        loaded = load_claims_for_subject(session, envelope.subject)
        assert len(loaded) == 1
        assert loaded[0].observed_value_json == value_json
        assert loaded[0].source_card_version == 7

    def test_missing_evidence_envelope_raises_before_any_integrity_error(
        self, session, envelope
    ) -> None:
        claim = replace(envelope.make_claim(), evidence_envelope_id=uuid4())
        with pytest.raises(ForensicObservationError) as exc:
            insert_claim_record(session, claim)
        assert exc.value.code == "forensic_claim_missing_evidence"
        # the session must still be usable: no half-applied flush happened
        good = envelope.make_claim()
        insert_claim_record(session, good)
        session.commit()
        assert session.get(ForensicClaimRecord, good.id) is not None

    def test_invalid_claim_key_is_rejected(self, session, envelope) -> None:
        claim = _raw_claim(
            id_=uuid4(),
            claim_key="not-a-hex-key" * 8,
            subject=envelope.subject,
            predicate="owns_asset",
            value_json=_canonical_json({"owner": "alice"}),
            observed_at=NOW,
            evidence_envelope_id=envelope.env_id,
            recorded_at=NOW,
        )
        with pytest.raises(ForensicObservationError) as exc:
            insert_claim_record(session, claim)
        assert exc.value.code == "forensic_claim_invalid_key"

    def test_second_correction_of_same_target_is_supersession_fork(
        self, session, envelope
    ) -> None:
        target = envelope.make_claim(predicate="fork_target", value={"note": "old"})
        insert_claim_record(session, target)
        first_correction = envelope.make_claim(
            predicate="fork_target",
            value={"note": "new"},
            recorded_at=NOW.replace(minute=1),
            supersedes_id=target.id,
            supersedes_reason="first correction",
        )
        insert_claim_record(session, first_correction)
        second_correction = envelope.make_claim(
            predicate="fork_target",
            value={"note": "newer"},
            recorded_at=NOW.replace(minute=2),
            supersedes_id=target.id,
            supersedes_reason="second correction",
        )
        with pytest.raises(ForensicObservationError) as exc:
            insert_claim_record(session, second_correction)
        assert exc.value.code == "forensic_claim_supersession_fork"

    def test_superseding_claim_without_reason_is_missing_rationale(
        self, session, envelope
    ) -> None:
        target = envelope.make_claim()
        insert_claim_record(session, target)
        claim = _raw_claim(
            id_=uuid4(),
            claim_key=build_claim_key(
                envelope.subject, "owns_asset",
                _digest(_canonical_json({"note": "x"})), NOW,
                envelope.source_card_id, 1,
            ),
            subject=envelope.subject,
            predicate="owns_asset",
            value_json=_canonical_json({"note": "x"}),
            observed_at=NOW,
            evidence_envelope_id=envelope.env_id,
            recorded_at=NOW.replace(minute=1),
            supersedes_id=target.id,
        )
        with pytest.raises(ForensicObservationError) as exc:
            insert_claim_record(session, claim)
        assert exc.value.code == "forensic_claim_missing_rationale"

    def test_superseding_missing_target_is_invalid_key(
        self, session, envelope
    ) -> None:
        claim = envelope.make_claim(
            supersedes_id=uuid4(),
            supersedes_reason="corrects a claim that was never recorded",
        )
        with pytest.raises(ForensicObservationError) as exc:
            insert_claim_record(session, claim)
        assert exc.value.code == "forensic_claim_invalid_key"


# ── relation inserts ────────────────────────────────────────────────────────


class TestInsertRelationRecord:
    def _relation(
        self,
        *,
        claim_id: UUID,
        related_claim_id: UUID,
        kind: ClaimRelationKind = ClaimRelationKind.CONTRADICTS,
        rationale: str = "same asset attributed to two different owners",
        recorded_at: datetime = NOW,
        env_id: UUID,
    ) -> ClaimRelation:
        return ClaimRelation(
            id=uuid4(),
            relation_key=build_relation_key(claim_id, related_claim_id, kind),
            claim_id=claim_id,
            related_claim_id=related_claim_id,
            kind=kind,
            rationale=rationale,
            evidence_envelope_id=env_id,
            recorded_at=recorded_at,
        )

    def test_missing_claim_target_is_invalid_key(self, session, envelope) -> None:
        a = envelope.make_claim()
        insert_claim_record(session, a)
        missing = uuid4()
        relation = self._relation(
            claim_id=a.id, related_claim_id=missing, env_id=envelope.env_id
        )
        with pytest.raises(ForensicObservationError) as exc:
            insert_claim_relation_record(session, relation)
        assert exc.value.code == "forensic_claim_invalid_key"

    def test_duplicate_pair_and_kind_is_duplicate_relation(
        self, session, envelope
    ) -> None:
        a = envelope.make_claim()
        b = envelope.make_claim(
            value={"owner": "bob"}, observed_at=NOW.replace(minute=1)
        )
        insert_claim_record(session, a)
        insert_claim_record(session, b)
        insert_claim_relation_record(
            session, self._relation(claim_id=a.id, related_claim_id=b.id, env_id=envelope.env_id)
        )
        duplicate = self._relation(
            claim_id=a.id,
            related_claim_id=b.id,
            rationale="re-recorded duplicate",
            recorded_at=NOW.replace(minute=1),
            env_id=envelope.env_id,
        )
        with pytest.raises(ForensicObservationError) as exc:
            insert_claim_relation_record(session, duplicate)
        assert exc.value.code == "forensic_claim_relation_duplicate"

    def test_reversed_pair_is_detected_as_duplicate(self, session, envelope) -> None:
        a = envelope.make_claim()
        b = envelope.make_claim(
            value={"owner": "bob"}, observed_at=NOW.replace(minute=1)
        )
        insert_claim_record(session, a)
        insert_claim_record(session, b)
        insert_claim_relation_record(
            session, self._relation(claim_id=a.id, related_claim_id=b.id, env_id=envelope.env_id)
        )
        reversed_pair = self._relation(
            claim_id=b.id,
            related_claim_id=a.id,
            rationale="same pair, reversed argument order",
            recorded_at=NOW.replace(minute=1),
            env_id=envelope.env_id,
        )
        with pytest.raises(ForensicObservationError) as exc:
            insert_claim_relation_record(session, reversed_pair)
        assert exc.value.code == "forensic_claim_relation_duplicate"

    def test_self_relation_is_rejected_at_repository(self, session, envelope) -> None:
        a = envelope.make_claim()
        insert_claim_record(session, a)
        relation = _raw_relation(
            id_=uuid4(),
            relation_key="a" * 64,
            claim_id=a.id,
            related_claim_id=a.id,
            kind=ClaimRelationKind.CONTRADICTS,
            rationale="self link",
            evidence_envelope_id=envelope.env_id,
            recorded_at=NOW,
        )
        with pytest.raises(ForensicObservationError) as exc:
            insert_claim_relation_record(session, relation)
        assert exc.value.code == "forensic_claim_relation_self"

    def test_whitespace_rationale_is_missing_rationale(self, session, envelope) -> None:
        a = envelope.make_claim()
        b = envelope.make_claim(
            value={"owner": "bob"}, observed_at=NOW.replace(minute=1)
        )
        insert_claim_record(session, a)
        insert_claim_record(session, b)
        relation = _raw_relation(
            id_=uuid4(),
            relation_key="a" * 64,
            claim_id=a.id,
            related_claim_id=b.id,
            kind=ClaimRelationKind.CONTRADICTS,
            rationale="   ",
            evidence_envelope_id=envelope.env_id,
            recorded_at=NOW,
        )
        with pytest.raises(ForensicObservationError) as exc:
            insert_claim_relation_record(session, relation)
        assert exc.value.code == "forensic_claim_missing_rationale"

    def test_relation_missing_evidence_is_missing_evidence_error(
        self, session, envelope
    ) -> None:
        a = envelope.make_claim()
        b = envelope.make_claim(
            value={"owner": "bob"}, observed_at=NOW.replace(minute=1)
        )
        insert_claim_record(session, a)
        insert_claim_record(session, b)
        relation = self._relation(
            claim_id=a.id, related_claim_id=b.id, env_id=uuid4()
        )
        with pytest.raises(ForensicObservationError) as exc:
            insert_claim_relation_record(session, relation)
        assert exc.value.code == "forensic_claim_missing_evidence"

    def test_relation_supersession_fork(self, session, envelope) -> None:
        a = envelope.make_claim()
        b = envelope.make_claim(
            value={"owner": "bob"}, observed_at=NOW.replace(minute=1)
        )
        c = envelope.make_claim(
            predicate="third_claim", recorded_at=NOW.replace(minute=2)
        )
        for claim in (a, b, c):
            insert_claim_record(session, claim)

        original = self._relation(
            claim_id=a.id, related_claim_id=b.id, env_id=envelope.env_id
        )
        insert_claim_relation_record(session, original)
        correction = self._relation(
            claim_id=b.id,
            related_claim_id=a.id,
            kind=ClaimRelationKind.COMPATIBLE,
            rationale="reassessed as compatible",
            recorded_at=NOW.replace(minute=1),
            env_id=envelope.env_id,
        )
        superseding = ClaimRelation(
            id=uuid4(),
            relation_key=correction.relation_key,
            claim_id=correction.claim_id,
            related_claim_id=correction.related_claim_id,
            kind=ClaimRelationKind.COMPATIBLE,
            rationale=correction.rationale,
            evidence_envelope_id=correction.evidence_envelope_id,
            recorded_at=correction.recorded_at,
            supersedes_id=original.id,
            supersedes_reason="corrected relation kind",
        )
        insert_claim_relation_record(session, superseding)

        fork = self._relation(
            claim_id=a.id,
            related_claim_id=c.id,
            rationale="second correction of the original",
            recorded_at=NOW.replace(minute=2),
            env_id=envelope.env_id,
        )
        fork_correction = ClaimRelation(
            id=uuid4(),
            relation_key=fork.relation_key,
            claim_id=fork.claim_id,
            related_claim_id=fork.related_claim_id,
            kind=fork.kind,
            rationale=fork.rationale,
            evidence_envelope_id=fork.evidence_envelope_id,
            recorded_at=fork.recorded_at,
            supersedes_id=original.id,
            supersedes_reason="second correction",
        )
        with pytest.raises(ForensicObservationError) as exc:
            insert_claim_relation_record(session, fork_correction)
        assert exc.value.code == "forensic_claim_supersession_fork"

    def test_relation_superseding_missing_target_is_invalid_key(
        self, session, envelope
    ) -> None:
        a = envelope.make_claim()
        b = envelope.make_claim(
            value={"owner": "bob"}, observed_at=NOW.replace(minute=1)
        )
        insert_claim_record(session, a)
        insert_claim_record(session, b)
        base = self._relation(
            claim_id=a.id, related_claim_id=b.id, env_id=envelope.env_id
        )
        orphan_correction = ClaimRelation(
            id=uuid4(),
            relation_key=base.relation_key,
            claim_id=base.claim_id,
            related_claim_id=base.related_claim_id,
            kind=base.kind,
            rationale=base.rationale,
            evidence_envelope_id=base.evidence_envelope_id,
            recorded_at=base.recorded_at,
            supersedes_id=uuid4(),
            supersedes_reason="corrects a relation that was never recorded",
        )
        with pytest.raises(ForensicObservationError) as exc:
            insert_claim_relation_record(session, orphan_correction)
        assert exc.value.code == "forensic_claim_invalid_key"


# ── assessment inserts ──────────────────────────────────────────────────────


class TestInsertAssessmentRecord:
    def test_missing_claim_scope_target_is_invalid_key(self, session, envelope) -> None:
        missing = uuid4()
        assessment = ClaimAssessment(
            id=uuid4(),
            assessment_key=build_assessment_key(
                missing, ClaimAssessmentKind.WITHDRAWN, NOW
            ),
            kind=ClaimAssessmentKind.WITHDRAWN,
            rationale="claim retracted by source",
            evidence_envelope_id=envelope.env_id,
            claim_id=missing,
            recorded_at=NOW,
        )
        with pytest.raises(ForensicObservationError) as exc:
            insert_claim_assessment_record(session, assessment)
        assert exc.value.code == "forensic_claim_invalid_key"

    def test_missing_relation_scope_target_is_invalid_key(self, session, envelope) -> None:
        missing = uuid4()
        assessment = ClaimAssessment(
            id=uuid4(),
            assessment_key=build_assessment_key(
                missing, ClaimAssessmentKind.UNRESOLVED, NOW
            ),
            kind=ClaimAssessmentKind.UNRESOLVED,
            rationale="pending further evidence",
            evidence_envelope_id=envelope.env_id,
            relation_id=missing,
            recorded_at=NOW,
        )
        with pytest.raises(ForensicObservationError) as exc:
            insert_claim_assessment_record(session, assessment)
        assert exc.value.code == "forensic_claim_invalid_key"

    def test_invalid_assessment_key_is_rejected(self, session, envelope) -> None:
        assessment = _raw_assessment(
            id_=uuid4(),
            assessment_key="not-hex",
            kind=ClaimAssessmentKind.WITHDRAWN,
            rationale="claim retracted by source",
            evidence_envelope_id=envelope.env_id,
            recorded_at=NOW,
            claim_id=uuid4(),
        )
        with pytest.raises(ForensicObservationError) as exc:
            insert_claim_assessment_record(session, assessment)
        assert exc.value.code == "forensic_claim_invalid_key"

    def test_claim_scoped_kind_on_relation_scope_is_scope_mismatch(
        self, session, six_claims
    ) -> None:
        relation = ClaimRelation(
            id=uuid4(),
            relation_key=build_relation_key(
                six_claims.a.id, six_claims.b.id, ClaimRelationKind.CONTRADICTS
            ),
            claim_id=six_claims.a.id,
            related_claim_id=six_claims.b.id,
            kind=ClaimRelationKind.CONTRADICTS,
            rationale="same asset attributed to two different owners",
            evidence_envelope_id=six_claims.env_id,
            recorded_at=NOW,
        )
        insert_claim_relation_record(session, relation)
        assessment = _raw_assessment(
            id_=uuid4(),
            assessment_key=build_assessment_key(
                relation.id, ClaimAssessmentKind.WITHDRAWN, NOW
            ),
            kind=ClaimAssessmentKind.WITHDRAWN,
            rationale="claim-scoped kind on relation scope",
            evidence_envelope_id=six_claims.env_id,
            recorded_at=NOW,
            relation_id=relation.id,
        )
        with pytest.raises(ForensicObservationError) as exc:
            insert_claim_assessment_record(session, assessment)
        assert exc.value.code == "forensic_claim_assessment_scope_mismatch"

    def test_relation_scoped_kind_on_claim_scope_is_scope_mismatch(
        self, session, six_claims
    ) -> None:
        assessment = _raw_assessment(
            id_=uuid4(),
            assessment_key=build_assessment_key(
                six_claims.a.id, ClaimAssessmentKind.RESOLVED_COMPATIBLE, NOW
            ),
            kind=ClaimAssessmentKind.RESOLVED_COMPATIBLE,
            rationale="relation-scoped kind on claim scope",
            evidence_envelope_id=six_claims.env_id,
            recorded_at=NOW,
            claim_id=six_claims.a.id,
        )
        with pytest.raises(ForensicObservationError) as exc:
            insert_claim_assessment_record(session, assessment)
        assert exc.value.code == "forensic_claim_assessment_scope_mismatch"

    def test_whitespace_rationale_is_missing_rationale(self, session, six_claims) -> None:
        assessment = _raw_assessment(
            id_=uuid4(),
            assessment_key=build_assessment_key(
                six_claims.a.id, ClaimAssessmentKind.WITHDRAWN, NOW
            ),
            kind=ClaimAssessmentKind.WITHDRAWN,
            rationale="  ",
            evidence_envelope_id=six_claims.env_id,
            recorded_at=NOW,
            claim_id=six_claims.a.id,
        )
        with pytest.raises(ForensicObservationError) as exc:
            insert_claim_assessment_record(session, assessment)
        assert exc.value.code == "forensic_claim_missing_rationale"

    def test_assessment_supersession_fork(self, session, envelope) -> None:
        a = envelope.make_claim()
        b = envelope.make_claim(
            value={"owner": "bob"}, observed_at=NOW.replace(minute=1)
        )
        insert_claim_record(session, a)
        insert_claim_record(session, b)
        relation = ClaimRelation(
            id=uuid4(),
            relation_key=build_relation_key(a.id, b.id, ClaimRelationKind.CONTRADICTS),
            claim_id=a.id,
            related_claim_id=b.id,
            kind=ClaimRelationKind.CONTRADICTS,
            rationale="same asset attributed to two different owners",
            evidence_envelope_id=envelope.env_id,
            recorded_at=NOW.replace(minute=2),
        )
        insert_claim_relation_record(session, relation)

        first = ClaimAssessment(
            id=uuid4(),
            assessment_key=build_assessment_key(
                relation.id, ClaimAssessmentKind.UNRESOLVED, NOW.replace(minute=3)
            ),
            kind=ClaimAssessmentKind.UNRESOLVED,
            rationale="both sources carry equal weight",
            evidence_envelope_id=envelope.env_id,
            relation_id=relation.id,
            recorded_at=NOW.replace(minute=3),
        )
        insert_claim_assessment_record(session, first)
        second = ClaimAssessment(
            id=uuid4(),
            assessment_key=build_assessment_key(
                relation.id, ClaimAssessmentKind.RESOLVED_COMPATIBLE,
                NOW.replace(minute=4),
            ),
            kind=ClaimAssessmentKind.RESOLVED_COMPATIBLE,
            rationale="later evidence reconciles the pair",
            evidence_envelope_id=envelope.env_id,
            relation_id=relation.id,
            recorded_at=NOW.replace(minute=4),
            supersedes_id=first.id,
            supersedes_reason="later evidence reconciles the pair",
        )
        insert_claim_assessment_record(session, second)
        fork = ClaimAssessment(
            id=uuid4(),
            assessment_key=build_assessment_key(
                relation.id, ClaimAssessmentKind.AMBIGUOUS, NOW.replace(minute=5)
            ),
            kind=ClaimAssessmentKind.AMBIGUOUS,
            rationale="second correction of the same assessment",
            evidence_envelope_id=envelope.env_id,
            relation_id=relation.id,
            recorded_at=NOW.replace(minute=5),
            supersedes_id=first.id,
            supersedes_reason="second correction",
        )
        with pytest.raises(ForensicObservationError) as exc:
            insert_claim_assessment_record(session, fork)
        assert exc.value.code == "forensic_claim_supersession_fork"

    def test_assessment_superseding_missing_target_is_invalid_key(
        self, session, envelope
    ) -> None:
        a = envelope.make_claim()
        insert_claim_record(session, a)
        orphan_correction = ClaimAssessment(
            id=uuid4(),
            assessment_key=build_assessment_key(
                a.id, ClaimAssessmentKind.WITHDRAWN, NOW.replace(minute=1)
            ),
            kind=ClaimAssessmentKind.WITHDRAWN,
            rationale="corrects an assessment that was never recorded",
            evidence_envelope_id=envelope.env_id,
            claim_id=a.id,
            recorded_at=NOW.replace(minute=1),
            supersedes_id=uuid4(),
            supersedes_reason="second thoughts about a ghost",
        )
        with pytest.raises(ForensicObservationError) as exc:
            insert_claim_assessment_record(session, orphan_correction)
        assert exc.value.code == "forensic_claim_invalid_key"


# ── loads (AC-211) ──────────────────────────────────────────────────────────


class TestLoads:
    def test_ac_211_loads_superseded_and_withdrawn_claims_in_deterministic_order(
        self, session, six_claims
    ) -> None:
        rows = load_claims_for_subject(session, six_claims.subject)
        assert len(rows) == 6
        assert [row.id for row in rows] == [
            six_claims.a.id, six_claims.b.id, six_claims.c.id,
            six_claims.d.id, six_claims.e.id, six_claims.f.id,
        ]
        present = {row.id for row in rows}
        assert six_claims.c.id in present  # withdrawn claim stays queryable
        assert six_claims.d.id in present  # superseded target stays queryable
        assert six_claims.e.id in present  # live correction
        assert {row.claim_key for row in rows} == {
            six_claims.a.claim_key, six_claims.b.claim_key, six_claims.c.claim_key,
            six_claims.d.claim_key, six_claims.e.claim_key, six_claims.f.claim_key,
        }

    def test_load_contradiction_state_missing_relation_is_invalid_key(
        self, session
    ) -> None:
        with pytest.raises(ForensicObservationError) as exc:
            load_contradiction_state(session, uuid4())
        assert exc.value.code == "forensic_claim_invalid_key"

    def test_load_subject_contradictions_returns_live_relations_with_states(
        self, session, ledger
    ) -> None:
        contradictions = load_subject_contradictions(session, ledger.subject)
        assert len(contradictions) == 1
        relation_row, state = contradictions[0]
        assert relation_row.id == ledger.relation.id
        assert relation_row.kind == ClaimRelationKind.CONTRADICTS.value
        assert state is ContradictionState.UNRESOLVED


# ── contradiction flow ──────────────────────────────────────────────────────


class TestContradictionFlow:
    def test_resolution_changes_state_but_supersedes_neither_claim(
        self, session, envelope
    ) -> None:
        a = envelope.make_claim(recorded_at=NOW.replace(minute=0))
        b = envelope.make_claim(
            value={"owner": "bob"},
            observed_at=NOW.replace(minute=1),
            recorded_at=NOW.replace(minute=1),
        )
        insert_claim_record(session, a)
        insert_claim_record(session, b)
        session.commit()
        relation = ClaimRelation(
            id=uuid4(),
            relation_key=build_relation_key(a.id, b.id, ClaimRelationKind.CONTRADICTS),
            claim_id=a.id,
            related_claim_id=b.id,
            kind=ClaimRelationKind.CONTRADICTS,
            rationale="same asset attributed to two different owners",
            evidence_envelope_id=envelope.env_id,
            recorded_at=NOW.replace(minute=2),
        )
        insert_claim_relation_record(session, relation)
        session.commit()
        assert load_contradiction_state(session, relation.id) is ContradictionState.UNRESOLVED

        unresolved = ClaimAssessment(
            id=uuid4(),
            assessment_key=build_assessment_key(
                relation.id, ClaimAssessmentKind.UNRESOLVED, NOW.replace(minute=3)
            ),
            kind=ClaimAssessmentKind.UNRESOLVED,
            rationale="both sources carry equal weight pending further evidence",
            evidence_envelope_id=envelope.env_id,
            relation_id=relation.id,
            recorded_at=NOW.replace(minute=3),
        )
        insert_claim_assessment_record(session, unresolved)
        session.commit()
        assert load_contradiction_state(session, relation.id) is ContradictionState.UNRESOLVED

        resolved = ClaimAssessment(
            id=uuid4(),
            assessment_key=build_assessment_key(
                relation.id, ClaimAssessmentKind.RESOLVED_COMPATIBLE,
                NOW.replace(minute=4),
            ),
            kind=ClaimAssessmentKind.RESOLVED_COMPATIBLE,
            rationale="later evidence reconciles the pair",
            evidence_envelope_id=envelope.env_id,
            relation_id=relation.id,
            recorded_at=NOW.replace(minute=4),
        )
        insert_claim_assessment_record(session, resolved)
        session.commit()
        assert (
            load_contradiction_state(session, relation.id)
            is ContradictionState.RESOLVED_COMPATIBLE
        )

        # resolution supersedes neither claim: both rows remain untouched
        count = (
            session.execute(select(func.count()).select_from(ForensicClaimRecord))
            .scalar_one()
        )
        assert count == 2
        rows = load_claims_for_subject(session, envelope.subject)
        assert {row.claim_key for row in rows} == {a.claim_key, b.claim_key}
        assert {row.observed_value_json for row in rows} == {
            a.observed_value_json, b.observed_value_json,
        }
        assert all(row.supersedes_id is None for row in rows)

    def test_superseded_assessment_is_ignored_by_state_derivation(
        self, session, envelope
    ) -> None:
        a = envelope.make_claim(recorded_at=NOW.replace(minute=0))
        b = envelope.make_claim(
            value={"owner": "bob"},
            observed_at=NOW.replace(minute=1),
            recorded_at=NOW.replace(minute=1),
        )
        insert_claim_record(session, a)
        insert_claim_record(session, b)
        relation = ClaimRelation(
            id=uuid4(),
            relation_key=build_relation_key(a.id, b.id, ClaimRelationKind.CONTRADICTS),
            claim_id=a.id,
            related_claim_id=b.id,
            kind=ClaimRelationKind.CONTRADICTS,
            rationale="same asset attributed to two different owners",
            evidence_envelope_id=envelope.env_id,
            recorded_at=NOW.replace(minute=2),
        )
        insert_claim_relation_record(session, relation)

        unresolved = ClaimAssessment(
            id=uuid4(),
            assessment_key=build_assessment_key(
                relation.id, ClaimAssessmentKind.UNRESOLVED, NOW.replace(minute=3)
            ),
            kind=ClaimAssessmentKind.UNRESOLVED,
            rationale="both sources carry equal weight",
            evidence_envelope_id=envelope.env_id,
            relation_id=relation.id,
            recorded_at=NOW.replace(minute=3),
        )
        insert_claim_assessment_record(session, unresolved)
        upheld = ClaimAssessment(
            id=uuid4(),
            assessment_key=build_assessment_key(
                relation.id, ClaimAssessmentKind.RESOLVED_UPHELD, NOW.replace(minute=4)
            ),
            kind=ClaimAssessmentKind.RESOLVED_UPHELD,
            rationale="later evidence upholds one side",
            evidence_envelope_id=envelope.env_id,
            relation_id=relation.id,
            recorded_at=NOW.replace(minute=4),
        )
        insert_claim_assessment_record(session, upheld)
        correction = ClaimAssessment(
            id=uuid4(),
            assessment_key=build_assessment_key(
                relation.id, ClaimAssessmentKind.AMBIGUOUS, NOW.replace(minute=5)
            ),
            kind=ClaimAssessmentKind.AMBIGUOUS,
            rationale="correction: assessment was mis-recorded",
            evidence_envelope_id=envelope.env_id,
            relation_id=relation.id,
            recorded_at=NOW.replace(minute=5),
            supersedes_id=upheld.id,
            supersedes_reason="assessment was mis-recorded",
        )
        insert_claim_assessment_record(session, correction)
        session.commit()
        # the correcting assessment is excluded from derivation; the latest
        # live (non-correcting) assessment wins
        assert (
            load_contradiction_state(session, relation.id)
            is ContradictionState.RESOLVED_UPHELD
        )


# ── projection eligibility (AC-212) ─────────────────────────────────────────


class TestProjectionEligibility:
    def test_ac_212_six_claim_scenario_exact_eligibility_tuples(
        self, session, ledger
    ) -> None:
        eligibilities = evaluate_subject_projection_eligibility(session, ledger.subject)
        assert [eligibility.claim_id for eligibility in eligibilities] == [
            ledger.a.id, ledger.b.id, ledger.c.id,
            ledger.d.id, ledger.e.id, ledger.f.id,
        ]
        expected = [
            (True, None, ContradictionState.UNRESOLVED),
            (True, None, ContradictionState.UNRESOLVED),
            (False, "forensic_claim_withdrawn_hold", None),
            (False, "forensic_claim_superseded_hold", None),
            (True, None, None),
            (False, "forensic_claim_insufficient_support_hold", None),
        ]
        assert [
            (e.eligible, e.hold_code, e.contradiction_state)
            for e in eligibilities
        ] == expected

    def test_ac_212_contradicted_pair_is_never_collapsed(self, session, ledger) -> None:
        eligibilities = evaluate_subject_projection_eligibility(session, ledger.subject)
        by_id = {eligibility.claim_id: eligibility for eligibility in eligibilities}
        assert by_id[ledger.a.id].eligible is True
        assert by_id[ledger.b.id].eligible is True
        assert by_id[ledger.a.id].contradiction_state is ContradictionState.UNRESOLVED
        assert by_id[ledger.b.id].contradiction_state is ContradictionState.UNRESOLVED
        assert len(eligibilities) == 6
        contradicted_eligible = [
            e for e in eligibilities if e.eligible and e.contradiction_state is not None
        ]
        assert len(contradicted_eligible) == 2
        assert {e.claim_id for e in contradicted_eligible} == {ledger.a.id, ledger.b.id}

    def test_resolved_contradiction_keeps_both_claims_eligible_with_resolved_state(
        self, session, ledger
    ) -> None:
        resolved = ClaimAssessment(
            id=uuid4(),
            assessment_key=build_assessment_key(
                ledger.relation.id, ClaimAssessmentKind.RESOLVED_COMPATIBLE,
                NOW.replace(minute=10),
            ),
            kind=ClaimAssessmentKind.RESOLVED_COMPATIBLE,
            rationale="later evidence reconciles the pair",
            evidence_envelope_id=ledger.env_id,
            relation_id=ledger.relation.id,
            recorded_at=NOW.replace(minute=10),
        )
        insert_claim_assessment_record(session, resolved)
        session.commit()
        eligibilities = evaluate_subject_projection_eligibility(session, ledger.subject)
        by_id = {eligibility.claim_id: eligibility for eligibility in eligibilities}
        assert by_id[ledger.a.id].contradiction_state is ContradictionState.RESOLVED_COMPATIBLE
        assert by_id[ledger.b.id].contradiction_state is ContradictionState.RESOLVED_COMPATIBLE
        assert by_id[ledger.a.id].eligible is True
        assert by_id[ledger.b.id].eligible is True
        assert by_id[ledger.c.id].hold_code == "forensic_claim_withdrawn_hold"


# ── append-only enforcement ─────────────────────────────────────────────────


class TestAppendOnlyEnforcement:
    @pytest.mark.parametrize(
        ("tablename", "id_attribute", "column"),
        [
            ("forensic_claim_records", "a", "observed_value_json"),
            ("forensic_claim_relation_records", "relation", "rationale"),
            ("forensic_claim_assessment_records", "unresolved", "rationale"),
        ],
    )
    def test_raw_update_is_rejected_by_trigger(
        self, session, ledger, tablename, id_attribute, column
    ) -> None:
        row_id = getattr(ledger, id_attribute).id
        with pytest.raises(IntegrityError, match="forensic records are append-only"):
            session.execute(
                text(
                    f"UPDATE {tablename} SET {column} = :value WHERE id = :rid"
                ),
                {"value": "tampered", "rid": row_id.hex},
            )
        session.rollback()

    @pytest.mark.parametrize(
        ("tablename", "id_attribute"),
        [
            ("forensic_claim_records", "a"),
            ("forensic_claim_relation_records", "relation"),
            ("forensic_claim_assessment_records", "unresolved"),
        ],
    )
    def test_raw_delete_is_rejected_by_trigger(
        self, session, ledger, tablename, id_attribute
    ) -> None:
        row_id = getattr(ledger, id_attribute).id
        with pytest.raises(IntegrityError, match="forensic records are append-only"):
            session.execute(
                text(f"DELETE FROM {tablename} WHERE id = :rid"),
                {"rid": row_id.hex},
            )
        session.rollback()

    @pytest.mark.parametrize(
        ("model", "id_attribute", "attr", "value"),
        [
            (ForensicClaimRecord, "a", "predicate", "tampered"),
            (ForensicClaimRelationRecord, "relation", "rationale", "tampered"),
            (ForensicClaimAssessmentRecord, "unresolved", "rationale", "tampered"),
        ],
    )
    def test_orm_attribute_mutation_is_rejected(
        self, session, ledger, model, id_attribute, attr, value
    ) -> None:
        row = session.get(model, getattr(ledger, id_attribute).id)
        setattr(row, attr, value)
        with pytest.raises(TypeError, match="append-only"):
            session.commit()
        session.rollback()

    @pytest.mark.parametrize(
        ("model", "id_attribute"),
        [
            (ForensicClaimRecord, "a"),
            (ForensicClaimRelationRecord, "relation"),
            (ForensicClaimAssessmentRecord, "unresolved"),
        ],
    )
    def test_orm_delete_is_rejected(
        self, session, ledger, model, id_attribute
    ) -> None:
        row = session.get(model, getattr(ledger, id_attribute).id)
        session.delete(row)
        with pytest.raises(TypeError, match="append-only"):
            session.commit()
        session.rollback()


# ── raw CHECK constraints ───────────────────────────────────────────────────


class TestCheckConstraints:
    def test_raw_insert_rejects_bad_predicate(self, session, envelope) -> None:
        with pytest.raises(IntegrityError):
            session.execute(
                text(
                    "INSERT INTO forensic_claim_records"
                    " (id, claim_key, subject_entity_key, predicate, observed_value_json,"
                    " value_digest, observed_at, source_card_id, source_card_version,"
                    " evidence_envelope_id, recorded_at)"
                    " VALUES (:id, :key, :subject, :predicate, :value_json, :digest,"
                    " :observed_at, :card, :version, :env, :recorded_at)"
                ),
                {
                    "id": uuid4().hex,
                    "key": "a" * 64,
                    "subject": envelope.subject,
                    "predicate": "Bad Predicate",
                    "value_json": _canonical_json({"owner": "x"}),
                    "digest": "a" * 64,
                    "observed_at": "2026-08-08 12:00:00",
                    "card": envelope.source_card_id,
                    "version": 1,
                    "env": envelope.env_id.hex,
                    "recorded_at": "2026-08-08 12:00:00",
                },
            )
        session.rollback()

    def test_raw_insert_rejects_superseding_without_reason(
        self, session, six_claims
    ) -> None:
        with pytest.raises(IntegrityError):
            session.execute(
                text(
                    "INSERT INTO forensic_claim_records"
                    " (id, claim_key, subject_entity_key, predicate, observed_value_json,"
                    " value_digest, observed_at, source_card_id, source_card_version,"
                    " evidence_envelope_id, recorded_at, supersedes_id, supersedes_reason)"
                    " VALUES (:id, :key, :subject, :predicate, :value_json, :digest,"
                    " :observed_at, :card, :version, :env, :recorded_at, :supersedes, NULL)"
                ),
                {
                    "id": uuid4().hex,
                    "key": "b" * 64,
                    "subject": six_claims.subject,
                    "predicate": "correction",
                    "value_json": _canonical_json({"note": "x"}),
                    "digest": "b" * 64,
                    "observed_at": "2026-08-08 12:00:00",
                    "card": "card-obs1968",
                    "version": 1,
                    "env": six_claims.env_id.hex,
                    "recorded_at": "2026-08-08 12:00:00",
                    "supersedes": six_claims.a.id.hex,
                },
            )
        session.rollback()

    def test_raw_insert_rejects_bogus_relation_kind(self, session, six_claims) -> None:
        with pytest.raises(IntegrityError):
            session.execute(
                text(
                    "INSERT INTO forensic_claim_relation_records"
                    " (id, relation_key, claim_id, related_claim_id, kind, rationale,"
                    " evidence_envelope_id, recorded_at)"
                    " VALUES (:id, :key, :cid, :rcid, :kind, :rationale, :env, :at)"
                ),
                {
                    "id": uuid4().hex,
                    "key": "c" * 64,
                    "cid": six_claims.a.id.hex,
                    "rcid": six_claims.b.id.hex,
                    "kind": "supercedes",
                    "rationale": "bogus kind",
                    "env": six_claims.env_id.hex,
                    "at": "2026-08-08 12:00:00",
                },
            )
        session.rollback()

    def test_raw_insert_rejects_empty_relation_rationale(
        self, session, six_claims
    ) -> None:
        with pytest.raises(IntegrityError):
            session.execute(
                text(
                    "INSERT INTO forensic_claim_relation_records"
                    " (id, relation_key, claim_id, related_claim_id, kind, rationale,"
                    " evidence_envelope_id, recorded_at)"
                    " VALUES (:id, :key, :cid, :rcid, :kind, :rationale, :env, :at)"
                ),
                {
                    "id": uuid4().hex,
                    "key": "d" * 64,
                    "cid": six_claims.a.id.hex,
                    "rcid": six_claims.b.id.hex,
                    "kind": "contradicts",
                    "rationale": "",
                    "env": six_claims.env_id.hex,
                    "at": "2026-08-08 12:00:00",
                },
            )
        session.rollback()

    def test_raw_insert_rejects_self_relation(self, session, six_claims) -> None:
        with pytest.raises(IntegrityError):
            session.execute(
                text(
                    "INSERT INTO forensic_claim_relation_records"
                    " (id, relation_key, claim_id, related_claim_id, kind, rationale,"
                    " evidence_envelope_id, recorded_at)"
                    " VALUES (:id, :key, :cid, :rcid, :kind, :rationale, :env, :at)"
                ),
                {
                    "id": uuid4().hex,
                    "key": "e" * 64,
                    "cid": six_claims.a.id.hex,
                    "rcid": six_claims.a.id.hex,
                    "kind": "contradicts",
                    "rationale": "self link",
                    "env": six_claims.env_id.hex,
                    "at": "2026-08-08 12:00:00",
                },
            )
        session.rollback()

    def test_raw_insert_rejects_both_scope_assessment(
        self, session, ledger
    ) -> None:
        with pytest.raises(IntegrityError):
            session.execute(
                text(
                    "INSERT INTO forensic_claim_assessment_records"
                    " (id, assessment_key, claim_id, relation_id, kind, rationale,"
                    " evidence_envelope_id, recorded_at)"
                    " VALUES (:id, :key, :cid, :rid, :kind, :rationale, :env, :at)"
                ),
                {
                    "id": uuid4().hex,
                    "key": "f" * 64,
                    "cid": ledger.a.id.hex,
                    "rid": ledger.relation.id.hex,
                    "kind": "withdrawn",
                    "rationale": "both scopes set",
                    "env": ledger.env_id.hex,
                    "at": "2026-08-08 12:00:00",
                },
            )
        session.rollback()

    def test_raw_insert_rejects_bogus_assessment_kind(
        self, session, ledger
    ) -> None:
        with pytest.raises(IntegrityError):
            session.execute(
                text(
                    "INSERT INTO forensic_claim_assessment_records"
                    " (id, assessment_key, claim_id, relation_id, kind, rationale,"
                    " evidence_envelope_id, recorded_at)"
                    " VALUES (:id, :key, :cid, :rid, :kind, :rationale, :env, :at)"
                ),
                {
                    "id": uuid4().hex,
                    "key": "0" * 64,
                    "cid": None,
                    "rid": ledger.relation.id.hex,
                    "kind": "made_up",
                    "rationale": "bogus kind",
                    "env": ledger.env_id.hex,
                    "at": "2026-08-08 12:00:00",
                },
            )
        session.rollback()

    def test_raw_insert_rejects_empty_assessment_rationale(
        self, session, ledger
    ) -> None:
        with pytest.raises(IntegrityError):
            session.execute(
                text(
                    "INSERT INTO forensic_claim_assessment_records"
                    " (id, assessment_key, claim_id, relation_id, kind, rationale,"
                    " evidence_envelope_id, recorded_at)"
                    " VALUES (:id, :key, :cid, :rid, :kind, :rationale, :env, :at)"
                ),
                {
                    "id": uuid4().hex,
                    "key": "1" * 64,
                    "cid": ledger.a.id.hex,
                    "rid": None,
                    "kind": "withdrawn",
                    "rationale": "",
                    "env": ledger.env_id.hex,
                    "at": "2026-08-08 12:00:00",
                },
            )
        session.rollback()


# ── error redaction ─────────────────────────────────────────────────────────


class TestErrorRedaction:
    def test_error_messages_never_embed_raw_values_or_rationale(
        self, session, envelope, six_claims
    ) -> None:
        # missing evidence: raw value must not leak
        claim = replace(
            envelope.make_claim(value={"payload": RAW_VALUE_PAYLOAD}),
            evidence_envelope_id=uuid4(),
        )
        with pytest.raises(ForensicObservationError) as exc:
            insert_claim_record(session, claim)
        assert exc.value.code == "forensic_claim_missing_evidence"
        assert RAW_VALUE_PAYLOAD not in str(exc.value)
        assert RAW_VALUE_PAYLOAD not in exc.value.safe_message

        # supersession fork: raw value must not leak
        target = envelope.make_claim(
            predicate="fork_target", value={"payload": RAW_VALUE_PAYLOAD}
        )
        insert_claim_record(session, target)
        first = envelope.make_claim(
            predicate="fork_target",
            value={"payload": RAW_VALUE_PAYLOAD},
            observed_at=NOW.replace(minute=1),
            recorded_at=NOW.replace(minute=1),
            supersedes_id=target.id,
            supersedes_reason="first correction",
        )
        insert_claim_record(session, first)
        second = envelope.make_claim(
            predicate="fork_target",
            value={"payload": RAW_VALUE_PAYLOAD},
            observed_at=NOW.replace(minute=2),
            recorded_at=NOW.replace(minute=2),
            supersedes_id=target.id,
            supersedes_reason="second correction",
        )
        with pytest.raises(ForensicObservationError) as exc:
            insert_claim_record(session, second)
        assert exc.value.code == "forensic_claim_supersession_fork"
        assert RAW_VALUE_PAYLOAD not in str(exc.value)

        # relation referencing a missing claim: raw rationale must not leak
        missing = uuid4()
        relation = ClaimRelation(
            id=uuid4(),
            relation_key=build_relation_key(
                six_claims.a.id, missing, ClaimRelationKind.CONTRADICTS
            ),
            claim_id=six_claims.a.id,
            related_claim_id=missing,
            kind=ClaimRelationKind.CONTRADICTS,
            rationale=RAW_RATIONALE_PAYLOAD,
            evidence_envelope_id=envelope.env_id,
            recorded_at=NOW,
        )
        with pytest.raises(ForensicObservationError) as exc:
            insert_claim_relation_record(session, relation)
        assert exc.value.code == "forensic_claim_invalid_key"
        assert RAW_RATIONALE_PAYLOAD not in str(exc.value)

        # duplicate relation: raw rationale must not leak
        first_relation = ClaimRelation(
            id=uuid4(),
            relation_key=build_relation_key(
                six_claims.a.id, six_claims.b.id, ClaimRelationKind.CONTRADICTS
            ),
            claim_id=six_claims.a.id,
            related_claim_id=six_claims.b.id,
            kind=ClaimRelationKind.CONTRADICTS,
            rationale="first recording",
            evidence_envelope_id=envelope.env_id,
            recorded_at=NOW,
        )
        insert_claim_relation_record(session, first_relation)
        duplicate = ClaimRelation(
            id=uuid4(),
            relation_key=build_relation_key(
                six_claims.a.id, six_claims.b.id, ClaimRelationKind.CONTRADICTS
            ),
            claim_id=six_claims.a.id,
            related_claim_id=six_claims.b.id,
            kind=ClaimRelationKind.CONTRADICTS,
            rationale=RAW_RATIONALE_PAYLOAD,
            evidence_envelope_id=envelope.env_id,
            recorded_at=NOW.replace(minute=1),
        )
        with pytest.raises(ForensicObservationError) as exc:
            insert_claim_relation_record(session, duplicate)
        assert exc.value.code == "forensic_claim_relation_duplicate"
        assert RAW_RATIONALE_PAYLOAD not in str(exc.value)

        # assessment missing scope target: raw rationale must not leak
        missing_assessment = ClaimAssessment(
            id=uuid4(),
            assessment_key=build_assessment_key(
                missing, ClaimAssessmentKind.WITHDRAWN, NOW
            ),
            kind=ClaimAssessmentKind.WITHDRAWN,
            rationale=RAW_RATIONALE_PAYLOAD,
            evidence_envelope_id=envelope.env_id,
            claim_id=missing,
            recorded_at=NOW,
        )
        with pytest.raises(ForensicObservationError) as exc:
            insert_claim_assessment_record(session, missing_assessment)
        assert exc.value.code == "forensic_claim_invalid_key"
        assert RAW_RATIONALE_PAYLOAD not in str(exc.value)

        # assessment supersession fork: raw rationale must not leak
        first_assessment = ClaimAssessment(
            id=uuid4(),
            assessment_key=build_assessment_key(
                first_relation.id, ClaimAssessmentKind.UNRESOLVED, NOW.replace(minute=2)
            ),
            kind=ClaimAssessmentKind.UNRESOLVED,
            rationale="both sources carry equal weight",
            evidence_envelope_id=envelope.env_id,
            relation_id=first_relation.id,
            recorded_at=NOW.replace(minute=2),
        )
        insert_claim_assessment_record(session, first_assessment)
        second_assessment = ClaimAssessment(
            id=uuid4(),
            assessment_key=build_assessment_key(
                first_relation.id, ClaimAssessmentKind.RESOLVED_COMPATIBLE,
                NOW.replace(minute=3),
            ),
            kind=ClaimAssessmentKind.RESOLVED_COMPATIBLE,
            rationale="later evidence reconciles the pair",
            evidence_envelope_id=envelope.env_id,
            relation_id=first_relation.id,
            recorded_at=NOW.replace(minute=3),
            supersedes_id=first_assessment.id,
            supersedes_reason="later evidence reconciles the pair",
        )
        insert_claim_assessment_record(session, second_assessment)
        fork_assessment = ClaimAssessment(
            id=uuid4(),
            assessment_key=build_assessment_key(
                first_relation.id, ClaimAssessmentKind.AMBIGUOUS, NOW.replace(minute=4)
            ),
            kind=ClaimAssessmentKind.AMBIGUOUS,
            rationale=RAW_RATIONALE_PAYLOAD,
            evidence_envelope_id=envelope.env_id,
            relation_id=first_relation.id,
            recorded_at=NOW.replace(minute=4),
            supersedes_id=first_assessment.id,
            supersedes_reason="second correction",
        )
        with pytest.raises(ForensicObservationError) as exc:
            insert_claim_assessment_record(session, fork_assessment)
        assert exc.value.code == "forensic_claim_supersession_fork"
        assert RAW_RATIONALE_PAYLOAD not in str(exc.value)
