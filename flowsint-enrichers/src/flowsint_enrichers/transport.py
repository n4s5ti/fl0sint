"""
Shared async HTTP transport with HTTP/2, optional QUIC/HTTP3, and graceful
fallback.

Usage::

    transport = AsyncTransport()
    html = await transport.fetch(url)                 # HTTP/2 via httpx
    html = await transport.fetch(url, enable_quic=True)  # try QUIC too
    html = transport.fetch_sync(url)                  # sync fallback

The fallback chain is::

    httpx AsyncClient (HTTP/2 + HTTP/1.1)
      -> aioquic (QUIC/HTTP3, opt-in)
        -> requests (sync, catch-all)
"""

import asyncio
import logging
from typing import Optional

log = logging.getLogger(__name__)

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


class AsyncTransport:
    """Async HTTP transport with HTTP/2, optional QUIC, and graceful fallback.

    Maintains a shared connection-pooled httpx AsyncClient.  The instance
    is designed to be long-lived (module-level singleton).

    Parameters
    ----------
    max_connections : int
        httpx connection-pool limit (default 50).
    default_timeout : int
        Per-request timeout in seconds (default 15).
    default_retries : int
        Number of retries for transient failures (default 1).
    """

    __slots__ = ("_httpx", "_limits", "_default_timeout", "_default_retries")

    def __init__(
        self,
        max_connections: int = 50,
        default_timeout: int = 15,
        default_retries: int = 1,
    ) -> None:
        self._httpx: Optional["httpx.AsyncClient"] = None
        self._limits = max_connections
        self._default_timeout = default_timeout
        self._default_retries = default_retries

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def fetch(
        self,
        url: str,
        *,
        timeout: Optional[int] = None,
        enable_quic: bool = False,
        max_retries: Optional[int] = None,
    ) -> Optional[str]:
        """Fetch *url* returning the response body as text, or ``None``.

        Fallback chain:
            1. httpx AsyncClient (HTTP/2 + HTTP/1.1, connection-pooled)
            2. aioquic (QUIC / HTTP/3, only when *enable_quic* is ``True``)
            3. ``requests`` (sync, catch-all)
        """
        body = await self._fetch_httpx(url, timeout=timeout, max_retries=max_retries)
        if body is not None:
            return body

        if HAS_QUIC and enable_quic:
            body = await self._fetch_quic(url, timeout=min(timeout or self._default_timeout, 5))
            if body is not None:
                return body

        return self._fetch_requests(url, timeout=timeout)

    def fetch_sync(
        self,
        url: str,
        *,
        timeout: Optional[int] = None,
        enable_quic: bool = False,
    ) -> Optional[str]:
        """Synchronous variant of :meth:`fetch` — calls ``requests`` directly."""
        if HAS_QUIC and enable_quic:
            try:
                body = asyncio.run(
                    self._fetch_quic(url, timeout=min(timeout or self._default_timeout, 5))
                )
                if body is not None:
                    return body
            except Exception:
                pass
        return self._fetch_requests(url, timeout=timeout)

    async def close(self) -> None:
        """Close the underlying httpx client, releasing connection pool."""
        if self._httpx is not None:
            await self._httpx.aclose()
            self._httpx = None

    # ------------------------------------------------------------------
    # Transport backends
    # ------------------------------------------------------------------

    async def _fetch_httpx(
        self,
        url: str,
        *,
        timeout: Optional[int] = None,
        max_retries: Optional[int] = None,
    ) -> Optional[str]:
        """HTTP/2 + HTTP/1.1 via httpx AsyncClient with connection pooling."""
        import httpx

        if self._httpx is None:
            self._httpx = httpx.AsyncClient(
                http2=True,
                timeout=self._default_timeout,
                follow_redirects=True,
                limits=httpx.Limits(max_connections=self._limits),
            )
        last_exc: Optional[Exception] = None
        attempts = (max_retries if max_retries is not None else self._default_retries) + 1
        for attempt in range(attempts):
            try:
                resp = await self._httpx.get(url, timeout=timeout or self._default_timeout)
                resp.raise_for_status()
                return resp.text
            except Exception as exc:
                if "Name or service not known" in str(exc):
                    return None  # DNS failure is terminal
                last_exc = exc
                if attempt < attempts - 1:
                    await asyncio.sleep(0.5 * (attempt + 1))
        log.debug("httpx failed for %s after %d attempt(s): %s", url, attempts, last_exc)
        return None

    async def _fetch_quic(self, url: str, timeout: int = 5) -> Optional[str]:
        """QUIC / HTTP/3 via aioquic (experimental, most servers ignore UDP 443)."""
        if not HAS_QUIC:
            return None
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
                return body if body else None

            def _build_proto(self, *args, **kwargs):
                from aioquic.asyncio.protocol import QuicConnectionProtocol

                class _H3Proto(QuicConnectionProtocol):
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

                return _H3Proto(*args, **kwargs)

        session = _QuicSession()
        return await session.run()

    def _fetch_requests(self, url: str, *, timeout: Optional[int] = None) -> Optional[str]:
        """Synchronous catch-all fallback via ``requests``."""
        import requests

        try:
            resp = requests.get(
                url,
                timeout=timeout or self._default_timeout,
                allow_redirects=True,
            )
            resp.raise_for_status()
            return resp.text
        except Exception as exc:
            log.debug("Requests fallback failed for %s: %s", url, exc)
            return None


# ---------------------------------------------------------------------------
# Module-level singleton (reuse across enrichers)
# ---------------------------------------------------------------------------
_default_transport: Optional[AsyncTransport] = None


def get_transport(
    max_connections: int = 50,
    default_timeout: int = 15,
    default_retries: int = 1,
) -> AsyncTransport:
    """Return the module-level :class:`AsyncTransport` singleton."""
    global _default_transport
    if _default_transport is None:
        _default_transport = AsyncTransport(
            max_connections=max_connections,
            default_timeout=default_timeout,
            default_retries=default_retries,
        )
    return _default_transport


__all__ = [
    "AsyncTransport",
    "get_transport",
    "HAS_QUIC",
]
