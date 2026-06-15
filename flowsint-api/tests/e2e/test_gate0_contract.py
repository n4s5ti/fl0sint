"""Gate 0 E2E contract tests for the current Flowsint stack.

These tests intentionally exercise the live HTTP API, graph backend, and Celery
worker before infrastructure replacement work begins. They are skipped unless
FLOWSINT_E2E_BASE_URL is set.
"""

from __future__ import annotations

import os
import time
import uuid
from typing import Any

import pytest
import requests


BASE_URL = os.getenv("FLOWSINT_E2E_BASE_URL", "").rstrip("/")


def _require_base_url() -> str:
    if not BASE_URL:
        pytest.skip("set FLOWSINT_E2E_BASE_URL to run live Flowsint E2E contracts")
    return BASE_URL


def _assert_status(response: requests.Response, expected: int) -> dict[str, Any]:
    assert response.status_code == expected, response.text
    if not response.content:
        return {}
    return response.json()


def test_gate0_user_graph_and_enricher_contract() -> None:
    base_url = _require_base_url()
    session = requests.Session()

    health = session.get(f"{base_url}/health", timeout=20)
    assert health.status_code == 200
    assert health.json() == {"status": "ok"}

    email = f"gate0-{uuid.uuid4().hex[:10]}@example.com"
    password = "Gate0Pass123!"

    registered = _assert_status(
        session.post(
            f"{base_url}/api/auth/register",
            json={"email": email, "password": password},
            timeout=20,
        ),
        201,
    )
    assert registered["email"] == email

    login = _assert_status(
        session.post(
            f"{base_url}/api/auth/token",
            data={"username": email, "password": password},
            timeout=20,
        ),
        200,
    )
    token = login["access_token"]
    session.headers.update({"Authorization": f"Bearer {token}"})

    profile = _assert_status(session.get(f"{base_url}/api/auth/me", timeout=20), 200)
    owner_id = profile["id"]
    assert profile["email"] == email

    investigation = _assert_status(
        session.post(
            f"{base_url}/api/investigations/create",
            json={
                "name": "Gate0 E2E",
                "description": "baseline fixture",
                "owner_id": owner_id,
            },
            timeout=20,
        ),
        201,
    )
    investigation_id = investigation["id"]
    assert investigation["current_user_role"] == "owner"

    sketch = _assert_status(
        session.post(
            f"{base_url}/api/sketches/create",
            json={
                "title": "Gate0 Sketch",
                "description": "baseline graph fixture",
                "owner_id": owner_id,
                "investigation_id": investigation_id,
            },
            timeout=20,
        ),
        201,
    )
    sketch_id = sketch["id"]

    def add_domain(label: str) -> str:
        payload = {
            "id": None,
            "nodeLabel": label,
            "nodeType": "Domain",
            "nodeMetadata": {},
            "nodeProperties": {"domain": label},
            "x": 100,
            "y": 100,
        }
        created = _assert_status(
            session.post(
                f"{base_url}/api/sketches/{sketch_id}/nodes/add",
                json=payload,
                timeout=20,
            ),
            200,
        )
        assert created["status"] == "node added"
        node_id = created["node"]["id"]
        assert node_id
        return node_id

    node1_id = add_domain("example.com")
    node2_id = add_domain("example.org")

    relation = _assert_status(
        session.post(
            f"{base_url}/api/sketches/{sketch_id}/relations/add",
            json={
                "source": node1_id,
                "target": node2_id,
                "type": "one-way",
                "label": "RELATED_TO",
            },
            timeout=20,
        ),
        200,
    )
    assert relation["status"] == "edge added"

    graph = _assert_status(
        session.get(f"{base_url}/api/sketches/{sketch_id}/graph", timeout=20),
        200,
    )
    assert len(graph["nds"]) == 2
    assert len(graph["rls"]) == 1
    assert {node["id"] for node in graph["nds"]} == {node1_id, node2_id}
    assert graph["rls"][0]["source"] == node1_id
    assert graph["rls"][0]["target"] == node2_id
    assert graph["rls"][0]["label"] == "RELATED_TO"

    launched = _assert_status(
        session.post(
            f"{base_url}/api/enrichers/domain_to_dummy/launch",
            json={"node_ids": [node1_id], "sketch_id": sketch_id},
            timeout=20,
        ),
        200,
    )
    scan_id = launched["id"]
    assert scan_id

    deadline = time.time() + 60
    scans: list[dict[str, Any]] = []
    while time.time() < deadline:
        scans = _assert_status(
            session.get(f"{base_url}/api/scans/sketch/{sketch_id}", timeout=20),
            200,
        )
        matching = [scan for scan in scans if scan["id"] == scan_id]
        if matching and matching[0]["status"] == "COMPLETED":
            break
        time.sleep(2)
    else:
        pytest.fail(f"scan {scan_id} did not complete; latest scans={scans!r}")

    enriched_graph = _assert_status(
        session.get(f"{base_url}/api/sketches/{sketch_id}/graph", timeout=20),
        200,
    )
    assert len(enriched_graph["nds"]) >= 3
    assert len(enriched_graph["rls"]) >= 1
