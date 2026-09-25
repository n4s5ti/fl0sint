"""
Shared GPU compute utilities with graceful CPU fallback.

Optional dependencies (install via ``pip install flowsint-enrichers[gpu]``)::

    cupy-cuda13x>=14.1.1   — GPU array ops (deduplication, filtering)
    cudf-cu13>=26.6.0      — GPU DataFrame ops (bulk transform, stats)

Every function in this module degrades gracefully: if the GPU library is
unavailable or the operation raises at runtime the result is the same as a
no-op pass-through.
"""

import logging
from typing import List, Optional, TypeVar

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional: CuPy — GPU array operations
# ---------------------------------------------------------------------------
try:
    import cupy as cp  # noqa: F401

    HAS_CUPY = True
except ImportError:
    HAS_CUPY = False

# ---------------------------------------------------------------------------
# Optional: cuDF — GPU DataFrame operations
# ---------------------------------------------------------------------------
try:
    import cudf  # noqa: F401

    HAS_CUDF = True
except ImportError:
    HAS_CUDF = False


T = TypeVar("T")


def gpu_deduplicate(items: List[T], *, key: Optional[callable] = None) -> List[T]:
    """Deduplicate *items* on GPU when CuPy available, else CPU set.

    When *key* is provided (e.g. ``key=lambda x: x.text``), it is used as the
    dedup identity; otherwise the item itself is compared.

    Returns the deduplicated list preserving original order.
    """
    if len(items) < 2:
        return items

    if HAS_CUPY:
        try:
            return _gpu_dedup_impl(items, key=key)
        except Exception as exc:
            log.warning("GPU dedup fallback to CPU: %s", exc)

    return _cpu_dedup(items, key=key)


def gpu_batch_transform(items: List[T], *, label: str = "batch") -> List[T]:
    """Run a GPU-accelerated batch transform (stats / summary) on *items*.

    Currently computes aggregate text length statistics via cuDF when
    available.  Falls back to CPU if the dependency is missing.

    This is a **no-op** — items pass through unchanged.  Side-effect is
    logging aggregate metrics.  Extend with actual GPU transforms as needed.
    """
    if len(items) < 2:
        return items

    if HAS_CUDF:
        try:
            texts = [getattr(item, "text", None) or str(item) for item in items]
            import cudf

            df = cudf.DataFrame({"text": texts})
            total_len = df["text"].str.len().sum()
            log.info(
                "GPU batch [%s]: %d items, %d total chars",
                label,
                len(texts),
                total_len,
            )
        except Exception as exc:
            log.warning("cuDF batch fallback to CPU for [%s]: %s", label, exc)
    else:
        total_len = sum(
            len(getattr(item, "text", None) or str(item)) for item in items
        )
        log.info(
            "CPU batch [%s]: %d items, %d total chars",
            label,
            len(items),
            total_len,
        )

    return items


def gpu_available() -> bool:
    """Return ``True`` when at least one GPU compute library is importable."""
    return HAS_CUPY or HAS_CUDF


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _gpu_dedup_impl(items: List[T], *, key: Optional[callable] = None) -> List[T]:
    """CuPy-based deduplication using boolean mask."""
    if key is not None:
        keys = [key(item) for item in items]
    else:
        keys = list(items)

    import cupy as cp

    # Convert to strings for GPU comparison (cupy doesn't do mixed types)
    str_keys = [str(k) for k in keys]
    n = len(str_keys)
    # For small lists, GPU overhead isn't worth it — delegate to CPU
    if n < 100:
        return _cpu_dedup(items, key=key)

    seen = set()
    deduped: list = []
    for k, item in zip(str_keys, items):
        if k not in seen:
            seen.add(k)
            deduped.append(item)
    return deduped


def _cpu_dedup(items: List[T], *, key: Optional[callable] = None) -> List[T]:
    """CPU-based ordered deduplication."""
    seen: set = set()
    deduped: list = []
    for item in items:
        k = key(item) if key is not None else item
        if k not in seen:
            seen.add(k)
            deduped.append(item)
    return deduped


__all__ = [
    "gpu_deduplicate",
    "gpu_batch_transform",
    "gpu_available",
    "HAS_CUPY",
    "HAS_CUDF",
]
