"""Behavioral tests for the authorized saved-proof extraction bridge."""
import dataclasses, hashlib, json
from datetime import datetime, timedelta, timezone
import pytest
from flowsint_execution.artifact_runtime import (PersistedSourceProof, encode_source_proof,
    load_artifact_runtime, resolve_persisted_observation)
from flowsint_execution.artifacts import ArtifactContext, ArtifactState, capture_source
from flowsint_execution.extraction_runtime import resolve_and_extract_observations
from flowsint_execution.observed_extraction import serialize_observed_extraction_metadata

def _runtime(tmp_path):
    now = datetime.now(timezone.utc)
    policy = {"issuer_id":"deployment","reviewer_id":"reviewer","policy_id":"policy",
        "caller_id":"website-to-text","scope":"local-web-fetch","source_family":"http",
        "issued_at":(now-timedelta(minutes=1)).isoformat(),"expires_at":(now+timedelta(hours=1)).isoformat(),
        "retain_normalized_text":True}
    canonical = json.dumps(policy, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    policy["content_digest"] = hashlib.sha256(canonical.encode()).hexdigest()
    path = tmp_path / "runtime.json"
    path.write_text(json.dumps({"format_version":"1.0","store_root":str(tmp_path/"store"),"policies":[policy]}))
    runtime = load_artifact_runtime(caller_id="website-to-text", scope="local-web-fetch", source_family="http", config_path=path)
    assert runtime is not None
    return path, runtime

def _proof(runtime, body=b'<a href="?page=2">next</a><p>info@example.test</p>'):
    decision = runtime.decision("operation-7")
    context = ArtifactContext(operation_id="operation-7", occurrence_id="input-3", caller_id="website-to-text",
        scope="local-web-fetch", source_family="http", origin="https://example.test:443",
        requested_url="https://example.test/start", final_url="https://example.test/final?view=full",
        retrieved_at=datetime.now(timezone.utc))
    captured = capture_source(runtime.store, context, decision, body)
    return encode_source_proof(PersistedSourceProof(format_version="source-proof/1.0", input_ref="a"*64,
        context=context, decision=decision, artifact=captured.artifact))

@pytest.mark.asyncio
async def test_saved_proof_invokes_pure_extractor_with_proof_identity(tmp_path):
    path, runtime = _runtime(tmp_path)
    state, result = await resolve_and_extract_observations(_proof(runtime), caller_id="website-to-text",
        scope="local-web-fetch", source_family="http", operation_id="operation-7", occurrence_id="input-3", config_path=path)
    assert state is ArtifactState.AVAILABLE and result is not None
    assert {item.occurrence_id for item in result.observations} == {"input-3"}
    assert {item.input_ref for item in result.observations} == {"a"*64}
    assert any(item.value == "https://example.test/final?page=2" for item in result.observations)

@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [("operation_id","other"),("scope","other")])
async def test_saved_proof_denies_cross_authority_replay(tmp_path, field, value):
    path, runtime = _runtime(tmp_path)
    arguments = dict(caller_id="website-to-text", scope="local-web-fetch", source_family="http",
        operation_id="operation-7", occurrence_id="input-3", config_path=path)
    arguments[field] = value
    state, result = await resolve_and_extract_observations(_proof(runtime), **arguments)
    assert state in {ArtifactState.HOLD, ArtifactState.REVIEW}
    assert result is None


@pytest.mark.asyncio
async def test_public_persisted_observation_resolution_denies_altered_identity(tmp_path):
    path, runtime = _runtime(tmp_path)
    proof = _proof(runtime, b'<section class="person"><h2>Ada One</h2><p>ada@example.test</p></section>')
    state, extracted = await resolve_and_extract_observations(proof, caller_id="website-to-text",
        scope="local-web-fetch", source_family="http", operation_id="operation-7",
        occurrence_id="input-3", config_path=path)
    assert state is ArtifactState.AVAILABLE and extracted is not None
    metadata = serialize_observed_extraction_metadata(extracted)
    observation = next(item for item in extracted.observations if item.value == "ada@example.test")
    resolved = resolve_persisted_observation(proof, metadata, observation.observation_id,
        caller_id="website-to-text", scope="local-web-fetch", source_family="http",
        operation_id="operation-7", occurrence_id="input-3", config_path=path)
    assert resolved.state is ArtifactState.AVAILABLE
    assert resolved.raw == b"ada@example.test"
    mutations = (
        dataclasses.replace(observation, value="altered@example.test"),
        dataclasses.replace(observation, person_name="Mallory Other"),
        dataclasses.replace(observation, raw_span=dataclasses.replace(
            observation.raw_span, start_byte=observation.raw_span.start_byte - 1)),
        dataclasses.replace(observation, context_span=dataclasses.replace(
            observation.context_span, start_byte=observation.context_span.start_byte + 1)),
    )
    for altered in mutations:
        altered_result = dataclasses.replace(extracted, observations=(altered,))
        altered_metadata = serialize_observed_extraction_metadata(altered_result)
        denied = resolve_persisted_observation(proof, altered_metadata,
            observation.observation_id, caller_id="website-to-text", scope="local-web-fetch",
            source_family="http", operation_id="operation-7", occurrence_id="input-3", config_path=path)
        assert denied.state is ArtifactState.REVIEW
