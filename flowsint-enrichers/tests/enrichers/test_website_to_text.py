"""Behavioral regressions for occurrence-safe WebsiteToText execution."""

import asyncio
import os
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import httpx

import flowsint_enrichers.website.to_text as website_module
from flowsint_enrichers.website.to_text import WebsiteTextOccurrence, WebsiteToText
from flowsint_execution.fetch import execute_fetch as real_execute_fetch
from flowsint_execution.fetch import FetchResult
from flowsint_execution.models import OutcomeStatus
from flowsint_types.phrase import Phrase
from flowsint_types.website import Website


class _SilentLogger:
    @staticmethod
    def info(*_args, **_kwargs):
        pass

    @staticmethod
    def error(*_args, **_kwargs):
        pass

    @staticmethod
    def completed(*_args, **_kwargs):
        pass


class _RecordingGraph:
    def __init__(self):
        self.nodes = []
        self.relationships = []
        self.messages = []
        self.flushes = 0

    def create_node_from_flowsint_type(self, *, node_obj):
        self.nodes.append(node_obj)

    def create_relationship(self, *, from_obj, to_obj, rel_label):
        self.relationships.append((from_obj, to_obj, rel_label))

    def log_graph_message(self, message):
        self.messages.append(message)

    def flush(self):
        self.flushes += 1


def _patch_transport(monkeypatch, handler):
    async def execute(operation):
        return await real_execute_fetch(
            operation, transport=httpx.MockTransport(handler)
        )

    monkeypatch.setattr(website_module, "execute_fetch", execute)


@pytest.mark.asyncio
async def test_legacy_execute_does_not_turn_policy_denial_into_successful_empty(
    make_enricher, monkeypatch
):
    enricher, _graph = make_enricher()
    enricher.params["enable_quic"] = True

    async def initialized():
        return None

    monkeypatch.setattr(enricher, "async_init", initialized)

    with pytest.raises(RuntimeError, match="unsafe_transport_disabled"):
        await enricher.execute([Website(url="https://denied.example")])


@contextmanager
def _loopback_pages():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/slow":
                time.sleep(0.05)
                body = b"<html><body>slow page</body></html>"
                status = 200
            elif self.path == "/fast":
                body = b"<html><body>fast page</body></html>"
                status = 200
            elif self.path == "/empty":
                body = b"<html><body></body></html>"
                status = 200
            else:
                body = b"not found"
                status = 404
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


@pytest.fixture
def make_enricher(monkeypatch):
    monkeypatch.setattr("flowsint_enrichers.website.to_text.Logger", _SilentLogger)
    monkeypatch.setattr("flowsint_core.core.enricher_base.Logger", _SilentLogger)

    def make(cls=WebsiteToText, **params):
        graph = _RecordingGraph()
        enricher = cls(
            sketch_id="def42-test",
            params_schema=[],
            params=params,
            graph_service=graph,
        )
        return enricher, graph

    return make


@pytest.mark.asyncio
async def test_legacy_public_execute_uses_loopback_occurrences_not_completion_zip(
    make_enricher, monkeypatch
):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    enricher, graph = make_enricher()
    with _loopback_pages() as base:
        slow = Website(url=f"{base}/slow")
        fast = Website(url=f"{base}/fast")
        results = await enricher.execute([slow, fast])

    assert [result.text for result in results] == ["slow page", "fast page"]
    assert [
        (str(source.url), output.text, label)
        for source, output, label in graph.relationships
    ] == [
        (str(slow.url), "slow page", "HAS_INNER_TEXT"),
        (str(fast.url), "fast page", "HAS_INNER_TEXT"),
    ]
    assert graph.flushes == 1


@pytest.mark.asyncio
async def test_occurrence_futures_preserve_middle_failure_duplicates_and_cancellation(
    make_enricher, monkeypatch
):
    enricher, _graph = make_enricher()

    async def fetch(request):
        url = str(request.url)
        if "slow" in url:
            await asyncio.sleep(0.02)
            return httpx.Response(200, text="slow")
        if "failure" in url:
            return httpx.Response(500)
        if "cancel" in url:
            raise asyncio.CancelledError()
        return httpx.Response(200, text="shared")

    _patch_transport(monkeypatch, fetch)
    websites = [
        Website(url="https://slow.example"),
        Website(url="https://failure.example"),
        Website(url="https://duplicate.example"),
        Website(url="https://duplicate.example"),
        Website(url="https://cancel.example"),
    ]

    occurrences = await enricher._scan_occurrences(websites)

    assert [occurrence.index for occurrence in occurrences] == [0, 1, 2, 3, 4]
    assert [occurrence.source for occurrence in occurrences] == websites
    assert [occurrence.status for occurrence in occurrences] == [
        OutcomeStatus.SUCCESS,
        OutcomeStatus.FAILURE,
        OutcomeStatus.SUCCESS,
        OutcomeStatus.SUCCESS,
        OutcomeStatus.HOLD,
    ]
    assert [
        tuple(output.text for output in occurrence.outputs)
        for occurrence in occurrences
    ] == [
        ("slow",),
        (),
        ("shared",),
        ("shared",),
        (),
    ]
    assert occurrences[1].diagnostic.code == "http_error"
    assert occurrences[4].diagnostic.code == "cancelled"


