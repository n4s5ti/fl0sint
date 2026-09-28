#!/home/n4s5ti/Documents/dev/fl0sint/.venv/bin/python
"""Independent DEF44/S04 adversarial reproductions against the immutable archive."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import tempfile
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from flowsint_execution.acquisition import AcquisitionRequest, InputOccurrence, Resources
from flowsint_execution.artifacts import (
    ArtifactContext,
    FilesystemArtifactStore,
    RetentionAuthority,
    capture_source,
    normalize_html,
    resolve_source,
)
from flowsint_execution.fetch import (
    FetchParameters,
    TrustedFetchPolicy,
    admit_fetch,
    execute_fetch_with_source_proof,
)
from flowsint_execution.models import canonical_input_hash


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def authority(**updates):
    values = {
        "issuer_id": "issuer-a",
        "reviewer_id": "reviewer-a",
        "policy_id": "policy-a",
        "policy_digest": hashlib.sha256(b"reviewed policy a").hexdigest(),
        "caller_id": "caller-a",
        "scope": "scope-a",
        "source_family": "http",
        "expires_at": NOW + timedelta(hours=3),
    }
    values.update(updates)
    return RetentionAuthority(**values)


def context(**updates):
    values = {
        "operation_id": "operation-a",
        "occurrence_id": "occurrence-a",
        "caller_id": "caller-a",
        "scope": "scope-a",
        "source_family": "http",
        "origin": "https://fixture.example:443",
        "requested_url": "https://fixture.example/page",
        "final_url": "https://fixture.example/page",
        "retrieved_at": NOW,
    }
    values.update(updates)
    return ArtifactContext(**values)


def normalization_repros():
    cases = {
        "literal_lt": b"<p>a < b</p>",
        "quoted_gt": b'<p title="1 > 0">ok</p>',
        "entities": b"<p>&lt; &amp; &#x41;</p>",
        "unicode": "<p>\U0001f600 caf\u00e9</p>".encode(),
        "repeated": b"<p>x</p><p>x</p>",
        "zero_width_separator": "<p>a</p>\u200b<p>b</p>".encode(),
        "raw_offset_gap": b"alpha < beta <b>gamma</b>",
        "style_with_lt": b"<style>x < y</style><p>ok</p>",
    }
    observed = {}
    for name, body in cases.items():
        value = normalize_html(body)
        observed[name] = {
            "normalized": value.text,
            "reproduce_from_unrelated_body": value.reproduce(b"<p>UNRELATED</p>"),
            "spans": [
                {
                    "raw": [span.raw_start, span.raw_end],
                    "normalized": [span.normalized_start, span.normalized_end],
                    "emitted": span.emitted_text,
                    "actual_raw_hex": body[span.raw_start : span.raw_end].hex(),
                }
                for span in value.spans
            ],
        }
    assert observed["quoted_gt"]["normalized"] == '0">ok'
    assert observed["raw_offset_gap"]["spans"][-1]["actual_raw_hex"] == b">gamm".hex()
    assert observed["literal_lt"]["reproduce_from_unrelated_body"] == observed["literal_lt"]["normalized"]
    return observed


def resolver_and_store_repros(root: Path):
    store = FilesystemArtifactStore(root / "store")
    decision = authority().issue("operation-a", now=NOW)
    captured = capture_source(store, context(), decision, b"retained bytes", now=NOW)
    record_path = root / "store" / "records" / f"{captured.artifact.snapshot_id}.json"
    record = json.loads(record_path.read_text())
    record.update(
        {
            "issuer_id": "tampered-issuer",
            "reviewer_id": "tampered-reviewer",
            "policy_id": "tampered-policy",
            "normalized": {"text": "tampered normalized", "spans": []},
        }
    )
    record_path.write_text(json.dumps(record, sort_keys=True, separators=(",", ":")))
    tampered_metadata = resolve_source(store, context(), decision, captured.artifact, now=NOW)
    assert tampered_metadata.body == b"retained bytes"

    relabeled_decision = decision.model_copy(
        update={
            "issuer_id": "other-issuer",
            "reviewer_id": "other-reviewer",
            "policy_id": "other-policy",
        }
    )
    relabeled_policy = resolve_source(
        store, context(), relabeled_decision, captured.artifact, now=NOW
    )
    assert relabeled_policy.body == b"retained bytes"

    future_decision = authority(expires_at=NOW + timedelta(hours=4)).issue(
        "operation-a", now=NOW + timedelta(hours=2)
    )
    future_capture = capture_source(
        store,
        context(occurrence_id="future-occurrence"),
        future_decision,
        b"future-authorized",
        now=NOW,
    )
    assert future_capture.state.value == "available"

    class BrokenStore:
        def write(self, body, metadata):
            raise OSError("fixture write failure")

    try:
        capture_source(BrokenStore(), context(), decision, b"consumed", now=NOW)
    except OSError as error:
        raw_store_exception = type(error).__name__ + ":" + str(error)
    else:
        raw_store_exception = "NO_EXCEPTION"
    assert raw_store_exception.startswith("OSError:")

    link_store = FilesystemArtifactStore(root / "link-store")
    link_body = b"ancestor symlink escape"
    digest = hashlib.sha256(link_body).hexdigest()
    outside = root / "outside"
    outside.mkdir()
    (root / "link-store" / "objects" / digest[:2]).symlink_to(outside, target_is_directory=True)
    linked = capture_source(link_store, context(occurrence_id="link"), decision, link_body, now=NOW)
    escaped_path = outside / f"{digest}.body"
    assert escaped_path.read_bytes() == link_body
    assert resolve_source(link_store, context(occurrence_id="link"), decision, linked.artifact, now=NOW).body == link_body

    return {
        "tampered_record_resolve": [tampered_metadata.state.value, tampered_metadata.reason],
        "relabeled_policy_resolve": [relabeled_policy.state.value, relabeled_policy.reason],
        "future_issued_capture": [future_capture.state.value, future_capture.reason],
        "store_write_failure": raw_store_exception,
        "symlink_ancestor_external_write": str(escaped_path),
        "span_resolver_exported": False,
    }


async def retain_normalized_false_repro(root: Path):
    live_now = datetime.now(timezone.utc)
    url = "https://fixture.example/page"
    capability = hashlib.sha256(b"capability").hexdigest()
    endpoint = hashlib.sha256(b"endpoint").hexdigest()
    policy = TrustedFetchPolicy(
        caller_id="caller-a",
        scope="scope-a",
        capability_digest=capability,
        endpoint_policy_digest=endpoint,
        allowed_origins=("https://fixture.example:443",),
    )
    request = AcquisitionRequest(
        operation_id="operation-a",
        caller_id="caller-a",
        scope="scope-a",
        capability_digest=capability,
        endpoint_policy_digest=endpoint,
        inputs=(
            InputOccurrence(
                occurrence_id="occurrence-a",
                input_ref=canonical_input_hash(url),
                type_tag="http_url",
                value=url,
            ),
        ),
        allocation=Resources(requests=1, bytes=1024, elapsed_seconds=1, concurrency=1),
    )
    decision = authority(
        retain_normalized_text=False,
        expires_at=live_now + timedelta(hours=1),
    ).issue("operation-a", now=live_now)
    result = await execute_fetch_with_source_proof(
        admit_fetch(request, policy, FetchParameters(max_bytes_per_input=1024)),
        artifact_store=FilesystemArtifactStore(root / "no-normalized"),
        retention_decision=decision,
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, content=b"<p>PROHIBITED NORMALIZED OUTPUT</p>")
        ),
    )
    outcome = result.outcomes[0]
    record = json.loads(next((root / "no-normalized" / "records").glob("*.json")).read_text())
    assert record["normalized"] is None
    assert outcome.normalized.text == "PROHIBITED NORMALIZED OUTPUT"
    assert outcome.spans
    return {
        "stored_normalized": record["normalized"],
        "returned_normalized": outcome.normalized.text,
        "returned_span_count": len(outcome.spans),
    }


def main():
    with tempfile.TemporaryDirectory(prefix="def44-review-") as temporary:
        root = Path(temporary)
        report = {
            "normalization": normalization_repros(),
            "resolver_store": resolver_and_store_repros(root),
            "retain_normalized_text_false": asyncio.run(retain_normalized_false_repro(root)),
        }
        print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
