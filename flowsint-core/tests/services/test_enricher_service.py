from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

from flowsint_core.core.services.enricher_service import EnricherService


def template_content() -> dict:
    return {
        "name": "approved-address-preview",
        "description": "Approved connector template",
        "category": "Location",
        "version": 1.0,
        "input": {"type": "Location", "key": "address"},
        "connector": {
            "destination_id": "approved_directory",
            "endpoint_id": "lookup",
            "capability": "enrich.read",
        },
        "output": {"type": "Location"},
    }


class Registry:
    def list(self, **_kwargs):
        return [
            {
                "name": "builtin_lookup",
                "category": "Location",
                "description": "Built-in enricher",
            }
        ]


def service_with_templates(*records) -> EnricherService:
    template_repository = MagicMock()
    template_repository.get_by_owner.return_value = list(records)
    return EnricherService(
        db=MagicMock(),
        custom_type_repo=MagicMock(),
        enricher_template_repo=template_repository,
    )


def test_combined_listing_discriminates_builtin_and_template_launch_targets():
    template_id = uuid4()
    service = service_with_templates(
        SimpleNamespace(id=template_id, content=template_content())
    )

    items = service.get_all_enrichers(None, uuid4(), Registry())

    assert items[0]["source"] == "builtin"
    assert items[0]["id"] == "builtin_lookup"
    assert items[1] == {
        "id": str(template_id),
        "source": "template",
        "type": "template",
        "name": "approved-address-preview",
        "description": "Approved connector template",
        "category": "Location",
        "class_name": "TemplateEnricher",
        "module": "flowsint_core.core.template_enricher",
        "documentation": None,
        "inputs": {"type": "Location", "properties": []},
        "outputs": {"type": "Location", "properties": []},
        "required_params": False,
        "params": {},
        "params_schema": [],
        "icon": None,
        "wobblyType": False,
    }


def test_combined_listing_omits_legacy_arbitrary_url_templates():
    legacy = template_content()
    legacy.pop("connector")
    legacy["request"] = {"method": "GET", "url": "https://unapproved.example"}
    legacy["response"] = {"expect": "json", "map": {"address": "address"}}
    service = service_with_templates(SimpleNamespace(id=uuid4(), content=legacy))

    items = service.get_all_enrichers(None, uuid4(), Registry())

    assert [item["source"] for item in items] == ["builtin"]
