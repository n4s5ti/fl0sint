"""S05 Integration bridge: authorized retained-snapshot extraction."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import asyncio
import hashlib
import json
import uuid
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from .acquisition import Resources
from .models import PersistedMetadata, RedactedDiagnostic
from .observed_extraction import ObservedExtractionResult

if TYPE_CHECKING:
    from os import PathLike
    from .observed_extraction import ExtractionPolicy
    from .artifacts import ArtifactState


@dataclass(frozen=True)
class LiveObservedExtraction:
    status: str
    state: str
    source_proof: str | None
    metadata: PersistedMetadata | None
    observations: ObservedExtractionResult | None
    actual_resources: Resources
    diagnostic: RedactedDiagnostic | str | None


def _origin(url: str) -> str:
    parts = urlsplit(url)
    port = parts.port if parts.port is not None else (443 if parts.scheme == "https" else 80)
    host = parts.hostname or ""
    host = f"[{host.lower()}]" if ":" in host else host.lower()
    return f"{parts.scheme}://{host}:{port}"


async def execute_live_observed_extraction(
    url: str, *, config_path: str | PathLike[str] | None,
    caller_id: str = "website-to-text", scope: str = "local-web-fetch",
) -> LiveObservedExtraction:
    """Fetch, retain, and extract one controlled URL without graph/auth services."""
    from .acquisition import AcquisitionRequest, InputOccurrence, Resources
    from .artifact_runtime import (PersistedSourceProof, encode_source_proof,
                                   load_artifact_runtime)
    from .artifacts import ArtifactContext, ArtifactState
    from .fetch import (FetchParameters, FetchStatus, TrustedFetchPolicy,
                        admit_fetch, execute_fetch_with_source_proof)
    from .models import canonical_input_hash
    from .observed_extraction import serialize_observed_extraction_metadata

    runtime = load_artifact_runtime(
        caller_id=caller_id, scope=scope, source_family="http", config_path=config_path,
    )
    empty = Resources(requests=0, bytes=0, elapsed_seconds=0.0, concurrency=0)
    if runtime is None:
        return LiveObservedExtraction("hold", ArtifactState.HOLD.value, None, None, None,
                                      empty, "current_policy_unavailable")
    origin = _origin(url)
    capability = hashlib.sha256(b"flowsint.website-to-text.fetch.v1").hexdigest()
    endpoint = hashlib.sha256(json.dumps(
        {"allowed_origins": [origin], "redirects": "same-origin-only"},
        sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()
    operation_id = f"observed-extraction-{uuid.uuid4().hex}"
    input_ref = canonical_input_hash(url)
    request = AcquisitionRequest(
        operation_id=operation_id, caller_id=caller_id, scope=scope,
        capability_digest=capability, endpoint_policy_digest=endpoint,
        inputs=(InputOccurrence(occurrence_id="input-0", input_ref=input_ref,
                                type_tag="http_url", value=url),),
        allocation=Resources(requests=4, bytes=4 * 1024 * 1024,
                             elapsed_seconds=10.0, concurrency=1),
    )
    operation = admit_fetch(request, TrustedFetchPolicy(
        caller_id=caller_id, scope=scope, capability_digest=capability,
        endpoint_policy_digest=endpoint, allowed_origins=(origin,),
    ), FetchParameters(max_redirects=3, max_retries=0,
                       max_bytes_per_input=1024 * 1024))
    decision = runtime.decision(operation_id)
    fetched = await execute_fetch_with_source_proof(
        operation, artifact_store=runtime.store, retention_decision=decision,
    )
    item = fetched.outcomes[0]
    if item.fetch_status is not FetchStatus.SUCCESS:
        return LiveObservedExtraction(item.fetch_status.value, item.capture.state.value,
                                      None, None, None, fetched.actual_resources,
                                      item.diagnostic)
    if item.capture.state is not ArtifactState.AVAILABLE or item.capture.artifact is None:
        return LiveObservedExtraction("hold" if item.capture.state is ArtifactState.HOLD else "review",
                                      item.capture.state.value, None, None, None,
                                      fetched.actual_resources, item.capture.reason)
    artifact = item.capture.artifact
    context = ArtifactContext(
        operation_id=operation_id, occurrence_id=item.occurrence_id,
        caller_id=caller_id, scope=scope, source_family="http", origin=artifact.origin,
        requested_url=artifact.requested_url, final_url=artifact.final_url,
        retrieved_at=artifact.retrieved_at, event_at=artifact.event_at,
    )
    proof = encode_source_proof(PersistedSourceProof(
        format_version="source-proof/1.0", input_ref=input_ref, context=context,
        decision=decision, artifact=artifact, spans=item.spans,
    ))
    if item.observations is None:
        return LiveObservedExtraction("review", ArtifactState.REVIEW.value, proof, None, None,
                                      fetched.actual_resources, "observation_result_unavailable")
    metadata = serialize_observed_extraction_metadata(item.observations)
    return LiveObservedExtraction("success", ArtifactState.AVAILABLE.value, proof, metadata,
                                  item.observations, fetched.actual_resources, None)


async def resolve_and_extract_observations(
    source_proof: str,
    *,
    caller_id: str,
    scope: str,
    source_family: str,
    operation_id: str,
    occurrence_id: str,
    config_path: str | PathLike[str] | None = None,
    policy: ExtractionPolicy | None = None,
    now: datetime | None = None,
) -> tuple[ArtifactState, ObservedExtractionResult | None]:
    """
    Authorized retained-snapshot observation extraction bridge.

    This function validates artifact state, resolves retained bytes, and invokes
    the pure extractor. It never mutates the graph and returns unreviewed
    observations as immutable value records.

    Args:
        source_proof: Encoded persisted source proof for the retained artifact.
        caller_id: Caller identity expected by the current artifact runtime.
        scope: Authorization scope expected by the current artifact runtime.
        source_family: Source family expected by the current artifact runtime.
        operation_id: Operation ID expected in the persisted proof.
        occurrence_id: Occurrence ID expected in the persisted proof.
        config_path: Optional artifact-runtime configuration path.
        policy: Optional ExtractionPolicy with S03 bounds (None = permissive defaults)
        now: Optional timestamp (default: now in UTC)

    Returns:
        (state, result) tuple where:
            - state: ArtifactState (AVAILABLE, HOLD, REVIEW, etc.)
            - result: ObservedExtractionResult or None if state is not AVAILABLE

    Contract:
        - Validate artifact state and current-policy authority
        - Resolve retained bytes from store
        - Invoke pure extractor on verified body using the proof's final URL,
          including any query string
        - Return state + result; no graph mutation
        - Return None result if state is HOLD or REVIEW
    """
    # Import here to avoid circular dependencies
    from .artifacts import ArtifactState
    from .artifact_runtime import decode_source_proof, resolve_persisted_source_proof
    from .observed_extraction import extract_observations

    try:
        proof = decode_source_proof(source_proof)
    except ValueError:
        return ArtifactState.REVIEW, None
    proof_result = resolve_persisted_source_proof(
        source_proof, caller_id=caller_id, scope=scope, source_family=source_family,
        operation_id=operation_id, occurrence_id=occurrence_id,
        config_path=config_path, now=now,
    )

    if proof_result.state != ArtifactState.AVAILABLE:
        return proof_result.state, None

    if proof_result.body is None:
        return ArtifactState.REVIEW, None

    result = await asyncio.to_thread(
        extract_observations, proof_result.body,
        artifact=proof.artifact, occurrence_id=proof.context.occurrence_id,
        input_ref=proof.input_ref, final_url=proof.context.final_url,
        policy=policy,
    )

    return ArtifactState.AVAILABLE, result


__all__ = ["LiveObservedExtraction", "execute_live_observed_extraction",
           "resolve_and_extract_observations"]
