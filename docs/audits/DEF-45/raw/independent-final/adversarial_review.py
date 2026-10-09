#!/usr/bin/env python3
from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path("/home/n4s5ti/.cache/def45-independent-final")
PYTHON = "/home/n4s5ti/Documents/dev/fl0sint/.venv/bin/python"
sys.path[:0] = [str(ROOT / p) for p in (
    "flowsint-core/src", "flowsint-enrichers/src", "flowsint-types/src",
    "flowsint-mcp-server/src", "flowsint-app/src")]
os.environ["AUTH_SECRET"] = "def45-fixture-only"
os.environ["REDIS_URL"] = "redis://127.0.0.1:6379"

from flowsint_execution.acquisition import ArtifactReference
from flowsint_execution.models import InputOutcome, OutcomeStatus, PersistedMetadata
from flowsint_execution.observed_extraction import (
    ExecutionState, GeneratedHypothesis, Observation, ObservationKind, RawSpan,
    extract_observations, parse_observed_extraction_metadata,
    resolve_observation_span, serialize_observed_extraction_metadata)
from flowsint_execution.url_policy import disclose_url

def check(label, condition):
    if not condition:
        raise AssertionError(label)
    print("PASS", label)

def ref(body, url="https://example.test/team/index.html?edition=2026"):
    digest = hashlib.sha256(body).hexdigest()
    return ArtifactReference(
        artifact_id="artifact-final", snapshot_id="snapshot-final",
        content_digest=digest, byte_length=len(body), locator="sha256:" + digest,
        source_family="web", origin="fixture", retrieved_at=datetime.now(timezone.utc),
        requested_url=url, final_url=url)

def extract(body):
    return extract_observations(body, artifact=ref(body), occurrence_id="occ-final",
        input_ref="a" * 64, final_url=ref(body).final_url)

# F1 plus exact duplicate/malformed span behavior.
body = (b'<section class="person"><p>before@example.test</p><h2>Ada One</h2>'
        b'<p>same@example.test same@example.test</p><h2>Bo Two</h2>'
        b'<p>bo@example.test</p></section><div class="office"><h2>Office</h2>'
        b'<p>officeperson@example.test</p></div><a href="/broken><p>x@example.test</p>')
result = extract(body)
contacts = {o.value: o for o in result.observations if "@" in o.value and o.value != "same@example.test"}
dupes = [o for o in result.observations if o.value == "same@example.test"]
check("temporal pre-heading contact remains unknown", contacts["before@example.test"].person_name is None)
check("later people remain separately bounded", contacts["bo@example.test"].person_name == "Bo Two" and b"Ada One" not in body[contacts["bo@example.test"].context_span.start_byte:contacts["bo@example.test"].context_span.end_byte])
check("generic office does not create person", contacts["officeperson@example.test"].person_name is None)
check("duplicates retain distinct exact spans", len(dupes) == 2 and dupes[0].raw_span != dupes[1].raw_span and all(body[o.raw_span.start_byte:o.raw_span.end_byte] == b"same@example.test" for o in dupes))
check("malformed href emits no executable link", not any(o.kind is ObservationKind.LINK and "broken" in o.value for o in result.observations))

# F2: actual resolver must reject every altered ownership/value/span field.
target = contacts["bo@example.test"]
resolved = resolve_observation_span(body, ref(body), target,
    expected_occurrence_id="occ-final", expected_input_ref="a" * 64)
check("canonical observation resolves exact bytes", resolved.raw == b"bo@example.test")
for label, altered in (
    ("value", dataclasses.replace(target, value="before@example.test")),
    ("occurrence", dataclasses.replace(target, occurrence_id="other")),
    ("span", dataclasses.replace(target, raw_span=RawSpan(target.raw_span.start_byte - 1, target.raw_span.end_byte))),
    ("snapshot", dataclasses.replace(target, snapshot_id="other"))):
    try:
        resolve_observation_span(body, ref(body), altered,
            expected_occurrence_id="occ-final", expected_input_ref="a" * 64)
    except Exception:
        pass
    else:
        raise AssertionError("resolver accepted altered " + label)
