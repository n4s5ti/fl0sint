"""Admitted, bounded async HTTP fetch shared by local acquisition callers.

The trusted policy is supplied by local code.  Values in an AcquisitionRequest do
not grant their own caller, scope, destination, or resource authority.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from enum import Enum
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Annotated, Literal
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, StrictInt, model_validator

from .acquisition import AcquisitionRequest, Resources, SourceProofSpanReference
from .artifacts import (
    ArtifactContext,
    ArtifactState,
    CaptureResult,
    FilesystemArtifactStore,
    NormalizedSource,
    RetentionDecision,
    capture_source,
    normalize_html,
)
from .models import RedactedDiagnostic, canonical_input_hash

Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
_REDIRECTS = {301, 302, 303, 307, 308}
_RETRYABLE = {502, 503, 504}
_CHARSET = re.compile(r"(?:^|;)\s*charset=([^;\s]+)", re.IGNORECASE)
_SOURCE_PROOF_WORKERS = ThreadPoolExecutor(max_workers=4, thread_name_prefix="source-proof")


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _origin(url: str) -> str:
    parts = urlsplit(url)
    if (
        parts.scheme not in ("http", "https")
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or any(character.isspace() for character in url)
    ):
        raise ValueError("expected HTTP(S) URL without credentials")
    try:
        parsed_port = parts.port
        port = parsed_port if parsed_port is not None else (443 if parts.scheme == "https" else 80)
        if port == 0:
            raise ValueError("invalid URL port")
    except ValueError as error:
        raise ValueError("invalid URL port") from error
    host = parts.hostname.lower()
    rendered_host = f"[{host}]" if ":" in host else host
    return f"{parts.scheme}://{rendered_host}:{port}"


def _canonical(value: object) -> bytes:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json", exclude_none=False)
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("utf-8")


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


class FetchParameters(_Frozen):
    """Bounded transport behavior; total time and bytes come from allocation."""

    max_redirects: StrictInt = Field(default=0, ge=0, le=10)
    max_retries: StrictInt = Field(default=0, ge=0, le=5)
    retry_backoff_seconds: float = Field(default=0.0, ge=0.0, le=10.0)
    max_bytes_per_input: StrictInt | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def finite_backoff(self):
        if not math.isfinite(self.retry_backoff_seconds):
            raise ValueError("retry backoff must be finite")
        return self


class TrustedFetchPolicy(_Frozen):
    """Trusted local caller and destination authority, never request-controlled."""

    caller_id: str = Field(min_length=1, max_length=256, pattern=r"^\S+$")
    scope: str = Field(min_length=1, max_length=1024)
    capability_digest: Digest
    endpoint_policy_digest: Digest
    allowed_origins: tuple[str, ...] = Field(min_length=1, max_length=256)

    @model_validator(mode="after")
    def normalized_origins(self):
        normalized = tuple(_origin(value) for value in self.allowed_origins)
        if normalized != self.allowed_origins or len(set(normalized)) != len(
            normalized
        ):
            raise ValueError(
                "allowed origins must be unique canonical origins with effective ports"
            )
        return self


class AdmittedInput(_Frozen):
    occurrence_id: str
    input_ref: Digest
    url: str


class _ExecutionClaim:
    """Process-local, concurrency-safe claim state for one admitted capability."""

    def __init__(self):
        self._lock = threading.Lock()
        self._claimed = False

    def claim(self) -> bool:
        with self._lock:
            if self._claimed:
                return False
            self._claimed = True
            return True


class AdmittedFetchOperation(_Frozen):
    operation_id: str
    inputs: tuple[AdmittedInput, ...]
    allocation: Resources
    policy: TrustedFetchPolicy
    parameters: FetchParameters
    request_digest: Digest
    policy_digest: Digest
    parameter_digest: Digest
    operation_seal: Digest
    _execution_claim: _ExecutionClaim = PrivateAttr(default_factory=_ExecutionClaim)

    def validate_seal(self) -> None:
        expected = _operation_seal(
            self.operation_id,
            self.inputs,
            self.allocation,
            self.policy,
            self.parameters,
            self.request_digest,
            self.policy_digest,
            self.parameter_digest,
        )
        if expected != self.operation_seal:
            raise ValueError("admitted operation seal mismatch")

    def claim_execution(self) -> None:
        if not self._execution_claim.claim():
            raise ValueError("admitted operation has already been claimed for execution")


class FetchStatus(str, Enum):
    SUCCESS = "success"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    POLICY_DENIED = "policy_denied"
    HTTP_ERROR = "http_error"
    TOOL_ERROR = "tool_error"
    CANCELLED = "cancelled"


class FetchOutcome(_Frozen):
    occurrence_id: str
    input_ref: Digest
    status: FetchStatus
    requested_origin: str
    final_origin: str | None = None
    requested_url: str | None = None
    final_url: str | None = None
    text: str | None = None
    body: bytes | None = None
    diagnostic: RedactedDiagnostic | None = None
    actual_resources: Resources

    @model_validator(mode="after")
    def coherent(self):
        if self.status is FetchStatus.SUCCESS:
            if self.text is None or self.body is None or self.diagnostic is not None:
                raise ValueError("successful fetch requires body and decoded text")
        elif self.text is not None or self.body is not None or self.diagnostic is None:
            raise ValueError("failed fetch requires diagnostic and no body")
        return self


class FetchResult(_Frozen):
    operation_id: str
    outcomes: tuple[FetchOutcome, ...]
    actual_resources: Resources


@dataclass(frozen=True)
class SourceProofOutcome:
    occurrence_id: str
    input_ref: str
    fetch_status: FetchStatus
    capture: CaptureResult
    normalized: NormalizedSource | None
    diagnostic: RedactedDiagnostic | None
    actual_resources: Resources
    spans: tuple[SpanReference, ...] = ()
    observations: object | None = None


@dataclass(frozen=True)
class SourceProofResult:
    operation_id: str
    outcomes: tuple[SourceProofOutcome, ...]
    actual_resources: Resources


def _operation_seal(
    operation_id, inputs, allocation, policy, parameters, *digests
) -> str:
    return _digest(
        {
            "operation_id": operation_id,
            "inputs": [item.model_dump(mode="json") for item in inputs],
            "allocation": allocation.model_dump(mode="json", exclude_none=False),
            "policy": policy.model_dump(mode="json"),
            "parameters": parameters.model_dump(mode="json"),
            "digests": digests,
        }
    )


def _positive_allocation(allocation: Resources) -> None:
    required = ("requests", "bytes", "elapsed_seconds", "concurrency")
    for name in required:
        value = getattr(allocation, name)
        if value is None or isinstance(value, bool) or value <= 0:
            raise ValueError(f"fetch allocation requires finite positive {name}")
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError(f"fetch allocation requires finite positive {name}")


def admit_fetch(
    request: AcquisitionRequest,
    policy: TrustedFetchPolicy,
    parameters: FetchParameters,
) -> AdmittedFetchOperation:
    """Validate untrusted values and bind an immutable executable operation."""

    # Revalidation defeats model_construct and mutable nested-input bypasses.
    request = AcquisitionRequest.model_validate(
        request.model_dump(mode="python", exclude_none=False), strict=True
    )
    policy = TrustedFetchPolicy.model_validate(
        policy.model_dump(mode="python"), strict=True
    )
    parameters = FetchParameters.model_validate(
        parameters.model_dump(mode="python"), strict=True
    )
    if (
        request.caller_id != policy.caller_id
        or request.scope != policy.scope
        or request.capability_digest != policy.capability_digest
        or request.endpoint_policy_digest != policy.endpoint_policy_digest
    ):
        raise ValueError("request does not match trusted fetch policy")
    _positive_allocation(request.allocation)
    if len(request.inputs) > request.allocation.requests:
        raise ValueError("allocation cannot dispatch every input once")

    allowed = set(policy.allowed_origins)
    inputs: list[AdmittedInput] = []
    for item in request.inputs:
        if item.type_tag != "http_url" or type(item.value) is not str:
            raise ValueError("fetch input must be an http_url string")
        if item.input_ref != canonical_input_hash(item.value):
            raise ValueError("fetch input reference mismatch")
        origin = _origin(item.value)
        if origin not in allowed:
            raise ValueError("fetch input origin is outside trusted policy")
        inputs.append(
            AdmittedInput(
                occurrence_id=item.occurrence_id,
                input_ref=item.input_ref,
                url=item.value,
            )
        )

    request_digest = _digest(request)
    policy_digest = _digest(policy)
    parameter_digest = _digest(parameters)
    admitted_inputs = tuple(inputs)
    seal = _operation_seal(
        request.operation_id,
        admitted_inputs,
        request.allocation,
        policy,
        parameters,
        request_digest,
        policy_digest,
        parameter_digest,
    )
    return AdmittedFetchOperation(
        operation_id=request.operation_id,
        inputs=admitted_inputs,
        allocation=request.allocation,
        policy=policy,
        parameters=parameters,
        request_digest=request_digest,
        policy_digest=policy_digest,
        parameter_digest=parameter_digest,
        operation_seal=seal,
    )


def _diagnostic(
    code: str, message: str, *, retryable: bool = False
) -> RedactedDiagnostic:
    return RedactedDiagnostic(
        code=code, safe_message=message[:512], retryable=retryable
    )


class _Ledger:
    def __init__(self, operation: AdmittedFetchOperation):
        self.request_limit = operation.allocation.requests or 0
        self.byte_limit = operation.allocation.bytes or 0
        self.started = time.monotonic()
        self.requests = 0
        self.bytes = 0
        self.by_occurrence = {
            item.occurrence_id: {"requests": 0, "bytes": 0}
            for item in operation.inputs
        }
        self.lock = asyncio.Lock()

    async def charge_request(self, occurrence_id: str) -> bool:
        async with self.lock:
            if self.requests >= self.request_limit:
                return False
            self.requests += 1
            self.by_occurrence[occurrence_id]["requests"] += 1
            return True

    async def charge_bytes(
        self, occurrence_id: str, count: int, per_input_limit: int
    ) -> bool:
        async with self.lock:
            occurrence = self.by_occurrence[occurrence_id]
            self.bytes += count
            occurrence["bytes"] += count
            return self.bytes <= self.byte_limit and occurrence["bytes"] <= per_input_limit

    def resources(self, *, requests=0, bytes_=0) -> Resources:
        return Resources(
            requests=requests,
            bytes=bytes_,
            elapsed_seconds=max(0.0, time.monotonic() - self.started),
        )

    def occurrence_resources(self, occurrence_id: str) -> Resources:
        consumed = self.by_occurrence[occurrence_id]
        return self.resources(
            requests=consumed["requests"], bytes_=consumed["bytes"]
        )


async def execute_fetch(
    operation: AdmittedFetchOperation,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> FetchResult:
    """Execute only a sealed operation using verified async HTTP streaming."""

    operation.validate_seal()
    operation.claim_execution()
    ledger = _Ledger(operation)
    concurrency = min(operation.allocation.concurrency or 1, len(operation.inputs) or 1)
    semaphore = asyncio.Semaphore(concurrency)
    timeout = operation.allocation.elapsed_seconds or 0.001
    client = httpx.AsyncClient(
        transport=transport,
        verify=True,
        trust_env=False,
        follow_redirects=False,
        timeout=httpx.Timeout(timeout),
        headers={"accept-encoding": "identity"},
    )

    async def one(item: AdmittedInput) -> FetchOutcome:
        requested_origin = _origin(item.url)

        def failed(status: FetchStatus, code: str, message: str, retryable=False):
            return FetchOutcome(
                occurrence_id=item.occurrence_id,
                input_ref=item.input_ref,
                status=status,
                requested_origin=requested_origin,
                diagnostic=_diagnostic(code, message, retryable=retryable),
                actual_resources=ledger.occurrence_resources(item.occurrence_id),
            )

        url = item.url
        redirects = retries = 0
        async with semaphore:
            while True:
                if not await ledger.charge_request(item.occurrence_id):
                    return failed(
                        FetchStatus.TOOL_ERROR,
                        "request_budget_exhausted",
                        "The request allocation was exhausted.",
                    )
                try:
                    request = client.build_request("GET", url)
                    response = await client.send(request, stream=True)
                except asyncio.CancelledError:
                    if asyncio.current_task().cancelling():
                        raise
                    return failed(
                        FetchStatus.CANCELLED,
                        "cancelled",
                        "The transport cancelled this occurrence.",
                    )
                except httpx.TimeoutException:
                    return failed(
                        FetchStatus.TIMEOUT,
                        "timeout",
                        "The HTTP operation timed out.",
                        True,
                    )
                except httpx.HTTPError:
                    if retries < operation.parameters.max_retries:
                        retries += 1
                        if operation.parameters.retry_backoff_seconds:
                            await asyncio.sleep(
                                operation.parameters.retry_backoff_seconds
                            )
                        continue
                    return failed(
                        FetchStatus.TOOL_ERROR,
                        "transport_error",
                        "The HTTP transport failed.",
                        True,
                    )

                try:
                    if response.status_code in _REDIRECTS:
                        location = response.headers.get("location")
                        if not location:
                            return failed(
                                FetchStatus.HTTP_ERROR,
                                "invalid_redirect",
                                "The redirect had no destination.",
                            )
                        destination = urljoin(url, location)
                        try:
                            destination_origin = _origin(destination)
                        except ValueError:
                            return failed(
                                FetchStatus.POLICY_DENIED,
                                "redirect_denied",
                                "The redirect destination was denied before dispatch.",
                            )
                        if destination_origin != requested_origin:
                            return failed(
                                FetchStatus.POLICY_DENIED,
                                "redirect_denied",
                                "The redirect destination was denied before dispatch.",
                            )
                        if redirects >= operation.parameters.max_redirects:
                            return failed(
                                FetchStatus.POLICY_DENIED,
                                "redirect_limit",
                                "The redirect limit was reached before dispatch.",
                            )
                        redirects += 1
                        url = destination
                        continue
                    if response.status_code == 429:
                        return failed(
                            FetchStatus.RATE_LIMITED,
                            "rate_limited",
                            "The server rate limited the request.",
                            True,
                        )
                    if (
                        response.status_code in _RETRYABLE
                        and retries < operation.parameters.max_retries
                    ):
                        retries += 1
                        if operation.parameters.retry_backoff_seconds:
                            await asyncio.sleep(
                                operation.parameters.retry_backoff_seconds
                            )
                        continue
                    if response.status_code < 200 or response.status_code >= 300:
                        return failed(
                            FetchStatus.HTTP_ERROR,
                            "http_error",
                            "The server returned an unsuccessful HTTP status.",
                        )
                    encoding = (
                        response.headers.get("content-encoding", "identity")
                        .lower()
                        .strip()
                    )
                    if encoding not in ("", "identity"):
                        return failed(
                            FetchStatus.TOOL_ERROR,
                            "unsupported_content_encoding",
                            "The response used an unsupported content encoding.",
                        )
                    length = response.headers.get("content-length")
                    per_input_limit = (
                        operation.parameters.max_bytes_per_input
                        or operation.allocation.bytes
                        or 0
                    )
                    if length is not None:
                        try:
                            if (
                                int(length)
                                > (operation.allocation.bytes or 0) - ledger.bytes
                                or int(length) > per_input_limit
                            ):
                                return failed(
                                    FetchStatus.TOOL_ERROR,
                                    "body_too_large",
                                    "The response exceeded the byte allocation.",
                                )
                        except ValueError:
                            return failed(
                                FetchStatus.TOOL_ERROR,
                                "invalid_content_length",
                                "The response content length was invalid.",
                            )
                    body = bytearray()
                    chunks = (
                        _single_chunk(response.content)
                        if response.is_stream_consumed
                        else response.aiter_raw()
                    )
                    async for chunk in chunks:
                        within_limit = await ledger.charge_bytes(
                            item.occurrence_id, len(chunk), per_input_limit
                        )
                        if not within_limit:
                            return failed(
                                FetchStatus.TOOL_ERROR,
                                "body_too_large",
                                "The response exceeded the byte allocation.",
                            )
                        body.extend(chunk)
                    content_type = response.headers.get("content-type", "")
                    match = _CHARSET.search(content_type)
                    charset = match.group(1).strip("\"'") if match else "utf-8"
                    try:
                        text = bytes(body).decode(charset, errors="strict")
                    except (LookupError, UnicodeDecodeError):
                        return failed(
                            FetchStatus.TOOL_ERROR,
                            "decode_error",
                            "The response body could not be decoded.",
                        )
                    return FetchOutcome(
                        occurrence_id=item.occurrence_id,
                        input_ref=item.input_ref,
                        status=FetchStatus.SUCCESS,
                        requested_origin=requested_origin,
                        final_origin=_origin(url),
                        requested_url=_redacted_location(item.url),
                        final_url=_redacted_location(url),
                        text=text,
                        body=bytes(body),
                        actual_resources=ledger.occurrence_resources(
                            item.occurrence_id
                        ),
                    )
                except asyncio.CancelledError:
                    if asyncio.current_task().cancelling():
                        raise
                    return failed(
                        FetchStatus.CANCELLED,
                        "cancelled",
                        "The transport cancelled this occurrence.",
                    )
                except httpx.TimeoutException:
                    return failed(
                        FetchStatus.TIMEOUT,
                        "timeout",
                        "The HTTP operation timed out.",
                        True,
                    )
                except httpx.HTTPError:
                    if retries < operation.parameters.max_retries:
                        retries += 1
                        if operation.parameters.retry_backoff_seconds:
                            await asyncio.sleep(
                                operation.parameters.retry_backoff_seconds
                            )
                        continue
                    return failed(
                        FetchStatus.TOOL_ERROR,
                        "transport_error",
                        "The HTTP transport failed.",
                        True,
                    )
                finally:
                    await response.aclose()

    tasks = [asyncio.create_task(one(item)) for item in operation.inputs]
    cleanup_budget = min(0.05, max(0.01, timeout))

    def consume_background_result(task: asyncio.Task) -> None:
        try:
            task.result()
        except (asyncio.CancelledError, Exception):
            pass

    async def finish_cleanup(pending: set[asyncio.Task]) -> None:
        if pending:
            await asyncio.wait(pending)
        await client.aclose()

    async def bounded_cleanup(pending: set[asyncio.Task]) -> None:
        cleanup = asyncio.create_task(finish_cleanup(pending))
        done, _ = await asyncio.wait({cleanup}, timeout=cleanup_budget)
        if not done:
            cleanup.add_done_callback(consume_background_result)

    def terminal_outcomes(
        pending_at_boundary: set[asyncio.Task],
        status: FetchStatus,
        code: str,
        message: str,
    ):
        outcomes = []
        for item, task in zip(operation.inputs, tasks):
            if task not in pending_at_boundary:
                outcomes.append(task.result())
                continue
            outcomes.append(
                FetchOutcome(
                    occurrence_id=item.occurrence_id,
                    input_ref=item.input_ref,
                    status=status,
                    requested_origin=_origin(item.url),
                    diagnostic=_diagnostic(
                        code, message, retryable=status is FetchStatus.TIMEOUT
                    ),
                    actual_resources=ledger.occurrence_resources(item.occurrence_id),
                )
            )
        return outcomes

    outcomes: list[FetchOutcome]
    waiter = asyncio.create_task(asyncio.wait(tasks, timeout=timeout))
    try:
        done, pending = await asyncio.shield(waiter)
        if pending:
            for task in pending:
                task.cancel()
            await bounded_cleanup(pending)
            outcomes = terminal_outcomes(
                pending,
                FetchStatus.TIMEOUT,
                "timeout",
                "The operation deadline expired.",
            )
        else:
            outcomes = [task.result() for task in tasks]
            await bounded_cleanup(set())
    except asyncio.CancelledError:
        current = asyncio.current_task()
        if current is not None:
            current.uncancel()
        waiter.cancel()
        waiter.add_done_callback(consume_background_result)
        pending = {task for task in tasks if not task.done()}
        for task in pending:
            task.cancel()
        await bounded_cleanup(pending)
        outcomes = terminal_outcomes(
            pending,
            FetchStatus.CANCELLED,
            "cancelled",
            "The operation was cancelled before completion.",
        )

    return FetchResult(
        operation_id=operation.operation_id,
        outcomes=tuple(outcomes),
        actual_resources=ledger.resources(
            requests=ledger.requests, bytes_=ledger.bytes
        ),
    )


async def execute_fetch_with_source_proof(
    operation: AdmittedFetchOperation,
    *,
    artifact_store: FilesystemArtifactStore | None,
    retention_decision: RetentionDecision | None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> SourceProofResult:
    """Run the sole fetch implementation and capture before raw bodies leave it."""
    started = time.monotonic()
    fetched = (
        await execute_fetch(operation)
        if transport is None
        else await execute_fetch(operation, transport=transport)
    )
    admitted = {item.occurrence_id: item for item in operation.inputs}
    returned_ids = [item.occurrence_id for item in fetched.outcomes]
    result_is_bound = (
        fetched.operation_id == operation.operation_id
        and len(fetched.outcomes) == len(operation.inputs)
        and len(returned_ids) == len(set(returned_ids))
        and set(returned_ids) == set(admitted)
        and all(
            item.input_ref == admitted[item.occurrence_id].input_ref
            for item in fetched.outcomes
            if item.occurrence_id in admitted
        )
    )
    if not result_is_bound:
        existing = {item.occurrence_id: item for item in fetched.outcomes}
        return SourceProofResult(
            operation.operation_id,
            tuple(
                SourceProofOutcome(
                    item.occurrence_id,
                    item.input_ref,
                    FetchStatus.TOOL_ERROR,
                    CaptureResult(ArtifactState.REVIEW, "unbound_fetch_result"),
                    None,
                    RedactedDiagnostic(
                        code="invalid_fetch_result",
                        safe_message="The fetch result did not match the admitted operation.",
                        retryable=False,
                    ),
                    existing[item.occurrence_id].actual_resources
                    if item.occurrence_id in existing
                    else Resources(requests=0, bytes=0, elapsed_seconds=0.0),
                )
                for item in operation.inputs
            ),
            fetched.actual_resources,
        )
    deadline = started + (operation.allocation.elapsed_seconds or 0.0)
    stop = threading.Event()

    def expired() -> bool:
        return stop.is_set() or time.monotonic() >= deadline

    def process() -> tuple[SourceProofOutcome, ...]:
        outcomes = []
        for item in fetched.outcomes:
            if item.status is not FetchStatus.SUCCESS:
                outcomes.append(SourceProofOutcome(item.occurrence_id, item.input_ref, item.status, CaptureResult(ArtifactState.REVIEW, "fetch_failed"), None, item.diagnostic, item.actual_resources))
                continue
            source = admitted[item.occurrence_id]
            if expired():
                raise TimeoutError
            context = ArtifactContext(
                operation_id=operation.operation_id, occurrence_id=item.occurrence_id,
                caller_id=operation.policy.caller_id, scope=operation.policy.scope,
                source_family="http", origin=item.final_origin or item.requested_origin,
                requested_url=item.requested_url or _redacted_location(source.url),
                final_url=item.final_url or _redacted_location(source.url),
                retrieved_at=datetime.now(timezone.utc),
            )
            try:
                normalized = normalize_html(item.body or b"")
            except ValueError:
                capture = CaptureResult(ArtifactState.REVIEW, "unsupported_source_encoding")
                normalized = None
            else:
                if expired():
                    raise TimeoutError
                if retention_decision is None:
                    capture = CaptureResult(ArtifactState.HOLD, "retention_policy_unavailable")
                elif artifact_store is None:
                    capture = CaptureResult(ArtifactState.HOLD, "artifact_store_unavailable")
                else:
                    capture = capture_source(artifact_store, context, retention_decision,
                                             item.body, normalized=normalized, cancelled=expired)
                if expired():
                    raise TimeoutError
                if capture.state is not ArtifactState.AVAILABLE:
                    normalized = None
            spans = ()
            observations = None
            if capture.artifact is not None and normalized is not None:
                spans = tuple(SourceProofSpanReference(
                    span_id=f"span-{capture.artifact.snapshot_id}-{index}", artifact_id=capture.artifact.artifact_id,
                    byte_start=span.raw_start, byte_end=span.raw_end,
                    normalized_start=span.normalized_start, normalized_end=span.normalized_end,
                    raw_offset_unit="byte", normalized_offset_unit="unicode_code_point", source_encoding="utf-8",
                ) for index, span in enumerate(normalized.spans))
                if expired():
                    raise TimeoutError
                from .observed_extraction import extract_observations
                observations = extract_observations(
                    item.body or b"", artifact=capture.artifact,
                    occurrence_id=item.occurrence_id, input_ref=item.input_ref,
                    final_url=context.final_url or "", cancelled=expired,
                )
                if expired():
                    raise TimeoutError
            outcomes.append(SourceProofOutcome(item.occurrence_id, item.input_ref, item.status,
                                               capture, normalized, item.diagnostic, item.actual_resources, spans,
                                               observations))
        return tuple(outcomes)

    def resources(elapsed: float, original: Resources) -> Resources:
        return Resources(requests=original.requests, bytes=original.bytes,
                         elapsed_seconds=elapsed, concurrency=original.concurrency)

    async def bounded_process() -> tuple[SourceProofOutcome, ...]:
        remaining = max(0.0, deadline - time.monotonic())
        if remaining <= 0:
            raise TimeoutError
        worker = _SOURCE_PROOF_WORKERS.submit(process)
        while not worker.done():
            if time.monotonic() >= deadline:
                worker.cancel()
                raise TimeoutError
            await asyncio.sleep(min(0.005, max(0.0, deadline - time.monotonic())))
        return worker.result()

    terminal_status = None
    try:
        outcomes = await bounded_process()
    except (TimeoutError, asyncio.TimeoutError):
        stop.set()
        terminal_status = FetchStatus.TIMEOUT
    except asyncio.CancelledError:
        stop.set()
        current = asyncio.current_task()
        if current is not None: current.uncancel()
        terminal_status = FetchStatus.CANCELLED

    elapsed = time.monotonic() - started
    total = resources(elapsed, fetched.actual_resources)
    if terminal_status is not None:
        code = "timeout" if terminal_status is FetchStatus.TIMEOUT else "cancelled"
        outcomes = tuple(SourceProofOutcome(
            item.occurrence_id, item.input_ref, terminal_status,
            CaptureResult(ArtifactState.HOLD, f"capture_{code}"), None,
            _diagnostic(code, "The source-proof operation did not complete within its allocation."),
            resources(elapsed, item.actual_resources), (),
        ) for item in fetched.outcomes)
    else:
        outcomes = tuple(SourceProofOutcome(
            item.occurrence_id, item.input_ref, item.fetch_status, item.capture,
            item.normalized, item.diagnostic, resources(elapsed, item.actual_resources), item.spans,
            item.observations,
        ) for item in outcomes)
    return SourceProofResult(fetched.operation_id, outcomes, total)


def _redacted_location(url: str) -> str:
    parts = urlsplit(url)
    host = f"[{parts.hostname}]" if parts.hostname and ":" in parts.hostname else parts.hostname
    port = f":{parts.port}" if parts.port is not None else ""
    sensitive = {"access_token", "api_key", "apikey", "auth", "authorization",
                 "code", "credential", "key", "password", "secret", "signature", "token"}
    try:
        pairs = parse_qsl(parts.query, keep_blank_values=True)
        query = "" if any(name.lower() in sensitive for name, _ in pairs) else urlencode(pairs)
    except ValueError:
        query = ""
    return urlunsplit((parts.scheme, f"{host}{port}", parts.path or "/", query, ""))


async def _single_chunk(content: bytes):
    """Adapt already-buffered injected transports without changing live streaming."""
    if content:
        yield content


__all__ = [
    "AdmittedFetchOperation",
    "FetchOutcome",
    "FetchParameters",
    "FetchResult",
    "FetchStatus",
    "TrustedFetchPolicy",
    "admit_fetch",
    "execute_fetch",
    "execute_fetch_with_source_proof",
    "SourceProofOutcome",
    "SourceProofResult",
]
