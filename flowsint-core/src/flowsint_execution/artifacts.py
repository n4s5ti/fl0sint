"""Reviewed source retention, exact text mapping, and authorized local resolution."""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import secrets
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from .acquisition import ArtifactReference

_DIGEST = r"^[a-f0-9]{64}$"
_TOKEN = re.compile(r"(?is)<!--.*?-->|<![^>]*>|</?([a-zA-Z][^\s/>]*)[^>]*>|([^<]+)")
_ENTITY = re.compile(r"&(?:#\d+|#x[0-9a-fA-F]+|[A-Za-z][A-Za-z0-9]+);")
_SPACE = re.compile(r"\s+")


class ArtifactState(str, Enum):
    AVAILABLE = "available"
    HOLD = "hold"
    REVIEW = "review"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ArtifactContext(_Strict):
    operation_id: str = Field(min_length=1, max_length=256)
    occurrence_id: str = Field(min_length=1, max_length=256)
    caller_id: str = Field(min_length=1, max_length=256)
    scope: str = Field(min_length=1, max_length=1024)
    source_family: str = Field(min_length=1, max_length=256)
    origin: str = Field(min_length=1, max_length=8192)
    requested_url: str | None = None
    final_url: str | None = None
    retrieved_at: AwareDatetime
    event_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def url_pair(self):
        if (self.requested_url is None) != (self.final_url is None):
            raise ValueError("requested and final URLs must be supplied together")
        return self


class RetentionDecision(_Strict):
    issuer_id: str = Field(min_length=1, max_length=256)
    reviewer_id: str = Field(min_length=1, max_length=256)
    policy_id: str = Field(min_length=1, max_length=256)
    policy_digest: str = Field(pattern=_DIGEST)
    operation_id: str = Field(min_length=1, max_length=256)
    caller_id: str = Field(min_length=1, max_length=256)
    scope: str = Field(min_length=1, max_length=1024)
    source_family: str = Field(min_length=1, max_length=256)
    issued_at: AwareDatetime
    expires_at: AwareDatetime
    retain_body: bool = True
    retain_normalized_text: bool = True

    @model_validator(mode="after")
    def valid_window(self):
        if self.expires_at <= self.issued_at:
            raise ValueError("retention decision expiry must follow issuance")
        if not self.retain_body:
            raise ValueError("S04 source proof requires retained body authority")
        return self


class RetentionAuthority(_Strict):
    """Trusted runtime configuration; templates and request values cannot create it."""

    issuer_id: str
    reviewer_id: str
    policy_id: str
    policy_digest: str = Field(pattern=_DIGEST)
    caller_id: str
    scope: str
    source_family: str
    expires_at: AwareDatetime
    retain_normalized_text: bool = True

    def issue(self, operation_id: str, *, now: datetime | None = None) -> RetentionDecision:
        issued = now or datetime.now(timezone.utc)
        return RetentionDecision(
            issuer_id=self.issuer_id,
            reviewer_id=self.reviewer_id,
            policy_id=self.policy_id,
            policy_digest=self.policy_digest,
            operation_id=operation_id,
            caller_id=self.caller_id,
            scope=self.scope,
            source_family=self.source_family,
            issued_at=issued,
            expires_at=self.expires_at,
            retain_normalized_text=self.retain_normalized_text,
        )


@dataclass(frozen=True)
class NormalizedSpan:
    raw_start: int
    raw_end: int
    normalized_start: int
    normalized_end: int
    emitted_text: str


@dataclass(frozen=True)
class NormalizedSource:
    text: str
    spans: tuple[NormalizedSpan, ...]
    encoding: str = "utf-8"
    raw_offset_unit: str = "byte"
    normalized_offset_unit: str = "unicode_code_point"

    def reproduce(self, body: bytes) -> str:
        if body.decode("utf-8", errors="strict") is None:  # pragma: no cover
            raise ValueError("unsupported_source_encoding")
        return "".join(span.emitted_text for span in self.spans)


@dataclass(frozen=True)
class CaptureResult:
    state: ArtifactState
    reason: str
    artifact: ArtifactReference | None = None

    def to_metadata(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "reason": self.reason,
            "artifact": self.artifact.model_dump(mode="json") if self.artifact else None,
        }


