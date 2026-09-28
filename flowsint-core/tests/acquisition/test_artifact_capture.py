from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from flowsint_execution.acquisition import AcquisitionRequest, InputOccurrence, Resources, SourceProofSpanReference
from flowsint_execution.artifacts import (
    ArtifactContext, ArtifactState, FilesystemArtifactStore, RetentionAuthority,
    capture_source, normalize_html, resolve_source, resolve_span,
)
from flowsint_execution.fetch import FetchParameters, TrustedFetchPolicy, admit_fetch, execute_fetch_with_source_proof
from flowsint_execution.models import canonical_input_hash

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)

def authority(**changes):
    values = dict(issuer_id="deployment-retention", reviewer_id="reviewer-1", policy_id="policy-1",
        policy_digest=hashlib.sha256(b"reviewed policy").hexdigest(), caller_id="caller-1", scope="scope-1",
        source_family="http", expires_at=NOW + timedelta(hours=1))
    values.update(changes)
    return RetentionAuthority(**values)

def context(**changes):
    values = dict(operation_id="operation-1", occurrence_id="occurrence-1", caller_id="caller-1", scope="scope-1",
        source_family="http", origin="https://example.test:443", requested_url="https://example.test/a",
        final_url="https://example.test/b", retrieved_at=NOW)
    values.update(changes)
    return ArtifactContext(**values)

def test_current_authority_required_and_exact_bindings(tmp_path):
    current = authority(); decision = current.issue("operation-1", now=NOW); store = FilesystemArtifactStore(tmp_path)
    captured = capture_source(store, context(), decision, b"source bytes", now=NOW)
    assert captured.state is ArtifactState.AVAILABLE
    absent = resolve_source(store, context(), decision, captured.artifact, now=NOW)
    assert (absent.state, absent.reason, absent.body) == (ArtifactState.HOLD, "current_policy_unavailable", None)
    assert resolve_source(FilesystemArtifactStore(tmp_path), context(), decision, captured.artifact,
                          authority=current, now=NOW).body == b"source bytes"
    for changed in ({"operation_id": "other"}, {"caller_id": "other"}, {"scope": "other"}):
        denied = resolve_source(store, context(**changed), decision, captured.artifact, authority=current, now=NOW)
        assert (denied.state, denied.reason, denied.body) == (ArtifactState.REVIEW, "authorization_mismatch", None)

def test_missing_tampered_truncated_and_expired_are_distinct(tmp_path):
    current = authority(); decision = current.issue("operation-1", now=NOW); store = FilesystemArtifactStore(tmp_path)
    captured = capture_source(store, context(), decision, b"complete", now=NOW)
    next(tmp_path.rglob("*.body")).unlink()
    assert resolve_source(store, context(), decision, captured.artifact, authority=current, now=NOW).reason == "artifact_missing"
    captured = capture_source(store, context(occurrence_id="two"), decision, b"complete", now=NOW)
    next(tmp_path.rglob("*.body")).write_bytes(b"tampered")
    tampered = resolve_source(store, context(occurrence_id="two"), decision, captured.artifact, authority=current, now=NOW)
    assert (tampered.state, tampered.reason, tampered.body) == (ArtifactState.REVIEW, "digest_mismatch", None)
    truncated = capture_source(store, context(occurrence_id="three"), decision, b"partial", complete=False, now=NOW)
    assert (truncated.state, truncated.reason) == (ArtifactState.REVIEW, "truncated_body")
    expired = resolve_source(store, context(occurrence_id="two"), decision, captured.artifact,
                             authority=current, now=NOW + timedelta(hours=2))
    assert expired.reason == "current_policy_expired"

def test_identical_bodies_deduplicate_with_distinct_immutable_records(tmp_path):
    store = FilesystemArtifactStore(tmp_path); decision = authority().issue("operation-1", now=NOW)
    first = capture_source(store, context(), decision, b"same", now=NOW)
    second = capture_source(store, context(occurrence_id="two"), decision, b"same", now=NOW)
    assert first.artifact.locator == second.artifact.locator
    assert first.artifact.snapshot_id != second.artifact.snapshot_id
    assert len(list(tmp_path.rglob("*.body"))) == 1
    assert {json.loads(line)["occurrence_id"] for line in (tmp_path / "lineage.jsonl").read_text().splitlines()} == {"occurrence-1", "two"}
    assert all(path.stat().st_mode & 0o077 == 0 for path in tmp_path.rglob("*.*") if path.is_file())

