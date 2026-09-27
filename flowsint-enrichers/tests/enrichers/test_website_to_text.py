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

from flowsint_enrichers.website.to_text import WebsiteTextOccurrence, WebsiteToText
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

    async def fetch(url, **_kwargs):
        if "slow" in url:
            await asyncio.sleep(0.02)
            return "slow"
        if "failure" in url:
            return None
        if "cancel" in url:
            raise asyncio.CancelledError()
        return "shared"

    monkeypatch.setattr(enricher, "_fetch_text_async", fetch)
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
    assert [tuple(output.text for output in occurrence.outputs) for occurrence in occurrences] == [
        ("slow",),
        (),
        ("shared",),
        ("shared",),
        (),
    ]
    assert occurrences[1].diagnostic.code == "transport_failed"
    assert occurrences[4].diagnostic.code == "cancelled"


@pytest.mark.asyncio
async def test_structured_execution_distinguishes_none_transport_failure_from_empty_success(
    make_enricher, monkeypatch
):
    enricher, _graph = make_enricher()

    async def fetch(url, **_kwargs):
        return None if "failed" in url else ""

    monkeypatch.setattr(enricher, "_fetch_text_async", fetch)
    result = await enricher.execute_structured(
        [Website(url="https://empty.example"), Website(url="https://failed.example")]
    )

    empty, failed = result.outcomes
    assert empty.status is OutcomeStatus.SUCCESS
    assert empty.outputs == ()
    assert empty.diagnostic is None
    assert failed.status is OutcomeStatus.FAILURE
    assert failed.outputs == ()
    assert failed.diagnostic.code == "transport_failed"


class _SectioningWebsiteToText(WebsiteToText):
    def _outputs_from_text(self, text):
        return (Phrase(text=text + " first"), Phrase(text=text + " second"))


@pytest.mark.asyncio
async def test_one_to_many_outputs_retain_one_source_for_every_graph_relationship(
    make_enricher, monkeypatch
):
    enricher, graph = make_enricher(_SectioningWebsiteToText)

    async def fetch(_url, **_kwargs):
        return "page"

    monkeypatch.setattr(enricher, "_fetch_text_async", fetch)
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

    async def fetch(_url, **_kwargs):
        return "shared"

    monkeypatch.setattr(enricher, "_fetch_text_async", fetch)
    first = Website(url="https://duplicate.example")
    second = Website(url="https://duplicate.example")

    first_result = await enricher.execute_structured([first, second])
    second_result = await enricher.execute_structured([first, second])

    assert [[output.text for output in outcome.outputs] for outcome in first_result.outcomes] == [
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

    async def fetch(url, **_kwargs):
        return None if "failed" in url else "kept"

    monkeypatch.setattr(enricher, "_fetch_text_async", fetch)
    result = await enricher.execute_structured(
        [Website(url="https://kept.example"), Website(url="https://failed.example")]
    )
    payload = result.model_dump_json()
    consumer = (
        "import json, sys; value = json.loads(sys.stdin.read()); "
        "assert [item['status'] for item in value['outcomes']] == ['success', 'failure']; "
        "assert value['outcomes'][0]['outputs'][0]['text'] == 'kept'; "
        "assert value['outcomes'][1]['diagnostic']['code'] == 'transport_failed'; "
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
    assert all(isinstance(occurrence, WebsiteTextOccurrence) for occurrence in occurrences)
    outputs = enricher.postprocess(occurrences)

    assert [output.text for output in outputs] == ["slow page", "fast page"]
    assert [(source, output.text) for source, output, _label in graph.relationships] == [
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

    async def fetch(_url, **_kwargs):
        nonlocal calls
        calls += 1
        return None if calls == 1 else "recovered"

    monkeypatch.setattr(enricher, "_fetch_text_async", fetch)
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

    async def fetch(url, **_kwargs):
        calls.append(url)
        fetch_started.set()
        await never_finish.wait()

    monkeypatch.setattr(enricher, "_fetch_text_async", fetch)
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