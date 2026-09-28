"""Strict YAML loader for deployment-owned connector templates."""

from typing import Any, Optional

import yaml

from flowsint_core.core.graph.serializer import TypeResolver
from flowsint_core.templates.types import Template


class YamlLoader:
    @staticmethod
    def load_enricher_yaml(filename: str) -> dict[str, Any] | yaml.YAMLError:
        with open(filename, encoding="utf-8") as stream:
            try:
                return yaml.safe_load(stream)
            except yaml.YAMLError as exc:
                return exc

    @staticmethod
    def parse_yaml_to_template(
        raw: dict[str, Any],
        type_resolver: Optional[TypeResolver] = None,
    ) -> Template:
        """Parse only strict connector fields; URL/header/body grammar is rejected."""
        if not isinstance(raw, dict):
            raise ValueError("Template must be a YAML dictionary")
        try:
            template = Template.model_validate(raw)
        except Exception as error:
            raise ValueError("Template connector configuration is invalid") from error
        if type_resolver is None:
            from flowsint_core.core.services.type_registry_service import (
                local_type_resolver,
            )

            type_resolver = local_type_resolver
        if type_resolver(template.input.type) is None:
            raise ValueError("Template input type is not registered")
        if type_resolver(template.output.type) is None:
            raise ValueError("Template output type is not registered")
        return template

    @staticmethod
    def get_template_from_file(
        filename: str,
        type_resolver: Optional[TypeResolver] = None,
    ) -> Template | None:
        template_dict = YamlLoader.load_enricher_yaml(filename)
        if not isinstance(template_dict, dict):
            return None
        return YamlLoader.parse_yaml_to_template(
            template_dict, type_resolver=type_resolver
        )