check("resolver rejects value/ownership/span tampering", True)

# F3/F7: direct constructor and strict versioned roundtrip, including no promotion.
for args in (("", "v", "b", ("o",)), ("h", "", "b", ("o",)), ("h", "v", "", ("o",)), ("h", "v", "b", ())):
    try: GeneratedHypothesis(*args)
    except ValueError: pass
    else: raise AssertionError("invalid direct hypothesis accepted")
check("direct hypothesis constructor is strict", True)
metadata = serialize_observed_extraction_metadata(result)
wire = metadata.model_dump_json()
restored_meta = PersistedMetadata.model_validate_json(wire, strict=True)
check("strict metadata wire roundtrip restores exact result", parse_observed_extraction_metadata(restored_meta) == result)
for field, value in (("kind", "generated_hypothesis"), ("review_state", "accepted"), ("execution_state", "executed")):
    payload = json.loads(json.dumps(metadata.payload))
    payload["observations"][0][field] = value
    try: parse_observed_extraction_metadata(PersistedMetadata(format_version=metadata.format_version, payload=payload))
    except (ValueError, TypeError): pass
    else: raise AssertionError("metadata promoted " + field)
check("metadata rejects generated/accepted/executed promotion", True)

# F4 and meaningful non-executable query links.
for alias in ("client_secret", "clientSecret", "auth_token", "passwd", "pwd", "API%5FKEY", "x-signature", "session_id"):
    check("credential alias redacted: " + alias, "fixture-secret" not in disclose_url(f"https://example.test/x?{alias}=fixture-secret&view=full"))
link_body = b'<a href="../directory?page=2&view=full">Directory</a>'
link = next(o for o in extract(link_body).observations if o.kind is ObservationKind.LINK)
check("meaningful query retained", link.value == "https://example.test/directory?page=2&view=full")
check("link remains non-executable", link.execution_state is ExecutionState.NOT_EXECUTABLE)

# F5/F6: clean environment imports/runs only core and honors the explicit missing path.
env = {"PATH": os.environ["PATH"], "PYTHONPATH": str(ROOT / "flowsint-core/src")}
run = subprocess.run([PYTHON, str(ROOT / "flowsint-core/examples/observed_extraction.py"),
    "--url", "https://127.0.0.1:1/", "--runtime-config", "/tmp/def45-no-such-runtime.json"],
    env=env, text=True, capture_output=True, timeout=10)
payload = json.loads(run.stdout)
check("clean CLI has no auth prerequisite", run.returncode == 0 and "AUTH_SECRET" not in run.stderr + run.stdout)
check("explicit runtime config controls live hold", payload["diagnostic"] == "current_policy_unavailable")
probe = subprocess.run([PYTHON, "-c", "import sys; from flowsint_execution.extraction_runtime import execute_live_observed_extraction; print('\\n'.join(sorted(k for k in sys.modules if 'graph' in k or 'auth' in k or 'flowsint_enrichers' in k)))"], env=env, text=True, capture_output=True, timeout=10)
check("live helper imports no graph/auth/enricher module", probe.returncode == 0 and probe.stdout.strip() == "")

# InputOutcome narrowing: actual construction and wire parsing reject old arbitrary metadata.
valid = InputOutcome(input_ref="b" * 64, status=OutcomeStatus.SUCCESS, metadata=(metadata,))
check("InputOutcome typed metadata survives wire roundtrip", InputOutcome.model_validate_json(valid.model_dump_json()).metadata == (metadata,))
try:
    InputOutcome(input_ref="b" * 64, status=OutcomeStatus.SUCCESS, metadata=({"legacy": True},))
except Exception: pass
else: raise AssertionError("arbitrary metadata accepted")
check("InputOutcome rejects arbitrary metadata", True)

print("ALL ADVERSARIAL CHECKS PASSED")
