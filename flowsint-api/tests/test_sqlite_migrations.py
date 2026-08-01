"""SQLite migration contract tests.

The embedded/single-container runtime needs an empty SQLite database to be
bootstrappable without replaying PostgreSQL-only DDL.
"""

from __future__ import annotations

from datetime import datetime, timezone

import os
import subprocess
import sys
from pathlib import Path
import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory


from sqlalchemy import create_engine, delete, text, update
from sqlalchemy.exc import DatabaseError
from sqlalchemy.orm import sessionmaker

from flowsint_core.core.enums import EventLevel
from flowsint_core.core.models import (
    Chat,
    ChatMessage,
    CustomType,
    EnricherTemplate,
    EvidenceEnvelopeRecord,
    Flow,
    FlowRun,
    Investigation,
    Key,
    Log,
    Profile,
    Scan,
    Sketch,
    StepRun,
)


CURRENT_ALEMBIC_HEAD = "e5f6a7b8c9d0"


def _upgrade_sqlite(db_path: Path) -> None:
    api_dir = Path(__file__).resolve().parents[1]
    repo_dir = api_dir.parent
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{db_path}"
    package_paths = [
        repo_dir / "flowsint-core" / "src",
        repo_dir / "flowsint-types" / "src",
        repo_dir / "flowsint-enrichers" / "src",
    ]
    inherited_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = os.pathsep.join(
        [*(str(path) for path in package_paths)]
        + ([inherited_pythonpath] if inherited_pythonpath else [])
    )
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=api_dir,
        env=env,
        text=True,
        capture_output=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert db_path.exists()

    engine = create_engine(f"sqlite:///{db_path}")
    with engine.connect() as connection:
        version = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
    assert version == CURRENT_ALEMBIC_HEAD


def test_alembic_head_bootstraps_empty_sqlite_database(tmp_path: Path) -> None:
    _upgrade_sqlite(tmp_path / "flowsint.db")


def test_stage4_is_the_sole_alembic_head() -> None:
    api_dir = Path(__file__).resolve().parents[1]
    config = Config(str(api_dir / "alembic.ini"))
    config.set_main_option("script_location", str(api_dir / "alembic"))
    script = ScriptDirectory.from_config(config)

    assert script.get_heads() == [CURRENT_ALEMBIC_HEAD]

def test_sqlite_evidence_schema_is_append_only(tmp_path: Path) -> None:
    db_path = tmp_path / "flowsint.db"
    _upgrade_sqlite(db_path)

    engine = create_engine(f"sqlite:///{db_path}")
    with engine.connect() as connection:
        evidence_column_info = {
            row[1]: row
            for row in connection.execute(text("PRAGMA table_info(evidence_envelope_records)"))
        }
        columns = {
            name: column[2] for name, column in evidence_column_info.items()
        }
        flow_run_column_info = {
            row[1]: row
            for row in connection.execute(text("PRAGMA table_info(flow_runs)"))
        }
        trigger_names = set(
            connection.execute(
                text(
                    "SELECT name FROM sqlite_master "
                    "WHERE type = 'trigger' AND tbl_name = 'evidence_envelope_records'"
                )
            ).scalars()
        )

    assert {"event_at", "retrieved_at", "ingested_at"} <= columns.keys()
    assert columns["source"] == "VARCHAR(256)"
    assert columns["source_rights"] == "VARCHAR(128)"
    assert evidence_column_info["retrieved_at"][3] == 1
    assert evidence_column_info["ingested_at"][3] == 1
    assert flow_run_column_info["owner_id"][3] == 1
    assert flow_run_column_info["lease_owner"][2] == "VARCHAR(255)"
    assert flow_run_column_info["lease_expires_at"][2] == "DATETIME"
    assert {
        "trg_evidence_envelopes_reject_delete",
        "trg_evidence_envelopes_reject_update",
    } <= trigger_names

    Session = sessionmaker(bind=engine)
    with Session() as session:
        profile = Profile(email="evidence@example.test", hashed_password="hash")
        recorded_at = datetime(2026, 8, 1, tzinfo=timezone.utc)
        session.add(profile)
        session.flush()
        run = FlowRun(
            owner_id=profile.id,
            idempotency_key="evidence-append-only",
            input_digest="a" * 64,
            input_count=1,
        )
        session.add(run)
        session.flush()
        step = StepRun(flow_run_id=run.id, step_key="evidence", input_count=1)
        session.add(step)
        session.flush()
        record = EvidenceEnvelopeRecord(
            flow_run_id=run.id,
            step_run_id=step.id,
            attempt=1,
            input_index=0,
            evidence_index=0,
            input_ref="a" * 64,
            status="success",
            retryable=False,
            mapped_outputs=[],
            retrieved_at=recorded_at,
            ingested_at=recorded_at,
            source="evidence-test",
        )
        session.add(record)
        session.commit()

        with pytest.raises(DatabaseError, match="append-only"):
            session.execute(
                update(EvidenceEnvelopeRecord)
                .where(EvidenceEnvelopeRecord.id == record.id)
                .values(source="mutated")
            )
        session.rollback()

        with pytest.raises(DatabaseError, match="append-only"):
            session.query(EvidenceEnvelopeRecord).filter(
                EvidenceEnvelopeRecord.id == record.id
            ).update({"source": "mutated"})
        session.rollback()

        with pytest.raises(DatabaseError, match="append-only"):
            session.execute(
                delete(EvidenceEnvelopeRecord).where(
                    EvidenceEnvelopeRecord.id == record.id
                )
            )
        session.rollback()

        with pytest.raises(DatabaseError, match="append-only"):
            session.query(EvidenceEnvelopeRecord).filter(
                EvidenceEnvelopeRecord.id == record.id
            ).delete()
        session.rollback()

        session.add(
            EvidenceEnvelopeRecord(
                flow_run_id=run.id,
                step_run_id=step.id,
                attempt=1,
                input_index=0,
                evidence_index=1,
                retrieved_at=recorded_at,
                ingested_at=recorded_at,
                input_ref="a" * 64,
                status="success",
                retryable=False,
                mapped_outputs=[],
                source="evidence-test",
                supersedes_id=record.id,
            )
        )
        session.commit()
        assert session.query(EvidenceEnvelopeRecord).count() == 2


