"""B6/OBS-1967 -- Adapter-record / projection-schema contract boundary.

Separates what an adapter observed (transport shape: pointers and payload
types) from what an approved projection asserts (bounded forensic semantics:
kinds, observation fields, relationship meanings). Either side revises
independently; an approved mapping declaration pins exact revisions of both
and states explicitly how unsupported and ambiguous adapter data is handled.

Legacy OBS-1788 profiles decompose deterministically into the three B6 parts
and recompose to their original snapshot and digest; the migration is
additive and never rewrites a legacy identity.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal

from flowsint_core.core.projection.contracts import (
    ApprovedProjectionProfile,
    Cardinality,
    EntityRule,
    ObservationRule,
    ProjectionKind,
    RelationshipRule,
    digest_snapshot,
)


ADAPTER_BOUNDARY_CONTRACT_VERSION = "v1"
LEGACY_PROFILE_NAMESPACE = "obs1788"

_SCHEMA_ID = re.compile(r"^[a-z][a-z0-9_]{0,127}$")

_SCALAR_VALUE_TYPES = ("string", "integer", "number", "boolean")


# ── Errors ────────────────────────────────────────────────────────────────────


class BoundaryContractError(ValueError):
    """A boundary violation with a stable diagnostic code and safe details."""

    def __init__(self, code: str, safe_message: str, details: tuple[str, ...] = ()) -> None:
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message
        self.details = details


class AdapterCompatibilityError(BoundaryContractError):
    """A non-additive adapter-record schema revision."""


class MappingValidationError(BoundaryContractError):
    """A mapping declaration inconsistent with its pinned schemas."""


class LegacyPreservationError(BoundaryContractError):
    """A legacy OBS-1788 profile that no longer round-trips exactly."""


# ── Adapter side: transport shape only ───────────────────────────────────────


class AdapterFieldType(StrEnum):
    """Payload types an adapter declares; transport shape, never semantics."""

    STRING = "string"
    INTEGER = "integer"
    NUMBER = "number"
    BOOLEAN = "boolean"
    OBJECT = "object"
    ARRAY = "array"
    ANY = "any"


@dataclass(frozen=True)
class AdapterFieldSpec:
    """One retained payload location an adapter promises to deliver."""

    pointer: str
    field_type: AdapterFieldType
    required: bool = False

    def __post_init__(self) -> None:
        _require_pointer(self.pointer)


@dataclass(frozen=True)
class AdapterRecordSchema:
    """Versioned description of what one adapter revision observed.

    Describes retained evidence shape only. Revisions are additive: a later
    revision keeps every prior pointer at the same type and may only relax
    ``required``. Nothing here names projection semantics.
    """

    schema_id: str
    revision: int
    source_id: str
    fields: tuple[AdapterFieldSpec, ...]
    supersedes_revision: int | None = None

    def __post_init__(self) -> None:
        _require_schema_id(self.schema_id, "adapter schema_id")
        _require_revision(self.revision, "adapter schema revision")
        _require_source_id(self.source_id)
        pointers = [spec.pointer for spec in self.fields]
        if len(set(pointers)) != len(pointers):
            raise BoundaryContractError(
                "adapter_schema_duplicate_pointer",
                "Adapter schema pointers must be unique",
            )
        if self.revision == 1 and self.supersedes_revision is not None:
            raise BoundaryContractError(
                "adapter_schema_bad_supersedes",
                "Revision 1 must not supersede another revision",
            )
        if self.revision > 1 and self.supersedes_revision != self.revision - 1:
            raise BoundaryContractError(
                "adapter_schema_bad_supersedes",
                "Revisions above 1 must supersede exactly the prior revision",
            )

    def field_by_pointer(self, pointer: str) -> AdapterFieldSpec | None:
        for spec in self.fields:
            if spec.pointer == pointer:
                return spec
        return None

    def snapshot(self) -> dict[str, Any]:
        return {
            "boundary_contract": ADAPTER_BOUNDARY_CONTRACT_VERSION,
            "record": "adapter_record_schema",
            "schema_id": self.schema_id,
            "revision": self.revision,
            "source_id": self.source_id,
            "supersedes_revision": self.supersedes_revision,
            "fields": [
                {
                    "pointer": spec.pointer,
                    "field_type": spec.field_type.value,
                    "required": spec.required,
                }
                for spec in self.fields
            ],
        }

    def digest(self) -> str:
        return digest_snapshot(self.snapshot())


def require_additive_revision(old: AdapterRecordSchema, new: AdapterRecordSchema) -> None:
    """Enforce the adapter compatibility rule between consecutive revisions.

    Additive means: same schema identity and source, revision advanced by
    one, every old pointer retained at an identical type, and no optional
    field promoted to required. New pointers may be added freely.
    """

    if new.schema_id != old.schema_id:
        raise AdapterCompatibilityError(
            "adapter_schema_identity_changed",
            "A revision must keep its schema_id",
        )
    if new.source_id != old.source_id:
        raise AdapterCompatibilityError(
            "adapter_schema_source_changed",
            "A revision must keep its source_id",
        )
    if new.revision != old.revision + 1 or new.supersedes_revision != old.revision:
        raise AdapterCompatibilityError(
            "adapter_schema_revision_gap",
            "A revision must supersede exactly the prior revision",
        )
    removed: list[str] = []
    retyped: list[str] = []
    promoted: list[str] = []
    for spec in old.fields:
        successor = new.field_by_pointer(spec.pointer)
        if successor is None:
            removed.append(spec.pointer)
        elif successor.field_type is not spec.field_type:
            retyped.append(spec.pointer)
        elif successor.required and not spec.required:
            promoted.append(spec.pointer)
    if removed or retyped or promoted:
        raise AdapterCompatibilityError(
            "adapter_schema_not_additive",
            "Adapter revisions must retain prior pointers, types, and optionality",
            details=tuple(
                f"removed:{p}" for p in removed
            )
            + tuple(f"retyped:{p}" for p in retyped)
            + tuple(f"promoted:{p}" for p in promoted),
        )


# ── Projection side: approved semantics only ─────────────────────────────────


@dataclass(frozen=True)
class ProjectionObservationSpec:
    """An approved observation field; names meaning, never payload location."""

    field_name: str
    value_type: Literal["string", "integer", "number", "boolean"]

    def __post_init__(self) -> None:
        _require_safe_name(self.field_name, "observation field name")
        if self.value_type not in _SCALAR_VALUE_TYPES:
            raise BoundaryContractError(
                "projection_schema_bad_value_type",
                "Observation value types are limited to approved scalars",
            )


@dataclass(frozen=True)
class ProjectionEntitySpec:
    """Approved semantics for one projected entity kind."""

    alias: str
    kind: ProjectionKind
    observations: tuple[ProjectionObservationSpec, ...]
    cardinality: Cardinality

    def __post_init__(self) -> None:
        _require_safe_name(self.alias, "entity alias")
        names = {spec.field_name for spec in self.observations}
        if len(names) != len(self.observations):
            raise BoundaryContractError(
                "projection_schema_duplicate_observation",
                "Observation field names must be unique within an entity spec",
            )

    def observation_by_name(self, field_name: str) -> ProjectionObservationSpec | None:
        for spec in self.observations:
            if spec.field_name == field_name:
                return spec
        return None


@dataclass(frozen=True)
class ProjectionRelationshipSpec:
    """Approved relationship semantics between declared entity aliases."""

    rule_id: str
    semantic: str
    from_alias: str
    to_alias: str
    cardinality: Cardinality

    def __post_init__(self) -> None:
        _require_safe_name(self.rule_id, "relationship rule ID")
        _require_safe_name(self.semantic, "relationship semantic")
        _require_safe_name(self.from_alias, "relationship source alias")
        _require_safe_name(self.to_alias, "relationship target alias")


@dataclass(frozen=True)
class ProjectionEntitySchema:
    """Versioned, approved projection vocabulary; contains no pointers."""

    schema_id: str
    revision: int
    entities: tuple[ProjectionEntitySpec, ...]
    relationships: tuple[ProjectionRelationshipSpec, ...] = ()

    def __post_init__(self) -> None:
        _require_schema_id(self.schema_id, "projection schema_id")
        _require_revision(self.revision, "projection schema revision")
        aliases = {spec.alias for spec in self.entities}
        if not aliases or len(aliases) != len(self.entities):
            raise BoundaryContractError(
                "projection_schema_bad_entities",
                "Projection schemas need uniquely named entity specs",
            )
        rule_ids = {spec.rule_id for spec in self.relationships}
        if len(rule_ids) != len(self.relationships):
            raise BoundaryContractError(
                "projection_schema_duplicate_relationship",
                "Relationship rule IDs must be unique within a projection schema",
            )
        for spec in self.relationships:
            if spec.from_alias not in aliases or spec.to_alias not in aliases:
                raise BoundaryContractError(
                    "projection_schema_undeclared_alias",
                    "Relationship specs must reference declared entity aliases",
                )

    def entity_by_alias(self, alias: str) -> ProjectionEntitySpec | None:
        for spec in self.entities:
            if spec.alias == alias:
                return spec
        return None

    def snapshot(self) -> dict[str, Any]:
        return {
            "boundary_contract": ADAPTER_BOUNDARY_CONTRACT_VERSION,
            "record": "projection_entity_schema",
            "schema_id": self.schema_id,
            "revision": self.revision,
            "entities": [
                {
                    "alias": spec.alias,
                    "kind": spec.kind.value,
                    "cardinality": {
                        "minimum": spec.cardinality.minimum,
                        "maximum": spec.cardinality.maximum,
                    },
                    "observations": [
                        {
                            "field_name": observation.field_name,
                            "value_type": observation.value_type,
                        }
                        for observation in spec.observations
                    ],
                }
                for spec in self.entities
            ],
            "relationships": [
                {
                    "rule_id": spec.rule_id,
                    "semantic": spec.semantic,
                    "from_alias": spec.from_alias,
                    "to_alias": spec.to_alias,
                    "cardinality": {
                        "minimum": spec.cardinality.minimum,
                        "maximum": spec.cardinality.maximum,
                    },
                }
                for spec in self.relationships
            ],
        }

    def digest(self) -> str:
        return digest_snapshot(self.snapshot())


# ── Mapping declaration: the only place both sides meet ─────────────────────


class UnmappedFieldPolicy(StrEnum):
    """Explicit fate of adapter fields no mapping rule references."""

    RETAIN_UNPROJECTED = "retain_unprojected"
    REJECT_RECORD = "reject_record"


class AmbiguousValuePolicy(StrEnum):
    """Explicit fate of adapter values that fail approved scalar validation."""

    SKIP_AND_FLAG = "skip_and_flag"
    REJECT_RECORD = "reject_record"


@dataclass(frozen=True)
class ObservationBinding:
    """Binds one approved observation field to one adapter evidence pointer."""

    field_name: str
    value_pointer: str

    def __post_init__(self) -> None:
        _require_safe_name(self.field_name, "observation field name")
        _require_pointer(self.value_pointer)


@dataclass(frozen=True)
class OmittedObservation:
    """An approved observation this adapter cannot supply, stated with a reason."""

    entity_alias: str
    field_name: str
    reason: str

    def __post_init__(self) -> None:
        _require_safe_name(self.entity_alias, "entity alias")
        _require_safe_name(self.field_name, "observation field name")
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise BoundaryContractError(
                "mapping_omission_needs_reason",
                "Omitted observations must state a non-empty reason",
            )


@dataclass(frozen=True)
class EntityMappingRule:
    """Locates one projection entity's evidence inside the adapter payload."""

    entity_alias: str
    items_pointer: str
    source_record_id_pointer: str
    effective_from_pointer: str
    effective_to_pointer: str | None
    observation_bindings: tuple[ObservationBinding, ...] = ()

    def __post_init__(self) -> None:
        _require_safe_name(self.entity_alias, "entity alias")
        _require_pointer(self.items_pointer)
        _require_pointer(self.source_record_id_pointer)
        _require_pointer(self.effective_from_pointer)
        if self.effective_to_pointer is not None:
            _require_pointer(self.effective_to_pointer)
        names = [binding.field_name for binding in self.observation_bindings]
        if len(set(names)) != len(names):
            raise BoundaryContractError(
                "mapping_duplicate_binding",
                "Observation bindings must be unique per entity rule",
            )


