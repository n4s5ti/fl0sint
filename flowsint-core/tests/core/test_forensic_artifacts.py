"""Tests for B5/OBS-1966 immutable artifacts and reconstructable source records."""

import pytest
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from flowsint_core.core.forensics.artifacts import (
    ARTIFACT_CONTRACT_VERSION,
    ArtifactAccess,
    ArtifactContentType,
    ArtifactLocator,
    ArtifactVerification,
    ImmutableArtifact,
    ProvenanceChain,
    SourceRecord,
    UnavailabilityReason,
    reconstruct_provenance_chain,
    revise_artifact,
    revise_source_record,
)


NOW = datetime(2026, 8, 9, tzinfo=timezone.utc)
SHA256_RE = r"^[0-9a-f]{64}$"
VALID_HASH = "a" * 64


# ── Helpers ───────────────────────────────────────────────────────────────────


def _locator(
    reference: str = "https://example.com/page",
    *,
    retrieved_at: datetime = NOW,
) -> ArtifactLocator:
    return ArtifactLocator(
        reference=reference,
        content_type=ArtifactContentType.WEB_PAGE,
        access=ArtifactAccess.PUBLIC,
        retrieved_at=retrieved_at,
    )


def _artifact(
    *,
    artifact_id: UUID | None = None,
    evidence_envelope_id: UUID | None = None,
    digest_sha256: str = VALID_HASH,
    locator: ArtifactLocator | None = None,
    content_type: ArtifactContentType = ArtifactContentType.WEB_PAGE,
    custody_subject: str = "subject:test-operator",
) -> ImmutableArtifact:
    return ImmutableArtifact(
        artifact_id=artifact_id or uuid4(),
        evidence_envelope_id=evidence_envelope_id or uuid4(),
        digest_sha256=digest_sha256,
        locator=locator or _locator(),
        content_type=content_type,
        custody_subject=custody_subject,
        created_at=NOW,
    )


def _source_record(
    *,
    source_card_id: UUID | None = None,
    evidence_envelope_id: UUID | None = None,
    artifact_ids: frozenset[UUID] | None = None,
    custody_subject: str = "subject:test-operator",
) -> SourceRecord:
    return SourceRecord(
        source_record_id=uuid4(),
        source_card_id=source_card_id or uuid4(),
        source_card_version=1,
        evidence_envelope_id=evidence_envelope_id or uuid4(),
        artifact_ids=artifact_ids or frozenset(),
        custody_subject=custody_subject,
        ingested_at=NOW,
    )


# ── ArtifactLocator ───────────────────────────────────────────────────────────


class TestArtifactLocator:
    def test_creates_available_locator(self) -> None:
        loc = _locator()
        assert loc.available is True
        assert loc.reference == "https://example.com/page"

    def test_rejects_empty_reference(self) -> None:
        with pytest.raises(ValueError, match="non-empty string"):
            ArtifactLocator(reference="  ")

    def test_verified_unavailable_requires_reason(self) -> None:
        with pytest.raises(ValueError, match="requires unavailability_reason"):
            ArtifactLocator(
                reference="https://example.com",
                verified_unavailable=True,
            )

    def test_verified_unavailable_makes_unavailable(self) -> None:
        loc = ArtifactLocator(
            reference="https://example.com",
            verified_unavailable=True,
            unavailability_reason=UnavailabilityReason.CONTENT_REMOVED,
            unavailable_since=NOW,
        )
        assert loc.available is False

    def test_unavailability_must_not_precede_retrieval(self) -> None:
        with pytest.raises(ValueError, match="must not precede"):
            ArtifactLocator(
                reference="https://example.com",
                retrieved_at=NOW,
                unavailable_since=NOW - timedelta(days=1),
            )

    def test_rejects_naive_datetime(self) -> None:
        with pytest.raises(TypeError, match="timezone-aware"):
            ArtifactLocator(reference="https://example.com", retrieved_at=datetime(2026, 8, 9))


# ── ImmutableArtifact ─────────────────────────────────────────────────────────


