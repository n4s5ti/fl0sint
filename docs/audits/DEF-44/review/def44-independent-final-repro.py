#!/home/n4s5ti/Documents/dev/fl0sint/.venv/bin/python
"""Independent read-only DEF44 corrected-archive verification and adversarial repros."""
from __future__ import annotations

import asyncio
import hashlib
import inspect
import importlib.util
import json
import os
import tempfile
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from pydantic import ConfigDict, BaseModel, ValidationError

from flowsint_execution.acquisition import (
    AcquisitionRequest, InputOccurrence, Resources, SpanReference,
)
from flowsint_execution.artifact_runtime import (
    PersistedSourceProof, decode_source_proof, encode_source_proof,
    load_artifact_runtime, resolve_persisted_source_proof, resolve_persisted_span,
)
from flowsint_execution.artifacts import (
    ArtifactContext, ArtifactState, FilesystemArtifactStore, RetentionAuthority,
    capture_source, normalize_html, resolve_source, resolve_span,
)
from flowsint_execution.fetch import (
    FetchParameters, TrustedFetchPolicy, admit_fetch, execute_fetch_with_source_proof,
)
from flowsint_execution.models import EvidenceEnvelope, canonical_input_hash


NOW = datetime.now(timezone.utc)


def write_config(path: Path, store: Path, *, caller="caller-a", scope="scope-a",
                 family="http", policy_id="policy-a", issued=None, expires=None,
                 retain_normalized_text=True) -> Path:
    policy = {
        "issuer_id": "issuer-a", "reviewer_id": "reviewer-a", "policy_id": policy_id,
        "caller_id": caller, "scope": scope, "source_family": family,
        "issued_at": (issued or NOW - timedelta(hours=1)).isoformat(),
        "expires_at": (expires or NOW + timedelta(hours=1)).isoformat(),
        "retain_normalized_text": retain_normalized_text,
    }
    canonical = json.dumps(policy, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    policy["content_digest"] = hashlib.sha256(canonical.encode()).hexdigest()
    path.write_text(json.dumps({"format_version": "1.0", "store_root": str(store), "policies": [policy]}))
    return path


def make_request(*, elapsed=1.0, url="https://fixture.example/a?secret=hidden"):
    cap = hashlib.sha256(b"cap").hexdigest(); endpoint = hashlib.sha256(b"endpoint").hexdigest()
    policy = TrustedFetchPolicy(caller_id="caller-a", scope="scope-a", capability_digest=cap,
                                endpoint_policy_digest=endpoint,
                                allowed_origins=("https://fixture.example:443",))
    request = AcquisitionRequest(operation_id="operation-a", caller_id="caller-a", scope="scope-a",
        capability_digest=cap, endpoint_policy_digest=endpoint,
        inputs=(InputOccurrence(occurrence_id="occurrence-a", input_ref=canonical_input_hash(url),
                                type_tag="http_url", value=url),),
        allocation=Resources(requests=1, bytes=2_000_000, elapsed_seconds=elapsed, concurrency=1))
    return admit_fetch(request, policy, FetchParameters(max_bytes_per_input=2_000_000))


def normalization_and_resolution(root: Path):
    cases = {
        "literal_lt": b"<p>a < b</p>",
        "quoted_gt": b'<p title="1 > 0">ok</p>',
        "comment": b"<p>a</p><!-- hidden > text --><p>b</p>",
        "unicode_entities": "<p>\U0001f600 caf\u00e9 &lt; &amp; &#x41;</p>".encode(),
        "repeated": b"<p>x</p><p>x</p>",
        "script_style": b"<script>bad < x</script><style>worse > y</style><p>ok</p>",
    }
    runtime = load_artifact_runtime(caller_id="caller-a", scope="scope-a", source_family="http",
                                    config_path=write_config(root/"runtime.json", root/"store"), now=NOW)
    assert runtime is not None
    evidence = {}
    for idx, (name, body) in enumerate(cases.items()):
        normalized = normalize_html(body)
        context = ArtifactContext(operation_id="operation-a", occurrence_id=f"occ-{idx}",
            caller_id="caller-a", scope="scope-a", source_family="http",
            origin="https://fixture.example:443", requested_url="https://fixture.example/a",
            final_url="https://fixture.example/a", retrieved_at=NOW)
        decision = runtime.decision("operation-a", now=NOW)
        captured = capture_source(runtime.store, context, decision, body, normalized=normalized, now=NOW)
        assert captured.state is ArtifactState.AVAILABLE
        proof_spans = []
        for span_idx, span in enumerate(normalized.spans):
            from flowsint_execution.acquisition import SourceProofSpanReference
            wire = SourceProofSpanReference(span_id=f"span-{captured.artifact.snapshot_id}-{span_idx}",
                artifact_id=captured.artifact.artifact_id, byte_start=span.raw_start, byte_end=span.raw_end,
                normalized_start=span.normalized_start, normalized_end=span.normalized_end)
            got = resolve_span(runtime.store, context, decision, captured.artifact, wire,
                               authority=runtime.authority, now=NOW)
            assert got.state is ArtifactState.AVAILABLE and got.text == span.emitted_text
            proof_spans.append(wire)
        assert normalized.reproduce(body) == normalized.text
        try: normalized.reproduce(b"<p>unrelated</p>")
        except ValueError: unrelated_denied = True
        else: unrelated_denied = False
        assert unrelated_denied
        evidence[name] = {"text": normalized.text, "spans": len(normalized.spans),
                          "unrelated_denied": unrelated_denied}
    assert evidence["quoted_gt"]["text"] == "ok"
    assert evidence["comment"]["text"] == "a b"
    assert evidence["script_style"]["text"] == "ok"

    # Corrupt a mapping without changing retained bytes: resolver must reject it.
    last = proof_spans[-1]
    bad = last.model_copy(update={"byte_start": last.byte_start + 1})
    denied = resolve_span(runtime.store, context, decision, captured.artifact, bad,
                          authority=runtime.authority, now=NOW)
    assert denied.state is ArtifactState.REVIEW
    return evidence


async def policy_and_elapsed(root: Path):
    config = write_config(root/"policy.json", root/"policy-store")
    runtime = load_artifact_runtime(caller_id="caller-a", scope="scope-a", source_family="http",
                                    config_path=config, now=NOW)
    body = b"<p>kept</p>"
    operation = make_request(elapsed=0.05)

    class SlowStore:
        def __init__(self, delegate): self.delegate = delegate
        def write(self, body, metadata):
            time.sleep(0.15)
            self.delegate.write(body, metadata)

    started = time.monotonic()
    result = await execute_fetch_with_source_proof(operation, artifact_store=SlowStore(runtime.store),
        retention_decision=runtime.decision("operation-a", now=NOW),
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, content=body)))
    wall = time.monotonic() - started
    outcome = result.outcomes[0]
    assert outcome.capture.state is ArtifactState.AVAILABLE
    elapsed_claim = result.actual_resources.elapsed_seconds
    elapsed_overrun = wall > operation.allocation.elapsed_seconds and elapsed_claim < wall

    # retain_normalized_text=false must not emit normalized output or spans.
    disabled_config = write_config(root/"disabled.json", root/"disabled-store",
                                   retain_normalized_text=False)
    disabled = load_artifact_runtime(caller_id="caller-a", scope="scope-a", source_family="http",
                                     config_path=disabled_config, now=NOW)
    denied = await execute_fetch_with_source_proof(make_request(), artifact_store=disabled.store,
        retention_decision=disabled.decision("operation-a", now=NOW),
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, content=body)))
    assert denied.outcomes[0].normalized is None and denied.outcomes[0].spans == ()
    assert denied.outcomes[0].capture.reason == "normalized_retention_prohibited"
    class BrokenStore:
        def write(self, body, metadata): raise OSError("fixture write failure")
    failed = await execute_fetch_with_source_proof(make_request(), artifact_store=BrokenStore(),
        retention_decision=runtime.decision("operation-a", now=NOW),
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, content=b"consumed")))
    assert failed.outcomes[0].capture.reason == "artifact_store_invalid"
    assert failed.outcomes[0].actual_resources.bytes == len(b"consumed")
    return {"post_fetch_wall_seconds": wall, "reported_elapsed_seconds": elapsed_claim,
            "allocation_seconds": operation.allocation.elapsed_seconds,
            "elapsed_overrun": elapsed_overrun,
            "normalized_policy_deny": denied.outcomes[0].capture.reason,
            "write_error_accounted_bytes": failed.outcomes[0].actual_resources.bytes}


