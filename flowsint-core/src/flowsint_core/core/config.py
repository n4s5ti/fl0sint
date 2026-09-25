import os
from dotenv import load_dotenv
from pathlib import Path

from flowsint_core.core.connector_egress import (
    DestinationRegistry,
    load_destination_registry,
)


load_dotenv()


class Settings:
    CELERY_BROKER_URL = os.environ["REDIS_URL"]
    CELERY_RESULT_BACKEND = os.environ["REDIS_URL"]

    # GPU / accelerator worker pool
    #   "prefork" — traditional process pool (default)
    #   "threads" — thread pool (shared GPU context)
    #   "gevent"  — gevent pool (alternative async)
    WORKER_POOL: str = os.environ.get("WORKER_POOL", "prefork")

    # Max GPU worker concurrency (tasks that can share the CUDA context)
    WORKER_GPU_CONCURRENCY: int = int(os.environ.get("WORKER_GPU_CONCURRENCY", "4"))

    # Default HTTP transport settings (used by flowsint_enrichers.transport)
    HTTP_MAX_CONNECTIONS: int = int(os.environ.get("HTTP_MAX_CONNECTIONS", "50"))
    HTTP_DEFAULT_TIMEOUT: int = int(os.environ.get("HTTP_DEFAULT_TIMEOUT", "15"))
    HTTP_ENABLE_QUIC: bool = os.environ.get("HTTP_ENABLE_QUIC", "false").lower() in ("1", "true", "yes")
    CONNECTOR_DESTINATIONS_PATH: Path | None = (
        Path(value) if (value := os.environ.get("CONNECTOR_DESTINATIONS_PATH")) else None
    )



settings = Settings()
destination_registry: DestinationRegistry = load_destination_registry(
    settings.CONNECTOR_DESTINATIONS_PATH
)
