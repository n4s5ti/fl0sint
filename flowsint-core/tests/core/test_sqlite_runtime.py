"""SQLite runtime engine contract for embedded mode."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_sqlite_engine_enables_foreign_keys(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{db_path}"

    script = """
from sqlalchemy import text
from flowsint_core.core.postgre_db import SessionLocal, engine
assert engine.url.drivername == 'sqlite'
with SessionLocal() as session:
    enabled = session.execute(text('PRAGMA foreign_keys')).scalar()
assert enabled == 1, enabled
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[2],
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
