"""Reviewed source retention, exact text mapping, and authorized local resolution."""
from __future__ import annotations

import fcntl, hashlib, html, json, os, re, secrets, stat
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator
from .acquisition import ArtifactReference, SourceProofSpanReference

_DIGEST = r"^[a-f0-9]{64}$"
# The persisted proof repeats normalized mapping metadata and is base64 encoded into
# an 8 MiB EvidenceEnvelope field.  Keeping store metadata below 4 MiB leaves a
# deterministic margin for the bounded context, decision, artifact and wire fields.
_MAX_METADATA_BYTES = 4 * 1024 * 1024
_MAX_EVIDENCE_PROOF_BYTES = 8 * 1024 * 1024
_MAX_ARTIFACT_BYTES = 1024 * 1024 * 1024

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
    issuer_id: str
    reviewer_id: str
    policy_id: str
    policy_digest: str = Field(pattern=_DIGEST)
    caller_id: str
    scope: str
    source_family: str
    expires_at: AwareDatetime
    retain_body: bool = True
    retain_normalized_text: bool = True
    def issue(self, operation_id: str, *, now: datetime | None = None) -> RetentionDecision:
        return RetentionDecision(
            issuer_id=self.issuer_id, reviewer_id=self.reviewer_id,
            policy_id=self.policy_id, policy_digest=self.policy_digest,
            operation_id=operation_id, caller_id=self.caller_id, scope=self.scope,
            source_family=self.source_family, issued_at=now or datetime.now(timezone.utc),
            expires_at=self.expires_at, retain_body=self.retain_body,
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
    source_digest: str = ""
    def reproduce(self, body: bytes) -> str:
        recomputed = normalize_html(body)
        if self != recomputed:
            raise ValueError("normalized_source_mismatch")
        return recomputed.text

@dataclass(frozen=True)
class CaptureResult:
    state: ArtifactState
    reason: str
    artifact: ArtifactReference | None = None
    def to_metadata(self) -> dict[str, Any]:
        return {"state": self.state.value, "reason": self.reason,
                "artifact": self.artifact.model_dump(mode="json") if self.artifact else None}

@dataclass(frozen=True)
class ResolveResult:
    state: ArtifactState
    reason: str
    body: bytes | None = None

@dataclass(frozen=True)
class SpanResolveResult:
    state: ArtifactState
    reason: str
    text: str | None = None

def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()

def _aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None

def _bound(context: ArtifactContext, decision: RetentionDecision, now: datetime) -> str | None:
    if not _aware(now) or not _aware(decision.issued_at) or not _aware(decision.expires_at):
        return "malformed_policy_time"
    if now < decision.issued_at:
        return "policy_not_yet_valid"
    if now >= decision.expires_at:
        return "policy_expired"
    if (context.operation_id != decision.operation_id or context.caller_id != decision.caller_id
            or context.scope != decision.scope or context.source_family != decision.source_family):
        return "authorization_mismatch"
    return None

def _current_policy(decision: RetentionDecision, authority: RetentionAuthority | None,
                    now: datetime) -> tuple[ArtifactState, str] | None:
    if authority is None:
        return ArtifactState.HOLD, "current_policy_unavailable"
    if not _aware(authority.expires_at):
        return ArtifactState.REVIEW, "malformed_current_policy"
    if now >= authority.expires_at:
        return ArtifactState.REVIEW, "current_policy_expired"
    fields = ("issuer_id", "reviewer_id", "policy_id", "policy_digest", "caller_id", "scope",
              "source_family", "expires_at", "retain_body", "retain_normalized_text")
    if any(getattr(decision, name) != getattr(authority, name) for name in fields):
        return ArtifactState.REVIEW, "current_policy_mismatch"
    return None

class FilesystemArtifactStore:
    """Content-addressed store with no-follow descriptor traversal."""
    def __init__(self, root: str | os.PathLike[str]):
        self.root = Path(root).absolute()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        root_fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for name in ("objects", "records"):
                try: os.mkdir(name, 0o700, dir_fd=root_fd)
                except FileExistsError: pass
                fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
                os.close(fd)
        finally: os.close(root_fd)
        self.objects, self.records = self.root / "objects", self.root / "records"
    def _root_fd(self) -> int:
        return os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    @staticmethod
    def _dir(fd: int, name: str) -> int:
        return os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
    @staticmethod
    def _read_bounded(fd: int, limit: int) -> bytes:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise OSError("unsafe or oversized artifact file")
        chunks, remaining = [], info.st_size
        while remaining:
            chunk = os.read(fd, min(remaining, 1024 * 1024))
            if not chunk: raise OSError("short artifact read")
            chunks.append(chunk); remaining -= len(chunk)
        if os.read(fd, 1): raise OSError("artifact grew during read")
        return b"".join(chunks)
    @staticmethod
    def _write_all(fd: int, data: bytes) -> None:
        view = memoryview(data)
        while view:
            count = os.write(fd, view)
            if count <= 0: raise OSError("short write")
            view = view[count:]
    def write(self, body: bytes, metadata: dict[str, Any]) -> None:
        digest, snapshot_id = hashlib.sha256(body).hexdigest(), metadata.get("snapshot_id")
        if len(body) > _MAX_ARTIFACT_BYTES or not isinstance(snapshot_id, str) or not re.fullmatch(r"[a-f0-9]{32}", snapshot_id):
            raise ValueError("invalid artifact")
        record_bytes = _canonical(metadata)
        if len(record_bytes) > _MAX_METADATA_BYTES: raise ValueError("oversized metadata")
        root_fd = self._root_fd()
        try:
            objects_fd = self._dir(root_fd, "objects")
            try:
                try: os.mkdir(digest[:2], 0o700, dir_fd=objects_fd)
                except FileExistsError: pass
                prefix_fd = self._dir(objects_fd, digest[:2])
                try:
                    name = f"{digest}.body"
                    temporary = f".{digest}.{secrets.token_hex(8)}.tmp"
                    object_fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=prefix_fd)
                    try:
                        try:
                            self._write_all(object_fd, body); os.fsync(object_fd)
                        finally: os.close(object_fd)
                        try: os.link(temporary, name, src_dir_fd=prefix_fd, dst_dir_fd=prefix_fd, follow_symlinks=False)
                        except FileExistsError:
                            object_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=prefix_fd)
                            try:
                                if self._read_bounded(object_fd, len(body)) != body: raise OSError("artifact collision")
                            finally: os.close(object_fd)
                    finally:
                        os.unlink(temporary, dir_fd=prefix_fd)
                finally: os.close(prefix_fd)
            finally: os.close(objects_fd)
            records_fd = self._dir(root_fd, "records")
            try:
                temporary = f".{snapshot_id}.{secrets.token_hex(8)}.tmp"
                fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=records_fd)
                try:
                    try: self._write_all(fd, record_bytes); os.fsync(fd)
                    finally: os.close(fd)
                    os.link(temporary, f"{snapshot_id}.json", src_dir_fd=records_fd, dst_dir_fd=records_fd, follow_symlinks=False)
                finally:
                    os.unlink(temporary, dir_fd=records_fd)
            finally: os.close(records_fd)
            fd = os.open("lineage.jsonl", os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600, dir_fd=root_fd)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
                self._write_all(fd, record_bytes + b"\n")
            finally: os.close(fd)
        finally: os.close(root_fd)
    def read(self, digest: str, snapshot_id: str, *, expected_length: int | None = None) -> tuple[bytes, dict[str, Any]]:
        if not re.fullmatch(_DIGEST, digest) or not re.fullmatch(r"[a-f0-9]{32}", snapshot_id): raise FileNotFoundError
        if expected_length is None or not 0 <= expected_length <= _MAX_ARTIFACT_BYTES: raise ValueError("invalid length")
        root_fd = self._root_fd()
        try:
            records_fd = self._dir(root_fd, "records")
            try:
                fd = os.open(f"{snapshot_id}.json", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=records_fd)
                try: metadata = json.loads(self._read_bounded(fd, _MAX_METADATA_BYTES))
                finally: os.close(fd)
            finally: os.close(records_fd)
            objects_fd = self._dir(root_fd, "objects")
            try:
                prefix_fd = self._dir(objects_fd, digest[:2])
                try:
                    fd = os.open(f"{digest}.body", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=prefix_fd)
                    try: body = self._read_bounded(fd, expected_length)
                    finally: os.close(fd)
                finally: os.close(prefix_fd)
            finally: os.close(objects_fd)
            return body, metadata
        finally: os.close(root_fd)

