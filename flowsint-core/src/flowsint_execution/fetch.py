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
import time
from enum import Enum
from typing import Annotated, Literal
from urllib.parse import urljoin, urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

from .acquisition import AcquisitionRequest, Resources
from .models import RedactedDiagnostic, canonical_input_hash

Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
_REDIRECTS = {301, 302, 303, 307, 308}
_RETRYABLE = {502, 503, 504}
_CHARSET = re.compile(r"(?:^|;)\s*charset=([^;\s]+)", re.IGNORECASE)


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
    outcomes: list[FetchOutcome]
    try:
        async with asyncio.timeout(timeout):
            outcomes = list(await asyncio.gather(*tasks))
    except TimeoutError:
        for task in tasks:
            task.cancel()
        partial = await asyncio.gather(*tasks, return_exceptions=True)
        outcomes = []
        for item, value in zip(operation.inputs, partial):
            if (
                isinstance(value, FetchOutcome)
                and value.status is not FetchStatus.CANCELLED
            ):
                outcomes.append(value)
            else:
                outcomes.append(
                    FetchOutcome(
                        occurrence_id=item.occurrence_id,
                        input_ref=item.input_ref,
                        status=FetchStatus.TIMEOUT,
                        requested_origin=_origin(item.url),
                        diagnostic=_diagnostic(
                            "timeout", "The operation deadline expired.", retryable=True
                        ),
                        actual_resources=ledger.occurrence_resources(
                            item.occurrence_id
                        ),
                    )
                )
    except asyncio.CancelledError:
        for task in tasks:
            task.cancel()
        partial = await asyncio.gather(*tasks, return_exceptions=True)
        outcomes = []
        for item, value in zip(operation.inputs, partial):
            if isinstance(value, FetchOutcome):
                outcomes.append(value)
            else:
                outcomes.append(
                    FetchOutcome(
                        occurrence_id=item.occurrence_id,
                        input_ref=item.input_ref,
                        status=FetchStatus.CANCELLED,
                        requested_origin=_origin(item.url),
                        diagnostic=_diagnostic(
                            "cancelled",
                            "The operation was cancelled before completion.",
                        ),
                        actual_resources=ledger.occurrence_resources(
                            item.occurrence_id
                        ),
                    )
                )
    finally:
        await client.aclose()

    return FetchResult(
        operation_id=operation.operation_id,
        outcomes=tuple(outcomes),
        actual_resources=ledger.resources(
            requests=ledger.requests, bytes_=ledger.bytes
        ),
    )


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
]