def test_sqlite_baseline_round_trips_core_orm_models(tmp_path: Path) -> None:
    db_path = tmp_path / "flowsint.db"
    _upgrade_sqlite(db_path)

    engine = create_engine(f"sqlite:///{db_path}")
    Session = sessionmaker(bind=engine)

    with Session() as session:
        session.execute(text("PRAGMA foreign_keys=ON"))

        profile = Profile(email="sqlite@example.com", hashed_password="hash")
        session.add(profile)
        session.flush()

        investigation = Investigation(
            name="SQLite Investigation",
            description="round trip",
            owner_id=profile.id,
        )
        session.add(investigation)
        session.flush()

        sketch = Sketch(
            title="SQLite Sketch",
            description="round trip",
            owner_id=profile.id,
            investigation_id=investigation.id,
        )
        flow = Flow(
            name="SQLite Flow",
            category=["network", "test"],
            flow_schema={"nodes": [], "edges": []},
        )
        analysis = Log(
            sketch_id=sketch.id,
            type=EventLevel.INFO,
            content={"message": "hello"},
        )
        scan = Scan(
            sketch=sketch,
            status=EventLevel.COMPLETED,
            details={"result": "ok"},
        )
        key = Key(
            name="test-key",
            owner_id=profile.id,
            ciphertext=b"ciphertext",
            iv=b"123456789012",
            salt=b"salt-salt-salt-12",
            key_version="V1",
        )
        custom_type = CustomType(
            name="SQLiteType",
            owner_id=profile.id,
            schema={"type": "object"},
            status="draft",
        )
        template = EnricherTemplate(
            name="SQLite Template",
            category="Domain",
            content={"steps": []},
            owner_id=profile.id,
        )
        chat = Chat(
            title="SQLite Chat",
            owner_id=profile.id,
            investigation_id=investigation.id,
        )
        message = ChatMessage(
            chat=chat,
            content={"text": "hello"},
            context={"source": "test"},
            is_bot=False,
        )

        session.add_all(
            [
                sketch,
                flow,
                analysis,
                scan,
                key,
                custom_type,
                template,
                chat,
                message,
            ]
        )
        session.commit()

    with Session() as session:
        assert session.query(Profile).filter_by(email="sqlite@example.com").one()
        assert session.query(Investigation).one().name == "SQLite Investigation"
        assert session.query(Sketch).one().title == "SQLite Sketch"
        assert session.query(Flow).one().flow_schema == {"nodes": [], "edges": []}
        assert session.query(Scan).one().details == {"result": "ok"}
        assert session.query(Log).one().content == {"message": "hello"}
        assert session.query(Key).one().ciphertext == b"ciphertext"
        assert session.query(CustomType).one().schema == {"type": "object"}
        assert session.query(EnricherTemplate).one().content == {"steps": []}
        assert session.query(Chat).one().messages[0].content == {"text": "hello"}
