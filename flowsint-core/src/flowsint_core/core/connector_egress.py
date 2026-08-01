"""Deployment-owned connector egress policy.

Templates may name a connector endpoint, but only this module may define where data
leaves the process or how a request is shaped.
"""

from __future__ import annotations

import hashlib
import json

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from urllib.parse import urlsplit

import yaml
from flowsint_types import get_type
from flowsint_types.registry import load_all_types
from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator, model_validator

if TYPE_CHECKING:
    from flowsint_core.templates.types import TemplateConnector

ConnectorCapability = Literal["enrich.read", "outreach.send", "transaction.write"]
RequestLocation = Literal["path", "query", "json"]
RequestValueType = Literal["string", "integer", "number", "boolean"]
_HEADER_NAME = re.compile(r"^[A-Za-z0-9-]+$")
_IDENTIFIER = r"^[a-z][a-z0-9_-]{0,127}$"


class ConnectorPolicyError(Exception):
    """A fail-closed connector policy rejection with no request material."""


class ConnectorValidationError(Exception):
    """A bounded validation failure without remote response material."""


class ConnectorCredentialUnavailable(Exception):
    """A registry-declared credential was unavailable."""


class ConnectorResponseTooLarge(Exception):
    """The remote response exceeded the configured bound."""


class ConnectorRedirectDenied(Exception):
    """A configured connector returned a redirect response."""


class ConnectorRequestField(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: StrictStr = Field(pattern=_IDENTIFIER)
    source_field: StrictStr = Field(pattern=_IDENTIFIER)
    location: RequestLocation
    value_type: RequestValueType
    required: bool = True
    max_length: int = Field(default=512, ge=1, le=16_384)


class ConnectorEndpointDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    endpoint_id: StrictStr = Field(pattern=_IDENTIFIER)
    capability: ConnectorCapability
    method: Literal["GET", "POST"]
    path: StrictStr = Field(min_length=1, max_length=1024, pattern=r"^/")
    input_type: StrictStr = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,127}$")
    output_type: StrictStr = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,127}$")
    fixed_query: dict[StrictStr, StrictStr] = Field(default_factory=dict)
    fixed_headers: dict[StrictStr, StrictStr] = Field(default_factory=dict)
    secret_headers: dict[StrictStr, StrictStr] = Field(default_factory=dict)
    request_fields: tuple[ConnectorRequestField, ...] = ()
    response_mappings: dict[StrictStr, StrictStr] = Field(default_factory=dict)
    response_array_path: StrictStr | None = Field(default=None, max_length=1024)
    timeout_seconds: float = Field(default=15.0, ge=0.1, le=60.0)
    max_response_bytes: int = Field(default=1_048_576, ge=1, le=8_388_608)
    enabled: bool = True

    @field_validator("fixed_headers", "secret_headers")
    @classmethod
    def validate_header_names(cls, value: dict[str, str]) -> dict[str, str]:
        if any(not _HEADER_NAME.fullmatch(name) for name in value):
            raise ValueError("invalid registry header name")
        return value

    @field_validator("fixed_headers", "fixed_query")
    @classmethod
    def reject_template_grammar(cls, value: dict[str, str]) -> dict[str, str]:
        if any("{{" in item or "}}" in item for item in value.values()):
            raise ValueError("registry fixed values cannot contain template grammar")
        return value

    @model_validator(mode="after")
    def validate_shape(self) -> ConnectorEndpointDefinition:
        names = [field.name for field in self.request_fields]
        if len(names) != len(set(names)):
            raise ValueError("duplicate registry request field")
        if set(self.fixed_headers) & set(self.secret_headers):
            raise ValueError("secret and fixed headers overlap")
        if self.method == "GET" and any(
            field.location == "json" for field in self.request_fields
        ):
            raise ValueError("GET connector endpoint cannot have JSON request fields")
        path_fields = set(re.findall(r"{([a-z][a-z0-9_-]{0,127})}", self.path))
        configured_path_fields = {
            field.name for field in self.request_fields if field.location == "path"
        }
        if path_fields != configured_path_fields:
            raise ValueError("registry path fields do not match path placeholders")
        return self


class DestinationDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    destination_id: StrictStr = Field(pattern=_IDENTIFIER)
    base_url: StrictStr = Field(min_length=1, max_length=2048)
    endpoints: tuple[ConnectorEndpointDefinition, ...]
    enabled: bool = True

    @field_validator("base_url")
    @classmethod
    def validate_https_origin(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("destination base_url must be an HTTPS origin")
        return value.rstrip("/")

    @model_validator(mode="after")
    def validate_endpoint_ids(self) -> DestinationDefinition:
        endpoint_ids = [endpoint.endpoint_id for endpoint in self.endpoints]
        if len(endpoint_ids) != len(set(endpoint_ids)):
            raise ValueError("duplicate destination endpoint")
        return self


class DestinationRegistryDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1]
    destinations: tuple[DestinationDefinition, ...]

    @model_validator(mode="after")
    def validate_destination_ids(self) -> DestinationRegistryDocument:
        destination_ids = [destination.destination_id for destination in self.destinations]
        if len(destination_ids) != len(set(destination_ids)):
            raise ValueError("duplicate destination")
        return self


@dataclass(frozen=True)
class ResolvedEndpoint:
    """A fully validated endpoint policy; this object never contains a secret value."""

    destination_id: str
    endpoint_id: str
    capability: ConnectorCapability
    policy_version: str
    base_url: str
    definition: ConnectorEndpointDefinition


@dataclass(frozen=True)
class EgressAuthorizer:
    """Runtime authority, intentionally independent of a template declaration."""

    allowed_capabilities: frozenset[ConnectorCapability] = frozenset()

    @classmethod
    def enrichment(cls) -> EgressAuthorizer:
        return cls(allowed_capabilities=frozenset({"enrich.read"}))

    def require(self, capability: ConnectorCapability) -> None:
        if capability not in self.allowed_capabilities:
            raise ConnectorPolicyError("connector runtime authority denied")


class DestinationRegistry:
    """Immutable deployment policy for template connector egress."""

    def __init__(self, document: DestinationRegistryDocument):
        self._document = document
        self._destinations = {
            destination.destination_id: destination
            for destination in document.destinations
        }

    @classmethod
    def empty(cls) -> DestinationRegistry:
        return cls(DestinationRegistryDocument(version=1, destinations=()))

    @property
    def policy_version(self) -> str:
        return str(self._document.version)

    def resolve(
        self,
        connector: TemplateConnector,
        input_type: str,
        output_type: str,
    ) -> ResolvedEndpoint:
        """Resolve and validate a template reference before any Vault or HTTP work."""
        destination = self._destinations.get(connector.destination_id)
        if destination is None or not destination.enabled:
            raise ConnectorPolicyError("connector destination denied")
        endpoint = next(
            (
                candidate
                for candidate in destination.endpoints
                if candidate.endpoint_id == connector.endpoint_id
            ),
            None,
        )
        if endpoint is None or not endpoint.enabled:
            raise ConnectorPolicyError("connector endpoint denied")
        if connector.capability != "enrich.read" or endpoint.capability != connector.capability:
            raise ConnectorPolicyError("connector capability denied")
        if endpoint.input_type != input_type or endpoint.output_type != output_type:
            raise ConnectorPolicyError("connector type denied")

        load_all_types()
        input_model = get_type(input_type, case_sensitive=True)
        output_model = get_type(output_type, case_sensitive=True)
        if input_model is None or output_model is None:
            raise ConnectorPolicyError("connector type denied")
        input_fields = input_model.model_fields
        output_fields = output_model.model_fields
        required_output_fields = {
            field_name
            for field_name, field in output_fields.items()
            if field.is_required()
        }
        if not required_output_fields.issubset(endpoint.response_mappings):
            raise ConnectorPolicyError("connector response mapping denied")
        if any(field.source_field not in input_fields for field in endpoint.request_fields):
            raise ConnectorPolicyError("connector request field denied")
        if any(field_name not in output_fields for field_name in endpoint.response_mappings):
            raise ConnectorPolicyError("connector response field denied")
        if not endpoint.response_mappings:
            raise ConnectorPolicyError("connector response mapping denied")
        return ResolvedEndpoint(
            destination_id=destination.destination_id,
            endpoint_id=endpoint.endpoint_id,
            capability=endpoint.capability,
            policy_version=self.policy_version,
            base_url=destination.base_url,
            definition=endpoint,
        )


def load_destination_registry(path: Path | None) -> DestinationRegistry:
    """Load strict deployment configuration; no path means an empty fail-closed policy."""
    if path is None:
        return DestinationRegistry.empty()
    try:
        raw_document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise RuntimeError("connector destination registry could not be loaded") from error
    try:
        document = DestinationRegistryDocument.model_validate(raw_document)
    except Exception as error:
        raise RuntimeError("connector destination registry is invalid") from error
    return DestinationRegistry(document)

def connector_template_digest(template: object) -> str:
    """Hash strict template content for worker replay without retaining it."""
    content = template.model_dump(mode="json")
    serialized = json.dumps(
        content, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
