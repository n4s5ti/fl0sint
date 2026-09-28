"""Behavior tests for admitted, bounded HTTP acquisition."""

import asyncio
import datetime
import ipaddress
import ssl
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from flowsint_execution.acquisition import (
    AcquisitionRequest,
    InputOccurrence,
    Resources,
)
from flowsint_execution.fetch import (
    FetchParameters,
    FetchStatus,
    TrustedFetchPolicy,
    admit_fetch,
    execute_fetch,
)
from flowsint_execution.models import canonical_input_hash

DIGEST = "a" * 64
POLICY_DIGEST = "b" * 64


def _request(urls=("https://example.test/a",), **allocation):
    return AcquisitionRequest(
        operation_id="op-1",
        caller_id="website-to-text",
        scope="local-web-fetch",
        capability_digest=DIGEST,
        endpoint_policy_digest=POLICY_DIGEST,
        inputs=tuple(
            InputOccurrence(
                occurrence_id=f"input-{index}",
                input_ref=canonical_input_hash(url),
                type_tag="http_url",
                value=url,
            )
            for index, url in enumerate(urls)
        ),
        allocation=Resources(
            requests=allocation.get("requests", 3),
            bytes=allocation.get("bytes", 1024),
            elapsed_seconds=allocation.get("elapsed_seconds", 1.0),
            concurrency=allocation.get("concurrency", 2),
        ),
    )


def _policy(*origins):
    return TrustedFetchPolicy(
        caller_id="website-to-text",
        scope="local-web-fetch",
        capability_digest=DIGEST,
        endpoint_policy_digest=POLICY_DIGEST,
        allowed_origins=origins or ("https://example.test:443",),
    )