@dataclass(frozen=True)
class ResolveResult:
    state: ArtifactState
    reason: str
    body: bytes | None = None


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _bound(context: ArtifactContext, decision: RetentionDecision, now: datetime) -> str | None:
    if now >= decision.expires_at:
        return "policy_expired"
    if (
        context.operation_id != decision.operation_id
        or context.caller_id != decision.caller_id
        or context.scope != decision.scope
        or context.source_family != decision.source_family
    ):
        return "authorization_mismatch"
    return None


class FilesystemArtifactStore:
    """Bounded local store. Its trusted root is never accepted from artifact metadata."""

    def __init__(self, root: str | os.PathLike[str]):
        self.root = Path(root).resolve()
        self.objects = self.root / "objects"
        self.records = self.root / "records"
        self.objects.mkdir(parents=True, exist_ok=True)
        self.records.mkdir(parents=True, exist_ok=True)

    def _object_path(self, digest: str) -> Path:
        if not re.fullmatch(_DIGEST, digest):
            raise ValueError("invalid digest")
        return self.objects / digest[:2] / f"{digest}.body"

    def write(self, body: bytes, metadata: dict[str, Any]) -> None:
        digest = hashlib.sha256(body).hexdigest()
        target = self._object_path(digest)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if target.is_symlink() or target.read_bytes() != body:
                raise OSError("artifact object collision")
        else:
            temporary = target.parent / f".{digest}.{secrets.token_hex(8)}.tmp"
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                with os.fdopen(fd, "wb") as output:
                    output.write(body)
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(temporary, target)
            finally:
                if temporary.exists():
                    temporary.unlink()
        record_id = metadata["snapshot_id"]
        record = self.records / f"{record_id}.json"
        record.write_bytes(_canonical(metadata))
        line = _canonical(metadata) + b"\n"
        fd = os.open(self.root / "lineage.jsonl", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, line)
        finally:
            os.close(fd)

    def read(self, digest: str, snapshot_id: str) -> tuple[bytes, dict[str, Any]]:
        if not re.fullmatch(r"^[a-f0-9]{32}$", snapshot_id):
            raise FileNotFoundError
        record = self.records / f"{snapshot_id}.json"
        target = self._object_path(digest)
        if record.is_symlink() or target.is_symlink():
            raise FileNotFoundError
        return target.read_bytes(), json.loads(record.read_bytes())


def capture_source(
    store: FilesystemArtifactStore,
    context: ArtifactContext,
    decision: RetentionDecision | None,
    body: bytes | None,
    *,
    complete: bool = True,
    normalized: NormalizedSource | None = None,
    now: datetime | None = None,
) -> CaptureResult:
    current = now or datetime.now(timezone.utc)
    if body is None:
        return CaptureResult(ArtifactState.REVIEW, "body_missing")
    if not complete:
        return CaptureResult(ArtifactState.REVIEW, "truncated_body")
    if decision is None:
        return CaptureResult(ArtifactState.HOLD, "retention_policy_unavailable")
    mismatch = _bound(context, decision, current)
    if mismatch:
        return CaptureResult(ArtifactState.REVIEW, mismatch)
    digest = hashlib.sha256(body).hexdigest()
    snapshot_id = secrets.token_hex(16)
    locator = f"sha256:{digest}"
    artifact = ArtifactReference(
        artifact_id=f"artifact-{snapshot_id}",
        snapshot_id=snapshot_id,
        content_digest=digest,
        byte_length=len(body),
        locator=locator,
        source_family=context.source_family,
        origin=context.origin,
        retrieved_at=context.retrieved_at,
        event_at=context.event_at,
        requested_url=context.requested_url,
        final_url=context.final_url,
    )
    metadata = {
        "snapshot_id": snapshot_id,
        "operation_id": context.operation_id,
        "occurrence_id": context.occurrence_id,
        "caller_id": context.caller_id,
        "scope": context.scope,
        "source_family": context.source_family,
        "origin": context.origin,
        "requested_url": context.requested_url,
        "final_url": context.final_url,
        "retrieved_at": context.retrieved_at.isoformat(),
        "event_at": context.event_at.isoformat() if context.event_at else None,
        "content_digest": digest,
        "byte_length": len(body),
        "policy_digest": decision.policy_digest,
        "issuer_id": decision.issuer_id,
        "reviewer_id": decision.reviewer_id,
        "expires_at": decision.expires_at.isoformat(),
        "normalized": ({"text": normalized.text, "spans": [asdict(s) for s in normalized.spans]} if normalized and decision.retain_normalized_text else None),
    }
    store.write(body, metadata)
    return CaptureResult(ArtifactState.AVAILABLE, "retained", artifact)


