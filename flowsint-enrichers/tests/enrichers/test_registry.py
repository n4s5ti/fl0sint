import importlib

import pytest
from flowsint_enrichers import ENRICHER_REGISTRY


def test_enricher_registry_enricher_found():
    enricher = ENRICHER_REGISTRY.get_enricher(
        "domain_to_ip", "123", "123", graph_service=object()
    )
    assert enricher.name() == "domain_to_ip"


def test_enricher_registry_enricher_not_found():
    with pytest.raises(Exception) as error:
        ENRICHER_REGISTRY.get_enricher("enricher_does_not_exist", "123", "123")
    assert "not found" in str(error.value)


def test_registry_contains_migrated_hetzner_enrichers():
    """Assert every migrated Hetzner enricher imports and is discoverable."""
    migrated_modules = [
        "flowsint_enrichers.organization.fire_enrich_company",
        "flowsint_enrichers.organization.fire_enrich_funding",
        "flowsint_enrichers.organization.fire_enrich_leadership",
        "flowsint_enrichers.organization.fire_enrich_metrics",
        "flowsint_enrichers.organization.fire_enrich_profile",
        "flowsint_enrichers.organization.fire_enrich_tech",
        "flowsint_enrichers.organization.to_fire_enrich_company_socials",
        "flowsint_enrichers.organization.to_fire_enrich_contact_emails",
        "flowsint_enrichers.organization.to_fire_enrich_contact_individuals",
        "flowsint_enrichers.organization.to_fire_enrich_contact_people",
        "flowsint_enrichers.phrase.to_contact_emails",
        "flowsint_enrichers.phrase.to_contact_individuals",
        "flowsint_enrichers.phrase.to_contact_phones",
        "flowsint_enrichers.social.to_blackbird",
        "flowsint_enrichers.social.to_osintgram",
        "flowsint_enrichers.website.to_fire_enrich_company",
    ]
    for module in migrated_modules:
        importlib.import_module(module)

    expected_names = [
        "organization_to_fire_enrich_funding",
        "organization_to_fire_enrich_leadership",
        "organization_to_fire_enrich_metrics",
        "organization_to_fire_enrich_profile",
        "organization_to_fire_enrich_tech",
        "organization_to_fire_enrich_company_socials",
        "organization_to_fire_enrich_contact_emails",
        "organization_to_fire_enrich_contact_individuals",
        "organization_to_fire_enrich_contact_people",
        "phrase_to_contact_emails",
        "phrase_to_contact_individuals",
        "phrase_to_contact_phones",
        "username_to_socials_blackbird",
        "username_to_instagram_osintgram",
        "website_to_fire_enrich_company",
    ]
    missing = [
        name for name in expected_names if not ENRICHER_REGISTRY.enricher_exists(name)
    ]
    assert not missing, f"Missing enrichers in registry: {missing}"
