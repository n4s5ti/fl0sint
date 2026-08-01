"""SQLite migration contract tests.

The embedded/single-container runtime needs an empty SQLite database to be
bootstrappable without replaying PostgreSQL-only DDL.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from flowsint_core.core.enums import EventLevel
from flowsint_core.core.models import (
    Chat,
    ChatMessage,
    CustomType,
    EnricherTemplate,
    Flow,
    Investigation,
    Key,
    Log,
    Profile,
    Scan,
    Sketch,
)


def _upgrade_sqlite(db_path: Path) -> None:
    api_dir = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{db_path}"

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
    assert version == "c3f4e5d6a7b8"


def test_alembic_head_bootstraps_empty_sqlite_database(tmp_path: Path) -> None:
    _upgrade_sqlite(tmp_path / "flowsint.db")


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
