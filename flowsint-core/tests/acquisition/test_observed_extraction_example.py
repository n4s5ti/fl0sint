from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys


def test_url_cli_has_no_auth_graph_or_enricher_prerequisite(tmp_path):
    root = Path(__file__).resolve().parents[3]
    env = {
        "PATH": os.environ["PATH"],
        "PYTHONPATH": str(root / "flowsint-core" / "src"),
    }
    run = subprocess.run(
        [sys.executable,
         str(root / "flowsint-core" / "examples" / "observed_extraction.py"),
         "--url", "https://127.0.0.1:1/", "--runtime-config",
         str(tmp_path / "missing-runtime.json")],
        env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10,
    )
    assert run.returncode == 0, run.stderr
    payload = json.loads(run.stdout)
    assert payload["status"] == "hold"
    assert payload["state"] == "hold"
    assert payload["diagnostic"] == "current_policy_unavailable"
    assert "AUTH_SECRET" not in run.stderr + run.stdout