def resolve_source(
    store: FilesystemArtifactStore,
    context: ArtifactContext,
    decision: RetentionDecision | None,
    artifact: ArtifactReference,
    *,
    now: datetime | None = None,
) -> ResolveResult:
    current = now or datetime.now(timezone.utc)
    if decision is None:
        return ResolveResult(ArtifactState.HOLD, "retention_policy_unavailable")
    mismatch = _bound(context, decision, current)
    if mismatch:
        return ResolveResult(ArtifactState.REVIEW, mismatch)
    try:
        body, metadata = store.read(artifact.content_digest, artifact.snapshot_id)
    except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError):
        return ResolveResult(ArtifactState.REVIEW, "artifact_missing")
    try:
        retained_until = datetime.fromisoformat(metadata["expires_at"])
    except (KeyError, TypeError, ValueError):
        return ResolveResult(ArtifactState.REVIEW, "metadata_mismatch")
    if current >= retained_until:
        return ResolveResult(ArtifactState.REVIEW, "artifact_expired")
    expected = {
        "operation_id": context.operation_id,
        "occurrence_id": context.occurrence_id,
        "caller_id": context.caller_id,
        "scope": context.scope,
        "source_family": context.source_family,
        "origin": context.origin,
        "requested_url": context.requested_url,
        "final_url": context.final_url,
        "policy_digest": decision.policy_digest,
        "content_digest": artifact.content_digest,
        "byte_length": artifact.byte_length,
    }
    if any(metadata.get(key) != value for key, value in expected.items()):
        return ResolveResult(ArtifactState.REVIEW, "metadata_mismatch")
    if (
        artifact.origin != context.origin
        or artifact.requested_url != context.requested_url
        or artifact.final_url != context.final_url
        or metadata.get("retrieved_at") != artifact.retrieved_at.isoformat()
    ):
        return ResolveResult(ArtifactState.REVIEW, "metadata_mismatch")
    if len(body) != artifact.byte_length or hashlib.sha256(body).hexdigest() != artifact.content_digest:
        return ResolveResult(ArtifactState.REVIEW, "digest_mismatch")
    return ResolveResult(ArtifactState.AVAILABLE, "resolved", body)


def normalize_html(body: bytes) -> NormalizedSource:
    """UTF-8 HTML to text with half-open raw-byte/code-point segment mapping."""
    try:
        source = body.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise ValueError("unsupported_source_encoding") from error
    chunks: list[tuple[str, int, int]] = []
    byte_cursor = 0
    ignored: str | None = None
    for match in _TOKEN.finditer(source):
        token = match.group(0)
        token_start = byte_cursor
        token_end = token_start + len(token.encode("utf-8"))
        byte_cursor = token_end
        tag = match.group(1).lower() if match.group(1) else None
        if tag:
            closing = token.startswith("</")
            if tag in ("script", "style"):
                ignored = None if closing else tag
            continue
        if match.group(2) is None or ignored:
            continue
        # One mapping segment per source text node keeps entity expansion and
        # whitespace collapse exact without allocating one record per character.
        chunks.append((html.unescape(token), token_start, token_end))

    pieces: list[tuple[str, int, int]] = []
    for value, raw_start, raw_end in chunks:
        clean = _SPACE.sub(" ", value).strip()
        if clean:
            if pieces:
                pieces.append((" ", pieces[-1][2], raw_start))
            pieces.append((clean, raw_start, raw_end))
    normalized_cursor = 0
    spans = []
    for emitted, raw_start, raw_end in pieces:
        end = normalized_cursor + len(emitted)
        spans.append(NormalizedSpan(raw_start, max(raw_start + 1, raw_end), normalized_cursor, end, emitted))
        normalized_cursor = end
    return NormalizedSource("".join(piece[0] for piece in pieces), tuple(spans))


__all__ = [
    "ArtifactContext", "ArtifactState", "CaptureResult", "FilesystemArtifactStore",
    "NormalizedSource", "NormalizedSpan", "ResolveResult", "RetentionAuthority",
    "RetentionDecision", "capture_source", "normalize_html", "resolve_source",
]
