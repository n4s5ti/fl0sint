"""API routes for strict registry-backed enricher templates."""

from typing import Any, List
from uuid import UUID

import yaml
from fastapi import APIRouter, Depends, HTTPException, Query, status
from flowsint_core.core.config import destination_registry
from flowsint_core.core.connector_egress import EgressAuthorizer
from flowsint_core.core.models import Profile
from flowsint_core.core.postgre_db import get_db
from flowsint_core.core.services import (
    ConflictError,
    NotFoundError,
    ValidationError,
    create_enricher_template_service,
    create_template_generator_service,
)
from flowsint_core.core.template_enricher import TemplateEnricher
from flowsint_core.core.vault import Vault
from flowsint_core.templates.types import Template
from flowsint_types.registry import get_type as get_type_from_registry, load_all_types
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.api.schemas.enricher_template import (
    ConnectorTestEvidenceMetadata,
    ConnectorTestOutcome,
    EnricherTemplateCreate,
    EnricherTemplateGenerateRequest,
    EnricherTemplateGenerateResponse,
    EnricherTemplateList,
    EnricherTemplateRead,
    EnricherTemplateTestRequest,
    EnricherTemplateTestResponse,
    EnricherTemplateUpdate,
)

router = APIRouter()


def _invalid_connector_template() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail={"code": "invalid_connector_template"},
    )


def _deserialize_template(content: dict[str, Any]) -> Template:
    """Validate stored or supplied content without reflecting rejected data."""
    try:
        template = Template.model_validate(content)
        destination_registry.resolve(
            template.connector, template.input.type, template.output.type
        )
    except Exception as error:
        raise _invalid_connector_template() from error
    return template


def _validate_wrapper_metadata(
    template: Template,
    name: str | None,
    description: str | None,
    category: str | None,
    version: float | None,
) -> None:
    if (
        (name is not None and name != template.name)
        or (description is not None and description != template.description)
        or (category is not None and category != template.category)
        or (version is not None and version != template.version)
    ):
        raise _invalid_connector_template()


def _safe_template_content(content: dict[str, Any]) -> dict[str, Any]:
    try:
        return _deserialize_template(content).model_dump(mode="json")
    except HTTPException:
        return {"status": "invalid_connector_template"}


def _safe_template_read(template) -> dict[str, Any]:
    payload = EnricherTemplateRead.model_validate(template).model_dump(mode="json")
    payload["content"] = _safe_template_content(template.content)
    return payload


@router.post(
    "", response_model=EnricherTemplateRead, status_code=status.HTTP_201_CREATED
)
def create_template(
    template: EnricherTemplateCreate,
    db: Session = Depends(get_db),
    current_user: Profile = Depends(get_current_user),
):
    """Create a strict template whose connector must resolve in deployment policy."""
    strict_template = _deserialize_template(template.content)
    _validate_wrapper_metadata(
        strict_template,
        template.name,
        template.description,
        template.category,
        template.version,
    )
    service = create_enricher_template_service(db)
    try:
        created = service.create_template(
            name=strict_template.name,
            description=strict_template.description,
            category=strict_template.category,
            version=strict_template.version,
            content=strict_template.model_dump(mode="json"),
            is_public=template.is_public,
            owner_id=current_user.id,
        )
    except ConflictError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "enricher_template_conflict"},
        ) from error
    return _safe_template_read(created)


@router.get("", response_model=List[EnricherTemplateList])
def list_templates(
    category: str = Query(None, description="Filter by category"),
    include_public: bool = Query(
        True, description="Include public templates from other users"
    ),
    db: Session = Depends(get_db),
    current_user: Profile = Depends(get_current_user),
):
    """List template metadata without returning connector configuration."""
    service = create_enricher_template_service(db)
    return service.list_templates(current_user.id, category, include_public)


@router.post("/generate", response_model=EnricherTemplateGenerateResponse)
async def generate_template(
    request: EnricherTemplateGenerateRequest,
    db: Session = Depends(get_db),
    current_user: Profile = Depends(get_current_user),
):
    """Generate only strict connector syntax that resolves in the registry."""
    load_all_types()
    input_schema = None
    output_schema = None
    if request.input_type:
        input_cls = get_type_from_registry(request.input_type, case_sensitive=True)
        if input_cls is None:
            raise _invalid_connector_template()
        input_schema = input_cls.model_json_schema()
    if request.output_type:
        output_cls = get_type_from_registry(request.output_type, case_sensitive=True)
        if output_cls is None:
            raise _invalid_connector_template()
        output_schema = output_cls.model_json_schema()
    service = create_template_generator_service(db)
    try:
        generated_yaml = await service.generate(
            prompt=request.prompt,
            owner_id=current_user.id,
            input_type=request.input_type,
            input_schema=input_schema,
            output_type=request.output_type,
            output_schema=output_schema,
        )
        strict_template = _deserialize_template(yaml.safe_load(generated_yaml))
    except Exception as error:
        raise _invalid_connector_template() from error
    return EnricherTemplateGenerateResponse(
        yaml_content=yaml.safe_dump(
            strict_template.model_dump(mode="json"), sort_keys=False
        )
    )


