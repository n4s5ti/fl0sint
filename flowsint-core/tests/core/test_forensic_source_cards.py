"""Tests for forensic source-card authority contract (B4/OBS-1965)."""

import pytest
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from flowsint_core.core.forensics.source_cards import (
    ArtifactLocatorMode,
    ArtifactLocatorPolicy,
    CollectionMethod,
    IndependenceAssessment,
    IndependenceRelationship,
    SourceCard,
    SourceCardFamily,
    SourceFamily,
    SourceIndependenceError,
    SourceReference,
    SourceUpstream,
    require_distinct_sources,
    revise_source_card,
    revise_source_family,
)


NOW = datetime(2026, 8, 9, tzinfo=timezone.utc)
FUTURE = NOW + timedelta(days=30)
EV = frozenset({uuid4()})


# ── Helpers ───────────────────────────────────────────────────────────────────


def _source_card(
    *,
    label: str = "example.com",
    publisher_origin: str = "Example Corp",
    collection_method: CollectionMethod = CollectionMethod.CONNECTOR,
    jurisdiction: str | None = "US-CA",
    scope: str | None = None,
    rights: frozenset[str] = frozenset({"permitted"}),
    reliability_context: str | None = "well-known news outlet",
    artifact_locator_policy: ArtifactLocatorPolicy | None = None,
    created_by_subject: str = "subject:test-operator",
) -> SourceCard:
    if artifact_locator_policy is None:
        artifact_locator_policy = ArtifactLocatorPolicy(
            mode=ArtifactLocatorMode.EXACT, expression="https://example.com",
        )
    return SourceCard(
        card_id=uuid4(),
        version=1,
        card_revision_of=None,
        label=label,
        publisher_origin=publisher_origin,
        collection_method=collection_method,
        jurisdiction=jurisdiction,
        scope=scope,
        rights=rights,
        reliability_context=reliability_context,
        artifact_locator_policy=artifact_locator_policy,
        scope_effective_from=NOW,
        scope_effective_until=None,
        created_at=NOW,
        created_by_subject=created_by_subject,
    )


def _assessment(
    card_a: SourceCard,
    card_b: SourceCard,
    *,
    relationship: IndependenceRelationship = IndependenceRelationship.INDEPENDENT,
    evidence_refs: frozenset[UUID] | None = None,
) -> IndependenceAssessment:
    if evidence_refs is None:
        evidence_refs = EV if relationship is not IndependenceRelationship.UNKNOWN else frozenset()
    return IndependenceAssessment(
        assessment_id=uuid4(),
        card_a_id=card_a.card_id,
        card_b_id=card_b.card_id,
        relationship=relationship,
        evidence_rationale="distinct domains, different registrars",
        evidence_references=evidence_refs,
        effective_from=NOW,
        effective_until=None,
        assessed_by_subject="subject:test-analyst",
        assessed_at=NOW,
    )


# ── SourceCard ────────────────────────────────────────────────────────────────


