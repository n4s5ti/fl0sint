"""Celery entry points for durable graph projection jobs."""
from __future__ import annotations

from uuid import UUID

from ..core.celery import celery
from ..core.postgre_db import SessionLocal
from ..core.projection import registry
from ..core.projection.service import GraphProjectionService


@celery.task(name="project_graph_evidence", bind=True)
def project_graph_evidence(self, job_id: str) -> dict[str, object]:
    """Project one committed outbox job; the job remains recoverable after publish loss."""
    session = SessionLocal()
    try:
        result = GraphProjectionService(session, registry.projection_registry).project(
            UUID(job_id), f"celery:{self.request.id}"
        )
        return {
            "job_id": str(result.job_id),
            "status": result.status,
            "attempt": result.attempt,
        }
    finally:
        session.close()


@celery.task(name="sweep_graph_projection_jobs", bind=True)
def sweep_graph_projection_jobs(self, limit: int = 100) -> list[dict[str, object]]:
    """Recover committed jobs whose direct Celery publish was lost or lease expired."""
    session = SessionLocal()
    try:
        results = GraphProjectionService(session, registry.projection_registry).sweep(
            f"celery-sweep:{self.request.id}", limit
        )
        return [
            {
                "job_id": str(result.job_id),
                "status": result.status,
                "attempt": result.attempt,
            }
            for result in results
        ]
    finally:
        session.close()