def test_concurrent_dedup_publication_keeps_every_occurrence(tmp_path):
    store = FilesystemArtifactStore(tmp_path); decision = authority().issue("operation-1", now=NOW)
    def retain(index):
        return capture_source(store, context(occurrence_id=f"occurrence-{index}"), decision, b"same", now=NOW)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(retain, range(24)))
    assert all(result.state is ArtifactState.AVAILABLE for result in results)
    assert len(list(tmp_path.rglob("*.body"))) == 1
    assert len(list((tmp_path / "records").glob("*.json"))) == 24
    assert len((tmp_path / "lineage.jsonl").read_text().splitlines()) == 24

@pytest.mark.parametrize(("body", "expected"), [
    (b'<p title="1 > 0">ok</p>', "ok"),
    (b"alpha < beta <b>gamma</b>", "alpha < beta gamma"),
    (b"<style>x < y</style><p>ok</p>", "ok"),
    (b"<p>a < b</p>", "a < b"),
    (b"<p>&lt; &amp; &#x41;</p>", "< & A"),
    ("<p>😀 café</p>".encode(), "😀 café"),
    (b"<p>x</p><!-- c --><p>x</p>", "x x"),
])
def test_exact_normalization(body, expected):
    value = normalize_html(body)
    assert value.text == expected
    assert value.reproduce(body) == expected
    assert "".join(span.emitted_text for span in value.spans) == expected
    assert all(0 <= span.raw_start < span.raw_end <= len(body) for span in value.spans)
    with pytest.raises(ValueError, match="normalized_source_mismatch"):
        value.reproduce(b"<p>unrelated</p>")

def test_span_resolver_validates_full_reference_and_recomputed_mapping(tmp_path):
    body = b'<p title="1 > 0">x&amp;</p><!-- gap --><p>x</p>'
    normalized = normalize_html(body); current = authority(); decision = current.issue("operation-1", now=NOW)
    store = FilesystemArtifactStore(tmp_path)
    captured = capture_source(store, context(), decision, body, normalized=normalized, now=NOW)
    mapped = normalized.spans[1]
    span = SourceProofSpanReference(span_id=f"span-{captured.artifact.snapshot_id}-1", artifact_id=captured.artifact.artifact_id,
        byte_start=mapped.raw_start, byte_end=mapped.raw_end, normalized_start=mapped.normalized_start,
        normalized_end=mapped.normalized_end, raw_offset_unit="byte", normalized_offset_unit="unicode_code_point",
        source_encoding="utf-8")
    resolved = resolve_span(store, context(), decision, captured.artifact, span, authority=current, now=NOW)
    assert (resolved.state, resolved.reason, resolved.text) == (ArtifactState.AVAILABLE, "resolved", mapped.emitted_text)
    altered = span.model_copy(update={"artifact_id": "artifact-wrong"})
    denied = resolve_span(store, context(), decision, captured.artifact, altered, authority=current, now=NOW)
    assert (denied.state, denied.reason, denied.text) == (ArtifactState.REVIEW, "span_mismatch", None)

def test_policy_future_replacement_and_normalized_flag(tmp_path):
    current = authority()
    future = current.issue("operation-1", now=NOW + timedelta(minutes=1))
    assert capture_source(FilesystemArtifactStore(tmp_path / "future"), context(), future, b"secret", now=NOW).reason == "policy_not_yet_valid"
    decision = current.issue("operation-1", now=NOW); store = FilesystemArtifactStore(tmp_path / "current")
    captured = capture_source(store, context(), decision, b"secret", now=NOW)
    replaced = resolve_source(store, context(), decision, captured.artifact, authority=authority(policy_id="replacement"), now=NOW)
    assert (replaced.state, replaced.reason, replaced.body) == (ArtifactState.REVIEW, "current_policy_mismatch", None)
    disabled = authority(retain_normalized_text=False); disabled_decision = disabled.issue("operation-1", now=NOW)
    held = capture_source(FilesystemArtifactStore(tmp_path / "disabled"), context(), disabled_decision,
                          b"<p>secret</p>", normalized=normalize_html(b"<p>secret</p>"), now=NOW)
    assert (held.state, held.reason, held.artifact) == (ArtifactState.HOLD, "normalized_retention_prohibited", None)
    raw = capture_source(FilesystemArtifactStore(tmp_path / "raw"), context(), disabled_decision, b"<p>secret</p>", now=NOW)
    assert raw.state is ArtifactState.AVAILABLE