class TestSourceCardConstruction:
    def test_creates_v1_card(self) -> None:
        card = _source_card()
        assert card.version == 1
        assert card.card_revision_of is None
        assert card.scope_effective_until is None

    def test_v1_must_not_have_revision(self) -> None:
        with pytest.raises(ValueError, match="v1 cannot have card_revision_of"):
            SourceCard(
                card_id=uuid4(), version=1, card_revision_of=uuid4(),
                label="x", publisher_origin="x",
                collection_method=CollectionMethod.CONNECTOR,
                jurisdiction=None, scope=None, rights=frozenset({"a"}),
                reliability_context=None,
                artifact_locator_policy=ArtifactLocatorPolicy(
                    mode=ArtifactLocatorMode.EXACT, expression="https://example.com",
                ),
                scope_effective_from=NOW, scope_effective_until=None,
                created_at=NOW, created_by_subject="subject:x",
            )

    def test_v2_must_have_revision(self) -> None:
        with pytest.raises(ValueError, match="version 2 must have card_revision_of"):
            SourceCard(
                card_id=uuid4(), version=2, card_revision_of=None,
                label="x", publisher_origin="x",
                collection_method=CollectionMethod.CONNECTOR,
                jurisdiction=None, scope=None, rights=frozenset({"a"}),
                reliability_context=None,
                artifact_locator_policy=ArtifactLocatorPolicy(
                    mode=ArtifactLocatorMode.EXACT, expression="https://example.com",
                ),
                scope_effective_from=NOW, scope_effective_until=None,
                created_at=NOW, created_by_subject="subject:x",
            )

    def test_rejects_naive_datetime(self) -> None:
        with pytest.raises(TypeError, match="timezone-aware"):
            SourceCard(
                card_id=uuid4(), version=1, card_revision_of=None,
                label="x", publisher_origin="x",
                collection_method=CollectionMethod.CONNECTOR,
                jurisdiction=None, scope=None, rights=frozenset({"a"}),
                reliability_context=None,
                artifact_locator_policy=ArtifactLocatorPolicy(
                    mode=ArtifactLocatorMode.EXACT, expression="https://example.com",
                ),
                scope_effective_from=datetime(2026, 8, 9),
                scope_effective_until=None,
                created_at=NOW, created_by_subject="subject:x",
            )

    def test_rejects_scope_until_before_from(self) -> None:
        with pytest.raises(ValueError, match="after scope_effective_from"):
            SourceCard(
                card_id=uuid4(), version=1, card_revision_of=None,
                label="x", publisher_origin="x",
                collection_method=CollectionMethod.CONNECTOR,
                jurisdiction=None, scope=None, rights=frozenset({"a"}),
                reliability_context=None,
                artifact_locator_policy=ArtifactLocatorPolicy(
                    mode=ArtifactLocatorMode.EXACT, expression="https://example.com",
                ),
                scope_effective_from=NOW,
                scope_effective_until=NOW - timedelta(days=1),
                created_at=NOW, created_by_subject="subject:x",
            )

    def test_optional_fields_can_be_none(self) -> None:
        card = _source_card(jurisdiction=None, scope=None, reliability_context=None)
        assert card.jurisdiction is None
        assert card.scope is None
        assert card.reliability_context is None


class TestSourceCardRevision:
    def test_creates_new_version(self) -> None:
        v1 = _source_card()
        closed, v2 = revise_source_card(v1, revised_by_subject="subject:editor")
        assert closed.scope_effective_until is not None
        assert v2.version == 2
        assert v2.card_revision_of == v1.card_id
        assert v2.card_id != v1.card_id
        assert v2.scope_effective_until is None

    def test_preserves_unchanged_fields(self) -> None:
        v1 = _source_card(label="my-source")
        closed, v2 = revise_source_card(
            v1,
            publisher_origin="new-publisher",
            revised_by_subject="subject:editor",
        )
        assert v2.label == "my-source"
        assert v2.publisher_origin == "new-publisher"

    def test_requires_revised_by_subject(self) -> None:
        v1 = _source_card()
        with pytest.raises(ValueError, match="revised_by_subject"):
            revise_source_card(v1)


# ── SourceFamily ──────────────────────────────────────────────────────────────


class TestSourceFamily:
    def test_creates_family(self) -> None:
        fid = uuid4()
        family = SourceFamily(
            family_id=fid,
            version=1,
            family_revision_of=None,
            label="Syndicated Content",
            rationale="all syndicated from AP wire",
            scope_effective_from=NOW,
            scope_effective_until=None,
            created_at=NOW,
            created_by_subject="subject:analyst",
        )
        assert family.family_id == fid
        assert family.label == "Syndicated Content"

    def test_rejects_empty_label(self) -> None:
        with pytest.raises(TypeError, match="non-empty string"):
            SourceFamily(
                family_id=uuid4(), version=1, family_revision_of=None,
                label="  ",
                rationale="x", scope_effective_from=NOW,
                scope_effective_until=None,
                created_at=NOW, created_by_subject="s",
            )

    def test_links_card_to_family(self) -> None:
        link = SourceCardFamily(
            card_id=uuid4(), family_id=uuid4(),
            family_version=1, joined_at=NOW, removed_at=None,
        )
        assert isinstance(link.card_id, UUID)