def policy_identity_and_symlinks(root: Path):
    authority = RetentionAuthority(issuer_id="i", reviewer_id="r", policy_id="p",
        policy_digest=hashlib.sha256(b"p").hexdigest(), caller_id="c", scope="s",
        source_family="http", expires_at=NOW+timedelta(hours=1))
    context = ArtifactContext(operation_id="o", occurrence_id="x", caller_id="c", scope="s",
        source_family="http", origin="https://x:443", requested_url="https://x/",
        final_url="https://x/", retrieved_at=NOW)
    store = FilesystemArtifactStore(root/"identity")
    future = authority.issue("o", now=NOW+timedelta(minutes=1))
    assert capture_source(store, context, future, b"x", now=NOW).reason == "policy_not_yet_valid"
    decision = authority.issue("o", now=NOW)
    captured = capture_source(store, context, decision, b"x", now=NOW)
    replaced = resolve_source(store, context, decision, captured.artifact,
        authority=authority.model_copy(update={"policy_id":"replacement"}), now=NOW)
    assert replaced.reason == "current_policy_mismatch"
    expired = resolve_source(store, context, decision, captured.artifact, authority=authority,
                             now=NOW+timedelta(hours=2))
    assert expired.reason in ("current_policy_expired", "policy_expired")

    body=b"symlink"; digest=hashlib.sha256(body).hexdigest(); symlink_store=FilesystemArtifactStore(root/"symlink")
    outside=root/"outside"; outside.mkdir(); (root/"symlink"/"objects"/digest[:2]).symlink_to(outside, target_is_directory=True)
    denied=capture_source(symlink_store, context, decision, body, now=NOW)
    assert denied.state is ArtifactState.REVIEW and not list(outside.iterdir())
    return {"future": "policy_not_yet_valid", "replacement": replaced.reason,
            "expired": expired.reason, "symlink_ancestor": denied.reason}


