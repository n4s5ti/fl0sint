import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest

from flowsint_execution.artifact_runtime import (
    PersistedSourceProof, decode_source_proof, encode_source_proof,
    load_artifact_runtime, resolve_persisted_source_proof,
)
from flowsint_execution.acquisition import SpanReference, SourceProofSpanReference
from pydantic import ValidationError
from flowsint_execution.artifacts import ArtifactContext, ArtifactState, capture_source


def _write_config(tmp_path, *, caller="runtime-caller", scope="runtime-scope", expired=False):
    now = datetime.now(timezone.utc)
    policy = {
        "issuer_id": "deployment-owner",
        "reviewer_id": "reviewed-by-operator",
        "policy_id": "retention-v1",
        "caller_id": caller,
        "scope": scope,
        "source_family": "http",
        "issued_at": (now - timedelta(hours=2)).isoformat(),
        "expires_at": (now - timedelta(hours=1) if expired else now + timedelta(hours=1)).isoformat(),
        "retain_normalized_text": True,
    }
    canonical = json.dumps(policy, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    policy["content_digest"] = hashlib.sha256(canonical.encode()).hexdigest()
    path = tmp_path / "artifact-runtime.json"
    path.write_text(json.dumps({
        "format_version": "1.0",
        "store_root": str(tmp_path / "store"),
        "policies": [policy],
    }))
    return path


def test_runtime_policy_and_persisted_proof_roundtrip(tmp_path):
    config = _write_config(tmp_path)
    runtime = load_artifact_runtime(
        caller_id="runtime-caller", scope="runtime-scope", source_family="http",
        config_path=config,
    )
    assert runtime is not None
    decision = runtime.decision("operation-1")
    retrieved = datetime.now(timezone.utc)
    context = ArtifactContext(
        operation_id="operation-1", occurrence_id="input-7",
        caller_id="runtime-caller", scope="runtime-scope", source_family="http",
        origin="https://example.test:443", requested_url="https://example.test/a",
        final_url="https://example.test/a", retrieved_at=retrieved,
    )
    capture = capture_source(runtime.store, context, decision, b"complete source")
    assert capture.state is ArtifactState.AVAILABLE
    proof = PersistedSourceProof(
        format_version="source-proof/1.0", input_ref="a" * 64,
        context=context, decision=decision, artifact=capture.artifact, spans=(),
    )
    encoded = encode_source_proof(proof)
    assert decode_source_proof(encoded) == proof
    resolved = resolve_persisted_source_proof(
        encoded, caller_id="runtime-caller", scope="runtime-scope",
        source_family="http", config_path=config,
    )
    assert resolved.state is ArtifactState.AVAILABLE
    assert resolved.body == b"complete source"
    assert str(tmp_path / "store") not in encoded


@pytest.mark.parametrize("case", ["missing", "expired", "scope", "revoked"])
def test_runtime_current_policy_fail_closed(tmp_path, case):
    config = _write_config(tmp_path, expired=case == "expired")
    if case == "missing":
        config = tmp_path / "missing.json"
    if case == "revoked":
        config = _write_config(tmp_path, caller="replacement-caller")
    scope = "wrong-scope" if case == "scope" else "runtime-scope"
    runtime = load_artifact_runtime(
        caller_id="runtime-caller", scope=scope, source_family="http",
        config_path=config,
    )
    assert runtime is None


def test_proof_version_rejection_is_explicit():
    with pytest.raises(ValueError, match="unsupported_source_proof_version"):
        decode_source_proof("sp2.invalid")


def test_v1_bundle_span_stays_strict_while_proof_mapping_is_separately_versioned():
    legacy = SpanReference(
        span_id="legacy", artifact_id="artifact", byte_start=0, byte_end=1
    )
    assert set(legacy.model_dump()) == {
        "span_id", "artifact_id", "byte_start", "byte_end", "field_pointer"
    }
    with pytest.raises(ValidationError):
        SpanReference.model_validate({
            **legacy.model_dump(), "normalized_start": 0, "normalized_end": 1,
        })
    proof = SourceProofSpanReference(
        span_id="mapped", artifact_id="artifact", byte_start=0, byte_end=1,
        normalized_start=0, normalized_end=1,
    )
    assert proof.proof_format_version == "1.0"
