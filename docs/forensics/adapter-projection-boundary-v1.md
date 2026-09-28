---
title: "Adapter-record / projection-schema boundary v1"
description: "Accepted B6 adapter-record and projection-schema boundary contract for OBS-1967."
version: "adapter-boundary/v1"
status: "Accepted"
issue: "OBS-1967"
---

# Adapter-record / projection-schema boundary v1

**Status:** **Accepted** (verified 2026-08-09; independent PASS by B6Verifier — round-1 finding OBS1967-V-001 remediated via §2.4 complete diagnostic-code registry, documented_raised=37, raised=37, undocumented=0; verified doc SHA ede26a20eb73… predates this Proposed→Accepted flip, per the B3 convention)  
**Contract version:** `adapter-boundary/v1` (`ADAPTER_BOUNDARY_CONTRACT_VERSION = "v1"`)  
**Runtime module:** `flowsint_core.core.forensics.adapter_boundary`  
**Barrier:** B6 — adapter-record / projection-schema boundary  
**Scope:** Normative contract for versioned adapter shape, approved projection semantics, and their explicit mapping. It does not implement payload ingestion, projection execution, a domain-pack registry, or LadybugDB wiring.

The key words **MUST**, **MUST NOT**, **REQUIRED**, **SHOULD**, and **MAY** are normative.

## 1. Decision

Adapter records carry transport shape only: retained JSON pointers, payload types, source identity, and revision compatibility. They do not assert domain meaning. Transport shape is not domain truth.

Projection schemas carry approved semantics only: entity kinds, observation fields and scalar types, relationship semantics, and cardinality. They contain no payload pointers.

`ApprovedMappingDeclaration` is the ONLY join point between those sides. It MUST pin the exact `(schema_id, revision)` of one `AdapterRecordSchema` and one `ProjectionEntitySchema`. No adapter pointer, payload type, or transport-only field may independently create, widen, or infer an approved entity, observation, relationship, or cardinality.

## 2. Boundary specification

### 2.1 Record kinds and version identity

| Record kind | Runtime type | Owns | Does not own |
| --- | --- | --- | --- |
| Adapter record schema | `AdapterRecordSchema` | `source_id`, retained `AdapterFieldSpec(pointer, field_type, required)` transport shape | Projection entities, observations, relationships, and domain semantics |
| Projection entity schema | `ProjectionEntitySchema` | `ProjectionEntitySpec`, `ProjectionObservationSpec`, `ProjectionRelationshipSpec`, `ProjectionKind`, and `Cardinality` | Adapter JSON pointers and transport placement |
| Approved mapping declaration | `ApprovedMappingDeclaration` | Exact adapter/projection pins, entity and relationship evidence-location rules, omissions, and handling policies | New projection semantics or undeclared adapter evidence |

All three records use a safe `schema_id` or `mapping_id` matching `^[a-z][a-z0-9_]{0,127}$`, and a positive integer `revision`. Entity aliases, observation names, and relationship rule IDs use the narrower safe-name rule `^[a-z][a-z0-9_]{0,63}$`.

Only `AdapterRecordSchema` has a `supersedes_revision` field. Revision 1 MUST have `supersedes_revision = null`; each later adapter revision MUST supersede exactly `revision - 1`. `require_additive_revision(old, new)` additionally requires the same `schema_id` and `source_id`, advances exactly one revision, retains every existing pointer at the same `AdapterFieldType`, and MUST NOT promote an optional field to required. It MAY add pointers and MAY relax required to optional. The implementation reports a non-additive change as `adapter_schema_not_additive`, with pointer-qualified detail such as `removed:/p`.

`ProjectionEntitySchema` and `ApprovedMappingDeclaration` have positive revisions but no `supersedes_revision` member or cross-declaration revision-chain validator. Their semantic lineage is established operationally by the exact pins and approval of a new mapping declaration, not inferred from an absent field.

### 2.2 Snapshots and digests

Each record's `snapshot()` includes `"boundary_contract": "v1"` and a record discriminator:

- `adapter_record_schema`
- `projection_entity_schema`
- `approved_mapping_declaration`

Each `digest()` hashes its snapshot with `digest_snapshot`: SHA-256 of `json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()`. Object keys are canonicalized by `sort_keys=True`; declared tuple/list order remains part of the snapshot. A digest therefore identifies the exact versioned record content, including an approved mapping's pins and policies.

### 2.3 Fail-closed mapping validation

`validate_mapping(mapping, adapter_schema, projection_schema)` MUST fail closed unless the mapping pins exactly the supplied adapter and projection revisions. It performs no payload-data lookup and does not mutate its inputs.

Validation MUST establish all of the following:

1. The adapter and projection pins match the supplied records exactly.
2. Every approved entity alias has exactly one mapping rule, and no rule names an undeclared entity.
3. Every approved relationship rule has exactly one mapping rule, and no rule names an undeclared relationship.
4. Every omission names an approved observation.
5. Every entity/relationship evidence pointer—including item, source-record ID, and effective-time pointers—is declared by the adapter schema.
6. Every observation binding names an approved observation and uses a compatible declared adapter field. `integer` satisfies a `number` observation; `any` satisfies every approved scalar observation type.
7. Every approved observation is either bound once or explicitly omitted; it MUST NOT be both.

