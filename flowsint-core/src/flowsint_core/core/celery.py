from celery import Celery
from .config import settings

celery = Celery(
    "flowsint",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=[
        "flowsint_core.tasks.event",
        "flowsint_core.tasks.enricher",
        "flowsint_core.tasks.flow",
        "flowsint_core.tasks.graph_projection",
    ],
)

celery.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=3600,  # 1 hour
    worker_max_tasks_per_child=1000,
    worker_prefetch_multiplier=4,
    # GPU / accelerator pool (overridden via --pool CLI flag at worker start)
    worker_pool=settings.WORKER_POOL,
    worker_concurrency=settings.WORKER_GPU_CONCURRENCY,
    beat_schedule={
        "sweep-graph-projection-jobs": {
            "task": "sweep_graph_projection_jobs",
            "schedule": 60.0,
        }
    },
)
