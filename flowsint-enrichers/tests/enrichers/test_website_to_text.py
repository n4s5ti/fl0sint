"""Behavioral regressions for occurrence-safe WebsiteToText execution."""

import asyncio
import os
import subprocess
import sys
import threading
import time
import hashlib
import json
from datetime import datetime, timedelta, timezone
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import httpx

import flowsint_enrichers.website.to_text as website_module
import flowsint_execution.fetch as fetch_module
from flowsint_execution.artifacts import ArtifactState, FilesystemArtifactStore, RetentionAuthority
from flowsint_execution.artifact_runtime import (
    decode_source_proof, resolve_persisted_source_proof, resolve_persisted_span,
)
from flowsint_execution.extraction_runtime import resolve_and_extract_observations
from flowsint_execution.observed_extraction import parse_observed_extraction_metadata
from flowsint_enrichers import ENRICHER_REGISTRY
from flowsint_enrichers.website.to_text import WebsiteFetchError, WebsiteTextOccurrence, WebsiteToText
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

    monkeypatch.setattr(fetch_module, "execute_fetch", execute)


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
def make_enricher(monkeypatch, tmp_path):
    monkeypatch.setattr("flowsint_enrichers.website.to_text.Logger", _SilentLogger)
    monkeypatch.setattr("flowsint_core.core.enricher_base.Logger", _SilentLogger)

    def make(cls=WebsiteToText, **params):
        graph = _RecordingGraph()
        enricher = cls(
            sketch_id="def42-test",
            params_schema=[],
            params=params,
            graph_service=graph,
            artifact_store=FilesystemArtifactStore(tmp_path / f"store-{len(list(tmp_path.iterdir()))}"),
            retention_authority=RetentionAuthority(
                issuer_id="test-deployment",
                reviewer_id="test-reviewer",
                policy_id="website-test-policy",
                policy_digest=hashlib.sha256(b"reviewed website test policy").hexdigest(),
                caller_id="website-to-text",
                scope="local-web-fetch",
                source_family="http",
                expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            ),
        )
        return enricher, graph

    return make


