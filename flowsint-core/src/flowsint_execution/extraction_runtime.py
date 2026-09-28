"""S05 Integration bridge: authorized retained-snapshot extraction."""

from __future__ import annotations

from datetime import datetime
import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from os import PathLike
    from .observed_extraction import ExtractionPolicy, ObservedExtractionResult
    from .artifacts import ArtifactState


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
        store: FilesystemArtifactStore for retained artifact access
        context: ArtifactContext for artifact resolution
        decision: RetentionDecision or None
        artifact: ArtifactReference with digest and snapshot identity
        occurrence_id: S02 occurrence ID (unique, preserve across dedupes)
        input_ref: S02 input reference hash (for grouping)
        final_url: Final requested URL (no query, per fetch.py:862-866)
        authority: Optional RetentionAuthority for current-policy validation
        policy: Optional ExtractionPolicy with S03 bounds (None = permissive defaults)
        now: Optional timestamp (default: now in UTC)

    Returns:
        (state, result) tuple where:
            - state: ArtifactState (AVAILABLE, HOLD, REVIEW, etc.)
            - result: ObservedExtractionResult or None if state is not AVAILABLE

    Contract:
        - Validate artifact state and current-policy authority
        - Resolve retained bytes from store
        - Invoke pure extractor on verified body
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


__all__ = ["resolve_and_extract_observations"]
