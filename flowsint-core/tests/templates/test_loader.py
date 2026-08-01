"""Tests for strict connector-template YAML parsing."""

from pathlib import Path

import pytest

from flowsint_core.templates.loader.yaml_loader import YamlLoader
from flowsint_core.templates.types import Template

TEST_DIR = Path(__file__).parent


def connector_template() -> dict:
    return {
        "name": "approved-connector",
        "description": "test",
        "category": "Location",
        "version": 1.0,
        "input": {"type": "Location", "key": "address"},
        "connector": {
            "destination_id": "approved_directory",
            "endpoint_id": "lookup",
            "capability": "enrich.read",
        },
        "output": {"type": "Location"},
        "evidence": {"source_rights": "test-only"},
    }


class TestYamlLoader:
    def test_yaml_loader_parses_strict_connector_template(self):
        parsed = YamlLoader.parse_yaml_to_template(
            connector_template(), type_resolver=lambda _name: object
        )

        assert isinstance(parsed, Template)
        assert parsed.connector.destination_id == "approved_directory"
        assert parsed.connector.endpoint_id == "lookup"
        assert parsed.connector.capability == "enrich.read"

    def test_yaml_loader_rejects_arbitrary_http_grammar(self):
        raw = connector_template()
        raw["request"] = {
            "method": "GET",
            "url": "https://unapproved.example/{{address}}",
        }

        with pytest.raises(ValueError, match="connector configuration"):
            YamlLoader.parse_yaml_to_template(raw, type_resolver=lambda _name: object)

    def test_yaml_loader_rejects_unregistered_input_or_output_types(self):
        with pytest.raises(ValueError, match="input type"):
            YamlLoader.parse_yaml_to_template(
                connector_template(), type_resolver=lambda _name: None
            )

    def test_wholesaler_fixture_is_registry_selection_only(self):
        parsed = YamlLoader.get_template_from_file(
            str(TEST_DIR / "wholesaler-address-preview.yaml"),
            type_resolver=lambda _name: object,
        )

        assert parsed is not None
        assert parsed.connector.destination_id == "census_geocoder"
        assert "request" not in parsed.model_dump(mode="json")
        assert "response" not in parsed.model_dump(mode="json")
