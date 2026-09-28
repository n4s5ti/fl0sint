"""Conservative disclosure policy for URLs retained outside fetch execution."""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_BENIGN_QUERY_NAMES = frozenset({"page", "view", "edition", "filter", "department"})
_CREDENTIAL_WORDS = frozenset({
    "access", "api", "auth", "authorization", "bearer", "client", "code",
    "credential", "key", "oauth", "pass", "passwd", "password", "pwd",
    "secret", "session", "signature", "signed", "sig", "token",
})


def _query_name_supported(name: str) -> bool:
    normalized = name.casefold()
    pieces = tuple(piece for piece in re.split(r"[^a-z0-9]+|(?<=[a-z])(?=[A-Z])", normalized) if piece)
    if normalized in _BENIGN_QUERY_NAMES:
        return True
    return not any(
        piece in _CREDENTIAL_WORDS
        or any(piece.startswith(word) or piece.endswith(word) for word in _CREDENTIAL_WORDS)
        for piece in pieces
    )


def disclose_url(url: str) -> str:
    """Return a report-safe URL, dropping an ambiguous credential-like query."""
    parts = urlsplit(url)
    host = f"[{parts.hostname}]" if parts.hostname and ":" in parts.hostname else parts.hostname
    port = f":{parts.port}" if parts.port is not None else ""
    try:
        pairs = parse_qsl(parts.query, keep_blank_values=True, strict_parsing=False)
        query = urlencode(pairs) if all(_query_name_supported(name) for name, _ in pairs) else ""
    except (UnicodeError, ValueError):
        query = ""
    return urlunsplit((parts.scheme, f"{host or ''}{port}", parts.path or "/", query, ""))


__all__ = ["disclose_url"]
