"""Immutable, redacted value models for structured enricher execution."""

from __future__ import annotations

import base64
import hashlib
import math
import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class OutcomeStatus(str, Enum):
    """Terminal status of one original enricher input."""

    SUCCESS = "success"
    FAILURE = "failure"
    HOLD = "hold"


class RedactedDiagnostic(BaseModel):
    """A stable diagnostic that is safe to retain outside process memory."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str = Field(min_length=1, max_length=128, pattern=r"^[a-z0-9_]+$")
    safe_message: str = Field(min_length=1, max_length=512)
    retryable: bool


class EvidenceEnvelope(BaseModel):
    """Retainable evidence metadata without a response body or rendered URL."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    input_ref: str = Field(pattern=r"^[a-f0-9]{64}$")
    destination_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,127}$")
    endpoint_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,127}$")
    capability: str = Field(pattern=r"^enrich\.read$")
    policy_version: str = Field(min_length=1, max_length=64)
    artifact_sha256: str | None = None
    artifact_reference: str | None = Field(default=None, max_length=256)
    source_rights: str = Field(default="unspecified", min_length=1, max_length=128)
    schema_version: str = Field(min_length=1, max_length=64)
    parser_version: str = Field(min_length=1, max_length=64)
    confidence: float = Field(ge=0.0, le=1.0)
    verification_state: str = Field(min_length=1, max_length=64)
    event_at: datetime | None = None
    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    ingested_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("event_at", "retrieved_at", "ingested_at")
    @classmethod
    def normalize_timestamp(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("evidence timestamps must be timezone-aware")
        return value.astimezone(timezone.utc)


class InputOutcome(BaseModel):
    """The grouped structured result for exactly one original input."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    input_ref: str = Field(pattern=r"^[a-f0-9]{64}$")
    status: OutcomeStatus
    outputs: tuple[Any, ...] = ()
    diagnostic: RedactedDiagnostic | None = None
    evidence: tuple[EvidenceEnvelope, ...] = ()

    @model_validator(mode="after")
    def require_diagnostic_for_non_success(self) -> InputOutcome:
        if self.status is not OutcomeStatus.SUCCESS and self.diagnostic is None:
            raise ValueError("non-success outcomes require a diagnostic")
        if self.status is not OutcomeStatus.SUCCESS and self.outputs:
            raise ValueError("non-success outcomes cannot include outputs")
        if self.status is OutcomeStatus.SUCCESS and self.diagnostic is not None:
            raise ValueError("success outcomes cannot include a diagnostic")
        return self


class StructuredExecutionResult(BaseModel):
    """Structured execution output retaining input cardinality and grouping."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    enricher_name: str = Field(min_length=1, max_length=256)
    outcomes: tuple[InputOutcome, ...]


def _canonicalize_input(value: Any) -> Any:
    """Convert supported input values to a deterministic JSON-compatible shape."""

    if isinstance(value, BaseModel):
        return _canonicalize_input(
            value.model_dump(mode="json", by_alias=True, exclude_none=False)
        )
    if isinstance(value, Mapping):
        return {
            str(key): _canonicalize_input(item)
            for key, item in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_canonicalize_input(item) for item in value]
    if isinstance(value, (set, frozenset)):
        canonical_items = [_canonicalize_input(item) for item in value]
        return sorted(
            canonical_items,
            key=lambda item: json.dumps(
                item, ensure_ascii=False, separators=(",", ":"), sort_keys=True
            ),
        )
    if isinstance(value, float):
        if math.isnan(value):
            return {"float": "nan"}
        if math.isinf(value):
            return {"float": "infinity" if value > 0 else "-infinity"}
        return value
    if isinstance(value, Enum):
        return _canonicalize_input(value.value)
    if isinstance(value, datetime):
        if value.tzinfo is not None and value.utcoffset() is not None:
            value = value.astimezone(timezone.utc)
        return value.isoformat()
    if isinstance(value, bytes):
        return {"bytes_base64": base64.b64encode(value).decode("ascii")}
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if hasattr(value, "model_dump"):
        return _canonicalize_input(value.model_dump(mode="json"))
    return {"type": f"{type(value).__module__}.{type(value).__qualname__}"}


def canonical_input_hash(value: Any) -> str:
    """Return the stable SHA-256 reference for an input without retaining it."""

    canonical = json.dumps(
        _canonicalize_input(value),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
