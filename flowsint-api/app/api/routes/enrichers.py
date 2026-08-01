from typing import List, Optional
from uuid import UUID


from fastapi import APIRouter, Depends, HTTPException, Query
from flowsint_core.core.celery import celery
from flowsint_core.core.config import destination_registry
from flowsint_core.core.connector_egress import connector_template_digest
from flowsint_core.core.graph import create_graph_service
from flowsint_core.core.models import Profile
from flowsint_core.core.postgre_db import get_db
from flowsint_core.core.services import (
    NotFoundError,
    PermissionDeniedError,
    create_enricher_service,
    create_enricher_template_service,
    create_flow_service,
)
from flowsint_core.core.services.type_registry_service import create_type_registry_service
from flowsint_core.templates.types import Template
from flowsint_enrichers import ENRICHER_REGISTRY, load_all_enrichers
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user

router = APIRouter()
load_all_enrichers()


class launchEnricherPayload(BaseModel):
    node_ids: List[str]
    sketch_id: str

class launchTemplatePayload(BaseModel):
    node_ids: List[str]
    sketch_id: str
    idempotency_key: Optional[str] = None


@router.post("/templates/{template_id}/launch")
async def launch_connector_template(
    template_id: UUID,
    payload: launchTemplatePayload,
    current_user: Profile = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Launch a strict connector template by immutable ID, never display name."""
    try:
        try:
            create_flow_service(db).get_sketch_for_launch(
                payload.sketch_id, current_user.id
            )
        except PermissionDeniedError as error:
            raise HTTPException(
                status_code=403, detail={"code": "sketch_launch_forbidden"}
            ) from error
        except NotFoundError as error:
            raise HTTPException(
                status_code=404, detail={"code": "sketch_not_found"}
            ) from error
        template_service = create_enricher_template_service(db)
        record = template_service.get_template(template_id, current_user.id)
        template = Template.model_validate(record.content)
        destination_registry.resolve(
            template.connector, template.input.type, template.output.type
        )
        type_registry = create_type_registry_service(db)
        resolver = type_registry.build_type_resolver(current_user.id)
        graph_service = create_graph_service(
            sketch_id=payload.sketch_id, type_resolver=resolver
        )
        entities = [
            entity.model_dump(mode="json", serialize_as_any=True)
            for entity in graph_service.get_nodes_by_ids_for_task(payload.node_ids)
        ]
        if not entities:
            raise HTTPException(
                status_code=404, detail={"code": "enricher_entities_not_found"}
            )
        task = celery.send_task(
            "run_connector_template",
            args=[
                str(record.id),
                connector_template_digest(template),
                entities,
                payload.sketch_id,
                str(current_user.id),
                payload.idempotency_key,
            ],
        )
        return {"id": task.id}
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_connector_template"},
        ) from error




@router.get("")
def get_enrichers(
    category: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: Profile = Depends(get_current_user),
):
    """Get all enrichers, optionally filtered by category."""
    enricher_service = create_enricher_service(db)
    return enricher_service.get_all_enrichers(
        category, current_user.id, ENRICHER_REGISTRY
    )


@router.post("/{enricher_name}/launch")
async def launch_enricher(
    enricher_name: str,
    payload: launchEnricherPayload,
    current_user: Profile = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        # Retrieve nodes from Neo4J by their element IDs
        type_registry = create_type_registry_service(db)
        resolver = type_registry.build_type_resolver(current_user.id)
        graph_service = create_graph_service(sketch_id=payload.sketch_id, type_resolver=resolver)
        entities = graph_service.get_nodes_by_ids_for_task(payload.node_ids)

        # Send deserialized nodes
        entities = [
            entity.model_dump(mode="json", serialize_as_any=True) for entity in entities
        ]
        if not entities:
            raise HTTPException(
                status_code=404, detail="No entities found with provided IDs"
            )

        if not ENRICHER_REGISTRY.enricher_exists(enricher_name):
            raise HTTPException(
                status_code=404, detail={"code": "enricher_not_found"}
            )
        task = celery.send_task(
            "run_enricher",
            args=[
                enricher_name,
                entities,
                payload.sketch_id,
                str(current_user.id),
            ],
        )
        return {"id": task.id}

    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(
            status_code=500, detail={"code": "enricher_launch_failed"}
        ) from error
