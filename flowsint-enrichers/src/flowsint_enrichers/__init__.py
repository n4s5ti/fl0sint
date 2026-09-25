"""
Flowsint Enrichers - Enricher modules for flowsint
"""

# Import registry utilities
from .registry import ENRICHER_REGISTRY, flowsint_enricher, load_all_enrichers

__version__ = "0.1.0"
__author__ = "dextmorgn <contact@flowsint.io>"

__all__ = [
    "ENRICHER_REGISTRY",
    "flowsint_enricher",
    "load_all_enrichers",
]

# Import shared infrastructure (lazy -- no heavy imports at module load)
from . import transport as _transport
from . import gpu as _gpu

HAS_QUIC = _transport.HAS_QUIC
HAS_CUPY = _gpu.HAS_CUPY
HAS_CUDF = _gpu.HAS_CUDF

__all__ += [
    "HAS_QUIC",
    "HAS_CUPY",
    "HAS_CUDF",
    "AsyncTransport",
    "gpu_deduplicate",
    "gpu_batch_transform",
    "gpu_available",
]

# Re-export names at module level for convenience
from .transport import AsyncTransport, get_transport
from .gpu import gpu_deduplicate, gpu_batch_transform, gpu_available
