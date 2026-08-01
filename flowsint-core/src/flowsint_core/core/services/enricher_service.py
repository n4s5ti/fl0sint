"""
Enricher service for managing enricher operations.
"""

from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy.orm import Session

from ..repositories import CustomTypeRepository, EnricherTemplateRepository
from flowsint_core.templates.types import Template
from .base import BaseService


class EnricherService(BaseService):
    """
    Service for enricher operations and listing.
    """

    def __init__(
        self,
        db: Session,
        custom_type_repo: CustomTypeRepository,
        enricher_template_repo: EnricherTemplateRepository,
        **kwargs,
    ):
        super().__init__(db, **kwargs)
        self._custom_type_repo = custom_type_repo
        self._enricher_template_repo = enricher_template_repo

    def get_enrichers(
        self, category: Optional[str], user_id: UUID, enricher_registry
    ) -> List[Dict[str, Any]]:
        if not category or category.lower() == "undefined":
            return enricher_registry.list(exclude=["n8n_connector"])

        custom_type = self._custom_type_repo.get_published_by_name_and_owner(
            category, user_id
        )

        if custom_type:
            return []
            return enricher_registry.list(exclude=["n8n_connector"], wobbly_type=True)

        return enricher_registry.list_by_input_type(category, exclude=["n8n_connector"])

    def get_all_enrichers(
        self, category: Optional[str], user_id: UUID, enricher_registry
    ) -> list:
        base_enrichers = [
            {
                **enricher,
                "id": enricher.get("id", enricher["name"]),
                "source": "builtin",
                "type": enricher.get("type", "builtin"),
            }
            for enricher in self.get_enrichers(category, user_id, enricher_registry)
        ]
        template_enrichers = []
        for record in self._enricher_template_repo.get_by_owner(user_id, category):
            try:
                template = Template.model_validate(record.content)
            except Exception:
                continue
            template_enrichers.append(
                {
                    "id": str(record.id),
                    "source": "template",
                    "type": "template",
                    "name": template.name,
                    "description": template.description,
                    "category": template.category,
                    "class_name": "TemplateEnricher",
                    "module": "flowsint_core.core.template_enricher",
                    "documentation": None,
                    "inputs": {"type": template.input.type, "properties": []},
                    "outputs": {"type": template.output.type, "properties": []},
                    "required_params": False,
                    "params": {},
                    "params_schema": [],
                    "icon": None,
                    "wobblyType": False,
                }
            )
        return [*base_enrichers, *template_enrichers]


def create_enricher_service(db: Session) -> EnricherService:
    return EnricherService(
        db=db,
        custom_type_repo=CustomTypeRepository(db),
        enricher_template_repo=EnricherTemplateRepository(db),
    )