# ── SourceReference ───────────────────────────────────────────────────────────


class TestSourceReference:
    def test_exactly_one_of_card_or_reason(self) -> None:
        with pytest.raises(ValueError, match="exactly one"):
            SourceReference(
                envelope_id=uuid4(),
                source_card_id=None, source_card_version=None,
                missing_source_reason=None,
                referenced_at=NOW, referenced_by_subject="subject:x",
            )

    def test_card_ref_requires_version(self) -> None:
        with pytest.raises(ValueError, match="must be >= 1"):
            SourceReference(
                envelope_id=uuid4(),
                source_card_id=uuid4(), source_card_version=None,
                missing_source_reason=None,
                referenced_at=NOW, referenced_by_subject="subject:x",
            )

    def test_valid_card_reference(self) -> None:
        ref = SourceReference(
            envelope_id=uuid4(),
            source_card_id=uuid4(), source_card_version=1,
            missing_source_reason=None,
            referenced_at=NOW, referenced_by_subject="subject:x",
        )
        assert ref.missing_source_reason is None

    def test_valid_missing_reason(self) -> None:
        ref = SourceReference(
            envelope_id=uuid4(),
            source_card_id=None, source_card_version=None,
            missing_source_reason="no source card available for this connector",
            referenced_at=NOW, referenced_by_subject="subject:x",
        )
        assert ref.source_card_id is None


# ── IndependenceAssessment ───────────────────────────────────────────────────


class TestIndependenceAssessment:
    def test_creates_assessment(self) -> None:
        card_a = _source_card()
        card_b = _source_card()
        assessment = _assessment(card_a, card_b)
        assert assessment.relationship is IndependenceRelationship.INDEPENDENT

    def test_rejects_self_assessment(self) -> None:
        card = _source_card()
        with pytest.raises(ValueError, match="cannot assess a source against itself"):
            _assessment(card, card)

    def test_rejects_empty_rationale(self) -> None:
        card_a = _source_card()
        card_b = _source_card()
        with pytest.raises(TypeError, match="non-empty string"):
            IndependenceAssessment(
                assessment_id=uuid4(),
                card_a_id=card_a.card_id, card_b_id=card_b.card_id,
                relationship=IndependenceRelationship.INDEPENDENT,
                evidence_rationale="  ",
                evidence_references=EV,
                effective_from=NOW, effective_until=None,
                assessed_by_subject="subject:x", assessed_at=NOW,
            )

    def test_all_relationship_values(self) -> None:
        card_a = _source_card()
        card_b = _source_card()
        for rel in IndependenceRelationship:
            refs = EV if rel is not IndependenceRelationship.UNKNOWN else frozenset()
            assessment = IndependenceAssessment(
                assessment_id=uuid4(),
                card_a_id=card_a.card_id, card_b_id=card_b.card_id,
                relationship=rel,
                evidence_rationale="x",
                evidence_references=refs,
                effective_from=NOW, effective_until=None,
                assessed_by_subject="subject:x", assessed_at=NOW,
            )
            assert assessment.relationship is rel


# ── require_distinct_sources ─────────────────────────────────────────────────


