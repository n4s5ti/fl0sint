"""SQLite integration tests for B5 forensic artifact/source-record repository (MVP)."""

import importlib
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from flowsint_core.core.models import (
    Base,
    ForensicArtifactRecord,
)
from flowsint_core.core.forensics.repository import (
    insert_artifact_record,
    insert_source_record,
    load_artifact_chain,
    load_artifacts_for_source_record,
    load_source_record_by_envelope,
    reconstruct_provenance_from_sqlite,
)


NOW = datetime(2026, 8, 9, tzinfo=timezone.utc)
VALID_HASH = "a" * 64

B5_EXPORTS = (
    "ARTIFACT_CONTRACT_VERSION",
    "ArtifactAccess",
    "ArtifactContentType",
    "ArtifactLocator",
    "ArtifactVerification",
    "ImmutableArtifact",
    "ProvenanceChain",
    "SourceRecord",
    "UnavailabilityReason",
    "reconstruct_provenance_chain",
    "revise_artifact",
    "revise_source_record",
    "insert_artifact_record",
    "insert_source_record",
    "load_artifact_chain",
    "load_artifacts_for_source_record",
    "load_source_record_by_envelope",
    "reconstruct_provenance_from_sqlite",
)


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


class TestArtifactInsertAndLoad:
    def test_insert_and_load(self, session: Session) -> None:
        art_id = uuid4()
        insert_artifact_record(
            session, artifact_id=art_id, evidence_envelope_id=uuid4(),
            digest_sha256=VALID_HASH, locator_reference="https://example.com/1",
            content_type="web_page", custody_subject="subject:test",
        )
        session.commit()
        loaded = session.get(ForensicArtifactRecord, art_id)
        assert loaded is not None
        assert loaded.digest_sha256 == VALID_HASH

    def test_supersedes_chain(self, session: Session) -> None:
        env_id = uuid4()
        art1 = uuid4()
        insert_artifact_record(
            session, artifact_id=art1, evidence_envelope_id=env_id,
            digest_sha256=VALID_HASH, locator_reference="https://example.com/1",
            content_type="web_page", custody_subject="subject:test",
        )
        session.commit()
        art2 = uuid4()
        insert_artifact_record(
            session, artifact_id=art2, evidence_envelope_id=env_id,
            digest_sha256=VALID_HASH, locator_reference="https://example.com/2",
            content_type="web_page", custody_subject="subject:editor",
            supersedes_artifact_id=art1, supersedes_reason="correction",
        )
        session.commit()
        chain = load_artifact_chain(session, art2)
        assert len(chain) == 2
        assert chain[0].artifact_id == art2
        assert chain[1].artifact_id == art1

    def test_supersedes_uniqueness_prevents_fork(self, session: Session) -> None:
        env_id = uuid4()
        art_id = uuid4()
        insert_artifact_record(
            session, artifact_id=art_id, evidence_envelope_id=env_id,
            digest_sha256=VALID_HASH, locator_reference="https://example.com/1",
            content_type="web_page", custody_subject="subject:test",
        )
        session.commit()
        insert_artifact_record(
            session, artifact_id=uuid4(), evidence_envelope_id=env_id,
            digest_sha256=VALID_HASH, locator_reference="https://example.com/2",
            content_type="web_page", custody_subject="subject:editor",
            supersedes_artifact_id=art_id, supersedes_reason="correction",
        )
        session.commit()
        with pytest.raises(Exception):
            insert_artifact_record(
                session, artifact_id=uuid4(), evidence_envelope_id=env_id,
                digest_sha256=VALID_HASH, locator_reference="https://example.com/3",
                content_type="web_page", custody_subject="subject:editor2",
                supersedes_artifact_id=art_id, supersedes_reason="second correction",
            )
            session.commit()


class TestSourceRecordInsertAndLoad:
    def test_insert_and_load(self, session: Session) -> None:
        sr_id = uuid4()
        env_id = uuid4()
        insert_source_record(
            session, source_record_id=sr_id, source_card_id=uuid4(),
            source_card_version=1, evidence_envelope_id=env_id,
            custody_subject="subject:test",
        )
        session.commit()
        loaded = load_source_record_by_envelope(session, env_id)
        assert loaded is not None