@pytest.mark.asyncio
async def test_structured_execution_distinguishes_none_transport_failure_from_empty_success(
    make_enricher, monkeypatch
):
    enricher, _graph = make_enricher()

    async def fetch(request):
        return (
            httpx.Response(500)
            if "failed" in str(request.url)
            else httpx.Response(200, content=b"")
        )

    _patch_transport(monkeypatch, fetch)
    result = await enricher.execute_structured(
        [Website(url="https://empty.example"), Website(url="https://failed.example")]
    )

    empty, failed = result.outcomes
    assert empty.status is OutcomeStatus.SUCCESS
    assert empty.outputs == ()
    assert empty.diagnostic is None
    assert failed.status is OutcomeStatus.FAILURE
    assert failed.outputs == ()
    assert failed.diagnostic.code == "http_error"


@pytest.mark.asyncio
async def test_max_response_bytes_is_enforced_for_each_input(make_enricher, monkeypatch):
    enricher, _graph = make_enricher(max_response_bytes=4)

    class OneChunk(httpx.AsyncByteStream):
        def __init__(self, content):
            self.content = content

        async def __aiter__(self):
            yield self.content

    async def fetch(request):
        content = b"12345" if "large" in str(request.url) else b"123"
        return httpx.Response(200, stream=OneChunk(content))

    _patch_transport(monkeypatch, fetch)
    occurrences = await enricher._scan_occurrences(
        [Website(url="https://large.example"), Website(url="https://small.example")]
    )

    assert [item.status for item in occurrences] == [
        OutcomeStatus.FAILURE,
        OutcomeStatus.SUCCESS,
    ]
    assert occurrences[0].actual_resources.bytes == 5


@pytest.mark.asyncio
async def test_occurrence_reconstruction_uses_id_and_marks_missing_result(
    make_enricher, monkeypatch
):
    enricher, _graph = make_enricher()

    async def execute(operation):
        fetched = await real_execute_fetch(
            operation,
            transport=httpx.MockTransport(lambda request: httpx.Response(200, text=request.url.host)),
        )
        return FetchResult(
            operation_id=fetched.operation_id,
            outcomes=(fetched.outcomes[1],),
            actual_resources=fetched.actual_resources,
        )

    monkeypatch.setattr(website_module, "execute_fetch", execute)
    first = Website(url="https://first.example")
    second = Website(url="https://second.example")
    occurrences = await enricher._scan_occurrences([first, second])

    assert [item.source for item in occurrences] == [first, second]
    assert occurrences[0].status is OutcomeStatus.FAILURE
    assert occurrences[0].diagnostic.code == "missing_fetch_result"
    assert occurrences[1].outputs[0].text == "second.example"


@pytest.mark.parametrize(
    ("name", "value"),
    [("max_response_bytes", True), ("max_concurrency", True), ("request_timeout", True)],
)
def test_boolean_numeric_parameters_are_rejected(make_enricher, name, value):
    enricher, _graph = make_enricher(**{name: value})
    with pytest.raises(ValueError, match=name):
        enricher._build_operation(
            ((0, Website(url="https://example.test"), "a" * 64),)
        )


class _SectioningWebsiteToText(WebsiteToText):
    def _outputs_from_text(self, text):
        return (Phrase(text=text + " first"), Phrase(text=text + " second"))


@pytest.mark.asyncio
async def test_one_to_many_outputs_retain_one_source_for_every_graph_relationship(
    make_enricher, monkeypatch
):
    enricher, graph = make_enricher(_SectioningWebsiteToText)

    async def fetch(_request):
        return httpx.Response(200, text="page")

    _patch_transport(monkeypatch, fetch)
    source = Website(url="https://many.example")
    result = await enricher.execute_structured([source])

    assert [output.text for output in result.outcomes[0].outputs] == [
        "page first",
        "page second",
    ]
    assert [
        (source_node, phrase.text, label)
        for source_node, phrase, label in graph.relationships
    ] == [
        (source, "page first", "HAS_INNER_TEXT"),
        (source, "page second", "HAS_INNER_TEXT"),
    ]