class TestRequireDistinctSources:
    def test_independent_passes(self) -> None:
        card_a = _source_card()
        card_b = _source_card()
        assessment = _assessment(card_a, card_b)
        require_distinct_sources(card_a, card_b, assessment, evaluated_at=NOW)

    def test_same_source_raises(self) -> None:
        card = _source_card()
        assessment = IndependenceAssessment(
            assessment_id=uuid4(),
            card_a_id=card.card_id, card_b_id=uuid4(),
            relationship=IndependenceRelationship.INDEPENDENT,
            evidence_rationale="x",
            evidence_references=EV,
            effective_from=NOW, effective_until=None,
            assessed_by_subject="subject:x", assessed_at=NOW,
        )
        with pytest.raises(SourceIndependenceError, match="same source"):
            require_distinct_sources(card, card, assessment, evaluated_at=NOW)

    def test_related_raises(self) -> None:
        card_a = _source_card()
        card_b = _source_card()
        assessment = _assessment(card_a, card_b, relationship=IndependenceRelationship.RELATED)
        with pytest.raises(SourceIndependenceError, match="RELATED"):
            require_distinct_sources(card_a, card_b, assessment, evaluated_at=NOW)

    def test_common_upstream_raises(self) -> None:
        card_a = _source_card()
        card_b = _source_card()
        assessment = _assessment(card_a, card_b, relationship=IndependenceRelationship.COMMON_UPSTREAM)
        with pytest.raises(SourceIndependenceError, match="COMMON_UPSTREAM"):
            require_distinct_sources(card_a, card_b, assessment, evaluated_at=NOW)

    def test_unknown_raises(self) -> None:
        card_a = _source_card()
        card_b = _source_card()
        assessment = _assessment(card_a, card_b, relationship=IndependenceRelationship.UNKNOWN)
        with pytest.raises(SourceIndependenceError, match="UNKNOWN"):
            require_distinct_sources(card_a, card_b, assessment, evaluated_at=NOW)

    def test_mismatched_assessment_raises(self) -> None:
        card_a = _source_card()
        card_b = _source_card()
        card_c = _source_card()
        assessment = _assessment(card_a, card_c)
        with pytest.raises(SourceIndependenceError, match="does not reference"):
            require_distinct_sources(card_a, card_b, assessment, evaluated_at=NOW)

    def test_future_assessment_raises(self) -> None:
        card_a = _source_card()
        card_b = _source_card()
        assessment = IndependenceAssessment(
            assessment_id=uuid4(),
            card_a_id=card_a.card_id, card_b_id=card_b.card_id,
            relationship=IndependenceRelationship.INDEPENDENT,
            evidence_rationale="x",
            evidence_references=EV,
            effective_from=FUTURE, effective_until=None,
            assessed_by_subject="subject:x", assessed_at=NOW,
        )
        with pytest.raises(SourceIndependenceError, match="not yet effective"):
            require_distinct_sources(card_a, card_b, assessment, evaluated_at=NOW)

    def test_expired_assessment_raises(self) -> None:
        card_a = _source_card()
        card_b = _source_card()
        assessment = IndependenceAssessment(
            assessment_id=uuid4(),
            card_a_id=card_a.card_id, card_b_id=card_b.card_id,
            relationship=IndependenceRelationship.INDEPENDENT,
            evidence_rationale="x",
            evidence_references=EV,
            effective_from=NOW - timedelta(days=30),
            effective_until=NOW - timedelta(days=1),
            assessed_by_subject="subject:x", assessed_at=NOW,
        )
        with pytest.raises(SourceIndependenceError, match="has expired"):
            require_distinct_sources(card_a, card_b, assessment, evaluated_at=NOW)

    def test_error_fields(self) -> None:
        card_a = _source_card()
        card_b = _source_card()
        assessment = _assessment(card_a, card_b, relationship=IndependenceRelationship.RELATED)
        with pytest.raises(SourceIndependenceError) as exc_info:
            require_distinct_sources(card_a, card_b, assessment, evaluated_at=NOW)
        assert exc_info.value.card_a_id == card_a.card_id
        assert exc_info.value.card_b_id == card_b.card_id
        assert exc_info.value.assessment_id == assessment.assessment_id
        assert "RELATED" in exc_info.value.reason


# ── SourceUpstream ────────────────────────────────────────────────────────────