@dataclass(frozen=True)
class RelationshipMappingRule:
    """Locates one approved relationship's evidence inside the adapter payload."""

    rule_id: str
    items_pointer: str
    from_source_record_id_pointer: str
    to_source_record_id_pointer: str
    effective_from_pointer: str
    effective_to_pointer: str | None

    def __post_init__(self) -> None:
        _require_safe_name(self.rule_id, "relationship rule ID")
        _require_pointer(self.items_pointer)
        _require_pointer(self.from_source_record_id_pointer)
        _require_pointer(self.to_source_record_id_pointer)
        _require_pointer(self.effective_from_pointer)
        if self.effective_to_pointer is not None:
            _require_pointer(self.effective_to_pointer)


@dataclass(frozen=True)
class ApprovedMappingDeclaration:
    """The approved join of one adapter revision to one projection revision.

    Pins exact ``(schema_id, revision)`` on both sides. Every approved
    observation is either bound to declared adapter evidence or omitted with
    a reason; unmapped adapter fields and ambiguous values follow the
    declared policies. Never authored by templates.
    """

    mapping_id: str
    revision: int
    adapter_schema_id: str
    adapter_schema_revision: int
    projection_schema_id: str
    projection_schema_revision: int
    entity_rules: tuple[EntityMappingRule, ...]
    relationship_rules: tuple[RelationshipMappingRule, ...] = ()
    omitted_observations: tuple[OmittedObservation, ...] = ()
    unmapped_field_policy: UnmappedFieldPolicy = UnmappedFieldPolicy.RETAIN_UNPROJECTED
    ambiguous_value_policy: AmbiguousValuePolicy = AmbiguousValuePolicy.REJECT_RECORD

    def __post_init__(self) -> None:
        _require_schema_id(self.mapping_id, "mapping_id")
        _require_revision(self.revision, "mapping revision")
        _require_schema_id(self.adapter_schema_id, "adapter schema_id")
        _require_revision(self.adapter_schema_revision, "adapter schema revision")
        _require_schema_id(self.projection_schema_id, "projection schema_id")
        _require_revision(self.projection_schema_revision, "projection schema revision")
        aliases = [rule.entity_alias for rule in self.entity_rules]
        if len(set(aliases)) != len(aliases):
            raise BoundaryContractError(
                "mapping_duplicate_entity_rule",
                "Entity mapping rules must be unique per alias",
            )
        rule_ids = [rule.rule_id for rule in self.relationship_rules]
        if len(set(rule_ids)) != len(rule_ids):
            raise BoundaryContractError(
                "mapping_duplicate_relationship_rule",
                "Relationship mapping rules must be unique per rule ID",
            )
        omissions = [(item.entity_alias, item.field_name) for item in self.omitted_observations]
        if len(set(omissions)) != len(omissions):
            raise BoundaryContractError(
                "mapping_duplicate_omission",
                "Omitted observations must be unique per entity and field",
            )

    def snapshot(self) -> dict[str, Any]:
        return {
            "boundary_contract": ADAPTER_BOUNDARY_CONTRACT_VERSION,
            "record": "approved_mapping_declaration",
            "mapping_id": self.mapping_id,
            "revision": self.revision,
            "adapter_schema_id": self.adapter_schema_id,
            "adapter_schema_revision": self.adapter_schema_revision,
            "projection_schema_id": self.projection_schema_id,
            "projection_schema_revision": self.projection_schema_revision,
            "unmapped_field_policy": self.unmapped_field_policy.value,
            "ambiguous_value_policy": self.ambiguous_value_policy.value,
            "entity_rules": [
                {
                    "entity_alias": rule.entity_alias,
                    "items_pointer": rule.items_pointer,
                    "source_record_id_pointer": rule.source_record_id_pointer,
                    "effective_from_pointer": rule.effective_from_pointer,
                    "effective_to_pointer": rule.effective_to_pointer,
                    "observation_bindings": [
                        {
                            "field_name": binding.field_name,
                            "value_pointer": binding.value_pointer,
                        }
                        for binding in rule.observation_bindings
                    ],
                }
                for rule in self.entity_rules
            ],
            "relationship_rules": [
                {
                    "rule_id": rule.rule_id,
                    "items_pointer": rule.items_pointer,
                    "from_source_record_id_pointer": rule.from_source_record_id_pointer,
                    "to_source_record_id_pointer": rule.to_source_record_id_pointer,
                    "effective_from_pointer": rule.effective_from_pointer,
                    "effective_to_pointer": rule.effective_to_pointer,
                }
                for rule in self.relationship_rules
            ],
            "omitted_observations": [
                {
                    "entity_alias": item.entity_alias,
                    "field_name": item.field_name,
                    "reason": item.reason,
                }
                for item in self.omitted_observations
            ],
        }

    def digest(self) -> str:
        return digest_snapshot(self.snapshot())


