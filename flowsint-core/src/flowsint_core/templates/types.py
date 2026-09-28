from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field



class TemplateInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: str = Field(..., description="Flowsint Type the template takes as input")
    key: str = Field(
        default="nodeLabel",
        description="Key attribute to extract from input type for template variables",
    )


class TemplateOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: str = Field(
        ..., description="Flowsint Type that the template should return as an output."
    )


class TemplateConnector(BaseModel):
    """A template reference to deployment-owned connector policy."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    destination_id: str = Field(
        min_length=1, max_length=128, pattern=r"^[a-z][a-z0-9_-]{0,127}$"
    )
    endpoint_id: str = Field(
        min_length=1, max_length=128, pattern=r"^[a-z][a-z0-9_-]{0,127}$"
    )
    capability: Literal["enrich.read"] = "enrich.read"



class TemplateGraphProjectionRef(BaseModel):
    """Optional reference to a system-owned graph projection profile."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    profile_id: str = Field(
        min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]{0,63}$"
    )
    revision: int = Field(ge=1)

class TemplateEvidenceConfig(BaseModel):
    """Retainable provenance metadata for structured template execution."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_rights: str = Field(
        default="unspecified",
        min_length=1,
        max_length=128,
        description="Rights basis for retaining outputs from this source",
    )
    schema_version: str = Field(
        default="1",
        min_length=1,
        max_length=64,
        description="Schema version used to interpret retained evidence",
    )
    parser_version: str = Field(
        default="1",
        min_length=1,
        max_length=64,
        description="Parser version used to produce retained evidence",
    )
    confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Confidence in the extracted evidence",
    )
    verification_state: str = Field(
        default="unverified",
        min_length=1,
        max_length=64,
        description="Verification state of the source evidence",
    )


class Template(BaseModel):
    """Strict connector template with no executable egress grammar."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(..., description="Name of the template")
    description: Optional[str] = Field(None, description="Description of the template")
    category: str = Field(..., description="Category of the template")
    version: float = Field(..., description="Version of the template")
    execution_mode: Literal["preview"] = Field(
        default="preview",
        description="Connector templates map policy-approved responses without graph writes.",
    )
    input: TemplateInput = Field(...)
    connector: TemplateConnector = Field(
        ..., description="Deployment-owned destination and endpoint selection."
    )
    output: TemplateOutput = Field(...)
    evidence: TemplateEvidenceConfig = Field(default_factory=TemplateEvidenceConfig)
    projection: TemplateGraphProjectionRef | None = Field(
        default=None,
        description="Optional system-approved graph projection profile reference.",
    )
