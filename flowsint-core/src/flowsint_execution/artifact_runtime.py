"""Deployment-owned source-artifact runtime configuration.

The configuration path is trusted process configuration.  Request, template, and
source-rights values never select a store or create retention authority.
"""

from __future__ import annotations

import hashlib
import json
import os
import base64
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from .acquisition import ArtifactReference, SourceProofSpanReference
from .artifacts import (
    ArtifactContext, ArtifactState, FilesystemArtifactStore, ResolveResult,
    RetentionAuthority, RetentionDecision, SpanResolveResult, resolve_source,
    resolve_span,
)

RUNTIME_CONFIG_ENV = "FLOWSINT_ARTIFACT_RUNTIME_CONFIG"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ReviewedRetentionPolicy(_Strict):
    issuer_id: str = Field(min_length=1, max_length=256)
    reviewer_id: str = Field(min_length=1, max_length=256)
    policy_id: str = Field(min_length=1, max_length=256)
    content_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    caller_id: str = Field(min_length=1, max_length=256)
    scope: str = Field(min_length=1, max_length=1024)
    source_family: str = Field(min_length=1, max_length=256)
    issued_at: AwareDatetime
    expires_at: AwareDatetime
    retain_normalized_text: bool = True

    @model_validator(mode="after")
    def valid_window(self):
        if self.expires_at <= self.issued_at:
            raise ValueError("reviewed policy expiry must follow issuance")
        return self


class ArtifactRuntimeConfig(_Strict):
    format_version: str = Field(pattern=r"^1\.0$")
    store_root: str = Field(min_length=1, max_length=4096)
    policies: tuple[ReviewedRetentionPolicy, ...]

    @field_validator("store_root")
    @classmethod
    def absolute_store_root(cls, value: str) -> str:
        if not Path(value).is_absolute():
            raise ValueError("artifact store root must be absolute")
        return value


class PersistedSourceProof(_Strict):
    """Body-free, capability-free metadata sufficient for authorized resolution."""

    format_version: str = Field(pattern=r"^source-proof/1\.0$")
    input_ref: str = Field(pattern=r"^[a-f0-9]{64}$")
    context: ArtifactContext
    decision: RetentionDecision
    artifact: ArtifactReference
    spans: tuple[SourceProofSpanReference, ...] = ()

    @model_validator(mode="after")
    def bound_identity(self):
        if self.context.operation_id != self.decision.operation_id:
            raise ValueError("proof operation mismatch")
        if any(span.artifact_id != self.artifact.artifact_id for span in self.spans):
            raise ValueError("proof span artifact mismatch")
        return self


@dataclass(frozen=True)
class ArtifactRuntime:
    store: FilesystemArtifactStore
    authority: RetentionAuthority

    def decision(self, operation_id: str, *, now: datetime | None = None) -> RetentionDecision:
        return self.authority.issue(operation_id, now=now)


def _policy_digest(policy: dict[str, Any]) -> str:
    content = {key: value for key, value in policy.items() if key != "content_digest"}
    canonical = json.dumps(content, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_artifact_runtime(
    *,
    caller_id: str,
    scope: str,
    source_family: str,
    config_path: str | os.PathLike[str] | None = None,
    now: datetime | None = None,
) -> ArtifactRuntime | None:
    """Load the current matching reviewed policy, or fail closed with ``None``."""
    selected_path = Path(config_path) if config_path is not None else None
    if selected_path is None:
        configured = os.environ.get(RUNTIME_CONFIG_ENV)
        if not configured:
            return None
        selected_path = Path(configured)
    try:
        raw_bytes = selected_path.read_bytes()
        raw = json.loads(raw_bytes)
        config = ArtifactRuntimeConfig.model_validate_json(raw_bytes, strict=True)
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    current = now or datetime.now(timezone.utc)
    matches = []
    for raw_policy, policy in zip(raw.get("policies", ()), config.policies, strict=True):
        if _policy_digest(raw_policy) != policy.content_digest:
            continue
        if (
            policy.caller_id == caller_id
            and policy.scope == scope
            and policy.source_family == source_family
            and policy.issued_at <= current < policy.expires_at
        ):
            matches.append(policy)
    if len(matches) != 1:
        return None
    policy = matches[0]
    authority = RetentionAuthority(
        issuer_id=policy.issuer_id,
        reviewer_id=policy.reviewer_id,
        policy_id=policy.policy_id,
        policy_digest=policy.content_digest,
        caller_id=policy.caller_id,
        scope=policy.scope,
        source_family=policy.source_family,
        expires_at=policy.expires_at,
        retain_normalized_text=policy.retain_normalized_text,
    )
    try:
        store = FilesystemArtifactStore(config.store_root)
    except OSError:
        return None
    return ArtifactRuntime(store=store, authority=authority)


def encode_source_proof(proof: PersistedSourceProof) -> str:
    raw = proof.model_dump_json(warnings="error").encode("utf-8")
    encoded = "sp1." + base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    if len(encoded) > 8 * 1024 * 1024:
        raise ValueError("source_proof_too_large")
    return encoded


def decode_source_proof(value: str) -> PersistedSourceProof:
    if not isinstance(value, str) or not value.startswith("sp1."):
        raise ValueError("unsupported_source_proof_version")
    encoded = value[4:]
    try:
        raw = base64.b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True)
        return PersistedSourceProof.model_validate_json(raw, strict=True)
    except Exception as error:
        raise ValueError("invalid_source_proof") from error


