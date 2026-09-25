"""Tests for B6/OBS-1967 adapter-record / projection-schema boundary contracts.

Covers AC-207, AC-208, and AC-209.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from flowsint_core.core.forensics import (
    AdapterCompatibilityError,
    AdapterFieldSpec,
    AdapterFieldType,
    AdapterRecordSchema,
    AmbiguousValuePolicy,
    ApprovedMappingDeclaration,
    BoundaryContractError,
    EntityMappingRule,
    MappingValidationError,
    ObservationBinding,
    OmittedObservation,
    ProjectionEntitySchema,
    ProjectionEntitySpec,
    ProjectionObservationSpec,
    ProjectionRelationshipSpec,
    RelationshipMappingRule,
    UnmappedFieldPolicy,
    decompose_legacy_profile,
    recompose_legacy_profile,
    require_additive_revision,
    validate_mapping,
    verify_legacy_round_trip,
)
from flowsint_core.core.projection.contracts import (
    ApprovedProjectionProfile,
    Cardinality,
    EntityRule,
    ObservationRule,
    ProjectionKind,
    RelationshipRule,
)


NOW = datetime(2026, 8, 9, tzinfo=timezone.utc)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _field(
    pointer: str,
    field_type: AdapterFieldType,
    *,
    required: bool = False,
) -> AdapterFieldSpec:
    return AdapterFieldSpec(pointer=pointer, field_type=field_type, required=required)


def _adapter_schema(
    *,
    schema_id: str = "adapter_schema",
    revision: int = 1,
    source_id: str = "source:adapter",
    fields: tuple[AdapterFieldSpec, ...] | None = None,
    supersedes_revision: int | None = None,
) -> AdapterRecordSchema:
    return AdapterRecordSchema(
        schema_id=schema_id,
        revision=revision,
        source_id=source_id,
        fields=fields
        or (
            _field("/people", AdapterFieldType.ARRAY),
            _field("/person_id", AdapterFieldType.STRING),
            _field("/observed_at", AdapterFieldType.STRING),
            _field("/name", AdapterFieldType.STRING),
            _field("/score", AdapterFieldType.INTEGER),
            _field("/any_value", AdapterFieldType.ANY),
            _field("/links", AdapterFieldType.ARRAY),
            _field("/link_from", AdapterFieldType.STRING),
            _field("/link_to", AdapterFieldType.STRING),
        ),
        supersedes_revision=(revision - 1 if revision > 1 else None)
        if supersedes_revision is None
        else supersedes_revision,
    )


def _entity(
    *,
    alias: str = "person",
    kind: ProjectionKind = ProjectionKind.PARTY,
    observations: tuple[ProjectionObservationSpec, ...] | None = None,
) -> ProjectionEntitySpec:
    return ProjectionEntitySpec(
        alias=alias,
        kind=kind,
        observations=observations or (ProjectionObservationSpec("name", "string"),),
        cardinality=Cardinality(),
    )


def _projection_schema(
    *,
    schema_id: str = "projection_schema",
    revision: int = 1,
    entities: tuple[ProjectionEntitySpec, ...] | None = None,
    relationships: tuple[ProjectionRelationshipSpec, ...] = (),
) -> ProjectionEntitySchema:
    return ProjectionEntitySchema(
        schema_id=schema_id,
        revision=revision,
        entities=entities or (_entity(),),
        relationships=relationships,
    )


def _entity_rule(
    *,
    entity_alias: str = "person",
    observation_bindings: tuple[ObservationBinding, ...] | None = None,
) -> EntityMappingRule:
    return EntityMappingRule(
        entity_alias=entity_alias,
        items_pointer="/people",
        source_record_id_pointer="/person_id",
        effective_from_pointer="/observed_at",
        effective_to_pointer=None,
        observation_bindings=observation_bindings
        or (ObservationBinding("name", "/name"),),
    )


def _mapping(
    *,
    mapping_id: str = "approved_mapping",
    revision: int = 1,
    adapter_schema_id: str = "adapter_schema",
    adapter_schema_revision: int = 1,
    projection_schema_id: str = "projection_schema",
    projection_schema_revision: int = 1,
    entity_rules: tuple[EntityMappingRule, ...] | None = None,
    relationship_rules: tuple[RelationshipMappingRule, ...] = (),
    omitted_observations: tuple[OmittedObservation, ...] = (),
    unmapped_field_policy: UnmappedFieldPolicy = UnmappedFieldPolicy.RETAIN_UNPROJECTED,
    ambiguous_value_policy: AmbiguousValuePolicy = AmbiguousValuePolicy.SKIP_AND_FLAG,
) -> ApprovedMappingDeclaration:
    return ApprovedMappingDeclaration(
        mapping_id=mapping_id,
        revision=revision,
        adapter_schema_id=adapter_schema_id,
        adapter_schema_revision=adapter_schema_revision,
        projection_schema_id=projection_schema_id,
        projection_schema_revision=projection_schema_revision,
        entity_rules=entity_rules or (_entity_rule(),),
        relationship_rules=relationship_rules,
        omitted_observations=omitted_observations,
        unmapped_field_policy=unmapped_field_policy,
        ambiguous_value_policy=ambiguous_value_policy,
    )


def _legacy_profile() -> ApprovedProjectionProfile:
    return ApprovedProjectionProfile(
        profile_id="legacy_profile",
        revision=2,
        source_id="source:legacy",
        entities=(
            EntityRule(
                alias="party",
                kind=ProjectionKind.PARTY,
                items_pointer="/parties",
                source_record_id_pointer="/party_id",
                effective_from_pointer="/observed_at",
                effective_to_pointer=None,
                observations=(
                    ObservationRule("name", "/name", "string"),
                    ObservationRule("rank", "/rank", "integer"),
                ),
                cardinality=Cardinality(minimum=1),
            ),
            EntityRule(
                alias="parcel",
                kind=ProjectionKind.PARCEL,
                items_pointer="/parcels",
                source_record_id_pointer="/parcel_id",
                effective_from_pointer="/observed_at",
                effective_to_pointer="/expires_at",
                observations=(ObservationRule("address", "/address", "string"),),
                cardinality=Cardinality(),
            ),
        ),
        relationships=(
            RelationshipRule(
                rule_id="owns",
                semantic="owns",
                items_pointer="/ownerships",
                from_alias="party",
                from_source_record_id_pointer="/owner_id",
                to_alias="parcel",
                to_source_record_id_pointer="/owned_parcel_id",
                effective_from_pointer="/observed_at",
                effective_to_pointer=None,
                cardinality=Cardinality(),
            ),
        ),
    )


# ── AC-207 adapter revisions ─────────────────────────────────────────────────


def test_ac207_accepts_additive_adapter_revision_without_changing_projection_digest() -> None:
    adapter_v1 = _adapter_schema()
    projection = _projection_schema()
    mapping_v1 = _mapping()
    adapter_v2 = _adapter_schema(
        revision=2,
        fields=adapter_v1.fields + (_field("/new_evidence", AdapterFieldType.STRING),),
    )
    mapping_v2 = _mapping(revision=2, adapter_schema_revision=2)

    require_additive_revision(adapter_v1, adapter_v2)
    validate_mapping(mapping_v1, adapter_v1, projection)
    validate_mapping(mapping_v2, adapter_v2, projection)

    assert projection.digest() == _projection_schema().digest()
    assert mapping_v2.projection_schema_revision == projection.revision


def test_ac207_rejects_removed_adapter_pointer_with_stable_details() -> None:
    old = _adapter_schema(fields=(_field("/name", AdapterFieldType.STRING),))
    new = _adapter_schema(revision=2, fields=(_field("/other", AdapterFieldType.STRING),))

    with pytest.raises(AdapterCompatibilityError) as error:
        require_additive_revision(old, new)

    assert error.value.code == "adapter_schema_not_additive"
    assert error.value.details == ("removed:/name",)


def test_ac207_rejects_retyped_adapter_pointer_with_stable_details() -> None:
    old = _adapter_schema(fields=(_field("/name", AdapterFieldType.STRING),))
    new = _adapter_schema(revision=2, fields=(_field("/name", AdapterFieldType.INTEGER),))

    with pytest.raises(AdapterCompatibilityError) as error:
        require_additive_revision(old, new)

    assert error.value.code == "adapter_schema_not_additive"
    assert error.value.details == ("retyped:/name",)


def test_ac207_rejects_optional_to_required_adapter_promotion_with_stable_details() -> None:
    old = _adapter_schema(fields=(_field("/name", AdapterFieldType.STRING),))
    new = _adapter_schema(
        revision=2,
        fields=(_field("/name", AdapterFieldType.STRING, required=True),),
    )

    with pytest.raises(AdapterCompatibilityError) as error:
        require_additive_revision(old, new)

    assert error.value.code == "adapter_schema_not_additive"
    assert error.value.details == ("promoted:/name",)


def test_ac207_rejects_adapter_schema_identity_change() -> None:
    old = _adapter_schema()
    new = _adapter_schema(schema_id="other_schema", revision=2)

    with pytest.raises(AdapterCompatibilityError) as error:
        require_additive_revision(old, new)

    assert error.value.code == "adapter_schema_identity_changed"
    assert error.value.details == ()


def test_ac207_rejects_adapter_source_change() -> None:
    old = _adapter_schema()
    new = _adapter_schema(revision=2, source_id="source:other")

    with pytest.raises(AdapterCompatibilityError) as error:
        require_additive_revision(old, new)

    assert error.value.code == "adapter_schema_source_changed"
    assert error.value.details == ()


def test_ac207_rejects_adapter_revision_gap() -> None:
    old = _adapter_schema()
    new = _adapter_schema(revision=3, supersedes_revision=2)

    with pytest.raises(AdapterCompatibilityError) as error:
        require_additive_revision(old, new)

    assert error.value.code == "adapter_schema_revision_gap"
    assert error.value.details == ()


# ── AC-208 mapping declarations ──────────────────────────────────────────────


def test_ac208_rejects_unbound_projection_observation() -> None:
    adapter = _adapter_schema()
    projection = _projection_schema(
        entities=(_entity(observations=(ProjectionObservationSpec("name", "string"), ProjectionObservationSpec("score", "number"))),),
    )
    mapping = _mapping()

    with pytest.raises(MappingValidationError) as error:
        validate_mapping(mapping, adapter, projection)

    assert error.value.code == "mapping_unbound_observation"
    assert error.value.details == ("person.score",)


def test_ac208_accepts_explicit_omission_with_reason_and_policies() -> None:
    adapter = _adapter_schema()
    projection = _projection_schema(
        entities=(_entity(observations=(ProjectionObservationSpec("name", "string"), ProjectionObservationSpec("score", "number"))),),
    )
    mapping = _mapping(
        omitted_observations=(OmittedObservation("person", "score", "adapter does not retain score"),),
        unmapped_field_policy=UnmappedFieldPolicy.RETAIN_UNPROJECTED,
        ambiguous_value_policy=AmbiguousValuePolicy.SKIP_AND_FLAG,
    )

    validate_mapping(mapping, adapter, projection)

    assert mapping.unmapped_field_policy is UnmappedFieldPolicy.RETAIN_UNPROJECTED
    assert mapping.ambiguous_value_policy is AmbiguousValuePolicy.SKIP_AND_FLAG
    assert mapping.omitted_observations[0].reason == "adapter does not retain score"


def test_ac208_rejects_omission_without_reason() -> None:
    with pytest.raises(BoundaryContractError) as error:
        OmittedObservation("person", "name", " ")

    assert error.value.code == "mapping_omission_needs_reason"
    assert error.value.details == ()


def test_ac208_rejects_observation_that_is_both_bound_and_omitted() -> None:
    adapter = _adapter_schema()
    projection = _projection_schema()
    mapping = _mapping(
        omitted_observations=(OmittedObservation("person", "name", "conflicting declaration"),),
    )

    with pytest.raises(MappingValidationError) as error:
        validate_mapping(mapping, adapter, projection)

    assert error.value.code == "mapping_bound_and_omitted"
    assert error.value.details == ("person.name",)


def test_ac208_rejects_undeclared_adapter_evidence_pointer() -> None:
    adapter = _adapter_schema()
    projection = _projection_schema()
    mapping = _mapping(
        entity_rules=(_entity_rule(observation_bindings=(ObservationBinding("name", "/missing"),)),),
    )

    with pytest.raises(MappingValidationError) as error:
        validate_mapping(mapping, adapter, projection)

    assert error.value.code == "mapping_undeclared_adapter_pointer"
    assert error.value.details == ("/missing",)


def test_ac208_rejects_incompatible_adapter_evidence_type() -> None:
    adapter = _adapter_schema()
    projection = _projection_schema()
    mapping = _mapping(
        entity_rules=(_entity_rule(observation_bindings=(ObservationBinding("name", "/score"),)),),
    )

    with pytest.raises(MappingValidationError) as error:
        validate_mapping(mapping, adapter, projection)

    assert error.value.code == "mapping_type_mismatch"
    assert error.value.details == ("person.name:integer->string",)


def test_ac208_accepts_integer_adapter_evidence_for_number_observation() -> None:
    adapter = _adapter_schema()
    projection = _projection_schema(
        entities=(_entity(observations=(ProjectionObservationSpec("score", "number"),)),),
    )
    mapping = _mapping(
        entity_rules=(_entity_rule(observation_bindings=(ObservationBinding("score", "/score"),)),),
    )

    validate_mapping(mapping, adapter, projection)

    assert mapping.entity_rules[0].observation_bindings[0].value_pointer == "/score"


def test_ac208_accepts_any_adapter_evidence_for_approved_scalar_observation() -> None:
    adapter = _adapter_schema()
    projection = _projection_schema(
        entities=(_entity(observations=(ProjectionObservationSpec("active", "boolean"),)),),
    )
    mapping = _mapping(
        entity_rules=(_entity_rule(observation_bindings=(ObservationBinding("active", "/any_value"),)),),
    )

    validate_mapping(mapping, adapter, projection)

    assert adapter.field_by_pointer("/any_value").field_type is AdapterFieldType.ANY  # type: ignore[union-attr]


def test_ac208_rejects_adapter_schema_pin_mismatch() -> None:
    adapter = _adapter_schema()
    projection = _projection_schema()
    mapping = _mapping(adapter_schema_revision=2)

    with pytest.raises(MappingValidationError) as error:
        validate_mapping(mapping, adapter, projection)

    assert error.value.code == "mapping_adapter_pin_mismatch"
    assert error.value.details == ()


def test_ac208_rejects_projection_schema_pin_mismatch() -> None:
    adapter = _adapter_schema()
    projection = _projection_schema()
    mapping = _mapping(projection_schema_revision=2)

    with pytest.raises(MappingValidationError) as error:
        validate_mapping(mapping, adapter, projection)

    assert error.value.code == "mapping_projection_pin_mismatch"
    assert error.value.details == ()


# ── AC-209 legacy preservation ───────────────────────────────────────────────


def test_ac209_preserves_legacy_profile_identity_snapshot_and_digest() -> None:
    profile = _legacy_profile()

    decomposed = verify_legacy_round_trip(profile)
    recomposed = recompose_legacy_profile(decomposed)

    assert decomposed.legacy_profile_id == profile.profile_id
    assert decomposed.legacy_revision == profile.revision
    assert decomposed.legacy_digest == profile.digest()
    assert recomposed.profile_id == profile.profile_id
    assert recomposed.revision == profile.revision
    assert recomposed.snapshot() == profile.snapshot()
    assert recomposed.digest() == profile.digest()


def test_ac209_derives_namespaced_legacy_component_ids() -> None:
    profile = _legacy_profile()

    decomposed = decompose_legacy_profile(profile)

    assert decomposed.adapter_schema.schema_id == "obs1788_legacy_profile_adapter"
    assert decomposed.projection_schema.schema_id == "obs1788_legacy_profile_projection"
    assert decomposed.mapping.mapping_id == "obs1788_legacy_profile_mapping"
    assert decomposed.adapter_schema.revision == profile.revision
    assert decomposed.projection_schema.revision == profile.revision
    assert decomposed.mapping.revision == profile.revision


# ── Construction validation ───────────────────────────────────────────────────


def test_rejects_invalid_boundary_schema_identifier() -> None:
    with pytest.raises(BoundaryContractError) as error:
        _adapter_schema(schema_id="Bad schema")

    assert error.value.code == "boundary_bad_identifier"
    assert error.value.details == ()


def test_rejects_adapter_schema_revision_below_one() -> None:
    with pytest.raises(BoundaryContractError) as error:
        _adapter_schema(revision=0)

    assert error.value.code == "boundary_bad_revision"
    assert error.value.details == ()


def test_rejects_boolean_adapter_schema_revision() -> None:
    with pytest.raises(BoundaryContractError) as error:
        _adapter_schema(revision=True)

    assert error.value.code == "boundary_bad_revision"
    assert error.value.details == ()


def test_rejects_invalid_adapter_field_pointer() -> None:
    with pytest.raises(BoundaryContractError) as error:
        _field("not-a-pointer", AdapterFieldType.STRING)

    assert error.value.code == "boundary_bad_pointer"
    assert error.value.details == ()


def test_rejects_duplicate_adapter_pointers() -> None:
    with pytest.raises(BoundaryContractError) as error:
        _adapter_schema(fields=(_field("/name", AdapterFieldType.STRING), _field("/name", AdapterFieldType.STRING)))

    assert error.value.code == "adapter_schema_duplicate_pointer"
    assert error.value.details == ()


def test_rejects_duplicate_projection_entity_aliases() -> None:
    with pytest.raises(BoundaryContractError) as error:
        _projection_schema(entities=(_entity(), _entity()))

    assert error.value.code == "projection_schema_bad_entities"
    assert error.value.details == ()


def test_rejects_duplicate_projection_relationship_rule_ids() -> None:
    relationship = ProjectionRelationshipSpec("owns", "owns", "person", "person", Cardinality())

    with pytest.raises(BoundaryContractError) as error:
        _projection_schema(relationships=(relationship, relationship))

    assert error.value.code == "projection_schema_duplicate_relationship"
    assert error.value.details == ()


def test_rejects_duplicate_entity_observation_bindings() -> None:
    with pytest.raises(BoundaryContractError) as error:
        _entity_rule(
            observation_bindings=(
                ObservationBinding("name", "/name"),
                ObservationBinding("name", "/any_value"),
            ),
        )

    assert error.value.code == "mapping_duplicate_binding"
    assert error.value.details == ()


def test_rejects_duplicate_mapping_omissions() -> None:
    omission = OmittedObservation("person", "name", "not supported")

    with pytest.raises(BoundaryContractError) as error:
        _mapping(omitted_observations=(omission, omission))

    assert error.value.code == "mapping_duplicate_omission"
    assert error.value.details == ()


def test_rejects_duplicate_mapping_entity_rules() -> None:
    with pytest.raises(BoundaryContractError) as error:
        _mapping(entity_rules=(_entity_rule(), _entity_rule()))

    assert error.value.code == "mapping_duplicate_entity_rule"
    assert error.value.details == ()


def test_rejects_duplicate_mapping_relationship_rules() -> None:
    relationship = RelationshipMappingRule(
        "owns", "/links", "/link_from", "/link_to", "/observed_at", None
    )

    with pytest.raises(BoundaryContractError) as error:
        _mapping(relationship_rules=(relationship, relationship))

    assert error.value.code == "mapping_duplicate_relationship_rule"
    assert error.value.details == ()


# ── Determinism ───────────────────────────────────────────────────────────────


def test_snapshots_and_digests_are_stable_for_equal_constructions() -> None:
    adapter_one = _adapter_schema()
    adapter_two = _adapter_schema()
    projection_one = _projection_schema()
    projection_two = _projection_schema()
    mapping_one = _mapping()
    mapping_two = _mapping()

    assert adapter_one.snapshot() == adapter_two.snapshot()
    assert adapter_one.digest() == adapter_two.digest()
    assert projection_one.snapshot() == projection_two.snapshot()
    assert projection_one.digest() == projection_two.digest()
    assert mapping_one.snapshot() == mapping_two.snapshot()
    assert mapping_one.digest() == mapping_two.digest()


def test_boundary_snapshots_are_json_safe() -> None:
    snapshots = (
        _adapter_schema().snapshot(),
        _projection_schema().snapshot(),
        _mapping().snapshot(),
        decompose_legacy_profile(_legacy_profile()).projection_schema.snapshot(),
    )

    for snapshot in snapshots:
        assert json.loads(json.dumps(snapshot)) == snapshot
