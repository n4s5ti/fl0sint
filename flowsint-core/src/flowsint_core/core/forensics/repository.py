"""B5/OBS-1966 -- SQLite-anchored source record and artifact repository.

Provides the authoritative persistence layer for forensic artifacts
and source records. LadybugDB projection is rebuildable from these
SQLite tables alone.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from flowsint_core.core.models import (
    ForensicArtifactRecord,
    ForensicSourceRecord,
)


def insert_artifact_record(
    session: Session,
    artifact_id: UUID,
    evidence_envelope_id: UUID,
    digest_sha256: str,
    locator_reference: str,
    content_type: str,
    custody_subject: str,
    *,
    source_record_id: UUID | None = None,
    locator_retrieval_context: str | None = None,
    content_version: str | None = None,
    access: str = "public",
    rights: str | None = None,
    retention_required: bool = True,
    verified_unavailable: bool = False,
    unavailability_reason: str | None = None,
    unavailable_since: datetime | None = None,
    retrieved_at: datetime | None = None,
    supersedes_artifact_id: UUID | None = None,
    supersedes_reason: str | None = None,
) -> ForensicArtifactRecord:
    """Insert a new immutable artifact record."""
    record = ForensicArtifactRecord(
        artifact_id=artifact_id,
        evidence_envelope_id=evidence_envelope_id,
        source_record_id=source_record_id,
        digest_sha256=digest_sha256,
        locator_reference=locator_reference,
        locator_retrieval_context=locator_retrieval_context,
        content_type=content_type,
        content_version=content_version,
        access=access,
        rights=rights,
        retention_required=retention_required,
        verified_unavailable=verified_unavailable,
        unavailability_reason=unavailability_reason,
        unavailable_since=unavailable_since,
        retrieved_at=retrieved_at,
        supersedes_artifact_id=supersedes_artifact_id,
        supersedes_reason=supersedes_reason,
        custody_subject=custody_subject,
    )
    session.add(record)
    session.flush()
    return record


def insert_source_record(
    session: Session,
    source_record_id: UUID,
    source_card_id: UUID,
    source_card_version: int,
    evidence_envelope_id: UUID,
    custody_subject: str,
    *,
    rights: str | None = None,
    supersedes_source_record_id: UUID | None = None,
    supersedes_reason: str | None = None,
) -> ForensicSourceRecord:
    """Insert a new source record linking source card to evidence envelope."""
    record = ForensicSourceRecord(
        source_record_id=source_record_id,
        source_card_id=source_card_id,
        source_card_version=source_card_version,
        evidence_envelope_id=evidence_envelope_id,
        rights=rights,
        supersedes_source_record_id=supersedes_source_record_id,
        supersedes_reason=supersedes_reason,
        custody_subject=custody_subject,
    )
    session.add(record)
    session.flush()
    return record


def load_source_record_by_envelope(
    session: Session,
    evidence_envelope_id: UUID,
) -> ForensicSourceRecord | None:
    """Load the most recent source record for a given evidence envelope."""
    return session.execute(
        select(ForensicSourceRecord).where(
            ForensicSourceRecord.evidence_envelope_id == evidence_envelope_id
        ).order_by(ForensicSourceRecord.ingested_at.desc())
    ).scalars().first()


def load_artifacts_for_source_record(
    session: Session,
    source_record_id: UUID,
) -> list[ForensicArtifactRecord]:
    """Load all artifacts for a source record."""
    return list(session.execute(
        select(ForensicArtifactRecord).where(
            ForensicArtifactRecord.source_record_id == source_record_id
        ).order_by(ForensicArtifactRecord.created_at.asc())
    ).scalars().all())


def load_artifact_chain(
    session: Session,
    artifact_id: UUID,
) -> list[ForensicArtifactRecord]:
    """Walk the supersedes chain from the given artifact back to the original."""
    records: list[ForensicArtifactRecord] = []
    current_id: UUID | None = artifact_id
    while current_id is not None:
        record = session.execute(
            select(ForensicArtifactRecord).where(
                ForensicArtifactRecord.artifact_id == current_id
            )
        ).scalar_one_or_none()
        if record is None:
            break
        records.append(record)
        current_id = record.supersedes_artifact_id
    return records


def reconstruct_provenance_from_sqlite(
    session: Session,
    evidence_envelope_id: UUID,
) -> dict:
    """Rebuild the full provenance chain from SQLite identifiers only.

    Returns a dict with all record IDs needed to rebuild the LadybugDB
    projection after a loss. No projection dependency.
    """
    source_record = load_source_record_by_envelope(session, evidence_envelope_id)
    if source_record is None:
        raise LookupError(
            f"No source record for evidence envelope {evidence_envelope_id}"
        )
    artifacts = load_artifacts_for_source_record(session, source_record.source_record_id)

    source_ids: list[UUID] = [source_record.source_record_id]
    current_supersedes = source_record.supersedes_source_record_id
    while current_supersedes is not None:
        source_ids.append(current_supersedes)
        prior = session.execute(
            select(ForensicSourceRecord.supersedes_source_record_id).where(
                ForensicSourceRecord.source_record_id == current_supersedes
            )
        ).scalar_one_or_none()
        current_supersedes = prior

    return {
        "evidence_envelope_id": str(evidence_envelope_id),
        "source_record_id": str(source_record.source_record_id),
        "source_card_id": str(source_record.source_card_id),
        "source_card_version": source_record.source_card_version,
        "artifact_ids": [str(a.artifact_id) for a in artifacts],
        "artifact_digests": {str(a.artifact_id): a.digest_sha256 for a in artifacts},
        "source_record_chain": [str(sid) for sid in source_ids],
        "reconstructed_at": datetime.now(timezone.utc).isoformat(),
    }
