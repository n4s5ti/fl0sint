"""DEF-46 runtime proofs for the enabled WebsiteToLinks acquisition path."""

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading

import pytest

import flowsint_core.core.enricher_base as enricher_base_module
import flowsint_enrichers.website.to_links as links_module
from flowsint_core.core.graph import CaptureGraphRepository
from flowsint_core.core.graph.connection import Neo4jConnection
from flowsint_enrichers.website.to_links import WebsiteToLinks
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


@contextmanager
def _crawler_site(link_count=4):
    links = "".join(
        f'<a href="/inside-{index}">inside {index}</a>' for index in range(link_count)
    )

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = (
                f"<html><body>{links}</body></html>".encode()
                if self.path == "/seed"
                else b"<html><body>leaf</body></html>"
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://test.localhost:{server.server_port}"
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


@pytest.fixture(autouse=True)
def _silence_legacy_logging(monkeypatch):
    monkeypatch.setattr(links_module, "Logger", _SilentLogger)
    monkeypatch.setattr(enricher_base_module, "Logger", _SilentLogger)


@pytest.mark.asyncio
async def test_enabled_crawler_auto_flushes_unreviewed_candidates_without_writes():
    """More than one capture batch is observed through the real crawler callback."""
    with _crawler_site(link_count=4) as base_url:
        scraper = WebsiteToLinks(sketch_id="def46-batch")
        results = await scraper.execute([Website(url=f"{base_url}/seed")])

    repository = scraper.graph_service.repository
    assert isinstance(repository, CaptureGraphRepository)
    assert repository.batch_queue == []
    assert any(
        operation.operation_type == "capture_batch_flush"
        for operation in repository.get_captured_operations()
    )
    candidates = results[0]["candidate_operations"]
    assert len(candidates) > repository.batch_size
    assert {candidate["disposition"] for candidate in candidates} == {"unreviewed"}
    assert {candidate["source_input"]["input_index"] for candidate in candidates} == {0}
    assert {candidate["source_input"]["source_url"] for candidate in candidates} == {
        f"{base_url}/seed"
    }
    assert all("element_id" not in candidate for candidate in candidates)


@pytest.mark.asyncio
async def test_real_discovery_callback_keeps_partial_candidates_after_exception(
    monkeypatch,
):
    """An exception raised from the direct callback cannot erase earlier captures."""
    with _crawler_site(link_count=1) as base_url:
        scraper = WebsiteToLinks(sketch_id="def46-callback")
        original_log = scraper.log_graph_message

        def fail_after_discovery(message):
            if "links to internal website" in message:
                raise RuntimeError("callback logging failure")
            return original_log(message)

        monkeypatch.setattr(scraper, "log_graph_message", fail_after_discovery)
        results = await scraper.execute([Website(url=f"{base_url}/seed")])

    candidates = results[0]["candidate_operations"]
    assert results[0]["internal_urls"] == []
    assert len(candidates) >= 6
    assert all(
        candidate["source_input"]
        == {
            "execution_id": results[0]["execution_id"],
            "input_index": 0,
            "source_url": f"{base_url}/seed",
        }
        for candidate in candidates
    )


@pytest.mark.asyncio
async def test_enabled_crawler_rejects_unsupported_graph_intent_explicitly():
    """Unsupported graph reads do not return an empty capture-mode stub."""
    with _crawler_site(link_count=0) as base_url:
        scraper = WebsiteToLinks(sketch_id="def46-unsupported")
        await scraper.execute([Website(url=f"{base_url}/seed")])

    with pytest.raises(NotImplementedError, match="query"):
        scraper.graph_service.query("MATCH (n) RETURN n")
    rejected = scraper.graph_service.repository.get_captured_operations()[-1]
    assert rejected.operation_type == "unsupported_publication_intent"
    assert rejected.error is not None


@pytest.mark.asyncio
async def test_enabled_crawler_runs_without_neo4j_credentials_or_connection(
    monkeypatch,
):
    """The public default constructor selects capture mode before Neo4j is touched."""
    monkeypatch.setenv("NEO4J_URI_BOLT", "")
    monkeypatch.setenv("NEO4J_USERNAME", "")
    monkeypatch.setenv("NEO4J_PASSWORD", "")

    def unexpected_connection(cls):
        raise AssertionError("capture-only crawler attempted a Neo4j connection")

    monkeypatch.setattr(
        Neo4jConnection, "get_instance", classmethod(unexpected_connection)
    )
    with _crawler_site(link_count=0) as base_url:
        scraper = WebsiteToLinks(sketch_id="def46-no-neo4j")
        results = await scraper.execute([Website(url=f"{base_url}/seed")])

    assert isinstance(scraper.graph_service.repository, CaptureGraphRepository)
    assert results[0]["candidate_operations"]


@pytest.mark.asyncio
async def test_reused_crawler_scopes_candidates_to_the_current_execution():
    """Repeated execution cannot return candidates from an earlier input."""
    with _crawler_site(link_count=1) as base_url:
        scraper = WebsiteToLinks(sketch_id="def46-reused-instance")
        first = await scraper.execute([Website(url=f"{base_url}/seed")])
        second = await scraper.execute([Website(url=f"{base_url}/seed")])

    first_result = first[0]
    second_result = second[0]
    first_candidates = first_result["candidate_operations"]
    second_candidates = second_result["candidate_operations"]
    assert len(first_candidates) == len(second_candidates)
    assert second_result["execution_id"] == first_result["execution_id"] + 1
    assert min(candidate["sequence"] for candidate in second_candidates) > max(
        candidate["sequence"] for candidate in first_candidates
    )
    assert {
        candidate["source_input"]["execution_id"] for candidate in second_candidates
    } == {second_result["execution_id"]}