class TestForensicRecordsAppendOnly:
    def test_rejects_artifact_update(self, session: Session) -> None:
        artifact = insert_artifact_record(
            session, artifact_id=uuid4(), evidence_envelope_id=uuid4(),
            digest_sha256=VALID_HASH, locator_reference="https://example.com/1",
            content_type="web_page", custody_subject="subject:test",
        )
        session.commit()
        artifact.locator_reference = "https://example.com/updated"
        with pytest.raises(IntegrityError, match="forensic records are append-only"):
            session.commit()
        session.rollback()

    def test_rejects_artifact_delete(self, session: Session) -> None:
        artifact = insert_artifact_record(
            session, artifact_id=uuid4(), evidence_envelope_id=uuid4(),
            digest_sha256=VALID_HASH, locator_reference="https://example.com/1",
            content_type="web_page", custody_subject="subject:test",
        )
        session.commit()
        session.delete(artifact)
        with pytest.raises(IntegrityError, match="forensic records are append-only"):
            session.commit()
        session.rollback()

    def test_rejects_source_record_update(self, session: Session) -> None:
        source_record = insert_source_record(
            session, source_record_id=uuid4(), source_card_id=uuid4(),
            source_card_version=1, evidence_envelope_id=uuid4(),
            custody_subject="subject:test",
        )
        session.commit()
        source_record.custody_subject = "subject:updated"
        with pytest.raises(IntegrityError, match="forensic records are append-only"):
            session.commit()
        session.rollback()

    def test_rejects_source_record_delete(self, session: Session) -> None:
        source_record = insert_source_record(
            session, source_record_id=uuid4(), source_card_id=uuid4(),
            source_card_version=1, evidence_envelope_id=uuid4(),
            custody_subject="subject:test",
        )
        session.commit()
        session.delete(source_record)
        with pytest.raises(IntegrityError, match="forensic records are append-only"):
            session.commit()
        session.rollback()


class TestForensicsPackageExports:
    def test_b5_exports_are_star_importable(self) -> None:
        forensics = importlib.import_module("flowsint_core.core.forensics")
        assert set(B5_EXPORTS).issubset(forensics.__all__)
        namespace: dict[str, object] = {}
        exec("from flowsint_core.core.forensics import *", namespace)
        assert set(B5_EXPORTS).issubset(namespace)


class TestArtifactsLinkedToSourceRecord:
    def test_link_artifacts(self, session: Session) -> None:
        sr_id = uuid4()
        env_id = uuid4()
        insert_source_record(
            session, source_record_id=sr_id, source_card_id=uuid4(),
            source_card_version=1, evidence_envelope_id=env_id,
            custody_subject="subject:test",
        )
        art_id = uuid4()
        insert_artifact_record(
            session, artifact_id=art_id, evidence_envelope_id=env_id,
            digest_sha256=VALID_HASH, locator_reference="https://example.com/1",
            content_type="web_page", custody_subject="subject:test",
            source_record_id=sr_id,
        )
        session.commit()
        artifacts = load_artifacts_for_source_record(session, sr_id)
        assert len(artifacts) == 1


class TestReconstructProvenance:
    def test_reconstructs(self, session: Session) -> None:
        env_id = uuid4()
        sr_id = uuid4()
        insert_source_record(
            session, source_record_id=sr_id, source_card_id=uuid4(),
            source_card_version=1, evidence_envelope_id=env_id,
            custody_subject="subject:test",
        )
        art_id = uuid4()
        insert_artifact_record(
            session, artifact_id=art_id, evidence_envelope_id=env_id,
            digest_sha256=VALID_HASH, locator_reference="https://example.com/page",
            content_type="web_page", custody_subject="subject:test",
            source_record_id=sr_id,
        )
        session.commit()
        result = reconstruct_provenance_from_sqlite(session, env_id)
        assert result["evidence_envelope_id"] == str(env_id)
        assert str(art_id) in result["artifact_ids"]

    def test_missing_envelope_raises(self, session: Session) -> None:
        with pytest.raises(LookupError, match="No source record"):
            reconstruct_provenance_from_sqlite(session, uuid4())