| Stable error code | Fail-closed meaning |
| --- | --- |
| `mapping_adapter_pin_mismatch` | The declaration does not pin the supplied adapter schema ID and revision. |
| `mapping_projection_pin_mismatch` | The declaration does not pin the supplied projection schema ID and revision. |
| `mapping_missing_entity_rule` | An approved entity has no mapping rule. |
| `mapping_undeclared_entity` | A mapping rule names an entity absent from the projection schema. |
| `mapping_missing_relationship_rule` | An approved relationship has no mapping rule. |
| `mapping_undeclared_relationship` | A relationship mapping rule names a rule absent from the projection schema. |
| `mapping_omission_undeclared` | An omission names an entity or observation absent from the projection schema. |
| `mapping_undeclared_observation` | A binding names an observation absent from its approved entity. |
| `mapping_undeclared_adapter_pointer` | A rule or binding references evidence not declared by the adapter schema. |
| `mapping_type_mismatch` | A declared adapter field type cannot satisfy the approved observation scalar type. |
| `mapping_bound_and_omitted` | An approved observation is both bound and omitted. |
| `mapping_unbound_observation` | An approved observation is neither bound nor explicitly omitted. |

### 2.4 Complete diagnostic-code registry

Sections 2.3 and 2.4 together enumerate every stable diagnostic code raised by `adapter-boundary/v1`. All codes subclass `BoundaryContractError`; a consumer MUST treat this registry, not exception text, as the diagnostic contract.

Construction-time codes raised while building any boundary record:

| Stable error code | Fail-closed meaning |
| --- | --- |
| `boundary_bad_identifier` | A `schema_id` or `mapping_id` fails the safe-identifier rule. |
| `boundary_bad_revision` | A revision is not a positive integer. |
| `boundary_bad_source_id` | An adapter `source_id` is empty, padded, or contains NUL. |
| `boundary_bad_pointer` | An evidence pointer does not use RFC 6901 syntax. |
| `boundary_bad_name` | An alias, observation field, semantic, or rule ID fails the safe-name rule. |
| `adapter_schema_duplicate_pointer` | An adapter schema declares the same pointer twice. |
| `adapter_schema_bad_supersedes` | Revision 1 declares a predecessor, or a later revision does not supersede exactly `revision - 1`. |
| `projection_schema_bad_value_type` | An observation declares a type outside the approved scalars. |
| `projection_schema_duplicate_observation` | An entity spec declares the same observation field twice. |
| `projection_schema_bad_entities` | A projection schema has no entities or duplicate aliases. |
| `projection_schema_duplicate_relationship` | A projection schema declares the same relationship rule ID twice. |
| `projection_schema_undeclared_alias` | A relationship spec references an undeclared entity alias. |
| `mapping_duplicate_binding` | An entity mapping rule binds the same observation field twice. |
| `mapping_omission_needs_reason` | An omission's reason is empty or whitespace. |
| `mapping_duplicate_entity_rule` | A declaration contains two mapping rules for one entity alias. |
| `mapping_duplicate_relationship_rule` | A declaration contains two mapping rules for one relationship rule ID. |
| `mapping_duplicate_omission` | A declaration omits the same entity/field pair twice. |

Adapter compatibility codes raised by `require_additive_revision` (`AdapterCompatibilityError`):

| Stable error code | Fail-closed meaning |
| --- | --- |
| `adapter_schema_identity_changed` | A revision changed its `schema_id`. |
| `adapter_schema_source_changed` | A revision changed its `source_id`. |
| `adapter_schema_revision_gap` | A revision does not advance by exactly one or does not supersede the prior revision. |
| `adapter_schema_not_additive` | A revision removed a pointer, retyped a pointer, or promoted an optional field to required (details name each offender). |

Legacy preservation codes raised during recomposition and round-trip proof (`LegacyPreservationError`):

| Stable error code | Fail-closed meaning |
| --- | --- |
| `legacy_recompose_missing_entity` | A legacy entity lost its mapping rule. |
| `legacy_recompose_missing_binding` | A legacy observation lost its evidence binding. |
| `legacy_recompose_missing_relationship` | A legacy relationship lost its mapping rule. |
| `legacy_round_trip_mismatch` | A recomposed profile no longer reproduces its approved identity, snapshot, or digest. |

## 3. Compatibility matrix

Any revision of either pinned side MUST be accompanied by a newly approved mapping declaration revision that pins the new exact pair. The existing declaration remains a record for its original pair; it does not silently follow either side. This is the exact-pin rule.