_TYPE_COMPATIBILITY: dict[str, frozenset[AdapterFieldType]] = {
    "string": frozenset({AdapterFieldType.STRING, AdapterFieldType.ANY}),
    "integer": frozenset({AdapterFieldType.INTEGER, AdapterFieldType.ANY}),
    "number": frozenset({AdapterFieldType.NUMBER, AdapterFieldType.INTEGER, AdapterFieldType.ANY}),
    "boolean": frozenset({AdapterFieldType.BOOLEAN, AdapterFieldType.ANY}),
}


def validate_mapping(
    mapping: ApprovedMappingDeclaration,
    adapter_schema: AdapterRecordSchema,
    projection_schema: ProjectionEntitySchema,
) -> None:
    """Fail closed unless the mapping joins exactly these two revisions.

    Raises :class:`MappingValidationError` with a stable code; never mutates
    and never consults payload data.
    """

    if (
        mapping.adapter_schema_id != adapter_schema.schema_id
        or mapping.adapter_schema_revision != adapter_schema.revision
    ):
        raise MappingValidationError(
            "mapping_adapter_pin_mismatch",
            "The mapping pins a different adapter schema revision",
        )
    if (
        mapping.projection_schema_id != projection_schema.schema_id
        or mapping.projection_schema_revision != projection_schema.revision
    ):
        raise MappingValidationError(
            "mapping_projection_pin_mismatch",
            "The mapping pins a different projection schema revision",
        )

    declared_aliases = {spec.alias for spec in projection_schema.entities}
    mapped_aliases = {rule.entity_alias for rule in mapping.entity_rules}
    missing_aliases = declared_aliases - mapped_aliases
    undeclared_aliases = mapped_aliases - declared_aliases
    if missing_aliases:
        raise MappingValidationError(
            "mapping_missing_entity_rule",
            "Every approved entity needs exactly one mapping rule",
            details=tuple(sorted(missing_aliases)),
        )
    if undeclared_aliases:
        raise MappingValidationError(
            "mapping_undeclared_entity",
            "Mapping rules must reference approved entity aliases",
            details=tuple(sorted(undeclared_aliases)),
        )

    declared_rules = {spec.rule_id for spec in projection_schema.relationships}
    mapped_rules = {rule.rule_id for rule in mapping.relationship_rules}
    missing_rules = declared_rules - mapped_rules
    undeclared_rules = mapped_rules - declared_rules
    if missing_rules:
        raise MappingValidationError(
            "mapping_missing_relationship_rule",
            "Every approved relationship needs exactly one mapping rule",
            details=tuple(sorted(missing_rules)),
        )
    if undeclared_rules:
        raise MappingValidationError(
            "mapping_undeclared_relationship",
            "Relationship mapping rules must reference approved rule IDs",
            details=tuple(sorted(undeclared_rules)),
        )

    omitted = {(item.entity_alias, item.field_name) for item in mapping.omitted_observations}
    for entity_alias, field_name in sorted(omitted):
        spec = projection_schema.entity_by_alias(entity_alias)
        if spec is None or spec.observation_by_name(field_name) is None:
            raise MappingValidationError(
                "mapping_omission_undeclared",
                "Omissions must name approved observation fields",
                details=(f"{entity_alias}.{field_name}",),
            )

    for rule in mapping.entity_rules:
        entity_spec = projection_schema.entity_by_alias(rule.entity_alias)
        assert entity_spec is not None  # alias coverage proven above
        _require_declared_field(adapter_schema, rule.items_pointer)
        _require_declared_field(adapter_schema, rule.source_record_id_pointer)
        _require_declared_field(adapter_schema, rule.effective_from_pointer)
        if rule.effective_to_pointer is not None:
            _require_declared_field(adapter_schema, rule.effective_to_pointer)

        bound = {binding.field_name for binding in rule.observation_bindings}
        for binding in rule.observation_bindings:
            observation = entity_spec.observation_by_name(binding.field_name)
            if observation is None:
                raise MappingValidationError(
                    "mapping_undeclared_observation",
                    "Bindings must name approved observation fields",
                    details=(f"{rule.entity_alias}.{binding.field_name}",),
                )
            adapter_field = _require_declared_field(adapter_schema, binding.value_pointer)
            if adapter_field.field_type not in _TYPE_COMPATIBILITY[observation.value_type]:
                raise MappingValidationError(
                    "mapping_type_mismatch",
                    "Adapter field types must satisfy approved observation types",
                    details=(
                        f"{rule.entity_alias}.{binding.field_name}:"
                        f"{adapter_field.field_type.value}->{observation.value_type}",
                    ),
                )
        for observation in entity_spec.observations:
            key = (rule.entity_alias, observation.field_name)
            if observation.field_name in bound and key in omitted:
                raise MappingValidationError(
                    "mapping_bound_and_omitted",
                    "An observation cannot be both bound and omitted",
                    details=(f"{rule.entity_alias}.{observation.field_name}",),
                )
            if observation.field_name not in bound and key not in omitted:
                raise MappingValidationError(
                    "mapping_unbound_observation",
                    "Unsupported observations must be omitted explicitly",
                    details=(f"{rule.entity_alias}.{observation.field_name}",),
                )

    for rule in mapping.relationship_rules:
        _require_declared_field(adapter_schema, rule.items_pointer)
        _require_declared_field(adapter_schema, rule.from_source_record_id_pointer)
        _require_declared_field(adapter_schema, rule.to_source_record_id_pointer)
        _require_declared_field(adapter_schema, rule.effective_from_pointer)
        if rule.effective_to_pointer is not None:
            _require_declared_field(adapter_schema, rule.effective_to_pointer)


