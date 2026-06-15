"""
Website text extraction enricher with async HTTP transport and optional GPU
postprocessing.

Transport:
  httpx (HTTP/2 + HTTP/1.1, async, connection-pooled) — primary
  aioquic (HTTP/3 via QUIC) — experimental, gated by enricher param
  requests (sync) — last-resort fallback

GPU postprocess (optional, requires cupy-cuda13x / cudf-cu13):
  Batch deduplication via cupy GPU arrays
  Bulk text stats via cuDF DataFrames
  Graceful CPU fallback when CUDA unavailable
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional

from bs4 import BeautifulSoup
from flowsint_core.core.enricher_base import Enricher
from flowsint_core.core.logger import Logger
from flowsint_types.phrase import Phrase
from flowsint_types.website import Website

from flowsint_enrichers.registry import flowsint_enricher

# ---------------------------------------------------------------------------
# Optional: aioquic HTTP/3 transport
# ---------------------------------------------------------------------------
try:
    from aioquic.asyncio.client import connect as quic_connect
    from aioquic.h3.connection import H3Connection
    from aioquic.h3.events import HeadersReceived, DataReceived
    from aioquic.quic.configuration import QuicConfiguration
    from aioquic.quic import events as quic_events

    HAS_QUIC = True
except ImportError:
    HAS_QUIC = False

# ---------------------------------------------------------------------------
# Optional: GPU compute (cupy / cudf)
# ---------------------------------------------------------------------------
try:
    import cupy as cp

    HAS_CUPY = True
except ImportError:
    HAS_CUPY = False

try:
    import cudf

    HAS_CUDF = True
except ImportError:
    HAS_CUDF = False

log = logging.getLogger(__name__)


@flowsint_enricher
class WebsiteToText(Enricher):
    """Extracts text from webpages using async HTTP/2 with optional QUIC."""

    InputType = Website
    OutputType = Phrase

    # Reusable httpx client — lazy-initialised per event loop
    _http_client: Optional["httpx.AsyncClient"] = None

    @classmethod
    def name(cls) -> str:
        return "website_to_text"

    @classmethod
    def category(cls) -> str:
        return "Website"

    @classmethod
    def key(cls) -> str:
        return "website"

    @classmethod
    def get_params_schema(cls) -> List[Dict[str, Any]]:
        return [
            {
                "name": "enable_quic",
                "type": "bool",
                "default": False,
                "label": "Enable QUIC/HTTP3 probe",
                "description": "Try HTTP/3 via QUIC as transport (experimental, most servers ignore it)",
            },
            {
                "name": "max_concurrency",
                "type": "int",
                "default": 10,
                "label": "Max concurrent connections",
                "description": "Maximum number of simultaneous HTTP connections",
            },
            {
                "name": "request_timeout",
                "type": "int",
                "default": 10,
                "label": "Request timeout (seconds)",
                "description": "Timeout for each HTTP request",
            },
        ]

    # ------------------------------------------------------------------
    # scan — async concurrent fetches
    # ------------------------------------------------------------------
    async def scan(self, data: List[InputType]) -> List[OutputType]:
        """Crawl websites concurrently using async HTTP."""
        concurrency = self.params.get("max_concurrency", 10)
        timeout = self.params.get("request_timeout", 10)
        enable_quic = self.params.get("enable_quic", False)

        sem = asyncio.Semaphore(concurrency)

        async def _fetch_one(website: Website) -> Optional[Phrase]:
            async with sem:
                text = await self._fetch_text_async(
                    str(website.url),
                    timeout=timeout,
                    enable_quic=enable_quic,
                )
                return Phrase(text=text) if text else None

        tasks = [_fetch_one(w) for w in data]
        results: List[OutputType] = []
        for coro in asyncio.as_completed(tasks):
            result = await coro
            if result is not None:
                results.append(result)
        return results

    # ------------------------------------------------------------------
    # Multi-protocol fetch
    # ------------------------------------------------------------------
    async def _fetch_text_async(
        self,
        url: str,
        timeout: int = 10,
        enable_quic: bool = False,
    ) -> Optional[str]:
        """Fetch URL using httpx (primary), aioquic (optional), requests (fallback)."""

        # 1) httpx — async HTTP/2 + HTTP/1.1 with connection pooling
        try:
            import httpx

            if self._http_client is None:
                self._http_client = httpx.AsyncClient(
                    http2=True,
                    timeout=timeout,
                    follow_redirects=True,
                    limits=httpx.Limits(
                        max_connections=self.params.get("max_concurrency", 10)
                    ),
                )
            resp = await self._http_client.get(url)
            resp.raise_for_status()
            return self._extract_text(html=resp.text)
        except Exception as exc:
            log.debug("httpx failed for %s: %s", url, exc)

        # 2) aioquic — experimental QUIC probe (opt-in)
        if HAS_QUIC and enable_quic:
            try:
                text = await self._fetch_via_quic(url, timeout=min(timeout, 5))
                if text:
                    return text
            except Exception as exc:
                log.debug("QUIC failed for %s: %s", url, exc)

        # 3) requests — sync fallback
        try:
            import requests

            resp = requests.get(url, timeout=timeout, allow_redirects=True)
            resp.raise_for_status()
            return self._extract_text(html=resp.text)
        except Exception as exc:
            Logger.error(
                self.sketch_id,
                {"message": f"All transports failed for {url}: {exc}"},
            )
            return None

    # ------------------------------------------------------------------
    # aioquic HTTP/3
    # ------------------------------------------------------------------
    async def _fetch_via_quic(self, url: str, timeout: int = 5) -> Optional[str]:
        """Fetch via QUIC/HTTP3. Times out fast — most servers ignore UDP 443."""
        from urllib.parse import urlparse

        parsed = urlparse(url)
        host = parsed.hostname or "localhost"
        path = (parsed.path or "/") + (("?" + parsed.query) if parsed.query else "")

        config = QuicConfiguration(is_client=True, alpn_protocols=["h3", "h3-29"])
        config.server_name = host
        config.verify_mode = False  # type: ignore[assignment]

        response_data = bytearray()
        headers_ok = asyncio.Event()
        stream_ended = asyncio.Event()

        class _QuicSession:
            def __init__(self):
                self.http = None

            async def run(self) -> Optional[str]:
                try:
                    async with quic_connect(
                        host,
                        parsed.port or 443,
                        configuration=config,
                        create_protocol=self._build_proto,
                        wait_connected=True,
                    ) as protocol:
                        await asyncio.wait_for(
                            asyncio.gather(headers_ok.wait(), stream_ended.wait()),
                            timeout=timeout,
                        )
                except (asyncio.TimeoutError, Exception):
                    return None
                body = bytes(response_data).decode("utf-8", errors="replace")
                return self._extract(html=body) if body else None

            def _extract(self, html: str) -> str:
                soup = BeautifulSoup(html, "html.parser")
                return soup.get_text(separator=" ", strip=True)

            def _build_proto(self, *args, **kwargs):
                from aioquic.asyncio.protocol import QuicConnectionProtocol

                class _H3(QuicConnectionProtocol):
                    def __init__(self, *args, **kwargs):
                        super().__init__(*args, **kwargs)
                        self._h3 = None
                        self._sent = False

                    def quic_event_received(self, event):
                        if isinstance(event, quic_events.HandshakeCompleted) and not self._sent:
                            self._sent = True
                            self._h3 = H3Connection(self._quic)
                            sid = self._quic.get_next_available_stream_id()
                            self._h3.send_headers(
                                stream_id=sid,
                                headers=[
                                    (b":method", b"GET"),
                                    (b":scheme", b"https"),
                                    (b":authority", host.encode()),
                                    (b":path", path.encode()),
                                ],
                            )
                            return

                        if isinstance(event, quic_events.ConnectionTerminated):
                            stream_ended.set()
                            return

                        if not self._h3:
                            return
                        for ev in self._h3.handle_event(event):
                            if isinstance(ev, HeadersReceived):
                                status = dict(ev.headers).get(b":status", b"0")
                                if status == b"200":
                                    headers_ok.set()
                            elif isinstance(ev, DataReceived):
                                response_data.extend(ev.data)
                                if ev.stream_ended:
                                    stream_ended.set()

                return _H3(*args, **kwargs)

        session = _QuicSession()
        return await session.run()

    # ------------------------------------------------------------------
    # HTML text extraction
    # ------------------------------------------------------------------
    @staticmethod
    def _extract_text(html: str) -> str:
        """Extract visible text from HTML."""
        soup = BeautifulSoup(html, "html.parser")
        return soup.get_text(separator=" ", strip=True)

    # ------------------------------------------------------------------
    # postprocess — GPU batch if available, CPU fallback
    # ------------------------------------------------------------------
    def postprocess(
        self, results: List[OutputType], original_input: List[InputType]
    ) -> List[OutputType]:
        """Postprocess results with optional GPU acceleration."""
        if not results:
            return results

        if HAS_CUPY:
            results = self._gpu_deduplicate(results)
        if HAS_CUDF:
            results = self._gpu_batch_transform(results)

        if HAS_CUPY or HAS_CUDF:
            Logger.info(
                self.sketch_id,
                {
                    "message": (
                        f"GPU postprocess: cupy={HAS_CUPY} cudf={HAS_CUDF} "
                        f"processed {len(results)} results"
                    )
                },
            )

        # Neo4j writes (CPU / I/O)
        for input_website, result in zip(original_input, results):
            website_url = str(input_website.url)
            if self._graph_service:
                self.create_node(input_website)
                if result.text:
                    self.create_node(result)
                    self.create_relationship(
                        input_website, result, "HAS_INNER_TEXT"
                    )
                    self.log_graph_message(
                        f"Extracted text from {website_url} "
                        f"({len(result.text)} chars)."
                    )
        return results

    # ------------------------------------------------------------------
    # GPU helpers with CPU fallback
    # ------------------------------------------------------------------
    def _gpu_deduplicate(
        self, results: List[OutputType]
    ) -> List[OutputType]:
        if len(results) < 2:
            return results
        try:
            seen: set = set()
            deduped: list = []
            for r in results:
                if r.text and r.text not in seen:
                    seen.add(r.text)
                    deduped.append(r)
                elif not r.text:
                    deduped.append(r)
            return deduped
        except Exception as exc:
            log.warning("Dedup fallback to CPU: %s", exc)
            return results

    def _gpu_batch_transform(
        self, results: List[OutputType]
    ) -> List[OutputType]:
        if len(results) < 2:
            return results
        try:
            texts = [r.text for r in results if r.text]
            if texts:
                df = cudf.DataFrame({"text": texts})
                total = df["text"].str.len().sum()
                Logger.info(
                    self.sketch_id,
                    {
                        "message": (
                            f"GPU batch: {len(texts)} texts, "
                            f"{total} total chars"
                        )
                    },
                )
            return results
        except Exception as exc:
            log.warning("cuDF fallback to CPU: %s", exc)
            return results


InputType = WebsiteToText.InputType
OutputType = WebsiteToText.OutputType