| Change | Identity preserved | Required re-approval |
| --- | --- | --- |
| Adapter revision | `schema_id` and `source_id` remain the same; every earlier pointer/type remains compatible. The prior projection schema ID, revision, and digest remain unchanged. | Approve a new mapping declaration revision that pins the new adapter revision and the unchanged projection revision. `require_additive_revision` MUST pass before that approval. |
| Projection revision | The adapter schema ID, revision, and digest remain unchanged. | Approve a new mapping declaration revision that pins the unchanged adapter revision and the new projection revision. Projection semantics MUST NOT be inferred from the adapter. |
| Mapping revision | The pinned adapter and projection schema IDs, revisions, and digests remain unchanged unless this revision intentionally changes one pin. | Validate and approve the complete new declaration. A pin change follows the exact-pin rule; an unchanged pair still requires validation of all rules, bindings, omissions, and policies. |
| `UnmappedFieldPolicy` or `AmbiguousValuePolicy` change | The adapter and projection schema IDs, revisions, and digests remain unchanged. The policy values are part of the mapping snapshot/digest. | Approve and validate a new mapping declaration revision for the same pinned pair. |

The first row is AC-207: an additive adapter revision does not alter the approved projection entity schema. The untouched projection schema's ID, revision, and digest MUST remain stable; only the newly approved mapping joins it to the adapter's new revision.

## 4. Unsupported and ambiguous data (AC-208)

The adapter schema declares the source of evidence through `source_id`; mapping rules locate the corresponding source-record and effective-time evidence using declared adapter pointers. A mapping MAY reference only adapter pointers declared by its pinned `AdapterRecordSchema`.

The mapping declares the fate of unsupported transport fields with `UnmappedFieldPolicy`:

| Policy | Value | Meaning |
| --- | --- | --- |
| Retain unprojected | `retain_unprojected` | The default. Adapter fields not referenced by a mapping rule are retained without creating projection semantics. |
| Reject record | `reject_record` | The declaration's explicit policy value for rejecting a record with unmapped adapter fields. |

The mapping declares the fate of ambiguous values that fail approved scalar validation with `AmbiguousValuePolicy`:

| Policy | Value | Meaning |
| --- | --- | --- |
| Reject record | `reject_record` | The default. Reject the record when an ambiguous value fails approved scalar validation. |
| Skip and flag | `skip_and_flag` | Skip the ambiguous value and flag it. |

Every approved observation MUST be bound to one compatible declared adapter evidence pointer or represented by `OmittedObservation(entity_alias, field_name, reason)`. The omission reason MUST be non-empty. Unsupported observations cannot be omitted silently, and a bound observation cannot also be omitted.

These declarations make evidence provenance and unsupported/ambiguous-data handling explicit at the boundary. They do not themselves ingest a payload or execute a projection.

## 5. OBS-1788 profile-preservation decision (AC-209)

OBS-1788 migration is additive. Existing legacy profiles remain registered under their original profile IDs, revisions, snapshots, and digests; legacy registry entries are never rewritten.

`decompose_legacy_profile(profile)` derives three B6 records with the legacy profile revision:

| Derived record | Derived ID |
| --- | --- |
| Adapter schema | `obs1788_{profile_id}_adapter` |
| Projection schema | `obs1788_{profile_id}_projection` |
| Mapping declaration | `obs1788_{profile_id}_mapping` |

The decomposition retains the original `legacy_profile_id`, `legacy_revision`, and `legacy_digest` in `DecomposedLegacyProfile`. `recompose_legacy_profile(decomposed)` rebuilds the original `ApprovedProjectionProfile`. `verify_legacy_round_trip(profile)` MUST prove identical profile ID, revision, snapshot, and digest; otherwise it raises `LegacyPreservationError`.

This boundary does not generalize the domain-pack registry (B8) and does not wire B9–B11 LadybugDB projection, connector, or related runtime integration.

## 6. Verification

### 6.1 Author self-checks

The following are author self-checks, not independent acceptance evidence:

- **AC-209:** `verify_legacy_round_trip` returned a decomposed profile whose recomposed legacy profile retained the original snapshot and digest.
- **AC-207:** an additive adapter revision was paired with an unchanged projection schema digest and a newly pinned mapping declaration.
- A non-additive adapter revision was rejected by `require_additive_revision`.
- An approved observation without a binding or declared omission was rejected by `validate_mapping` with `mapping_unbound_observation`.

### 6.2 Pending independent verification

Independent verification required by the OBS-1967 audit contract is pending. The verifier MUST establish runtime behavior rather than only inspect declarations:

1. Mutate an adapter payload/schema by adding an unmapped field, apply the declared policy, and confirm that no projection entity schema or projected entity semantics changes without an approved mapping revision.
2. Mutate a mapping declaration and confirm that the prior approved OBS-1788 profile still reconstructs under its original ID, revision, snapshot, and digest.
3. Exercise the exact-pin, additive-revision, omitted-observation, undeclared-pointer, and type-compatibility rejection paths, confirming the stable codes in this specification.

## 7. Non-goals and later barriers

This Proposed B6 contract does not:

- ingest, retain, or validate adapter payload values at runtime;
- create or mutate projection entities or relationships;
- define or generalize the B8 domain-pack registry;
- wire B9 projection construction or B10/B11 LadybugDB/connector integration;
- alter legacy OBS-1788 registry entries; or
- replace the original OBS-1788 profile identity with a derived B6 identifier.

Later wiring MUST preserve the exact pins, additive adapter compatibility, explicit omission/policy declarations, and legacy round-trip preservation defined here.
