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
    CONNECTOR_DESTINATIONS_PATH: Path | None = (
        Path(value) if (value := os.environ.get("CONNECTOR_DESTINATIONS_PATH")) else None
    )


settings = Settings()
destination_registry: DestinationRegistry = load_destination_registry(
    settings.CONNECTOR_DESTINATIONS_PATH
)