class TestImmutableArtifact:
    def test_creates_artifact(self) -> None:
        art = _artifact()
        assert art.digest_sha256 == VALID_HASH
        assert art.is_available is True
        assert art.is_correction is False

    def test_rejects_invalid_digest(self) -> None:
        with pytest.raises(ValueError, match="64-char hex"):
            _artifact(digest_sha256="not-a-hex-hash")

    def test_rejects_self_supersede(self) -> None:
        art_id = uuid4()
        with pytest.raises(ValueError, match="cannot supersede itself"):
            ImmutableArtifact(
                artifact_id=art_id,
                evidence_envelope_id=uuid4(),
                digest_sha256=VALID_HASH,
                locator=_locator(),
                content_type=ArtifactContentType.WEB_PAGE,
                custody_subject="s",
                created_at=NOW,
                supersedes_artifact_id=art_id,
                supersedes_reason="test",
            )

    def test_supersedes_requires_reason(self) -> None:
        with pytest.raises(ValueError, match="supersedes_reason is required"):
            ImmutableArtifact(
                artifact_id=uuid4(),
                evidence_envelope_id=uuid4(),
                digest_sha256=VALID_HASH,
                locator=_locator(),
                content_type=ArtifactContentType.WEB_PAGE,
                custody_subject="s",
                created_at=NOW,
                supersedes_artifact_id=uuid4(),
            )

    def test_reason_without_supersedes(self) -> None:
        with pytest.raises(ValueError, match="supersedes_reason is meaningless"):
            ImmutableArtifact(
                artifact_id=uuid4(),
                evidence_envelope_id=uuid4(),
                digest_sha256=VALID_HASH,
                locator=_locator(),
                content_type=ArtifactContentType.WEB_PAGE,
                custody_subject="s",
                created_at=NOW,
                supersedes_reason="orphan reason",
            )

    def test_unavailable_locator(self) -> None:
        loc = ArtifactLocator(
            reference="https://example.com",
            verified_unavailable=True,
            unavailability_reason=UnavailabilityReason.ACCESS_REVOKED,
            unavailable_since=NOW,
        )
        art = _artifact(locator=loc)
        assert art.is_available is False

    def test_is_correction(self) -> None:
        art = ImmutableArtifact(
            artifact_id=uuid4(),
            evidence_envelope_id=uuid4(),
            digest_sha256=VALID_HASH,
            locator=_locator(),
            content_type=ArtifactContentType.WEB_PAGE,
            custody_subject="s",
            created_at=NOW,
            supersedes_artifact_id=uuid4(),
            supersedes_reason="correction",
        )
        assert art.is_correction is True


# ── SourceRecord ──────────────────────────────────────────────────────────────


class TestSourceRecord:
    def test_creates_source_record(self) -> None:
        sr = _source_record()
        assert sr.source_card_version == 1
        assert sr.is_correction is False

    def test_rejects_version_zero(self) -> None:
        with pytest.raises(ValueError, match="source_card_version must be >= 1"):
            SourceRecord(
                source_record_id=uuid4(),
                source_card_id=uuid4(),
                source_card_version=0,
                evidence_envelope_id=uuid4(),
                custody_subject="s",
                ingested_at=NOW,
            )

    def test_rejects_self_supersede(self) -> None:
        sr_id = uuid4()
        with pytest.raises(ValueError, match="cannot supersede itself"):
            SourceRecord(
                source_record_id=sr_id,
                source_card_id=uuid4(),
                source_card_version=1,
                evidence_envelope_id=uuid4(),
                custody_subject="s",
                ingested_at=NOW,
                supersedes_source_record_id=sr_id,
                supersedes_reason="test",
            )

    def test_supersedes_requires_reason(self) -> None:
        with pytest.raises(ValueError, match="supersedes_reason is required"):
            SourceRecord(
                source_record_id=uuid4(),
                source_card_id=uuid4(),
                source_card_version=1,
                evidence_envelope_id=uuid4(),
                custody_subject="s",
                ingested_at=NOW,
                supersedes_source_record_id=uuid4(),
            )


# ── ArtifactVerification ──────────────────────────────────────────────────────


class TestArtifactVerification:
    def test_creates_verification(self) -> None:
        v = ArtifactVerification(
            verification_id=uuid4(),
            artifact_id=uuid4(),
            digest_match=True,
            locator_reachable=True,
            verified_at=NOW,
            verified_by_subject="subject:auditor",
        )
        assert v.digest_match is True

    def test_rejects_naive_datetime(self) -> None:
        with pytest.raises(TypeError, match="timezone-aware"):
            ArtifactVerification(
                verification_id=uuid4(),
                artifact_id=uuid4(),
                digest_match=True,
                locator_reachable=True,
                verified_at=datetime(2026, 8, 9),
                verified_by_subject="s",
            )


# ── ProvenanceChain ───────────────────────────────────────────────────────────