# ── Legacy OBS-1788 preservation (additive migration) ────────────────────────


@dataclass(frozen=True)
class DecomposedLegacyProfile:
    """The three B6 parts of one legacy profile plus its preserved identity."""

    adapter_schema: AdapterRecordSchema
    projection_schema: ProjectionEntitySchema
    mapping: ApprovedMappingDeclaration
    legacy_profile_id: str
    legacy_revision: int
    legacy_digest: str


def decompose_legacy_profile(profile: ApprovedProjectionProfile) -> DecomposedLegacyProfile:
    """Split a legacy OBS-1788 profile along the B6 boundary.

    Deterministic and additive: derived records reference the legacy
    identity; the legacy snapshot, ID, revision, and digest are retained
    unchanged and remain resolvable in the OBS-1788 registry.
    """

    fields: dict[str, AdapterFieldSpec] = {}

    def declare(pointer: str, field_type: AdapterFieldType) -> None:
        existing = fields.get(pointer)
        if existing is not None and existing.field_type is not field_type:
            field_type = AdapterFieldType.ANY
        fields[pointer] = AdapterFieldSpec(pointer=pointer, field_type=field_type)

    entity_specs: list[ProjectionEntitySpec] = []
    entity_rules: list[EntityMappingRule] = []
    for entity in profile.entities:
        declare(entity.items_pointer, AdapterFieldType.ARRAY)
        declare(entity.source_record_id_pointer, AdapterFieldType.ANY)
        declare(entity.effective_from_pointer, AdapterFieldType.ANY)
        if entity.effective_to_pointer is not None:
            declare(entity.effective_to_pointer, AdapterFieldType.ANY)
        for observation in entity.observations:
            declare(observation.value_pointer, AdapterFieldType(observation.value_type))
        entity_specs.append(
            ProjectionEntitySpec(
                alias=entity.alias,
                kind=entity.kind,
                observations=tuple(
                    ProjectionObservationSpec(
                        field_name=observation.field_name,
                        value_type=observation.value_type,
                    )
                    for observation in entity.observations
                ),
                cardinality=entity.cardinality,
            )
        )
        entity_rules.append(
            EntityMappingRule(
                entity_alias=entity.alias,
                items_pointer=entity.items_pointer,
                source_record_id_pointer=entity.source_record_id_pointer,
                effective_from_pointer=entity.effective_from_pointer,
                effective_to_pointer=entity.effective_to_pointer,
                observation_bindings=tuple(
                    ObservationBinding(
                        field_name=observation.field_name,
                        value_pointer=observation.value_pointer,
                    )
                    for observation in entity.observations
                ),
            )
        )

    relationship_specs: list[ProjectionRelationshipSpec] = []
    relationship_rules: list[RelationshipMappingRule] = []
    for relationship in profile.relationships:
        declare(relationship.items_pointer, AdapterFieldType.ARRAY)
        declare(relationship.from_source_record_id_pointer, AdapterFieldType.ANY)
        declare(relationship.to_source_record_id_pointer, AdapterFieldType.ANY)
        declare(relationship.effective_from_pointer, AdapterFieldType.ANY)
        if relationship.effective_to_pointer is not None:
            declare(relationship.effective_to_pointer, AdapterFieldType.ANY)
        relationship_specs.append(
            ProjectionRelationshipSpec(
                rule_id=relationship.rule_id,
                semantic=relationship.semantic,
                from_alias=relationship.from_alias,
                to_alias=relationship.to_alias,
                cardinality=relationship.cardinality,
            )
        )
        relationship_rules.append(
            RelationshipMappingRule(
                rule_id=relationship.rule_id,
                items_pointer=relationship.items_pointer,
                from_source_record_id_pointer=relationship.from_source_record_id_pointer,
                to_source_record_id_pointer=relationship.to_source_record_id_pointer,
                effective_from_pointer=relationship.effective_from_pointer,
                effective_to_pointer=relationship.effective_to_pointer,
            )
        )

    base_id = f"{LEGACY_PROFILE_NAMESPACE}_{profile.profile_id}"
    adapter_schema = AdapterRecordSchema(
        schema_id=f"{base_id}_adapter",
        revision=profile.revision,
        source_id=profile.source_id,
        fields=tuple(fields[pointer] for pointer in sorted(fields)),
        supersedes_revision=profile.revision - 1 if profile.revision > 1 else None,
    )
    projection_schema = ProjectionEntitySchema(
        schema_id=f"{base_id}_projection",
        revision=profile.revision,
        entities=tuple(entity_specs),
        relationships=tuple(relationship_specs),
    )
    mapping = ApprovedMappingDeclaration(
        mapping_id=f"{base_id}_mapping",
        revision=profile.revision,
        adapter_schema_id=adapter_schema.schema_id,
        adapter_schema_revision=adapter_schema.revision,
        projection_schema_id=projection_schema.schema_id,
        projection_schema_revision=projection_schema.revision,
        entity_rules=tuple(entity_rules),
        relationship_rules=tuple(relationship_rules),
        omitted_observations=(),
        unmapped_field_policy=UnmappedFieldPolicy.RETAIN_UNPROJECTED,
        ambiguous_value_policy=AmbiguousValuePolicy.REJECT_RECORD,
    )
    validate_mapping(mapping, adapter_schema, projection_schema)
    return DecomposedLegacyProfile(
        adapter_schema=adapter_schema,
        projection_schema=projection_schema,
        mapping=mapping,
        legacy_profile_id=profile.profile_id,
        legacy_revision=profile.revision,
        legacy_digest=profile.digest(),
    )