class TestSourceUpstream:
    def test_creates_upstream_edge(self) -> None:
        upstream = _source_card()
        downstream = _source_card()
        edge = SourceUpstream(
            upstream_edge_id=uuid4(),
            upstream_card_id=upstream.card_id,
            downstream_card_id=downstream.card_id,
            evidence_rationale="republishes content from upstream",
            evidence_references=EV,
            effective_from=NOW,
            effective_until=None,
            assessed_by_subject="subject:analyst",
            assessed_at=NOW,
        )
        assert edge.upstream_card_id == upstream.card_id

    def test_rejects_self_upstream(self) -> None:
        card = _source_card()
        with pytest.raises(ValueError, match="upstream of itself"):
            SourceUpstream(
                upstream_edge_id=uuid4(),
                upstream_card_id=card.card_id,
                downstream_card_id=card.card_id,
                evidence_rationale="x",
                evidence_references=EV,
                effective_from=NOW,
                effective_until=None,
                assessed_by_subject="subject:x",
                assessed_at=NOW,
            )

    def test_requires_evidence(self) -> None:
        card_a = _source_card()
        card_b = _source_card()
        with pytest.raises(ValueError, match="must not be empty"):
            SourceUpstream(
                upstream_edge_id=uuid4(),
                upstream_card_id=card_a.card_id,
                downstream_card_id=card_b.card_id,
                evidence_rationale="x",
                evidence_references=frozenset(),
                effective_from=NOW,
                effective_until=None,
                assessed_by_subject="subject:x",
                assessed_at=NOW,
            )

    def test_rejects_naive_datetime(self) -> None:
        card_a = _source_card()
        card_b = _source_card()
        with pytest.raises(TypeError, match="timezone-aware"):
            SourceUpstream(
                upstream_edge_id=uuid4(),
                upstream_card_id=card_a.card_id,
                downstream_card_id=card_b.card_id,
                evidence_rationale="x",
                evidence_references=EV,
                effective_from=datetime(2026, 8, 9),
                effective_until=None,
                assessed_by_subject="s",
                assessed_at=NOW,
            )

    def test_effective_until_after_from(self) -> None:
        card_a = _source_card()
        card_b = _source_card()
        with pytest.raises(ValueError, match="after effective_from"):
            SourceUpstream(
                upstream_edge_id=uuid4(),
                upstream_card_id=card_a.card_id,
                downstream_card_id=card_b.card_id,
                evidence_rationale="x",
                evidence_references=EV,
                effective_from=NOW,
                effective_until=NOW - timedelta(days=1),
                assessed_by_subject="s",
                assessed_at=NOW,
            )


# ── revise_source_family ─────────────────────────────────────────────────────


class TestReviseSourceFamily:
    def test_revision_closes_prior(self) -> None:
        fid = uuid4()
        v1 = SourceFamily(
            family_id=fid, version=1, family_revision_of=None,
            label="Test Family", rationale="initial",
            scope_effective_from=NOW, scope_effective_until=None,
            created_at=NOW, created_by_subject="subject:analyst",
        )
        closed, v2 = revise_source_family(v1, label="Test Family v2", revised_by_subject="s")
        assert closed.scope_effective_until is not None
        assert v2.version == 2
        assert v2.family_revision_of == fid
        assert v2.label == "Test Family v2"
        assert v2.scope_effective_until is None

    def test_requires_revised_by_subject(self) -> None:
        v1 = SourceFamily(
            family_id=uuid4(), version=1, family_revision_of=None,
            label="Fam", rationale="r",
            scope_effective_from=NOW, scope_effective_until=None,
            created_at=NOW, created_by_subject="s",
        )
        with pytest.raises(ValueError, match="revised_by_subject"):
            revise_source_family(v1)

    def test_preserves_unchanged_fields(self) -> None:
        v1 = SourceFamily(
            family_id=uuid4(), version=1, family_revision_of=None,
            label="Fam", rationale="initial rationale",
            scope_effective_from=NOW, scope_effective_until=None,
            created_at=NOW, created_by_subject="s",
        )
        closed, v2 = revise_source_family(v1, label="Fam v2", revised_by_subject="s")
        assert v2.rationale == "initial rationale"

    def test_updates_rationale(self) -> None:
        v1 = SourceFamily(
            family_id=uuid4(), version=1, family_revision_of=None,
            label="Fam", rationale="old",
            scope_effective_from=NOW, scope_effective_until=None,
            created_at=NOW, created_by_subject="s",
        )
        closed, v2 = revise_source_family(
            v1, label="Fam v2", rationale="new rationale", revised_by_subject="s"
        )
        assert v2.rationale == "new rationale"