@router.post("/{template_id}/test", response_model=EnricherTemplateTestResponse)
async def test_template(
    template_id: UUID,
    test_request: EnricherTemplateTestRequest,
    db: Session = Depends(get_db),
    current_user: Profile = Depends(get_current_user),
):
    """Return only redacted status and policy provenance for a connector test."""
    service = create_enricher_template_service(db)
    try:
        db_template = service.get_template(template_id, current_user.id)
    except NotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "enricher_template_not_found"},
        ) from error
    template = _deserialize_template(db_template.content)
    enricher = TemplateEnricher(
        template=template,
        registry=destination_registry,
        runtime_authorizer=EgressAuthorizer.enrichment(),
    )
    enricher.vault = Vault(db=db, owner_id=current_user.id)
    result = await enricher.execute_structured([test_request.input_value])
    outcomes = [
        ConnectorTestOutcome(
            status=outcome.status.value,
            visible_outputs=len(outcome.outputs),
            diagnostic=outcome.diagnostic,
            evidence=[
                ConnectorTestEvidenceMetadata(
                    destination_id=evidence.destination_id,
                    endpoint_id=evidence.endpoint_id,
                    capability=evidence.capability,
                    policy_version=evidence.policy_version,
                    artifact_sha256=evidence.artifact_sha256,
                    artifact_reference=evidence.artifact_reference,
                )
                for evidence in outcome.evidence
            ],
        )
        for outcome in result.outcomes
    ]
    return EnricherTemplateTestResponse(
        success=all(outcome.status.value == "success" for outcome in result.outcomes),
        destination_id=enricher.endpoint.destination_id,
        endpoint_id=enricher.endpoint.endpoint_id,
        capability=enricher.endpoint.capability,
        outcomes=outcomes,
    )


@router.get("/{template_id}", response_model=EnricherTemplateRead)
def get_template(
    template_id: UUID,
    db: Session = Depends(get_db),
    current_user: Profile = Depends(get_current_user),
):
    """Return only strict content from a stored template."""
    service = create_enricher_template_service(db)
    try:
        return _safe_template_read(service.get_template(template_id, current_user.id))
    except NotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "enricher_template_not_found"},
        ) from error


@router.put("/{template_id}", response_model=EnricherTemplateRead)
def update_template(
    template_id: UUID,
    update_data: EnricherTemplateUpdate,
    db: Session = Depends(get_db),
    current_user: Profile = Depends(get_current_user),
):
    """Update a template without accepting arbitrary HTTP grammar."""
    update_fields = update_data.model_dump(exclude_unset=True)
    service = create_enricher_template_service(db)
    try:
        existing = service.get_owned_template(template_id, current_user.id)
    except NotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "enricher_template_not_found"},
        ) from error

    metadata_fields = ("name", "description", "category", "version")
    if "content" in update_fields:
        strict_template = _deserialize_template(update_fields["content"])
        if any(
            field in update_fields
            and update_fields[field] != getattr(strict_template, field)
            for field in metadata_fields
        ):
            raise _invalid_connector_template()
    else:
        strict_template = _deserialize_template(existing.content)
        metadata_updates = {
            field: update_fields[field]
            for field in metadata_fields
            if field in update_fields
        }
        if metadata_updates:
            strict_template = _deserialize_template(
                strict_template.model_copy(update=metadata_updates).model_dump(mode="json")
            )

    if "content" in update_fields or any(
        field in update_fields for field in metadata_fields
    ):
        update_fields["content"] = strict_template.model_dump(mode="json")
        for field in metadata_fields:
            update_fields[field] = getattr(strict_template, field)

    try:
        updated = service.update_template(
            template_id=template_id,
            owner_id=current_user.id,
            update_data=update_fields,
        )
    except NotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "enricher_template_not_found"},
        ) from error
    except ConflictError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "enricher_template_conflict"},
        ) from error
    return _safe_template_read(updated)


@router.delete("/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_template(
    template_id: UUID,
    db: Session = Depends(get_db),
    current_user: Profile = Depends(get_current_user),
):
    """Delete an enricher template. Only the owner can delete."""
    service = create_enricher_template_service(db)
    try:
        service.delete_template(template_id, current_user.id)
    except NotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "enricher_template_not_found"},
        ) from error
    return None