@contextmanager
def _server(handler, *, tls_context=None):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    if tls_context is not None:
        server.socket = tls_context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def _self_signed_context(tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    now = datetime.datetime.now(datetime.timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(1)
        .not_valid_before(now - datetime.timedelta(minutes=1))
        .not_valid_after(now + datetime.timedelta(minutes=5))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    key_path = tmp_path / "server.key"
    certificate_path = tmp_path / "server.crt"
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    certificate_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certificate_path, key_path)
    return context


@pytest.mark.asyncio
async def test_cross_origin_redirect_is_denied_before_dispatch():
    hits = []

    async def handler(request):
        hits.append(str(request.url))
        return httpx.Response(302, headers={"location": "https://other.test/secret"})

    operation = admit_fetch(_request(), _policy(), FetchParameters(max_redirects=2))
    result = await execute_fetch(operation, transport=httpx.MockTransport(handler))

    assert hits == ["https://example.test/a"]
    assert result.outcomes[0].status is FetchStatus.POLICY_DENIED
    assert result.actual_resources.requests == 1


@pytest.mark.asyncio
async def test_retry_and_multiple_inputs_share_one_request_allocation():
    hits = []

    async def handler(request):
        hits.append(str(request.url))
        return httpx.Response(503)

    operation = admit_fetch(
        _request(("https://example.test/a", "https://example.test/b"), requests=2),
        _policy(),
        FetchParameters(max_retries=2),
    )
    result = await execute_fetch(operation, transport=httpx.MockTransport(handler))

    assert len(hits) == 2
    assert result.actual_resources.requests == 2
    assert all(outcome.status is not FetchStatus.SUCCESS for outcome in result.outcomes)


@pytest.mark.asyncio
async def test_streaming_byte_limit_never_returns_oversized_success():
    class Chunked(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"x" * 4
            yield b"x" * 5

    async def handler(_request):
        return httpx.Response(200, stream=Chunked())

    operation = admit_fetch(_request(bytes=8), _policy(), FetchParameters())
    result = await execute_fetch(operation, transport=httpx.MockTransport(handler))

    outcome = result.outcomes[0]
    assert outcome.status is FetchStatus.TOOL_ERROR
    assert outcome.body is None
    assert outcome.actual_resources.bytes == 9
    assert result.actual_resources.bytes == 9


@pytest.mark.asyncio
async def test_stream_read_error_is_typed_and_other_occurrences_finish():
    class Broken(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"abc"
            raise httpx.ReadError("unsafe detail")

    finished = asyncio.Event()

    async def handler(request):
        if request.url.path == "/broken":
            return httpx.Response(200, stream=Broken())
        await asyncio.sleep(0)
        finished.set()
        return httpx.Response(200, content=b"ok")

    operation = admit_fetch(
        _request(
            ("https://example.test/broken", "https://example.test/ok"),
            requests=2,
        ),
        _policy(),
        FetchParameters(),
    )
    result = await execute_fetch(operation, transport=httpx.MockTransport(handler))

    assert finished.is_set()
    assert [outcome.status for outcome in result.outcomes] == [
        FetchStatus.TOOL_ERROR,
        FetchStatus.SUCCESS,
    ]
    assert result.outcomes[0].diagnostic.code == "transport_error"
    assert result.outcomes[0].actual_resources.requests == 1
    assert result.outcomes[0].actual_resources.bytes == 3
    assert result.actual_resources.requests == sum(
        outcome.actual_resources.requests for outcome in result.outcomes
    )
    assert result.actual_resources.bytes == sum(
        outcome.actual_resources.bytes for outcome in result.outcomes
    )


@pytest.mark.asyncio
async def test_stream_read_timeout_is_typed_and_keeps_delivered_bytes():
    class TimedOut(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"seen"
            raise httpx.ReadTimeout("unsafe detail")

    async def handler(_request):
        return httpx.Response(200, stream=TimedOut())

    operation = admit_fetch(_request(), _policy(), FetchParameters())
    result = await execute_fetch(operation, transport=httpx.MockTransport(handler))

    assert result.outcomes[0].status is FetchStatus.TIMEOUT
    assert result.outcomes[0].actual_resources.requests == 1
    assert result.outcomes[0].actual_resources.bytes == 4
    assert result.actual_resources.bytes == 4


@pytest.mark.asyncio
async def test_deadline_preserves_per_occurrence_accounting_during_stream_and_wait():
    streaming = asyncio.Event()

    class Stalled(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"used"
            streaming.set()
            await asyncio.Event().wait()

    async def handler(request):
        if request.url.path == "/stream":
            return httpx.Response(200, stream=Stalled())
        await asyncio.Event().wait()

    operation = admit_fetch(
        _request(
            ("https://example.test/stream", "https://example.test/wait"),
            requests=2,
            elapsed_seconds=0.03,
        ),
        _policy(),
        FetchParameters(),
    )
    result = await execute_fetch(operation, transport=httpx.MockTransport(handler))

    assert streaming.is_set()
    assert [outcome.status for outcome in result.outcomes] == [
        FetchStatus.TIMEOUT,
        FetchStatus.TIMEOUT,
    ]
    assert [outcome.actual_resources.requests for outcome in result.outcomes] == [1, 1]
    assert [outcome.actual_resources.bytes for outcome in result.outcomes] == [4, 0]
    assert result.actual_resources.requests == 2
    assert result.actual_resources.bytes == 4


@pytest.mark.asyncio
async def test_deadline_during_retry_backoff_preserves_failed_attempt():
    async def handler(_request):
        return httpx.Response(503)

    operation = admit_fetch(
        _request(requests=2, elapsed_seconds=0.02),
        _policy(),
        FetchParameters(max_retries=1, retry_backoff_seconds=0.2),
    )
    result = await execute_fetch(operation, transport=httpx.MockTransport(handler))

    assert result.outcomes[0].status is FetchStatus.TIMEOUT
    assert result.outcomes[0].actual_resources.requests == 1
    assert result.actual_resources.requests == 1


@pytest.mark.asyncio
async def test_deadline_while_waiting_for_semaphore_preserves_zero_for_undispatched():
    entered = asyncio.Event()

    async def handler(_request):
        entered.set()
        await asyncio.Event().wait()

    operation = admit_fetch(
        _request(
            ("https://example.test/active", "https://example.test/waiting"),
            requests=2,
            concurrency=1,
            elapsed_seconds=0.02,
        ),
        _policy(),
        FetchParameters(),
    )
    result = await execute_fetch(operation, transport=httpx.MockTransport(handler))

    assert entered.is_set()
    assert [item.actual_resources.requests for item in result.outcomes] == [1, 0]
    assert result.actual_resources.requests == 1


@pytest.mark.asyncio
async def test_per_input_byte_limit_is_independent_of_pooled_allocation():
    class OneChunk(httpx.AsyncByteStream):
        def __init__(self, content):
            self.content = content

        async def __aiter__(self):
            yield self.content

    async def handler(request):
        content = b"12345" if request.url.path == "/large" else b"123"
        return httpx.Response(200, stream=OneChunk(content))

    operation = admit_fetch(
        _request(
            ("https://example.test/large", "https://example.test/small"),
            requests=2,
            bytes=8,
        ),
        _policy(),
        FetchParameters(max_bytes_per_input=4),
    )
    result = await execute_fetch(operation, transport=httpx.MockTransport(handler))

    assert [outcome.status for outcome in result.outcomes] == [
        FetchStatus.TOOL_ERROR,
        FetchStatus.SUCCESS,
    ]
    assert result.outcomes[0].actual_resources.bytes == 5
    assert result.actual_resources.bytes == 8


@pytest.mark.asyncio
async def test_empty_429_timeout_decode_and_http_error_are_distinct():
    async def handler(request):
        path = request.url.path
        if path == "/empty":
            return httpx.Response(200, content=b"")
        if path == "/rate":
            return httpx.Response(429)
        if path == "/decode":
            return httpx.Response(
                200,
                content=b"\xff",
                headers={"content-type": "text/plain; charset=utf-8"},
            )
        return httpx.Response(500)

    urls = tuple(
        f"https://example.test/{name}" for name in ("empty", "rate", "decode", "http")
    )
    operation = admit_fetch(_request(urls, requests=4), _policy(), FetchParameters())
    result = await execute_fetch(operation, transport=httpx.MockTransport(handler))

    assert [item.status for item in result.outcomes] == [
        FetchStatus.SUCCESS,
        FetchStatus.RATE_LIMITED,
        FetchStatus.TOOL_ERROR,
        FetchStatus.HTTP_ERROR,
    ]
    assert result.outcomes[0].text == ""
    assert all(
        len(item.diagnostic.safe_message) <= 512
        for item in result.outcomes
        if item.diagnostic
    )


def test_admission_rejects_replacement_and_nonfinite_allocation():
    operation = admit_fetch(_request(), _policy(), FetchParameters())
    replaced = operation.model_copy(
        update={"parameters": FetchParameters(max_redirects=1)}
    )
    with pytest.raises(ValueError, match="seal"):
        replaced.validate_seal()

    with pytest.raises(ValueError, match="allocation"):
        admit_fetch(_request(requests=0), _policy(), FetchParameters())

    with pytest.raises((TypeError, ValueError)):
        FetchParameters(max_retries=True)


def test_origin_port_zero_is_not_normalized_to_default_port():
    with pytest.raises(ValueError, match="port"):
        _policy("https://example.test:0")


@pytest.mark.asyncio
async def test_cancellation_returns_typed_accounting():
    started = asyncio.Event()

    async def handler(_request):
        started.set()
        await asyncio.Event().wait()

    operation = admit_fetch(_request(), _policy(), FetchParameters())
    task = asyncio.create_task(
        execute_fetch(operation, transport=httpx.MockTransport(handler))
    )
    await started.wait()
    task.cancel()
    result = await asyncio.wait_for(task, 0.25)

    assert result.outcomes[0].status is FetchStatus.CANCELLED
    assert result.actual_resources.requests == 1


@pytest.mark.asyncio
async def test_real_loopback_cross_origin_redirect_never_hits_target():
    target_hits = 0

    class Target(BaseHTTPRequestHandler):
        def do_GET(self):
            nonlocal target_hits
            target_hits += 1
            self.send_response(200)
            self.end_headers()

        def log_message(self, *_args):
            pass

    with _server(Target) as target:

        class Redirect(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(302)
                self.send_header(
                    "Location", f"http://127.0.0.1:{target.server_port}/target"
                )
                self.end_headers()

            def log_message(self, *_args):
                pass

        with _server(Redirect) as source:
            url = f"http://127.0.0.1:{source.server_port}/start"
            origin = f"http://127.0.0.1:{source.server_port}"
            operation = admit_fetch(
                _request((url,)), _policy(origin), FetchParameters(max_redirects=2)
            )
            result = await execute_fetch(operation)

    assert result.outcomes[0].status is FetchStatus.POLICY_DENIED
    assert target_hits == 0


@pytest.mark.asyncio
async def test_real_untrusted_self_signed_tls_is_rejected(tmp_path):
    class Page(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"must not be trusted")

        def log_message(self, *_args):
            pass

    with _server(Page, tls_context=_self_signed_context(tmp_path)) as server:
        url = f"https://127.0.0.1:{server.server_port}/"
        origin = f"https://127.0.0.1:{server.server_port}"
        operation = admit_fetch(
            _request((url,), requests=1), _policy(origin), FetchParameters()
        )
        result = await execute_fetch(operation)

    assert result.outcomes[0].status is FetchStatus.TOOL_ERROR
    assert result.actual_resources.requests == 1


@pytest.mark.asyncio
async def test_real_stalled_body_obeys_total_deadline():
    class Stalled(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.flush()
            time.sleep(0.5)

        def log_message(self, *_args):
            pass

    with _server(Stalled) as server:
        url = f"http://127.0.0.1:{server.server_port}/"
        origin = f"http://127.0.0.1:{server.server_port}"
        operation = admit_fetch(
            _request((url,), elapsed_seconds=0.1), _policy(origin), FetchParameters()
        )
        started = time.monotonic()
        result = await execute_fetch(operation)
        duration = time.monotonic() - started

    assert result.outcomes[0].status is FetchStatus.TIMEOUT
    assert duration < 0.4
