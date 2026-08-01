"""Pydantic schemas for strict registry-backed enricher templates."""

from datetime import datetime
from typing import Any, Dict, Literal, Optional

from flowsint_core.core.execution import RedactedDiagnostic
from flowsint_core.templates.types import Template
from pydantic import UUID4, BaseModel, Field

from .base import ORMBase


class EnricherTemplateCreate(BaseModel):
    """Create a template whose executable fields are validated strict content."""

    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = Field(None, max_length=1000)
    category: str = Field(..., min_length=1, max_length=100)
    version: float = Field(default=1.0, ge=0)
    content: Dict[str, Any]
    is_public: bool = False


class EnricherTemplateUpdate(BaseModel):
    """Update a template without reintroducing arbitrary HTTP fields."""

    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = Field(None, max_length=1000)
    category: Optional[str] = Field(None, min_length=1, max_length=100)
    version: Optional[float] = Field(None, ge=0)
    content: Optional[Dict[str, Any]] = None
    is_public: Optional[bool] = None


class EnricherTemplateRead(ORMBase):
    id: UUID4
    name: str
    description: Optional[str]
    category: str
    version: float
    content: Dict[str, Any]
    is_public: bool
    owner_id: UUID4
    created_at: datetime
    updated_at: datetime


class EnricherTemplateList(ORMBase):
    id: UUID4
    name: str
    description: Optional[str]
    category: str
    version: float
    is_public: bool
    owner_id: UUID4
    created_at: datetime
    updated_at: datetime


class EnricherTemplateTestRequest(BaseModel):
    input_value: str = Field(..., min_length=1)


class ConnectorTestEvidenceMetadata(BaseModel):
    destination_id: str
    endpoint_id: str
    capability: Literal["enrich.read"]
    policy_version: str
    artifact_sha256: str | None = None
    artifact_reference: str | None = None


class ConnectorTestOutcome(BaseModel):
    status: Literal["success", "failure", "hold"]
    visible_outputs: int = Field(ge=0)
    diagnostic: RedactedDiagnostic | None = None
    evidence: list[ConnectorTestEvidenceMetadata] = Field(default_factory=list)


class EnricherTemplateTestResponse(BaseModel):
    """Safe test output: identifiers, outcome statuses, and no egress material."""

    success: bool
    destination_id: str
    endpoint_id: str
    capability: Literal["enrich.read"]
    outcomes: list[ConnectorTestOutcome]


class EnricherTemplateGenerateRequest(BaseModel):
    prompt: str = Field(..., min_length=10, max_length=16000)
    input_type: Optional[str] = None
    output_type: Optional[str] = None


class EnricherTemplateGenerateResponse(BaseModel):
    yaml_content: str
