"""Fetch one controlled URL, retain source proof, and resolve it in a fresh store."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from flowsint_execution.acquisition import AcquisitionRequest, InputOccurrence, Resources
from flowsint_execution.artifacts import ArtifactContext, FilesystemArtifactStore, RetentionAuthority, resolve_source
from flowsint_execution.fetch import FetchParameters, TrustedFetchPolicy, admit_fetch, execute_fetch_with_source_proof
from flowsint_execution.models import canonical_input_hash


async def run(url: str, store_root: Path) -> dict[str, object]:
    parts = urlsplit(url)
    port = parts.port or (443 if parts.scheme == "https" else 80)
    origin = f"{parts.scheme}://{parts.hostname}:{port}"
    capability = hashlib.sha256(b"source-proof-example-v1").hexdigest()
    endpoint = hashlib.sha256(origin.encode()).hexdigest()
    operation_id = "source-proof-example"
    occurrence_id = "fixture-1"
    policy = TrustedFetchPolicy(caller_id="source-proof-example", scope="controlled-fixture", capability_digest=capability, endpoint_policy_digest=endpoint, allowed_origins=(origin,))
    request = AcquisitionRequest(
        operation_id=operation_id, caller_id=policy.caller_id, scope=policy.scope,
        capability_digest=capability, endpoint_policy_digest=endpoint,
        inputs=(InputOccurrence(occurrence_id=occurrence_id, input_ref=canonical_input_hash(url), type_tag="http_url", value=url),),
        allocation=Resources(requests=2, bytes=1_000_000, elapsed_seconds=10, concurrency=1),
    )
    operation = admit_fetch(request, policy, FetchParameters(max_redirects=1, max_bytes_per_input=1_000_000))
    authority = RetentionAuthority(
        issuer_id="local-example", reviewer_id="local-fixture-reviewer", policy_id="controlled-fixture-v1",
        policy_digest=hashlib.sha256(b"controlled fixture reviewed retention v1").hexdigest(),
        caller_id=policy.caller_id, scope=policy.scope, source_family="http",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
    )
    decision = authority.issue(operation_id)
    result = await execute_fetch_with_source_proof(operation, artifact_store=FilesystemArtifactStore(store_root), retention_decision=decision)
    outcome = result.outcomes[0]
    if outcome.capture.artifact is None or outcome.normalized is None:
        return {"state": outcome.capture.state.value, "reason": outcome.capture.reason}
    context = ArtifactContext(
        operation_id=operation_id, occurrence_id=occurrence_id, caller_id=policy.caller_id,
        scope=policy.scope, source_family="http", origin=outcome.capture.artifact.origin,
        requested_url=outcome.capture.artifact.requested_url, final_url=outcome.capture.artifact.final_url,
        retrieved_at=outcome.capture.artifact.retrieved_at,
    )
    resolved = resolve_source(FilesystemArtifactStore(store_root), context, decision, outcome.capture.artifact)
    return {
        "state": resolved.state.value,
        "digest": outcome.capture.artifact.content_digest,
        "normalized_text": outcome.normalized.text,
        "spans": [span.model_dump(mode="json") for span in outcome.spans],
        "resolved_bytes": len(resolved.body or b""),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("fixture_url", help="Controlled HTTP(S) fixture URL")
    parser.add_argument("store", type=Path, help="New or existing trusted artifact-store directory")
    arguments = parser.parse_args()
    print(json.dumps(asyncio.run(run(arguments.fixture_url, arguments.store)), sort_keys=True))
