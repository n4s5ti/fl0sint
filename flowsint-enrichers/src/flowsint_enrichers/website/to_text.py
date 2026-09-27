"""Website text extraction with source-owned asynchronous occurrences.

Transport:
  httpx (HTTP/2 + HTTP/1.1, async, connection-pooled) — primary
  aioquic (HTTP/3 via QUIC) — experimental, gated by enricher param
  requests (sync) — last-resort fallback

Each transport future retains its original Website, input index, terminal status,
and output tuple until graph capture and the final legacy list adapter.
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

from bs4 import BeautifulSoup
from flowsint_core.core.enricher_base import Enricher
from flowsint_core.core.forensics import legacy_execution_boundary
from flowsint_core.core.logger import Logger
from flowsint_execution.models import (
    InputOutcome,
    OutcomeStatus,
    RedactedDiagnostic,
    StructuredExecutionResult,
    canonical_input_hash,
)
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


log = logging.getLogger(__name__)

@dataclass(frozen=True)
class WebsiteTextOccurrence:
    """Immutable ownership record that survives concurrent transport work."""

    source: Website
    index: int
    input_ref: str
    status: OutcomeStatus
    outputs: tuple[Phrase, ...] = ()
    diagnostic: RedactedDiagnostic | None = None

    def to_input_outcome(self) -> InputOutcome:
        return InputOutcome(
            input_ref=self.input_ref,
            status=self.status,
            outputs=self.outputs,
            diagnostic=self.diagnostic,
        )


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
    def _outputs_from_text(self, text: str) -> tuple[Phrase, ...]:
        """Build the per-occurrence outputs without flattening their ownership."""
        return (Phrase(text=text),) if text else ()

    @staticmethod
    def _diagnostic(code: str, message: str) -> RedactedDiagnostic:
        return RedactedDiagnostic(code=code, safe_message=message, retryable=False)

    def _cancelled_occurrence(
        self, index: int, source: Website, input_ref: str
    ) -> WebsiteTextOccurrence:
        return WebsiteTextOccurrence(
            source=source,
            index=index,
            input_ref=input_ref,
            status=OutcomeStatus.HOLD,
            diagnostic=self._diagnostic(
                "cancelled", "The operation was cancelled before completion."
            ),
        )

    def _failed_occurrence(
        self, index: int, source: Website, input_ref: str
    ) -> WebsiteTextOccurrence:
        return WebsiteTextOccurrence(
            source=source,
            index=index,
            input_ref=input_ref,
            status=OutcomeStatus.FAILURE,
            diagnostic=self._diagnostic(
                "unexpected_error", "Unexpected processing failure."
            ),
        )

    async def _fetch_occurrence(
        self, index: int, source: Website, input_ref: str, semaphore: asyncio.Semaphore
    ) -> WebsiteTextOccurrence:
        """Return one terminal, source-owned outcome from one transport future."""
        try:
            async with semaphore:
                text = await self._fetch_text_async(
                    str(source.url),
                    timeout=self.params.get("request_timeout", 10),
                    enable_quic=self.params.get("enable_quic", False),
                )
            if text is None:
                return WebsiteTextOccurrence(
                    source=source,
                    index=index,
                    input_ref=input_ref,
                    status=OutcomeStatus.FAILURE,
                    diagnostic=self._diagnostic(
                        "transport_failed", "All configured transports failed."
                    ),
                )
            return WebsiteTextOccurrence(
                source=source,
                index=index,
                input_ref=input_ref,
                status=OutcomeStatus.SUCCESS,
                outputs=self._outputs_from_text(text),
            )
        except asyncio.CancelledError:
            return self._cancelled_occurrence(index, source, input_ref)
        except Exception:
            return self._failed_occurrence(index, source, input_ref)

    async def _scan_occurrence_specs(
        self, specs: Sequence[tuple[int, Website, str]]
    ) -> tuple[WebsiteTextOccurrence, ...]:
        semaphore = asyncio.Semaphore(self.params.get("max_concurrency", 10))
        tasks = tuple(
            asyncio.create_task(self._fetch_occurrence(index, source, input_ref, semaphore))
            for index, source, input_ref in specs
        )
        try:
            results = await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            for task in tasks:
                task.cancel()
            results = await asyncio.gather(*tasks, return_exceptions=True)

        occurrences: list[WebsiteTextOccurrence] = []
        for position, result in enumerate(results):
            index, source, input_ref = specs[position]
            if isinstance(result, WebsiteTextOccurrence):
                occurrences.append(result)
            elif isinstance(result, asyncio.CancelledError):
                occurrences.append(self._cancelled_occurrence(index, source, input_ref))
            else:
                occurrences.append(self._failed_occurrence(index, source, input_ref))
        return tuple(occurrences)

    async def _scan_occurrences(
        self, data: Sequence[Website], *, start_index: int = 0
    ) -> tuple[WebsiteTextOccurrence, ...]:
        return await self._scan_occurrence_specs(
            tuple(
                (start_index + offset, source, canonical_input_hash(source))
                for offset, source in enumerate(data)
            )
        )

    @staticmethod
    def _legacy_outputs(
        occurrences: Sequence[WebsiteTextOccurrence],
    ) -> List[OutputType]:
        return [
            output
            for occurrence in occurrences
            if occurrence.status is OutcomeStatus.SUCCESS
            for output in occurrence.outputs
        ]

    async def scan(self, data: List[InputType]) -> List[WebsiteTextOccurrence]:
        """Return source-owned occurrence envelopes for postprocess graph capture."""
        return list(await self._scan_occurrences(data))
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
    # postprocess — source-owned graph capture and legacy adaptation
    # ------------------------------------------------------------------
    def postprocess(
        self, occurrences: Sequence[WebsiteTextOccurrence]
    ) -> List[OutputType]:
        """Capture graph edges from source-owned occurrence envelopes only."""
        retained = tuple(occurrences)
        if any(not isinstance(occurrence, WebsiteTextOccurrence) for occurrence in retained):
            raise TypeError("WebsiteToText.postprocess requires occurrence envelopes from scan().")
        self._postprocess_occurrences(retained)
        return self._legacy_outputs(retained)

    def _postprocess_occurrences(
        self, occurrences: Sequence[WebsiteTextOccurrence]
    ) -> None:

        if not self._graph_service:
            return
        for occurrence in occurrences:
            if occurrence.status is not OutcomeStatus.SUCCESS:
                continue
            self.create_node(occurrence.source)
            for output in occurrence.outputs:
                if not output.text:
                    continue
                self.create_node(output)
                self.create_relationship(
                    occurrence.source, output, "HAS_INNER_TEXT"
                )
                self.log_graph_message(
                    f"Extracted text from {occurrence.source.url} "
                    f"({len(output.text)} chars)."
                )

    @legacy_execution_boundary("legacy_enricher_execute")
    async def execute(self, values: List[Any]) -> List[OutputType]:
        if self.name() != "enricher_orchestrator":
            Logger.info(self.sketch_id, {"message": f"Enricher {self.name()} started."})
        try:
            await self.async_init()
            occurrences = await self.scan(self.preprocess(values))
            processed = self.postprocess(occurrences)
            self._graph_service.flush()
            if self.name() != "enricher_orchestrator":
                Logger.completed(
                    self.sketch_id, {"message": f"Enricher {self.name()} finished."}
                )
            return processed
        except asyncio.CancelledError:
            raise
        except Exception:
            if self.name() != "enricher_orchestrator":
                Logger.error(
                    self.sketch_id, {"message": f"Enricher {self.name()} errored."}
                )
            return []

    async def execute_structured(self, values: List[Any]) -> StructuredExecutionResult:
        """Preserve every original input as one terminal structured outcome."""
        outcomes: list[InputOutcome | None] = [None] * len(values)
        if self.name() != "enricher_orchestrator":
            Logger.info(self.sketch_id, {"message": f"Enricher {self.name()} started."})

        try:
            await self.async_init()
        except asyncio.CancelledError:
            outcomes = [
                InputOutcome(
                    input_ref=canonical_input_hash(value),
                    status=OutcomeStatus.HOLD,
                    diagnostic=self._diagnostic(
                        "cancelled", "The operation was cancelled before completion."
                    ),
                )
                for value in values
            ]
        except Exception as error:
            diagnostic = self._classify_structured_exception(error)
            outcomes = [
                InputOutcome(
                    input_ref=canonical_input_hash(value),
                    status=OutcomeStatus.FAILURE,
                    diagnostic=diagnostic,
                )
                for value in values
            ]
        else:
            adapter, primary_field = self._input_validation_context()
            specs: list[tuple[int, Website, str]] = []
            for index, value in enumerate(values):
                try:
                    source = self._validate_single_input(
                        value, adapter=adapter, primary_field=primary_field
                    )
                except Exception as error:
                    outcomes[index] = InputOutcome(
                        input_ref=canonical_input_hash(value),
                        status=OutcomeStatus.FAILURE,
                        diagnostic=self._classify_structured_exception(error),
                    )
                else:
                    specs.append((index, source, canonical_input_hash(value)))
            try:
                occurrences = await self._scan_occurrence_specs(specs)
                self.postprocess(occurrences)
            except asyncio.CancelledError:
                for index, value in enumerate(values):
                    if outcomes[index] is None:
                        outcomes[index] = InputOutcome(
                            input_ref=canonical_input_hash(value),
                            status=OutcomeStatus.HOLD,
                            diagnostic=self._diagnostic(
                                "cancelled", "The operation was cancelled before completion."
                            ),
                        )
            except Exception as error:
                diagnostic = self._classify_structured_exception(error)
                for index, value in enumerate(values):
                    if outcomes[index] is None:
                        outcomes[index] = InputOutcome(
                            input_ref=canonical_input_hash(value),
                            status=OutcomeStatus.FAILURE,
                            diagnostic=diagnostic,
                        )
            else:
                for occurrence in occurrences:
                    outcomes[occurrence.index] = occurrence.to_input_outcome()
        finally:
            try:
                self._graph_service.flush()
            except Exception:
                Logger.error(
                    self.sketch_id,
                    {"message": f"Enricher {self.name()} graph flush failed."},
                )

        final_outcomes: list[InputOutcome] = []
        for index, value in enumerate(values):
            outcome = outcomes[index]
            if outcome is None:
                outcome = InputOutcome(
                    input_ref=canonical_input_hash(value),
                    status=OutcomeStatus.FAILURE,
                    diagnostic=self._diagnostic(
                        "unexpected_error", "Processing ended without a terminal outcome."
                    ),
                )
            final_outcomes.append(outcome)
        if self.name() != "enricher_orchestrator":
            Logger.completed(self.sketch_id, {"message": f"Enricher {self.name()} finished."})
        return StructuredExecutionResult(
            enricher_name=self.name(),
            outcomes=tuple(final_outcomes),
        )




InputType = WebsiteToText.InputType
OutputType = WebsiteToText.OutputType