def _runtime_config(tmp_path, caller="website-to-text", scope="local-web-fetch"):
    now = datetime.now(timezone.utc)
    policy = {
        "issuer_id": "deployment-owner", "reviewer_id": "operator-reviewer",
        "policy_id": "website-runtime-v1", "caller_id": caller, "scope": scope,
        "source_family": "http", "issued_at": (now - timedelta(hours=1)).isoformat(),
        "expires_at": (now + timedelta(hours=1)).isoformat(),
        "retain_normalized_text": True,
    }
    canonical = json.dumps(policy, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    policy["content_digest"] = hashlib.sha256(canonical.encode()).hexdigest()
    path = tmp_path / "runtime.json"
    path.write_text(json.dumps({"format_version": "1.0", "store_root": str(tmp_path / "runtime-store"), "policies": [policy]}))
    return path


@pytest.mark.asyncio
async def test_registry_runtime_config_emits_retrievable_structured_proof(tmp_path, monkeypatch):
    config = _runtime_config(tmp_path)
    monkeypatch.setenv("FLOWSINT_ARTIFACT_RUNTIME_CONFIG", str(config))
    monkeypatch.setattr("flowsint_enrichers.website.to_text.Logger", _SilentLogger)
    monkeypatch.setattr("flowsint_core.core.enricher_base.Logger", _SilentLogger)
    _patch_transport(monkeypatch, lambda _request: httpx.Response(200, text="retained registry text"))
    enricher = ENRICHER_REGISTRY.get_enricher(
        "website_to_text", "runtime-sketch", "runtime-scan",
        params={}, graph_service=_RecordingGraph(),
    )
    result = await enricher.execute_structured([Website(url="https://runtime.example/path?secret=hidden")])
    outcome = result.outcomes[0]
    assert outcome.status is OutcomeStatus.SUCCESS
    reference = outcome.evidence[0].artifact_reference
    proof = decode_source_proof(reference)
    assert proof.context.occurrence_id == "input-0"
    observation_result = parse_observed_extraction_metadata(outcome.metadata[0])
    assert observation_result.observations == ()
    assert len(proof.input_ref) == 64
    resolved = resolve_persisted_source_proof(
        reference, caller_id="website-to-text", scope="local-web-fetch",
        source_family="http", operation_id=proof.context.operation_id,
        occurrence_id=proof.context.occurrence_id, config_path=config,
    )
    assert resolved.state is ArtifactState.AVAILABLE
    assert resolved.body == b"retained registry text"
    span = resolve_persisted_span(
        reference, proof.spans[0].span_id,
        caller_id="website-to-text", scope="local-web-fetch",
        source_family="http", operation_id=proof.context.operation_id,
        occurrence_id=proof.context.occurrence_id, config_path=config,
    )
    assert span.state is ArtifactState.AVAILABLE
    assert span.text == outcome.outputs[0].text[
        proof.spans[0].normalized_start:proof.spans[0].normalized_end
    ]
    serialized = result.model_dump_json()
    assert "hidden" not in serialized
    assert str(tmp_path / "runtime-store") not in serialized


@pytest.mark.asyncio
async def test_live_and_saved_paths_share_observation_result(tmp_path, monkeypatch):
    config = _runtime_config(tmp_path)
    monkeypatch.setenv("FLOWSINT_ARTIFACT_RUNTIME_CONFIG", str(config))
    monkeypatch.setattr("flowsint_enrichers.website.to_text.Logger", _SilentLogger)
    monkeypatch.setattr("flowsint_core.core.enricher_base.Logger", _SilentLogger)
    body = '<a href="?page=2">next</a><p>info@example.test</p>'
    _patch_transport(monkeypatch, lambda _request: httpx.Response(200, text=body))
    enricher = WebsiteToText(sketch_id="parity", params_schema=[], params={}, graph_service=_RecordingGraph())
    live = await enricher.execute_structured([Website(url="https://runtime.example/final?view=full")])
    outcome = live.outcomes[0]
    metadata = outcome.metadata[0]
    proof_value = outcome.evidence[0].artifact_reference
    proof = decode_source_proof(proof_value)
    state, saved = await resolve_and_extract_observations(
        proof_value, caller_id="website-to-text", scope="local-web-fetch", source_family="http",
        operation_id=proof.context.operation_id, occurrence_id=proof.context.occurrence_id, config_path=config,
    )
    assert state is ArtifactState.AVAILABLE and saved is not None
    assert metadata.format_version == "observed-extraction/1.0"
    assert parse_observed_extraction_metadata(metadata) == saved
    assert [item["observation_id"] for item in metadata.payload["observations"]] == [item.observation_id for item in saved.observations]
    assert all(item["execution_state"] == "not_executable" for item in metadata.payload["observations"])


@pytest.mark.asyncio
async def test_missing_reviewed_retention_holds_structured_and_legacy_raises(monkeypatch):
    monkeypatch.setattr("flowsint_enrichers.website.to_text.Logger", _SilentLogger)
    monkeypatch.setattr("flowsint_core.core.enricher_base.Logger", _SilentLogger)
    _patch_transport(monkeypatch, lambda _request: httpx.Response(200, text="consumed body"))
    enricher = WebsiteToText(sketch_id="hold-test", params_schema=[], params={}, graph_service=_RecordingGraph())
    structured = await enricher.execute_structured([Website(url="https://held.example")])
    assert structured.outcomes[0].status is OutcomeStatus.HOLD
    assert structured.outcomes[0].outputs == ()
    with pytest.raises(WebsiteFetchError, match="retention_policy_unavailable"):
        await enricher.execute([Website(url="https://held-again.example")])


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
async def test_occurrence_reconstruction_rejects_missing_result(
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

    monkeypatch.setattr(fetch_module, "execute_fetch", execute)
    first = Website(url="https://first.example")
    second = Website(url="https://second.example")
    occurrences = await enricher._scan_occurrences([first, second])

    assert [item.source for item in occurrences] == [first, second]
    assert [item.status for item in occurrences] == [
        OutcomeStatus.FAILURE,
        OutcomeStatus.FAILURE,
    ]
    assert {item.diagnostic.code for item in occurrences} == {"invalid_fetch_result"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation",
    ["operation", "input_ref", "missing", "unknown", "duplicate", "extra"],
)
async def test_occurrence_reconstruction_rejects_unbound_results(
    make_enricher, monkeypatch, mutation
):
    enricher, _graph = make_enricher()

    async def execute(operation):
        fetched = await real_execute_fetch(
            operation,
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, text=request.url.host)
            ),
        )
        outcomes = list(fetched.outcomes)
        operation_id = fetched.operation_id
        if mutation == "operation":
            operation_id = "wrong-operation"
        elif mutation == "input_ref":
            outcomes[0] = outcomes[0].model_copy(update={"input_ref": "c" * 64})
        elif mutation == "missing":
            outcomes.pop()
        elif mutation == "unknown":
            outcomes[0] = outcomes[0].model_copy(update={"occurrence_id": "unknown"})
        elif mutation == "duplicate":
            outcomes[1] = outcomes[1].model_copy(
                update={"occurrence_id": outcomes[0].occurrence_id}
            )
        else:
            outcomes.append(
                outcomes[0].model_copy(update={"occurrence_id": "unexpected"})
            )
        return FetchResult(
            operation_id=operation_id,
            outcomes=tuple(outcomes),
            actual_resources=fetched.actual_resources,
        )

    monkeypatch.setattr(fetch_module, "execute_fetch", execute)
    occurrences = await enricher._scan_occurrences(
        [Website(url="https://first.example"), Website(url="https://second.example")]
    )

    assert [item.status for item in occurrences] == [
        OutcomeStatus.FAILURE,
        OutcomeStatus.FAILURE,
    ]
    assert {item.diagnostic.code for item in occurrences} == {"invalid_fetch_result"}


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
