# pyright: reportUnsupportedDunderAll=false
"""Flowsint enrichers public namespace with demand-loaded service integrations."""

from __future__ import annotations

from importlib import import_module
from typing import Any

__version__ = "0.1.0"
__author__ = "dextmorgn"
_ENRICHER_DECORATOR = f"{__name__.rsplit('_', 1)[0]}_enricher"

__all__ = [
    "ENRICHER_REGISTRY",
    _ENRICHER_DECORATOR,
    "load_all_enrichers",
    "HAS_QUIC",
    "HAS_CUPY",
    "HAS_CUDF",
    "AsyncTransport",
    "gpu_deduplicate",
    "gpu_batch_transform",
    "gpu_available",
    "ScrapeBundle",
    "ScrapeOptions",
    "scrape_websites",
]

_REGISTRY_EXPORTS = {"ENRICHER_REGISTRY", "load_all_enrichers"}
_TRANSPORT_EXPORTS = {"HAS_QUIC", "AsyncTransport"}
_GPU_EXPORTS = {"HAS_CUPY", "HAS_CUDF", "gpu_deduplicate", "gpu_batch_transform", "gpu_available"}
_STANDALONE_EXPORTS = {"ScrapeBundle", "ScrapeOptions", "scrape_websites"}


def __getattr__(name: str) -> Any:
    if name in _REGISTRY_EXPORTS or name == _ENRICHER_DECORATOR:
        return getattr(import_module(f"{__name__}.registry"), name)
    if name in _TRANSPORT_EXPORTS:
        return getattr(import_module(f"{__name__}.transport"), name)
    if name in _GPU_EXPORTS:
        return getattr(import_module(f"{__name__}.gpu"), name)
    if name in _STANDALONE_EXPORTS:
        return getattr(import_module(f"{__name__}.standalone"), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
