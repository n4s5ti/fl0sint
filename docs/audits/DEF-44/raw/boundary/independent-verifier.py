from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from flowsint_execution.acquisition import (
    AcquisitionRequest, InputOccurrence, OutcomeStatus, Resources, parse_bundle,
)
from flowsint_execution.artifact_runtime import (
    PersistedSourceProof, encode_source_proof, load_artifact_runtime,
    resolve_persisted_source_proof,
)
from flowsint_execution.artifacts import ArtifactContext, ArtifactState, FilesystemArtifactStore, normalize_html
from flowsint_execution.fetch import (
    FetchParameters, FetchStatus, TrustedFetchPolicy, admit_fetch,
    execute_fetch_with_source_proof,
)
from flowsint_execution.models import canonical_input_hash


def write_config(root: Path) -> Path:
    now = datetime.now(timezone.utc)
    policy = {
        "issuer_id": "deployment-owner", "reviewer_id": "independent-reviewer",
        "policy_id": "retention-v1", "caller_id": "runtime-caller",
        "scope": "runtime-scope", "source_family": "http",
        "issued_at": (now - timedelta(hours=1)).isoformat(),
        "expires_at": (now + timedelta(hours=1)).isoformat(),
        "retain_normalized_text": True,
    }
    canonical = json.dumps(policy, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    policy["content_digest"] = hashlib.sha256(canonical.encode()).hexdigest()
    path = root / "runtime.json"
    path.write_text(json.dumps({"format_version": "1.0", "store_root": str(root / "store"), "policies": [policy]}))
    return path


def admitted(operation_id: str, elapsed: float):
    url = "https://fixture.example/page"
    capability = hashlib.sha256(b"capability").hexdigest()
    endpoint = hashlib.sha256(b"endpoint").hexdigest()
    policy = TrustedFetchPolicy(caller_id="runtime-caller", scope="runtime-scope", capability_digest=capability,
                                endpoint_policy_digest=endpoint, allowed_origins=("https://fixture.example:443",))
    request = AcquisitionRequest(
        operation_id=operation_id, caller_id="runtime-caller", scope="runtime-scope",
        capability_digest=capability, endpoint_policy_digest=endpoint,
        inputs=(InputOccurrence(occurrence_id="occurrence-1", input_ref=canonical_input_hash(url),
                                type_tag="http_url", value=url),),
        allocation=Resources(requests=1, bytes=100_000, elapsed_seconds=elapsed, concurrency=1),
    )
    return admit_fetch(request, policy, FetchParameters(max_bytes_per_input=100_000))


def authorization_and_compaction(root: Path, config: Path):
    runtime = load_artifact_runtime(caller_id="runtime-caller", scope="runtime-scope", source_family="http", config_path=config)
    assert runtime is not None
    operation_id = "operation-a"
    decision = runtime.decision(operation_id)
    body = b"x " * 40_000
    normalized = normalize_html(body)
    assert normalized.reproduce(body) == body.decode().rstrip()
    assert len(normalized.spans) == 1
    context = ArtifactContext(
        operation_id=operation_id, occurrence_id="occurrence-a", caller_id="runtime-caller",
        scope="runtime-scope", source_family="http", origin="https://fixture.example:443",
        requested_url="https://fixture.example/page", final_url="https://fixture.example/page",
        retrieved_at=datetime.now(timezone.utc),
    )
    from flowsint_execution.artifacts import capture_source
    captured = capture_source(runtime.store, context, decision, body, normalized=normalized)
    assert captured.state is ArtifactState.AVAILABLE
    from flowsint_execution.acquisition import SourceProofSpanReference
    spans = tuple(SourceProofSpanReference(
        span_id=f"span-{captured.artifact.snapshot_id}-{i}", artifact_id=captured.artifact.artifact_id,
        byte_start=s.raw_start, byte_end=s.raw_end, normalized_start=s.normalized_start,
        normalized_end=s.normalized_end, raw_offset_unit="byte",
        normalized_offset_unit="unicode_code_point", source_encoding="utf-8",
    ) for i, s in enumerate(normalized.spans))
    encoded = encode_source_proof(PersistedSourceProof(
        format_version="source-proof/1.0", input_ref="a" * 64, context=context,
        decision=decision, artifact=captured.artifact, spans=spans,
    ))
    good = resolve_persisted_source_proof(encoded, caller_id="runtime-caller", scope="runtime-scope",
                                           source_family="http", operation_id=operation_id,
                                           occurrence_id="occurrence-a", config_path=config)
    bad_operation = resolve_persisted_source_proof(encoded, caller_id="runtime-caller", scope="runtime-scope",
                                                    source_family="http", operation_id="operation-b",
                                                    occurrence_id="occurrence-a", config_path=config)
    bad_occurrence = resolve_persisted_source_proof(encoded, caller_id="runtime-caller", scope="runtime-scope",
                                                     source_family="http", operation_id=operation_id,
                                                     occurrence_id="occurrence-b", config_path=config)
    assert good.state is ArtifactState.AVAILABLE
    assert (bad_operation.state, bad_operation.reason, bad_operation.body) == (ArtifactState.REVIEW, "authorization_mismatch", None)
    assert (bad_occurrence.state, bad_occurrence.reason, bad_occurrence.body) == (ArtifactState.REVIEW, "authorization_mismatch", None)
    assert len(encoded) <= 8 * 1024 * 1024
    return {"normalized_bytes": len(body), "span_count": len(spans), "proof_bytes": len(encoded),
            "proof_limit": 8 * 1024 * 1024, "authorized": good.state.value,
            "wrong_operation": [bad_operation.state.value, bad_operation.reason],
            "wrong_occurrence": [bad_occurrence.state.value, bad_occurrence.reason]}


async def timeout_and_cancellation(root: Path, config: Path):
    runtime = load_artifact_runtime(caller_id="runtime-caller", scope="runtime-scope", source_family="http", config_path=config)
    class SlowStore:
        def __init__(self, path): self.delegate = FilesystemArtifactStore(path); self.writes = 0
        def write(self, body, metadata):
            self.writes += 1
            time.sleep(0.2)
            self.delegate.write(body, metadata)

    body = b"<p>consumed</p>"
    transport = httpx.MockTransport(lambda _: httpx.Response(200, content=body))
    slow = SlowStore(root / "slow-timeout")
    started = time.monotonic()
    result = await execute_fetch_with_source_proof(admitted("timeout-op", 0.05), artifact_store=slow,
        retention_decision=runtime.authority.issue("timeout-op"), transport=transport)
    wall = time.monotonic() - started
    outcome = result.outcomes[0]
    assert wall < 0.15 and outcome.fetch_status is FetchStatus.TIMEOUT
    assert outcome.capture.artifact is None and outcome.normalized is None and outcome.spans == ()
    assert result.actual_resources.bytes == len(body) and result.actual_resources.elapsed_seconds >= 0.045

    cancelled_store = SlowStore(root / "slow-cancel")
    started_cancel = time.monotonic()
    task = asyncio.create_task(execute_fetch_with_source_proof(admitted("cancel-op", 1.0), artifact_store=cancelled_store,
        retention_decision=runtime.authority.issue("cancel-op"), transport=transport))
    await asyncio.sleep(0.02)
    task.cancel()
    cancelled = await task
    cancel_wall = time.monotonic() - started_cancel
    cancelled_outcome = cancelled.outcomes[0]
    assert cancel_wall < 0.15 and cancelled_outcome.fetch_status is FetchStatus.CANCELLED
    assert cancelled_outcome.capture.artifact is None and cancelled_outcome.normalized is None and cancelled_outcome.spans == ()
    assert cancelled.actual_resources.bytes == len(body)
    await asyncio.sleep(0.25)
    return {"timeout": {"wall": wall, "reported_elapsed": result.actual_resources.elapsed_seconds,
                         "bytes": result.actual_resources.bytes, "status": outcome.fetch_status.value,
                         "artifact": None, "span_count": 0, "store_writes_started": slow.writes},
            "cancellation": {"wall": cancel_wall, "reported_elapsed": cancelled.actual_resources.elapsed_seconds,
                             "bytes": cancelled.actual_resources.bytes, "status": cancelled_outcome.fetch_status.value,
                             "artifact": None, "span_count": 0, "store_writes_started": cancelled_store.writes}}


async def empty_example(root: Path):
    example = Path("flowsint-core/examples/source_proof.py").resolve()
    spec = importlib.util.spec_from_file_location("independent_source_proof_example", example)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    real = execute_fetch_with_source_proof
    async def empty_fetch(operation, *, artifact_store, retention_decision):
        return await real(operation, artifact_store=artifact_store, retention_decision=retention_decision,
                          transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"<html></html>")))
    module.execute_fetch_with_source_proof = empty_fetch
    result = await module.run("https://fixture.example/empty", root / "example-store")
    bundle = parse_bundle(result["bundle"])
    outcome = bundle.outcomes[0]
    assert result["state"] == ArtifactState.AVAILABLE.value
    assert outcome.status is OutcomeStatus.VALID_NO_RESULT
    assert outcome.candidate_ids == () and outcome.completion_witness is not None
    return {"state": result["state"], "status": outcome.status.value, "candidates": len(outcome.candidate_ids),
            "completion_witness": outcome.completion_witness.reference, "serialized_bytes": len(result["bundle"])}


async def main():
    with tempfile.TemporaryDirectory(prefix="def44-independent-") as temporary:
        root = Path(temporary)
        config = write_config(root)
        report = {
            "authorization_and_compaction": authorization_and_compaction(root, config),
            "timeout_and_cancellation": await timeout_and_cancellation(root, config),
            "empty_example": await empty_example(root),
        }
        print(json.dumps(report, indent=2, sort_keys=True))


asyncio.run(main())