def resolve_persisted_source_proof(
    value: str,
    *,
    caller_id: str,
    scope: str,
    source_family: str,
    operation_id: str,
    occurrence_id: str | None = None,
    config_path: str | os.PathLike[str] | None = None,
    now: datetime | None = None,
) -> ResolveResult:
    """Resolve persisted proof using only the current deployment-owned authority."""
    try:
        proof = decode_source_proof(value)
    except ValueError:
        return ResolveResult(ArtifactState.REVIEW, "proof_metadata_invalid")
    if (
        proof.context.caller_id != caller_id
        or proof.context.scope != scope
        or proof.context.source_family != source_family
        or proof.context.operation_id != operation_id
        or proof.decision.operation_id != operation_id
        or (occurrence_id is not None and proof.context.occurrence_id != occurrence_id)
    ):
        return ResolveResult(ArtifactState.REVIEW, "authorization_mismatch")
    runtime = load_artifact_runtime(
        caller_id=caller_id, scope=scope, source_family=source_family,
        config_path=config_path, now=now,
    )
    if runtime is None:
        return ResolveResult(ArtifactState.HOLD, "current_policy_unavailable")
    return resolve_source(
        runtime.store, proof.context, proof.decision, proof.artifact,
        authority=runtime.authority, now=now,
    )


def resolve_persisted_span(
    value: str,
    span_id: str,
    *,
    caller_id: str,
    scope: str,
    source_family: str,
    operation_id: str,
    occurrence_id: str,
    config_path: str | os.PathLike[str] | None = None,
    now: datetime | None = None,
) -> SpanResolveResult:
    try:
        proof = decode_source_proof(value)
    except ValueError:
        return SpanResolveResult(ArtifactState.REVIEW, "proof_metadata_invalid")
    if (
        proof.context.caller_id != caller_id
        or proof.context.scope != scope
        or proof.context.source_family != source_family
        or proof.context.operation_id != operation_id
        or proof.decision.operation_id != operation_id
        or proof.context.occurrence_id != occurrence_id
    ):
        return SpanResolveResult(ArtifactState.REVIEW, "authorization_mismatch")
    matches = tuple(span for span in proof.spans if span.span_id == span_id)
    if len(matches) != 1:
        return SpanResolveResult(ArtifactState.REVIEW, "span_mismatch")
    runtime = load_artifact_runtime(
        caller_id=caller_id, scope=scope, source_family=source_family,
        config_path=config_path, now=now,
    )
    if runtime is None:
        return SpanResolveResult(ArtifactState.HOLD, "current_policy_unavailable")
    return resolve_span(
        runtime.store, proof.context, proof.decision, proof.artifact, matches[0],
        authority=runtime.authority, now=now,
    )


__all__ = [
    "ArtifactRuntime", "ArtifactRuntimeConfig", "PersistedSourceProof",
    "ReviewedRetentionPolicy", "RUNTIME_CONFIG_ENV", "decode_source_proof",
    "encode_source_proof", "load_artifact_runtime", "resolve_persisted_source_proof",
    "resolve_persisted_span",
]
