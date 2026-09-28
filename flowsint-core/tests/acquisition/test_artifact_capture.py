from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest
import httpx

from flowsint_execution.artifacts import (
    ArtifactContext,
    ArtifactState,
    FilesystemArtifactStore,
    RetentionAuthority,
    capture_source,
    normalize_html,
    resolve_source,
)
from flowsint_execution.acquisition import AcquisitionRequest, InputOccurrence, Resources
from flowsint_execution.fetch import FetchParameters, TrustedFetchPolicy, admit_fetch, execute_fetch_with_source_proof
from flowsint_execution.models import canonical_input_hash


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def authority(**changes):
    values = dict(
        issuer_id="deployment-retention",
        reviewer_id="reviewer-1",
        policy_id="policy-1",
        policy_digest=hashlib.sha256(b"reviewed policy").hexdigest(),
        caller_id="caller-1",
        scope="scope-1",
        source_family="http",
        expires_at=NOW + timedelta(hours=1),
    )
    values.update(changes)
    return RetentionAuthority(**values)


def context(**changes):
    values = dict(
        operation_id="operation-1",
        occurrence_id="occurrence-1",
        caller_id="caller-1",
        scope="scope-1",
        source_family="http",
        origin="https://example.test:443",
        requested_url="https://example.test/a",
        final_url="https://example.test/b",
        retrieved_at=NOW,
    )
    values.update(changes)
    return ArtifactContext(**values)


def test_capture_resolves_after_fresh_store_instance_and_denies_other_bindings(tmp_path):
    store = FilesystemArtifactStore(tmp_path)
    decision = authority().issue("operation-1", now=NOW)
    captured = capture_source(store, context(), decision, b"source bytes", now=NOW)

    assert captured.state is ArtifactState.AVAILABLE
    assert captured.artifact.content_digest == hashlib.sha256(b"source bytes").hexdigest()
    assert captured.artifact.requested_url == "https://example.test/a"
    assert captured.artifact.final_url == "https://example.test/b"
    assert resolve_source(FilesystemArtifactStore(tmp_path), context(), decision, captured.artifact, now=NOW).body == b"source bytes"
    for changed in (
        {"operation_id": "other"},
        {"caller_id": "other"},
        {"scope": "other"},
    ):
        denied = resolve_source(store, context(**changed), decision, captured.artifact, now=NOW)
        assert denied.state is ArtifactState.REVIEW
        assert denied.reason == "authorization_mismatch"
        assert denied.body is None


def test_missing_policy_holds_after_consumed_fetch_without_retaining_bytes(tmp_path):
    store = FilesystemArtifactStore(tmp_path)
    result = capture_source(store, context(), None, b"consumed secret", now=NOW)
    assert result.state is ArtifactState.HOLD
    assert result.artifact is None
    assert list(tmp_path.rglob("*.body")) == []
    assert b"consumed secret" not in json.dumps(result.to_metadata()).encode()


def test_missing_tampered_truncated_and_expired_are_distinct_review_states(tmp_path):
    store = FilesystemArtifactStore(tmp_path)
    decision = authority().issue("operation-1", now=NOW)
    captured = capture_source(store, context(), decision, b"complete", now=NOW)
    body_path = next(tmp_path.rglob("*.body"))

    body_path.unlink()
    missing = resolve_source(store, context(), decision, captured.artifact, now=NOW)
    assert (missing.state, missing.reason) == (ArtifactState.REVIEW, "artifact_missing")

    captured = capture_source(store, context(occurrence_id="occurrence-2"), decision, b"complete", now=NOW)
    body_path = next(tmp_path.rglob("*.body"))
    body_path.write_bytes(b"tampered")
    tampered = resolve_source(store, context(occurrence_id="occurrence-2"), decision, captured.artifact, now=NOW)
    assert (tampered.state, tampered.reason, tampered.body) == (ArtifactState.REVIEW, "digest_mismatch", None)

    truncated = capture_source(store, context(occurrence_id="occurrence-3"), decision, b"partial", complete=False, now=NOW)
    assert (truncated.state, truncated.reason) == (ArtifactState.REVIEW, "truncated_body")
    assert truncated.artifact is None

    expired = resolve_source(store, context(occurrence_id="occurrence-2"), decision, captured.artifact, now=NOW + timedelta(hours=2))
    assert (expired.state, expired.reason, expired.body) == (ArtifactState.REVIEW, "policy_expired", None)

    renewed = authority(expires_at=NOW + timedelta(hours=3)).issue("operation-1", now=NOW + timedelta(hours=1, minutes=1))
    artifact_expired = resolve_source(store, context(occurrence_id="occurrence-2"), renewed, captured.artifact, now=NOW + timedelta(hours=1, minutes=2))
    assert (artifact_expired.state, artifact_expired.reason, artifact_expired.body) == (ArtifactState.REVIEW, "artifact_expired", None)