class TestProvenanceChain:
    def test_creates_chain(self) -> None:
        env_id = uuid4()
        chain = ProvenanceChain(
            evidence_envelope_id=env_id,
            source_record_id=uuid4(),
            source_card_id=uuid4(),
            source_card_version=1,
            artifact_ids=frozenset({uuid4()}),
            supersedes_envelope_id=None,
            created_at=NOW,
        )
        assert chain.evidence_envelope_id == env_id
        assert chain.has_superseded_prior is False

    def test_has_superseded_prior(self) -> None:
        chain = ProvenanceChain(
            evidence_envelope_id=uuid4(),
            source_record_id=uuid4(),
            source_card_id=uuid4(),
            source_card_version=1,
            artifact_ids=frozenset({uuid4()}),
            supersedes_envelope_id=uuid4(),
            created_at=NOW,
        )
        assert chain.has_superseded_prior is True


# ── reconstruct_provenance_chain ──────────────────────────────────────────────


class TestReconstructProvenanceChain:
    def test_reconstructs_chain(self) -> None:
        art = _artifact()
        sr = _source_record(artifact_ids=frozenset({art.artifact_id}))
        chain = reconstruct_provenance_chain(sr, frozenset({art}))
        assert chain.evidence_envelope_id == sr.evidence_envelope_id
        assert chain.source_card_id == sr.source_card_id
        assert chain.artifact_ids == frozenset({art.artifact_id})

    def test_rejects_empty_artifacts(self) -> None:
        sr = _source_record()
        with pytest.raises(ValueError, match="at least one artifact"):
            reconstruct_provenance_chain(sr, frozenset())

    def test_rejects_mismatched_artifacts(self) -> None:
        art = _artifact()
        sr = _source_record(artifact_ids=frozenset({uuid4()}))
        with pytest.raises(ValueError, match="must match"):
            reconstruct_provenance_chain(sr, frozenset({art}))

    def test_preserves_supersedes(self) -> None:
        art = _artifact()
        sr = _source_record(artifact_ids=frozenset({art.artifact_id}))
        superseded_id = uuid4()
        chain = reconstruct_provenance_chain(sr, frozenset({art}), superseded_id)
        assert chain.supersedes_envelope_id == superseded_id


# ── revise_artifact ───────────────────────────────────────────────────────────


class TestReviseArtifact:
    def test_creates_superseding_artifact(self) -> None:
        prior = _artifact()
        revised = revise_artifact(prior, revised_by_subject="subject:editor")
        assert revised.is_correction is True
        assert revised.supersedes_artifact_id == prior.artifact_id
        assert revised.artifact_id != prior.artifact_id
        assert revised.evidence_envelope_id == prior.evidence_envelope_id
        assert revised.custody_subject == "subject:editor"

    def test_requires_revised_by_subject(self) -> None:
        prior = _artifact()
        with pytest.raises(ValueError, match="revised_by_subject is required"):
            revise_artifact(prior, revised_by_subject="")

    def test_preserves_evidence_envelope(self) -> None:
        prior = _artifact()
        revised = revise_artifact(prior, revised_by_subject="s")
        assert revised.evidence_envelope_id == prior.evidence_envelope_id

    def test_can_override_locator(self) -> None:
        prior = _artifact()
        new_loc = _locator(reference="https://new.example.com")
        revised = revise_artifact(prior, locator=new_loc, revised_by_subject="s")
        assert revised.locator.reference == "https://new.example.com"


# ── revise_source_record ──────────────────────────────────────────────────────


class TestReviseSourceRecord:
    def test_creates_superseding_source_record(self) -> None:
        prior = _source_record()
        revised = revise_source_record(prior, revised_by_subject="subject:editor")
        assert revised.is_correction is True
        assert revised.supersedes_source_record_id == prior.source_record_id
        assert revised.source_record_id != prior.source_record_id

    def test_requires_revised_by_subject(self) -> None:
        prior = _source_record()
        with pytest.raises(ValueError, match="revised_by_subject is required"):
            revise_source_record(prior, revised_by_subject="")

    def test_preserves_source_card(self) -> None:
        card_id = uuid4()
        prior = _source_record(source_card_id=card_id)
        revised = revise_source_record(prior, revised_by_subject="s")
        assert revised.source_card_id == card_id


# ── Contract version ──────────────────────────────────────────────────────────


def test_contract_version() -> None:
    assert ARTIFACT_CONTRACT_VERSION == "v1"