def persisted_policy_and_operation(root: Path):
    config = write_config(root/"persisted.json", root/"persisted-store")
    runtime = load_artifact_runtime(caller_id="caller-a", scope="scope-a", source_family="http",
                                    config_path=config, now=NOW)
    decision = runtime.decision("operation-a", now=NOW)
    context = ArtifactContext(operation_id="operation-a", occurrence_id="occurrence-a",
        caller_id="caller-a", scope="scope-a", source_family="http",
        origin="https://fixture.example:443", requested_url="https://fixture.example/a",
        final_url="https://fixture.example/a", retrieved_at=NOW)
    captured = capture_source(runtime.store, context, decision, b"operation-a secret", now=NOW)
    proof = encode_source_proof(PersistedSourceProof(format_version="source-proof/1.0",
        input_ref="a"*64, context=context, decision=decision, artifact=captured.artifact, spans=()))
    sig = str(inspect.signature(resolve_persisted_source_proof))
    assert "operation_id" not in inspect.signature(resolve_persisted_source_proof).parameters
    cross_operation = resolve_persisted_source_proof(proof, caller_id="caller-a", scope="scope-a",
                                                     source_family="http", config_path=config, now=NOW)
    assert cross_operation.state is ArtifactState.AVAILABLE

    # Changing current deployment config after capture is observed at resolution.
    write_config(config, root/"persisted-store", policy_id="replacement")
    replaced = resolve_persisted_source_proof(proof, caller_id="caller-a", scope="scope-a",
                                              source_family="http", config_path=config, now=NOW)
    assert replaced.state is ArtifactState.REVIEW and replaced.reason == "current_policy_mismatch"
    os.unlink(config)
    revoked = resolve_persisted_source_proof(proof, caller_id="caller-a", scope="scope-a",
                                             source_family="http", config_path=config, now=NOW)
    assert revoked.state is ArtifactState.HOLD
    return {"resolver_signature": sig, "same_caller_scope_cross_operation": cross_operation.state.value,
            "replacement": replaced.reason, "revocation": revoked.reason,
            "proof_bytes": len(proof)}