@pytest.mark.asyncio
async def test_duplicate_content_remains_independent_across_reinvocation(
    make_enricher, monkeypatch
):
    enricher, graph = make_enricher()

    async def fetch(_request):
        return httpx.Response(200, text="shared")

    _patch_transport(monkeypatch, fetch)
    first = Website(url="https://duplicate.example")
    second = Website(url="https://duplicate.example")

    first_result = await enricher.execute_structured([first, second])
    second_result = await enricher.execute_structured([first, second])

    assert [
        [output.text for output in outcome.outputs] for outcome in first_result.outcomes
    ] == [
        ["shared"],
        ["shared"],
    ]
    assert [outcome.input_ref for outcome in first_result.outcomes] == [
        outcome.input_ref for outcome in second_result.outcomes
    ]
    assert len(graph.relationships) == 4


@pytest.mark.asyncio
async def test_structured_result_json_has_a_separate_process_consumer(
    make_enricher, monkeypatch
):
    enricher, _graph = make_enricher()

    async def fetch(request):
        return (
            httpx.Response(500)
            if "failed" in str(request.url)
            else httpx.Response(200, text="kept")
        )

    _patch_transport(monkeypatch, fetch)
    result = await enricher.execute_structured(
        [Website(url="https://kept.example"), Website(url="https://failed.example")]
    )
    payload = result.model_dump_json()
    consumer = (
        "import json, sys; value = json.loads(sys.stdin.read()); "
        "assert [item['status'] for item in value['outcomes']] == ['success', 'failure']; "
        "assert value['outcomes'][0]['outputs'][0]['text'] == 'kept'; "
        "assert value['outcomes'][1]['diagnostic']['code'] == 'http_error'; "
        "print('consumer-ok')"
    )
    completed = subprocess.run(
        [sys.executable, "-c", consumer],
        input=payload,
        text=True,
        capture_output=True,
        check=False,
        env=os.environ.copy(),
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "consumer-ok"


@pytest.mark.asyncio
async def test_public_scan_then_postprocess_captures_owned_occurrence_edges(
    make_enricher, monkeypatch
):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    enricher, graph = make_enricher()
    with _loopback_pages() as base:
        slow = Website(url=f"{base}/slow")
        fast = Website(url=f"{base}/fast")
        occurrences = await enricher.scan([slow, fast])

    assert [occurrence.source for occurrence in occurrences] == [slow, fast]
    assert all(
        isinstance(occurrence, WebsiteTextOccurrence) for occurrence in occurrences
    )
    outputs = enricher.postprocess(occurrences)

    assert [output.text for output in outputs] == ["slow page", "fast page"]
    assert [
        (source, output.text) for source, output, _label in graph.relationships
    ] == [
        (slow, "slow page"),
        (fast, "fast page"),
    ]

    with pytest.raises(TypeError, match="occurrence envelopes"):
        enricher.postprocess([Phrase(text="detached")])


@pytest.mark.asyncio
async def test_structured_leading_failure_keeps_raw_identity_and_recovers_on_reinvocation(
    make_enricher, monkeypatch
):
    from flowsint_execution.models import canonical_input_hash

    enricher, _graph = make_enricher()
    calls = 0

    async def fetch(_request):
        nonlocal calls
        calls += 1
        return (
            httpx.Response(500) if calls == 1 else httpx.Response(200, text="recovered")
        )

    _patch_transport(monkeypatch, fetch)
    raw_value = "https://retry.example"

    first = await enricher.execute_structured([raw_value])
    second = await enricher.execute_structured([raw_value])

    assert first.outcomes[0].status is OutcomeStatus.FAILURE
    assert second.outcomes[0].status is OutcomeStatus.SUCCESS
    assert [output.text for output in second.outcomes[0].outputs] == ["recovered"]
    assert first.outcomes[0].input_ref == canonical_input_hash(raw_value)
    assert second.outcomes[0].input_ref == canonical_input_hash(raw_value)


@pytest.mark.asyncio
async def test_parent_cancellation_returns_held_outcomes_without_leaking_pending_work(
    make_enricher, monkeypatch
):
    enricher, _graph = make_enricher(max_concurrency=1)
    fetch_started = asyncio.Event()
    never_finish = asyncio.Event()
    calls = []

    async def fetch(request):
        calls.append(str(request.url))
        fetch_started.set()
        await never_finish.wait()

    _patch_transport(monkeypatch, fetch)
    values = ["https://first.example", "https://second.example"]
    task = asyncio.create_task(enricher.execute_structured(values))
    await fetch_started.wait()
    task.cancel()
    result = await task

    assert calls[0] == "https://first.example/"
    assert set(calls).issubset({"https://first.example/", "https://second.example/"})
    assert [outcome.status for outcome in result.outcomes] == [
        OutcomeStatus.HOLD,
        OutcomeStatus.HOLD,
    ]
    assert [outcome.diagnostic.code for outcome in result.outcomes] == [
        "cancelled",
        "cancelled",
    ]
