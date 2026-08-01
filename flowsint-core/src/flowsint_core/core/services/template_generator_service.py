"""
Service for AI-assisted enricher template generation.

Uses an LLM to generate valid enricher template YAML from a free-text prompt.
"""

import json
import os
import re
from typing import Any, Dict, Optional
from uuid import UUID

import yaml
from sqlalchemy.orm import Session

from ..llm import ChatMessage, MessageRole, create_llm_provider
from .base import BaseService
from .exceptions import ValidationError
from .vault_service import VaultService
from flowsint_core.templates.types import Template


_SYSTEM_PROMPT = """\
You are a YAML template generator for Flowsint enrichers. Given a user's description, \
generate a valid registry-backed enricher template in YAML format.

## Security Boundary

Templates select deployment-approved connector identifiers. They never define network \
destinations, HTTP methods, paths, headers, query parameters, request bodies, response \
mappings, or secret placement. Never emit a `request` or `response` section and never \
emit a URL. Inventing an identifier is not authority: runtime rejects identifiers that \
are absent from the deployment's destination registry.

## Template Schema

### Required fields:
- `name` (str): Unique lowercase, hyphenated template name
- `category` (str): Category matching the input type
- `version` (float): Template version, starting at 1.0
- `input`:
  - `type` (str): Flowsint input type
  - `key` (str, default `nodeLabel`): Input field used by the approved endpoint
- `connector`:
  - `destination_id` (str): Deployment-approved destination identifier
  - `endpoint_id` (str): Approved endpoint identifier under that destination
  - `capability`: Must be exactly `enrich.read`
- `output`:
  - `type` (str): Flowsint output type configured for the approved endpoint

### Optional fields:
- `description` (str): Human-readable description
- `execution_mode`: Must remain `preview`
- `evidence`: Bounded provenance metadata (`source_rights`, `schema_version`, \
  `parser_version`, `confidence`, and `verification_state`)
- `projection`: Reference to a system-approved graph projection profile
  - `profile_id` (str): Approved profile identifier
  - `revision` (int): Approved profile revision

## Examples

### Example 1: Approved IP lookup
```yaml
name: ip-directory-lookup
category: Ip
version: 1.0
input:
  type: Ip
  key: address
connector:
  destination_id: approved_ip_directory
  endpoint_id: lookup
  capability: enrich.read
output:
  type: Ip
```

### Example 2: Approved lookup with graph projection
```yaml
name: projected-ip-lookup
category: Ip
version: 1.0
input:
  type: Ip
  key: address
connector:
  destination_id: approved_commercial_ip
  endpoint_id: lookup
  capability: enrich.read
output:
  type: Ip
projection:
  profile_id: ip_observations
  revision: 1
```

## Instructions

- Output only the YAML template: no explanations or markdown fences.
- Infer category and input/output types from the user's description.
- Select only connector identifiers supplied by the user or deployment context; do not \
  invent destination authority.
- Capability must be exactly `enrich.read`. Never emit outreach or transaction authority.
- Never emit URLs, methods, paths, headers, parameters, bodies, response mappings, or \
  secret values/placement.
"""


def _extract_yaml(text: str) -> str:
    """Extract YAML content from LLM response, stripping markdown fences if present."""
    # Try to extract from markdown code fences
    match = re.search(r"```(?:ya?ml)?\s*\n(.*?)```", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return text.strip()




class TemplateGeneratorService(BaseService):
    """Generates enricher template YAML from a free-text prompt using an LLM."""

    def __init__(self, db: Session, vault_service: VaultService):
        super().__init__(db=db)
        self._vault_service = vault_service

    def _get_llm_provider(self, owner_id: UUID):
        provider_name = os.environ.get("LLM_PROVIDER", "mistral")
        vault_key = f"{provider_name.upper()}_API_KEY"
        api_key = self._vault_service.get_secret(owner_id, vault_key)
        return create_llm_provider(provider=provider_name, api_key=api_key)

    def _build_type_context(
        self,
        input_type: Optional[str],
        input_schema: Optional[Dict[str, Any]],
        output_type: Optional[str],
        output_schema: Optional[Dict[str, Any]],
    ) -> str:
        """Build additional context about input/output type schemas."""
        parts: list[str] = []
        if input_type and input_schema:
            parts.append(
                f"## Input type: {input_type}\n"
                f"The template MUST use `input.type: {input_type}`.\n"
                f"Schema (available fields on the input):\n"
                f"```json\n{json.dumps(input_schema, indent=2)}\n```"
            )
        if output_type and output_schema:
            parts.append(
                f"## Output type: {output_type}\n"
                f"The template MUST use `output.type: {output_type}`.\n"
                "Select only an approved connector endpoint configured to return this type.\n"
                f"Schema (fields produced by that endpoint):\n"
                f"```json\n{json.dumps(output_schema, indent=2)}\n```"
            )
        return "\n\n".join(parts)

    async def generate(
        self,
        prompt: str,
        owner_id: UUID,
        input_type: Optional[str] = None,
        input_schema: Optional[Dict[str, Any]] = None,
        output_type: Optional[str] = None,
        output_schema: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Generate an enricher template YAML from a free-text description.

        Args:
            prompt: User's free-text description of the desired enricher.
            owner_id: ID of the user (for vault-based LLM API key).
            input_type: Name of the input Flowsint type (e.g. "Ip").
            input_schema: JSON schema of the input type.
            output_type: Name of the output Flowsint type (e.g. "SocialAccount").
            output_schema: JSON schema of the output type.

        Returns:
            Raw YAML string of the generated template.

        Raises:
            ValidationError: If the LLM output is not valid YAML or doesn't
                match the Template schema.
        """
        provider = self._get_llm_provider(owner_id)

        system_content = _SYSTEM_PROMPT
        type_context = self._build_type_context(
            input_type, input_schema, output_type, output_schema
        )
        if type_context:
            system_content += (
                "\n\n## Type Constraints (from the user's selection)\n\n"
                + type_context
                + "\n\nThe selected approved connector endpoint must match both types. "
                "Never add template-authored transport or response-mapping fields."
            )

        messages = [
            ChatMessage(role=MessageRole.SYSTEM, content=system_content),
            ChatMessage(role=MessageRole.USER, content=prompt),
        ]

        response = await provider.complete(messages)
        yaml_str = _extract_yaml(response)

        # Validate the YAML
        try:
            parsed = yaml.safe_load(yaml_str)
        except yaml.YAMLError as e:
            raise ValidationError(f"LLM produced invalid YAML: {e}")

        if not isinstance(parsed, dict):
            raise ValidationError("LLM produced non-object YAML output")

        try:
            Template(**parsed)
        except Exception as e:
            raise ValidationError(f"Generated template failed validation: {e}")

        return yaml_str


def create_template_generator_service(db: Session) -> TemplateGeneratorService:
    return TemplateGeneratorService(db=db, vault_service=VaultService(db=db))