def compatibility_and_bounds(root: Path):
    class StrictV1(BaseModel):
        model_config = ConfigDict(extra="forbid", strict=True)
        span_id: str; artifact_id: str; byte_start: int|None=None; byte_end: int|None=None; field_pointer: str|None=None
    legacy = SpanReference(span_id="s", artifact_id="a", byte_start=0, byte_end=1)
    StrictV1.model_validate(legacy.model_dump(), strict=True)

    # A record just below the 8 MiB store cap expands above the 8 MiB evidence-field cap in base64.
    body = (b"x " * 40_000)
    normalized = normalize_html(body)
    authority = RetentionAuthority(issuer_id="i", reviewer_id="r", policy_id="p",
        policy_digest=hashlib.sha256(b"p").hexdigest(), caller_id="caller-a", scope="scope-a",
        source_family="http", expires_at=NOW+timedelta(hours=1))
    context = ArtifactContext(operation_id="operation-a", occurrence_id="large", caller_id="caller-a",
        scope="scope-a", source_family="http", origin="https://fixture.example:443",
        requested_url="https://fixture.example/a", final_url="https://fixture.example/a", retrieved_at=NOW)
    store = FilesystemArtifactStore(root/"large")
    decision = authority.issue("operation-a", now=NOW)
    result = capture_source(store, context,
                            decision, body,
                            normalized=normalized, now=NOW)
    assert result.state is ArtifactState.AVAILABLE
    from flowsint_execution.acquisition import SourceProofSpanReference
    spans = tuple(SourceProofSpanReference(span_id=f"span-{result.artifact.snapshot_id}-{i}",
        artifact_id=result.artifact.artifact_id, byte_start=s.raw_start, byte_end=s.raw_end,
        normalized_start=s.normalized_start, normalized_end=s.normalized_end)
        for i, s in enumerate(normalized.spans))
    proof = encode_source_proof(PersistedSourceProof(format_version="source-proof/1.0", input_ref="a"*64,
        context=context, decision=decision, artifact=result.artifact, spans=spans))
    try:
        EvidenceEnvelope(input_ref="a"*64, destination_id="website_to_text", endpoint_id="shared_http_fetch",
            capability="enrich.read", policy_version="source-proof/1.0", artifact_sha256=result.artifact.content_digest,
            artifact_reference=proof, source_rights="reviewed_runtime_policy", schema_version="source-proof/1.0",
            parser_version="html-normalizer/1", confidence=1.0, verification_state="retained")
    except ValidationError: proof_rejected = True
    else: proof_rejected = False
    assert len(proof) > 8 * 1024 * 1024 and proof_rejected

    too_large_body = b"x " * 50_000
    too_large = capture_source(FilesystemArtifactStore(root/"too-large"), context, decision,
        too_large_body, normalized=normalize_html(too_large_body), now=NOW)
    assert too_large.state is ArtifactState.REVIEW and too_large.reason == "artifact_store_invalid"
    return {"legacy_v1_fields": sorted(legacy.model_dump()), "fragmented_span_count": len(normalized.spans),
            "proof_bytes": len(proof), "proof_rejected_after_retention": proof_rejected,
            "oversized_metadata": too_large.reason,
            "evidence_reference_max": EvidenceEnvelope.model_fields["artifact_reference"].metadata[-1].max_length}


async def example_bundle(root: Path):
    example_path = Path(os.environ["ARCHIVE"]) / "flowsint-core/examples/source_proof.py"
    spec = importlib.util.spec_from_file_location("def44_source_proof_example", example_path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    real = execute_fetch_with_source_proof
    async def empty_transport(operation, *, artifact_store, retention_decision):
        return await real(operation, artifact_store=artifact_store, retention_decision=retention_decision,
                          transport=httpx.MockTransport(lambda _request: httpx.Response(200, content=b"<html></html>")))
    module.execute_fetch_with_source_proof = empty_transport
    try:
        value = await module.run("https://fixture.example/empty", root/"example-store")
    except Exception as error:
        empty = f"{type(error).__name__}:{error}"
    else:
        empty = {"state": value["state"], "bundle_length": len(value.get("bundle", ""))}
    return {"valid_empty_result": empty}


async def main():
    with tempfile.TemporaryDirectory(prefix="def44-independent-final-") as td:
        root = Path(td)
        report = {
            "normalization": normalization_and_resolution(root),
            "policy_and_elapsed": await policy_and_elapsed(root),
            "policy_identity_and_symlinks": policy_identity_and_symlinks(root),
            "persisted_policy_and_operation": persisted_policy_and_operation(root),
            "compatibility_and_bounds": compatibility_and_bounds(root),
            "example": await example_bundle(root),
        }
        print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