def recompose_legacy_profile(decomposed: DecomposedLegacyProfile) -> ApprovedProjectionProfile:
    """Rebuild the legacy profile from its B6 parts."""

    projection_schema = decomposed.projection_schema
    mapping = decomposed.mapping
    entities: list[EntityRule] = []
    for spec in projection_schema.entities:
        rule = next(
            (item for item in mapping.entity_rules if item.entity_alias == spec.alias),
            None,
        )
        if rule is None:
            raise LegacyPreservationError(
                "legacy_recompose_missing_entity",
                "A legacy entity lost its mapping rule",
                details=(spec.alias,),
            )
        bindings = {binding.field_name: binding for binding in rule.observation_bindings}
        observations: list[ObservationRule] = []
        for observation in spec.observations:
            binding = bindings.get(observation.field_name)
            if binding is None:
                raise LegacyPreservationError(
                    "legacy_recompose_missing_binding",
                    "A legacy observation lost its evidence binding",
                    details=(f"{spec.alias}.{observation.field_name}",),
                )
            observations.append(
                ObservationRule(
                    field_name=observation.field_name,
                    value_pointer=binding.value_pointer,
                    value_type=observation.value_type,
                )
            )
        entities.append(
            EntityRule(
                alias=spec.alias,
                kind=spec.kind,
                items_pointer=rule.items_pointer,
                source_record_id_pointer=rule.source_record_id_pointer,
                effective_from_pointer=rule.effective_from_pointer,
                effective_to_pointer=rule.effective_to_pointer,
                observations=tuple(observations),
                cardinality=spec.cardinality,
            )
        )

    relationships: list[RelationshipRule] = []
    for spec in projection_schema.relationships:
        rule = next(
            (item for item in mapping.relationship_rules if item.rule_id == spec.rule_id),
            None,
        )
        if rule is None:
            raise LegacyPreservationError(
                "legacy_recompose_missing_relationship",
                "A legacy relationship lost its mapping rule",
                details=(spec.rule_id,),
            )
        relationships.append(
            RelationshipRule(
                rule_id=spec.rule_id,
                semantic=spec.semantic,
                items_pointer=rule.items_pointer,
                from_alias=spec.from_alias,
                from_source_record_id_pointer=rule.from_source_record_id_pointer,
                to_alias=spec.to_alias,
                to_source_record_id_pointer=rule.to_source_record_id_pointer,
                effective_from_pointer=rule.effective_from_pointer,
                effective_to_pointer=rule.effective_to_pointer,
                cardinality=spec.cardinality,
            )
        )

    return ApprovedProjectionProfile(
        profile_id=decomposed.legacy_profile_id,
        revision=decomposed.legacy_revision,
        source_id=decomposed.adapter_schema.source_id,
        entities=tuple(entities),
        relationships=tuple(relationships),
    )