def test_tampered_policy_and_normalized_metadata_are_review(tmp_path):
    current = authority(); decision = current.issue("operation-1", now=NOW); store = FilesystemArtifactStore(tmp_path)
    body = b"<p>kept</p>"; captured = capture_source(store, context(), decision, body, normalized=normalize_html(body), now=NOW)
    record = tmp_path / "records" / f"{captured.artifact.snapshot_id}.json"
    metadata = json.loads(record.read_text()); metadata["normalized"]["text"] = "leak"; record.write_text(json.dumps(metadata))
    denied = resolve_source(store, context(), decision, captured.artifact, authority=current, now=NOW)
    assert (denied.state, denied.reason, denied.body) == (ArtifactState.REVIEW, "normalized_metadata_mismatch", None)

    second_store = FilesystemArtifactStore(tmp_path / "naive")
    second = capture_source(second_store, context(), decision, body, now=NOW)
    second_record = tmp_path / "naive" / "records" / f"{second.artifact.snapshot_id}.json"
    naive = json.loads(second_record.read_text()); naive["expires_at"] = "2026-01-01T01:00:00"
    second_record.write_text(json.dumps(naive))
    malformed = resolve_source(second_store, context(), decision, second.artifact, authority=current, now=NOW)
    assert (malformed.state, malformed.reason, malformed.body) == (ArtifactState.REVIEW, "metadata_mismatch", None)

def test_symlink_ancestor_and_store_failure_are_typed(tmp_path):
    decision = authority().issue("operation-1", now=NOW); body = b"escape"
    root = tmp_path / "store"; store = FilesystemArtifactStore(root)
    prefix = hashlib.sha256(body).hexdigest()[:2]; outside = tmp_path / "outside"; outside.mkdir()
    (root / "objects" / prefix).symlink_to(outside, target_is_directory=True)
    denied = capture_source(store, context(), decision, body, now=NOW)
    assert denied.state is ArtifactState.REVIEW and not list(outside.iterdir())

    read_root = tmp_path / "read-store"; read_store = FilesystemArtifactStore(read_root)
    captured = capture_source(read_store, context(), decision, body, now=NOW)
    read_prefix = read_root / "objects" / prefix
    read_prefix.rename(read_root / "saved-prefix")
    read_prefix.symlink_to(outside, target_is_directory=True)
    read_denied = resolve_source(read_store, context(), decision, captured.artifact, authority=authority(), now=NOW)
    assert (read_denied.state, read_denied.reason, read_denied.body) == (ArtifactState.REVIEW, "artifact_missing", None)
    class BrokenStore:
        def write(self, body, metadata): raise PermissionError("offline")
    failed = capture_source(BrokenStore(), context(), decision, b"consumed", now=NOW)
    assert (failed.state, failed.reason, failed.artifact) == (ArtifactState.HOLD, "artifact_store_unavailable", None)
    class InvalidStore:
        def write(self, body, metadata): raise OSError("unsafe storage")
    invalid = capture_source(InvalidStore(), context(), decision, b"consumed", now=NOW)
    assert (invalid.state, invalid.reason, invalid.artifact) == (ArtifactState.REVIEW, "artifact_store_invalid", None)

def test_unsupported_encoding():
    with pytest.raises(ValueError, match="unsupported_source_encoding"): normalize_html(b"\xff")

@pytest.mark.asyncio
async def test_shared_fetch_capture_roundtrip(tmp_path):
    url = "https://fixture.example/page"; capability = hashlib.sha256(b"capability").hexdigest(); endpoint = hashlib.sha256(b"endpoint").hexdigest()
    policy = TrustedFetchPolicy(caller_id="caller-1", scope="scope-1", capability_digest=capability,
        endpoint_policy_digest=endpoint, allowed_origins=("https://fixture.example:443",))
    request = AcquisitionRequest(operation_id="operation-1", caller_id="caller-1", scope="scope-1",
        capability_digest=capability, endpoint_policy_digest=endpoint,
        inputs=(InputOccurrence(occurrence_id="occurrence-1", input_ref=canonical_input_hash(url), type_tag="http_url", value=url),),
        allocation=Resources(requests=1, bytes=1024, elapsed_seconds=1, concurrency=1))
    live = authority(expires_at=datetime.now(timezone.utc) + timedelta(hours=1)); decision = live.issue("operation-1")
    result = await execute_fetch_with_source_proof(admit_fetch(request, policy, FetchParameters(max_bytes_per_input=1024)),
        artifact_store=FilesystemArtifactStore(tmp_path), retention_decision=decision,
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"<p>x&amp; x</p>")))
    outcome = result.outcomes[0]
    resolved_context = context(origin="https://fixture.example:443", requested_url=url, final_url=url,
                               retrieved_at=outcome.capture.artifact.retrieved_at)
    assert resolve_source(FilesystemArtifactStore(tmp_path), resolved_context, decision, outcome.capture.artifact,
                          authority=live).body == b"<p>x&amp; x</p>"
