"""B5/OBS-1966 -- Immutable artifacts and reconstructable source records.

Links source-card versions and append-only evidence envelopes into a
SQLite-anchored provenance chain. Every artifact carries a verifiable
locator, digest, retrieval context, rights, and explicit unavailability
state. Corrections are append-only superseding records.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from uuid import UUID, uuid4


ARTIFACT_CONTRACT_VERSION = "v1"


class ArtifactAccess(StrEnum):
    PUBLIC = "public"
    CREDENTIALED = "credentialed"
    RESTRICTED = "restricted"
    UNAVAILABLE = "unavailable"


class ArtifactContentType(StrEnum):
    WEB_PAGE = "web_page"
    API_RESPONSE = "api_response"
    FILE_DOWNLOAD = "file_download"
    STREAMING = "streaming"
    DATABASE_EXPORT = "database_export"
    OTHER = "other"


class UnavailabilityReason(StrEnum):
    CONTENT_REMOVED = "content_removed"
    ACCESS_REVOKED = "access_revoked"
    ENDPOINT_DECOMMISSIONED = "endpoint_decommissioned"
    RATE_LIMITED = "rate_limited"
    GEOGRAPHIC_RESTRICTION = "geographic_restriction"
    LEGAL_TAKEDOWN = "legal_takedown"
    TRANSIENT_FAILURE = "transient_failure"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ArtifactLocator:
    """A retrievable or explicitly unavailable artifact address."""

    reference: str
    retrieval_context: str | None = None
    content_type: ArtifactContentType | None = None
    access: ArtifactAccess = ArtifactAccess.PUBLIC
    retrieved_at: datetime | None = None
    unavailable_since: datetime | None = None
    unavailability_reason: UnavailabilityReason | None = None
    verified_unavailable: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.reference, str) or not self.reference.strip():
            raise ValueError("artifact locator reference must be a non-empty string")
        if self.verified_unavailable and self.unavailability_reason is None:
            raise ValueError("verified_unavailable requires unavailability_reason")
        if self.unavailable_since is not None:
            _require_aware(self.unavailable_since, "unavailable_since")
        if self.retrieved_at is not None:
            _require_aware(self.retrieved_at, "retrieved_at")
        if (
            self.unavailable_since is not None
            and self.retrieved_at is not None
            and self.unavailable_since < self.retrieved_at
        ):
            raise ValueError("unavailable_since must not precede retrieved_at")

    @property
    def available(self) -> bool:
        return not self.verified_unavailable and self.retrieved_at is not None


@dataclass(frozen=True)
class ImmutableArtifact:
    """A content-addressable artifact anchored to a SQLite evidence envelope."""

    artifact_id: UUID
    evidence_envelope_id: UUID
    digest_sha256: str
    locator: ArtifactLocator
    content_type: ArtifactContentType
    custody_subject: str
    created_at: datetime
    content_version: str | None = None
    rights: str | None = None
    retention_required: bool = True
    supersedes_artifact_id: UUID | None = None
    supersedes_reason: str | None = None

    def __post_init__(self) -> None:
        if not re.match(r"^[0-9a-f]{64}$", self.digest_sha256):
            raise ValueError("digest_sha256 must be a 64-char hex string")
        if self.artifact_id == self.supersedes_artifact_id:
            raise ValueError("artifact cannot supersede itself")
        if self.supersedes_artifact_id is not None and not self.supersedes_reason:
            raise ValueError("supersedes_reason is required when superseding")
        if self.supersedes_reason and self.supersedes_artifact_id is None:
            raise ValueError("supersedes_reason is meaningless without supersedes_artifact_id")
        _require_aware(self.created_at, "created_at")

    @property
    def is_available(self) -> bool:
        return self.locator.available

    @property
    def is_correction(self) -> bool:
        return self.supersedes_artifact_id is not None


@dataclass(frozen=True)
class SourceRecord:
    """Binds a source-card version to a forensic evidence envelope."""

    source_record_id: UUID
    source_card_id: UUID
    source_card_version: int
    evidence_envelope_id: UUID
    custody_subject: str
    ingested_at: datetime
    artifact_ids: frozenset[UUID] = field(default_factory=frozenset)
    rights: str | None = None
    supersedes_source_record_id: UUID | None = None
    supersedes_reason: str | None = None

    def __post_init__(self) -> None:
        if self.source_card_version < 1:
            raise ValueError("source_card_version must be >= 1")
        if self.source_record_id == self.supersedes_source_record_id:
            raise ValueError("source record cannot supersede itself")
        _require_aware(self.ingested_at, "ingested_at")
        if self.supersedes_source_record_id is not None and not self.supersedes_reason:
            raise ValueError("supersedes_reason is required when superseding a source record")
        if self.supersedes_reason and self.supersedes_source_record_id is None:
            raise ValueError("supersedes_reason is meaningless without supersedes_source_record_id")

    @property
    def is_correction(self) -> bool:
        return self.supersedes_source_record_id is not None


@dataclass(frozen=True)
class ArtifactVerification:
    """Records a digest/locator verification outcome for an artifact."""

    verification_id: UUID
    artifact_id: UUID
    digest_match: bool
    locator_reachable: bool
    verified_at: datetime
    verified_by_subject: str
    details: str | None = None

    def __post_init__(self) -> None:
        _require_aware(self.verified_at, "verified_at")


@dataclass(frozen=True)
class ProvenanceChain:
    """A reconstructable chain from SQLite identifiers only.

    Carries all record IDs needed to rebuild provenance without
    relying on LadybugDB or any in-memory state.
    """

    evidence_envelope_id: UUID
    source_record_id: UUID
    source_card_id: UUID
    source_card_version: int
    artifact_ids: frozenset[UUID]
    supersedes_envelope_id: UUID | None
    created_at: datetime

    @property
    def has_superseded_prior(self) -> bool:
        return self.supersedes_envelope_id is not None


def reconstruct_provenance_chain(
    source_record: SourceRecord,
    artifacts: frozenset[ImmutableArtifact],
    supersedes_envelope_id: UUID | None = None,
) -> ProvenanceChain:
    """Build a provenance chain from SQLite-anchored identifiers.

    This is the reconstruction path after a LadybugDB loss. Every
    identifier comes from SQLite -- no projection dependency.
    """
    if not artifacts:
        raise ValueError("provenance chain requires at least one artifact")
    artifact_ids = frozenset(a.artifact_id for a in artifacts)
    if artifact_ids != source_record.artifact_ids:
        raise ValueError("artifact_ids in source record must match the provided artifacts")
    return ProvenanceChain(
        evidence_envelope_id=source_record.evidence_envelope_id,
        source_record_id=source_record.source_record_id,
        source_card_id=source_record.source_card_id,
        source_card_version=source_record.source_card_version,
        artifact_ids=artifact_ids,
        supersedes_envelope_id=supersedes_envelope_id,
        created_at=source_record.ingested_at,
    )


def revise_artifact(
    prior: ImmutableArtifact,
    *,
    locator: ArtifactLocator | None = None,
    digest_sha256: str | None = None,
    content_type: ArtifactContentType | None = None,
    content_version: str | None = None,
    rights: str | None = None,
    retention_required: bool | None = None,
    revised_by_subject: str,
    revised_at: datetime | None = None,
) -> ImmutableArtifact:
    """Create a superseding artifact that preserves the prior chain."""
    if revised_by_subject is None or not revised_by_subject.strip():
        raise ValueError("revised_by_subject is required")
    now = revised_at if revised_at is not None else datetime.now(timezone.utc)
    _require_aware(now, "revised_at")
    return ImmutableArtifact(
        artifact_id=uuid4(),
        evidence_envelope_id=prior.evidence_envelope_id,
        digest_sha256=digest_sha256 if digest_sha256 is not None else prior.digest_sha256,
        locator=locator if locator is not None else prior.locator,
        content_type=content_type if content_type is not None else prior.content_type,
        content_version=content_version if content_version is not None else prior.content_version,
        rights=rights if rights is not None else prior.rights,
        retention_required=(
            retention_required if retention_required is not None else prior.retention_required
        ),
        custody_subject=revised_by_subject,
        created_at=now,
        supersedes_artifact_id=prior.artifact_id,
        supersedes_reason=f"Revised by {revised_by_subject}",
    )


def revise_source_record(
    prior: SourceRecord,
    *,
    artifact_ids: frozenset[UUID] | None = None,
    rights: str | None = None,
    revised_by_subject: str,
    revised_at: datetime | None = None,
) -> SourceRecord:
    """Create a superseding source record that preserves the prior chain."""
    if revised_by_subject is None or not revised_by_subject.strip():
        raise ValueError("revised_by_subject is required")
    now = revised_at if revised_at is not None else datetime.now(timezone.utc)
    _require_aware(now, "revised_at")
    return SourceRecord(
        source_record_id=uuid4(),
        source_card_id=prior.source_card_id,
        source_card_version=prior.source_card_version,
        evidence_envelope_id=prior.evidence_envelope_id,
        artifact_ids=artifact_ids if artifact_ids is not None else prior.artifact_ids,
        rights=rights if rights is not None else prior.rights,
        custody_subject=revised_by_subject,
        ingested_at=now,
        supersedes_source_record_id=prior.source_record_id,
        supersedes_reason=f"Revised by {revised_by_subject}",
    )


def _require_aware(dt: datetime, name: str) -> None:
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise TypeError(f"{name} must be timezone-aware")