def verify_legacy_round_trip(profile: ApprovedProjectionProfile) -> DecomposedLegacyProfile:
    """Prove AC-209 for one profile: decompose, recompose, compare digests."""

    decomposed = decompose_legacy_profile(profile)
    recomposed = recompose_legacy_profile(decomposed)
    if (
        recomposed.profile_id != profile.profile_id
        or recomposed.revision != profile.revision
        or recomposed.snapshot() != profile.snapshot()
        or recomposed.digest() != decomposed.legacy_digest
    ):
        raise LegacyPreservationError(
            "legacy_round_trip_mismatch",
            "The decomposed profile no longer reproduces its approved identity",
            details=(profile.profile_id,),
        )
    return decomposed


# ── Helpers ───────────────────────────────────────────────────────────────────


def _require_schema_id(value: str, description: str) -> None:
    if not isinstance(value, str) or not _SCHEMA_ID.fullmatch(value):
        raise BoundaryContractError(
            "boundary_bad_identifier",
            f"Boundary {description} is invalid",
        )


def _require_revision(value: int, description: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise BoundaryContractError(
            "boundary_bad_revision",
            f"Boundary {description} must be a positive integer",
        )


def _require_source_id(value: str) -> None:
    if (
        not isinstance(value, str)
        or not value.strip()
        or value != value.strip()
        or "\0" in value
    ):
        raise BoundaryContractError(
            "boundary_bad_source_id",
            "Adapter schema source_id is invalid",
        )


def _require_pointer(pointer: str) -> None:
    if not isinstance(pointer, str) or (pointer and not pointer.startswith("/")):
        raise BoundaryContractError(
            "boundary_bad_pointer",
            "Boundary JSON pointers must use RFC 6901 syntax",
        )


def _require_safe_name(value: str, description: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"^[a-z][a-z0-9_]{0,63}$", value):
        raise BoundaryContractError(
            "boundary_bad_name",
            f"Boundary {description} is invalid",
        )


def _require_declared_field(
    adapter_schema: AdapterRecordSchema, pointer: str
) -> AdapterFieldSpec:
    spec = adapter_schema.field_by_pointer(pointer)
    if spec is None:
        raise MappingValidationError(
            "mapping_undeclared_adapter_pointer",
            "Mappings may reference only declared adapter evidence",
            details=(pointer,),
        )
    return spec