def test_identical_bodies_share_content_but_keep_occurrence_lineage(tmp_path):
    store = FilesystemArtifactStore(tmp_path)
    decision = authority().issue("operation-1", now=NOW)
    first = capture_source(store, context(), decision, b"same", now=NOW)
    second = capture_source(store, context(occurrence_id="occurrence-2"), decision, b"same", now=NOW)
    assert first.artifact.locator == second.artifact.locator
    assert len(list(tmp_path.rglob("*.body"))) == 1
    records = [json.loads(line) for line in (tmp_path / "lineage.jsonl").read_text().splitlines()]
    assert {record["occurrence_id"] for record in records} == {"occurrence-1", "occurrence-2"}


def test_normalization_maps_unicode_entities_whitespace_repetition_and_cross_nodes():
    body = "<p>A&amp;  café</p><div>A&amp;<b>café</b></div>".encode()
    extracted = normalize_html(body)
    assert extracted.text == "A& café A& café"
    assert extracted.encoding == "utf-8"
    assert all(span.raw_start < span.raw_end for span in extracted.spans)
    assert all(span.normalized_start < span.normalized_end for span in extracted.spans)
    assert tuple((s.normalized_start, s.normalized_end) for s in extracted.spans) == tuple(sorted((s.normalized_start, s.normalized_end) for s in extracted.spans))
    assert extracted.reproduce(body) == extracted.text


def test_unsupported_encoding_is_review_not_success():
    with pytest.raises(ValueError, match="unsupported_source_encoding"):
        normalize_html(b"\xff")


@pytest.mark.asyncio
async def test_shared_fetch_capture_roundtrips_exact_source_and_spans(tmp_path):
    url = "https://fixture.example/page"
    capability = hashlib.sha256(b"capability").hexdigest()
    endpoint = hashlib.sha256(b"endpoint").hexdigest()
    policy = TrustedFetchPolicy(caller_id="caller-1", scope="scope-1", capability_digest=capability, endpoint_policy_digest=endpoint, allowed_origins=("https://fixture.example:443",))
    request = AcquisitionRequest(
        operation_id="operation-1", caller_id="caller-1", scope="scope-1", capability_digest=capability, endpoint_policy_digest=endpoint,
        inputs=(InputOccurrence(occurrence_id="occurrence-1", input_ref=canonical_input_hash(url), type_tag="http_url", value=url),),
        allocation=Resources(requests=1, bytes=1024, elapsed_seconds=1, concurrency=1),
    )
    live_authority = authority(expires_at=datetime.now(timezone.utc) + timedelta(hours=1))
    decision = live_authority.issue("operation-1")
    result = await execute_fetch_with_source_proof(
        admit_fetch(request, policy, FetchParameters(max_bytes_per_input=1024)),
        artifact_store=FilesystemArtifactStore(tmp_path),
        retention_decision=decision,
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, content="<p>x&amp; x</p>".encode())),
    )
    outcome = result.outcomes[0]
    assert outcome.normalized.text == "x& x"
    assert outcome.capture.artifact is not None
    assert outcome.spans
    resolved_context = context(origin="https://fixture.example:443", requested_url=url, final_url=url, retrieved_at=outcome.capture.artifact.retrieved_at)
    resolved = resolve_source(FilesystemArtifactStore(tmp_path), resolved_context, decision, outcome.capture.artifact)
    assert resolved.body == b"<p>x&amp; x</p>"