def capture_source(store: FilesystemArtifactStore, context: ArtifactContext,
                   decision: RetentionDecision | None, body: bytes | None, *, complete: bool = True,
                   normalized: NormalizedSource | None = None, now: datetime | None = None,
                   cancelled: Callable[[], bool] | None = None) -> CaptureResult:
    current = now or datetime.now(timezone.utc)
    if cancelled is not None and cancelled(): return CaptureResult(ArtifactState.HOLD, "capture_cancelled")
    if body is None: return CaptureResult(ArtifactState.REVIEW, "body_missing")
    if not complete: return CaptureResult(ArtifactState.REVIEW, "truncated_body")
    if decision is None: return CaptureResult(ArtifactState.HOLD, "retention_policy_unavailable")
    mismatch = _bound(context, decision, current)
    if mismatch: return CaptureResult(ArtifactState.REVIEW, mismatch)
    if normalized is not None and not decision.retain_normalized_text:
        return CaptureResult(ArtifactState.HOLD, "normalized_retention_prohibited")
    if normalized is not None:
        try: normalized.reproduce(body)
        except (ValueError, UnicodeDecodeError): return CaptureResult(ArtifactState.REVIEW, "normalized_source_mismatch")
    if cancelled is not None and cancelled(): return CaptureResult(ArtifactState.HOLD, "capture_cancelled")
    digest, snapshot_id = hashlib.sha256(body).hexdigest(), secrets.token_hex(16)
    artifact = ArtifactReference(artifact_id=f"artifact-{snapshot_id}", snapshot_id=snapshot_id,
        content_digest=digest, byte_length=len(body), locator=f"sha256:{digest}", source_family=context.source_family,
        origin=context.origin, retrieved_at=context.retrieved_at, event_at=context.event_at,
        requested_url=context.requested_url, final_url=context.final_url)
    metadata = {"snapshot_id": snapshot_id, "artifact_id": artifact.artifact_id,
        "operation_id": context.operation_id, "occurrence_id": context.occurrence_id,
        "caller_id": context.caller_id, "scope": context.scope, "source_family": context.source_family,
        "origin": context.origin, "requested_url": context.requested_url, "final_url": context.final_url,
        "retrieved_at": context.retrieved_at.isoformat(), "event_at": context.event_at.isoformat() if context.event_at else None,
        "content_digest": digest, "byte_length": len(body), "policy_digest": decision.policy_digest,
        "policy_id": decision.policy_id, "issuer_id": decision.issuer_id, "reviewer_id": decision.reviewer_id,
        "issued_at": decision.issued_at.isoformat(), "expires_at": decision.expires_at.isoformat(),
        "retain_body": decision.retain_body, "retain_normalized_text": decision.retain_normalized_text,
        "normalized": ({"text": normalized.text, "spans": [asdict(s) for s in normalized.spans],
                        "source_digest": normalized.source_digest} if normalized else None)}
    if normalized is not None:
        wire_spans = [SourceProofSpanReference(
            span_id=f"span-{snapshot_id}-{index}", artifact_id=artifact.artifact_id,
            byte_start=span.raw_start, byte_end=span.raw_end,
            normalized_start=span.normalized_start, normalized_end=span.normalized_end,
            raw_offset_unit="byte", normalized_offset_unit="unicode_code_point",
            source_encoding="utf-8",
        ).model_dump(mode="json") for index, span in enumerate(normalized.spans)]
        proof_payload = {"format_version": "source-proof/1.0", "input_ref": "0" * 64,
            "context": context.model_dump(mode="json"), "decision": decision.model_dump(mode="json"),
            "artifact": artifact.model_dump(mode="json"), "spans": wire_spans}
        proof_raw = _canonical(proof_payload)
        encoded_size = 4 + ((len(proof_raw) * 4 + 2) // 3)
        if encoded_size > _MAX_EVIDENCE_PROOF_BYTES:
            return CaptureResult(ArtifactState.REVIEW, "source_proof_too_large")
    if cancelled is not None and cancelled(): return CaptureResult(ArtifactState.HOLD, "capture_cancelled")
    try: store.write(body, metadata)
    except (FileNotFoundError, PermissionError): return CaptureResult(ArtifactState.HOLD, "artifact_store_unavailable")
    except (OSError, ValueError, TypeError): return CaptureResult(ArtifactState.REVIEW, "artifact_store_invalid")
    if cancelled is not None and cancelled(): return CaptureResult(ArtifactState.HOLD, "capture_cancelled")
    return CaptureResult(ArtifactState.AVAILABLE, "retained", artifact)

def resolve_source(store: FilesystemArtifactStore, context: ArtifactContext,
                   decision: RetentionDecision | None, artifact: ArtifactReference, *,
                   authority: RetentionAuthority | None = None, now: datetime | None = None) -> ResolveResult:
    current = now or datetime.now(timezone.utc)
    if decision is None: return ResolveResult(ArtifactState.HOLD, "retention_policy_unavailable")
    policy = _current_policy(decision, authority, current)
    if policy: return ResolveResult(*policy)
    mismatch = _bound(context, decision, current)
    if mismatch: return ResolveResult(ArtifactState.REVIEW, mismatch)
    try: body, metadata = store.read(artifact.content_digest, artifact.snapshot_id, expected_length=artifact.byte_length)
    except (FileNotFoundError, OSError, ValueError, TypeError, json.JSONDecodeError): return ResolveResult(ArtifactState.REVIEW, "artifact_missing")
    try:
        issued, expiry = datetime.fromisoformat(metadata["issued_at"]), datetime.fromisoformat(metadata["expires_at"])
        if not _aware(issued) or not _aware(expiry): raise ValueError
    except (KeyError, TypeError, ValueError): return ResolveResult(ArtifactState.REVIEW, "metadata_mismatch")
    if current >= expiry: return ResolveResult(ArtifactState.REVIEW, "artifact_expired")
    expected = {"snapshot_id": artifact.snapshot_id, "artifact_id": artifact.artifact_id,
        "operation_id": context.operation_id, "occurrence_id": context.occurrence_id, "caller_id": context.caller_id,
        "scope": context.scope, "source_family": context.source_family, "origin": context.origin,
        "requested_url": context.requested_url, "final_url": context.final_url,
        "retrieved_at": artifact.retrieved_at.isoformat(), "event_at": artifact.event_at.isoformat() if artifact.event_at else None,
        "policy_digest": decision.policy_digest, "policy_id": decision.policy_id, "issuer_id": decision.issuer_id,
        "reviewer_id": decision.reviewer_id, "issued_at": decision.issued_at.isoformat(),
        "expires_at": decision.expires_at.isoformat(), "retain_body": decision.retain_body,
        "retain_normalized_text": decision.retain_normalized_text, "content_digest": artifact.content_digest,
        "byte_length": artifact.byte_length}
    if any(metadata.get(k) != v for k, v in expected.items()): return ResolveResult(ArtifactState.REVIEW, "metadata_mismatch")
    if artifact.origin != context.origin or artifact.requested_url != context.requested_url or artifact.final_url != context.final_url:
        return ResolveResult(ArtifactState.REVIEW, "metadata_mismatch")
    if len(body) != artifact.byte_length or hashlib.sha256(body).hexdigest() != artifact.content_digest:
        return ResolveResult(ArtifactState.REVIEW, "digest_mismatch")
    try: fresh = normalize_html(body)
    except ValueError: return ResolveResult(ArtifactState.REVIEW, "normalized_source_mismatch")
    stored = metadata.get("normalized")
    wanted = ({"text": fresh.text, "spans": [asdict(s) for s in fresh.spans], "source_digest": fresh.source_digest}
              if decision.retain_normalized_text else None)
    if stored is not None and stored != wanted: return ResolveResult(ArtifactState.REVIEW, "normalized_metadata_mismatch")
    return ResolveResult(ArtifactState.AVAILABLE, "resolved", body)

def resolve_span(store: FilesystemArtifactStore, context: ArtifactContext,
                 decision: RetentionDecision | None, artifact: ArtifactReference, span: SourceProofSpanReference, *,
                 authority: RetentionAuthority | None = None, now: datetime | None = None) -> SpanResolveResult:
    resolved = resolve_source(store, context, decision, artifact, authority=authority, now=now)
    if resolved.state is not ArtifactState.AVAILABLE or resolved.body is None:
        return SpanResolveResult(resolved.state, resolved.reason)
    if decision is None or not decision.retain_normalized_text:
        return SpanResolveResult(ArtifactState.REVIEW, "normalized_retention_prohibited")
    fresh = normalize_html(resolved.body)
    for index, mapped in enumerate(fresh.spans):
        if (getattr(span, "span_id", None) == f"span-{artifact.snapshot_id}-{index}"
            and getattr(span, "artifact_id", None) == artifact.artifact_id
            and getattr(span, "field_pointer", None) is None
            and getattr(span, "byte_start", None) == mapped.raw_start
            and getattr(span, "byte_end", None) == mapped.raw_end
            and getattr(span, "normalized_start", None) == mapped.normalized_start
            and getattr(span, "normalized_end", None) == mapped.normalized_end
            and getattr(span, "raw_offset_unit", None) == "byte"
            and getattr(span, "normalized_offset_unit", None) == "unicode_code_point"
            and getattr(span, "source_encoding", None) == "utf-8"):
            return SpanResolveResult(ArtifactState.AVAILABLE, "resolved",
                fresh.text[mapped.normalized_start:mapped.normalized_end])
    return SpanResolveResult(ArtifactState.REVIEW, "span_mismatch")

@dataclass(frozen=True)
class _Atom:
    raw_start: int
    raw_end: int
    text: str

class _ExactHTMLText(HTMLParser):
    def __init__(self, source: str, char_to_byte: list[int]):
        super().__init__(convert_charrefs=False)
        self.source, self.char_to_byte, self.line_starts = source, char_to_byte, [0]
        self.atoms: list[_Atom] = []
        self.ignored: list[str] = []
        for match in re.finditer("\n", source): self.line_starts.append(match.end())
    def _add(self, raw: str, emitted: str) -> None:
        if self.ignored or not raw: return
        line, column = self.getpos(); start = self.line_starts[line - 1] + column; end = start + len(raw)
        if self.source[start:end] != raw: raise ValueError("html_position_mismatch")
        self.atoms.append(_Atom(self.char_to_byte[start], self.char_to_byte[end], emitted))
    def handle_data(self, data: str) -> None: self._add(data, data)
    def handle_entityref(self, name: str) -> None:
        raw = f"&{name};"; self._add(raw, html.unescape(raw))
    def handle_charref(self, name: str) -> None:
        raw = f"&#{name};"; self._add(raw, html.unescape(raw))
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in ("script", "style"): self.ignored.append(tag.lower())
    def handle_endtag(self, tag: str) -> None:
        if self.ignored and self.ignored[-1] == tag.lower(): self.ignored.pop()

def normalize_html(body: bytes) -> NormalizedSource:
    try: source = body.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error: raise ValueError("unsupported_source_encoding") from error
    char_to_byte = [0]
    for character in source: char_to_byte.append(char_to_byte[-1] + len(character.encode("utf-8")))
    parser = _ExactHTMLText(source, char_to_byte)
    try: parser.feed(source); parser.close()
    except (ValueError, AssertionError) as error: raise ValueError("malformed_html_mapping") from error
    spans: list[NormalizedSpan] = []; normalized = ""; pending: tuple[int, int] | None = None; previous_end = None
    def emit(text: str, raw_start: int, raw_end: int) -> None:
        nonlocal normalized
        if not text or raw_start >= raw_end: return
        start = len(normalized); normalized += text
        spans.append(NormalizedSpan(raw_start, raw_end, start, len(normalized), text))
    for atom in parser.atoms:
        if previous_end is not None and atom.raw_start > previous_end and normalized: pending = (previous_end, atom.raw_start)
        # One deterministic mapping per text node is enough even when whitespace is
        # normalized.  Entity callbacks remain separate atoms because their raw byte
        # ranges differ.  This avoids token/character-scale metadata growth.
        value = re.sub(r"\s+", " ", atom.text, flags=re.UNICODE)
        leading = value.startswith(" ")
        trailing = value.endswith(" ")
        content = value.strip(" ")
        if leading and normalized:
            pending = (atom.raw_start, atom.raw_end)
        if content:
            if pending and normalized: emit(" ", *pending)
            pending = None
            emit(content, atom.raw_start, atom.raw_end)
        if trailing and normalized:
            pending = (atom.raw_start, atom.raw_end)
        previous_end = atom.raw_end
    result = NormalizedSource(normalized, tuple(spans), source_digest=hashlib.sha256(body).hexdigest())
    if "".join(span.emitted_text for span in spans) != normalized: raise ValueError("normalized_mapping_invariant")
    return result

__all__ = ["ArtifactContext", "ArtifactState", "CaptureResult", "FilesystemArtifactStore",
    "NormalizedSource", "NormalizedSpan", "ResolveResult", "SpanResolveResult", "RetentionAuthority",
    "RetentionDecision", "capture_source", "normalize_html", "resolve_source", "resolve_span"]
