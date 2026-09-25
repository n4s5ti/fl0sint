---
title: "Namespaced domain-pack registry v1"
description: "Accepted B8 namespaced domain-pack registry contract for OBS-1969."
version: "domain-packs/v1"
status: "Accepted"
issue: "OBS-1969"
---

# Namespaced domain-pack registry v1

**Status:** **Accepted** (verified 2026-08-09; independent B8Verify round 1 returned overall FAIL — its runtime probes a–d all passed fail-closed: registration collisions/order independence, unqualified-ambiguity rejection, approved-profile version floors incl. downgrade rejection, AC-303 pre/post-migration digest stability, with the 12-code stable registry and §5 signatures exact — but finding OBS1969-01 showed `derive_legacy_pack` consumed a single-pass `profiles` iterable twice. Remediated via one-time tuple materialization plus regression test; final independent PASS by B8FixVerify: verifier-authored generator probe, 71/71 focused tests, no residual multi-consumption.)  
**Contract version:** `domain-packs/v1` (`DOMAIN_PACKS_CONTRACT_VERSION = "v1"`)  
**Runtime module:** `flowsint_core.core.forensics.domain_packs`  
**Barrier:** B8 — namespaced domain-pack registry  
**Scope:** Normative contract for frozen domain-pack declarations, deterministic keys and digests, fail-closed in-memory registration and resolution, and additive migration of fenced legacy vocabulary into the `legacy` namespace. It defines a pure contract layer only.

The key words **MUST**, **MUST NOT**, **REQUIRED**, **SHOULD**, and **MAY** are normative.

## 1. Decision

A domain term has no stable meaning without its namespace, owner, version, and compatibility rule. A `CanonicalSymbol` therefore identifies a term together with its domain namespace and `SymbolKind`; a `DomainPack` identifies the owner, declaration set, version, dependencies, and any projection approval that make that meaning usable.

Registration MUST fail closed. A registry MUST reject collisions, incompatible dependencies, and mutating-path downgrades rather than selecting an arbitrary pack or silently replacing a declaration. A pack without the required `PackApproval` MUST NOT expand projection-facing semantics.

## 2. Canonical symbol identity

`CanonicalSymbol` has the canonical form `namespace:kind:name`. `namespace` and `name` MUST each match `^[a-z][a-z0-9_]{0,63}$`; the registry MUST reject invalid caller-supplied resolution namespaces with `domain_pack_invalid_namespace`. Symbol identity includes `kind`, so the same `name` in two kinds is not the same symbol.

| `SymbolKind` member | Canonical value | Meaning |
| --- | --- | --- |
| `ENTITY_TYPE` | `entity_type` | Domain entity type |
| `PREDICATE` | `predicate` | Domain predicate |
| `RELATIONSHIP_SEMANTIC` | `relationship_semantic` | Approved relationship semantic |
| `PROJECTION_ENTITY_KIND` | `projection_entity_kind` | Approved projection entity kind |
| `ADAPTER_SCHEMA` | `adapter_schema` | Adapter schema identifier |
| `ENRICHER` | `enricher` | Enricher identifier |

B4 claim relation and assessment kinds are CHECK-constraint-pinned ledger vocabularies. They are NOT pack-extensible in v1.

`build_symbol_key(namespace, kind, name)` MUST return the SHA-256 key over NUL-separated parts under domain `obs1969/symbol/v1`. It MUST NOT reuse a key domain from another contract.

## 3. Declarations and digests

A `SymbolDeclaration` contains `symbol`, `definition_json`, `definition_digest`, `introduced_in_version`, `deprecated`, and `deprecated_reason`. Its definition payload MUST be canonical JSON, produced and round-trip checked with:

`json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)`

`definition_digest` MUST be the recomputed SHA-256 hex digest of the UTF-8 bytes of `definition_json`; construction MUST reject a mismatch. Callers cannot represent a declaration whose stored digest does not describe its stored canonical payload. `introduced_in_version` MUST be at least 1. A deprecated declaration MUST have a non-empty `deprecated_reason`, and a non-deprecated declaration MUST NOT have one.

## 4. Pack identity, compatibility, and approval (AC-302)

A frozen `DomainPack` contains `namespace`, non-empty `owner`, positive `version`, `declarations`, `dependencies`, and optional `approval`. A pack snapshot MUST be canonical and `digest()` MUST be SHA-256 of its canonical JSON snapshot using the same canonical JSON recipe as §3. `build_pack_key(namespace, version, pack_digest)` MUST return the SHA-256 key over NUL-separated parts under domain `obs1969/pack/v1`.

Each `PackDependency` MUST name a safe namespace, require `minimum_version >= 1`, and, when `maximum_version` is present, require `maximum_version >= minimum_version`. A pack MUST NOT depend on its own namespace and MUST NOT list a dependency namespace twice.

`PackApproval` binds projection-facing declarations to non-empty, duplicate-free OBS-1788 `(profile_id, profile_revision)` pairs. Each `profile_id` MUST use the safe-name rule and each `profile_revision` MUST be at least 1. A pack declaring `PROJECTION_ENTITY_KIND` or `RELATIONSHIP_SEMANTIC` MUST have `approval`; this is the AC-302 authorization fence. `PackApproval` does not change an OBS-1788 profile snapshot or digest.

## 5. Registry semantics and collision/compatibility matrix (AC-301)

`DomainPackRegistry(packs)` validates the final deduplicated pack set without relying on input order. It MAY retain multiple versions of one namespace, but its resolver MUST select that namespace's highest version unless the caller supplies `version=`. `packs()` MUST return a deterministic sorted tuple.

`register(pack)` is the only mutating path and MUST share the constructor's set-union semantics exactly: the same `(namespace, version)` with a different digest is a digest conflict, identical `(namespace, version, digest)` registration is an idempotent no-op, an owner mismatch across any versions is a namespace conflict, and registering an older version alongside a newer one MUST be accepted — it never changes default resolution. Registration MUST NOT raise a downgrade error; a registry built from `[v1, v2]` MUST equal one built from `[v2, v1]`, whether through the constructor or sequential `register()`. After construction and after each successful registration, every dependency MUST resolve to at least one final-set pack version within its inclusive `[minimum_version, maximum_version]` range (any registered version in range satisfies it); an absent maximum has no upper bound, and a registration that breaks closure MUST fail closed without retaining the pack.

`resolve(namespace, kind, name, *, version: int | None = None, minimum_version: int | None = None, allow_deprecated: bool = False)` MUST resolve exactly one declaration in the selected pack version. `version=` pins an exact version for historical replay; `minimum_version=` sets a floor, and when the namespace's best available version is lower, resolution MUST fail closed with `domain_pack_version_downgrade`. Supplying both is a `ValueError`. `resolve_unqualified(kind, name, *, allow_deprecated=False)` MUST resolve only a declaration present in exactly one namespace. Both MUST refuse a deprecated declaration unless `allow_deprecated=True`.

`require_approved_pack(registry, namespace, *, profile_id, profile_revision, minimum_version: int | None = None)` MUST return the highest registered version of `namespace` whose `PackApproval` covers `(profile_id, profile_revision)`. When no version's approval covers the pair, it MUST fail closed with `domain_pack_unapproved_projection_extension`; when the best approved version is below `minimum_version`, it MUST fail closed with `domain_pack_version_downgrade`. This is the consumption path that makes a pack-version downgrade against an approved profile fail closed.

The AC-301 matrix is the registry's complete collision and compatibility outcome contract. AC-302's approval fence is included because construction validates it; AC-303 migration enters this same registry without a privileged bypass.

| Scenario | Outcome | Stable error code |
| --- | --- | --- |
| Same namespace has two owners | Reject the registry or registration. | `domain_pack_namespace_conflict` |
| Same `(namespace, version)` has different digests | Reject; content is not interchangeable. | `domain_pack_digest_conflict` |
| Same `(namespace, version)` has the same digest | Keep one pack; registration is a no-op. | None |
| `register()` receives a version lower than the namespace's highest | Accept the coexisting older version; default resolution remains the highest version. | None |
| `resolve(minimum_version=…)` or `require_approved_pack(minimum_version=…)` finds the best (approved) version below the floor | Reject the downgrade at resolution time. | `domain_pack_version_downgrade` |
| A dependency namespace is absent from the final set | Reject dependency closure. | `domain_pack_missing_dependency` |
| No dependency version falls within its declared range | Reject dependency closure. | `domain_pack_dependency_incompatible` |
| An unqualified kind/name exists in two or more namespaces | Refuse to choose a namespace. | `domain_pack_ambiguous_symbol` |
| A requested namespace, version, kind, or name is not declared | Refuse resolution. | `domain_pack_unknown_symbol` |
| A deprecated declaration is resolved without `allow_deprecated=True` | Refuse resolution. | `domain_pack_deprecated_symbol` |
| A projection-facing declaration has no `PackApproval` | Reject the pack. | `domain_pack_unapproved_projection_extension` |
| `require_approved_pack` finds no version whose approval covers `(profile_id, profile_revision)` | Refuse approved-pack selection. | `domain_pack_unapproved_projection_extension` |

## 6. Additive canonical namespace migration (AC-303)

`LEGACY_NAMESPACE = "legacy"` and `LEGACY_PACK_OWNER = "flowsint_legacy_migration"` reserve a deterministic, additive migration pack. `derive_legacy_pack(*, type_names, enricher_names, profiles)` MUST sort and deduplicate its inputs, create version 1 declarations, and use `{"bare_name": name, "kind": kind.value}` as every declaration's canonical definition payload. It MUST NOT normalize a bare name: a name that fails the safe-name rule MUST raise `ValueError` naming the offending bare name.

`canonicalize_bare_reference(kind, bare_name)` MUST be pure and return the corresponding `legacy` `CanonicalSymbol`. `migrate_profile_references(profile)` MUST return a frozen `ProfileMigrationView` with `profile_id`, `profile_revision`, the original `profile.digest()` as `profile_digest`, and `references: tuple[MigratedReference, ...]`. It MUST map entity aliases, relationship semantics, and projection kinds to legacy symbols without mutating or re-deriving the profile snapshot; it MUST assert that the original OBS-1788 digest remains byte-stable.

When a derived legacy pack has projection-facing declarations, its `approval` MUST contain the passed profiles' `(profile_id, revision)` pairs; otherwise it MUST be `None`. Thus AC-303 is additive and subject to AC-301 registry validation and AC-302 approval rather than bypassing either rule.

| Bare surface | `SymbolKind` | Canonical form |
| --- | --- | --- |
| Legacy type class names | `entity_type` | `legacy:entity_type:<name>` |
| Enricher names | `enricher` | `legacy:enricher:<name>` |
| Profile entity aliases | `entity_type` | `legacy:entity_type:<alias>` |
| Relationship semantics | `relationship_semantic` | `legacy:relationship_semantic:<semantic>` |
| `ProjectionKind` values used by profile entity rules | `projection_entity_kind` | `legacy:projection_entity_kind:<value>` |
| B6 adapter schema IDs | `adapter_schema` | Already namespaced under the `obs1788_*` precedent; no bare-name rewrite is implied. |

The migration MUST NOT rewire `TYPE_REGISTRY`, `ENRICHER_REGISTRY`, or `CustomType` paths. They remain fenced under B2.

## 7. Stable-code registry

`DomainPackError` is a `RuntimeError` subclass with stable `code` and `safe_message`. Consumers MUST use the code, not exception text, as the diagnostic contract.

| Stable error code | Trigger condition |
| --- | --- |
| `domain_pack_invalid_namespace` | Registry-level namespace or other registry string input is malformed. |
| `domain_pack_namespace_conflict` | A namespace is associated with more than one owner. |
| `domain_pack_digest_conflict` | The same `(namespace, version)` has different pack digests. |
| `domain_pack_version_downgrade` | Resolution with `minimum_version=`, or `require_approved_pack`, finds the best (approved) version below the requested floor. |
| `domain_pack_missing_dependency` | A declared dependency namespace is absent from the final pack set. |
| `domain_pack_dependency_incompatible` | A dependency is self-referential, duplicated, or has no final-set version in range. |
| `domain_pack_duplicate_symbol` | One pack repeats a `(kind, name)` declaration. |
| `domain_pack_symbol_conflict` | A declaration namespace differs from its pack namespace. |
| `domain_pack_unknown_symbol` | Resolution cannot find the requested namespace, version, kind, or name. |
| `domain_pack_ambiguous_symbol` | Unqualified resolution finds the kind/name in multiple namespaces. |
| `domain_pack_unapproved_projection_extension` | A pack declares `PROJECTION_ENTITY_KIND` or `RELATIONSHIP_SEMANTIC` without approval. |
| `domain_pack_deprecated_symbol` | A deprecated declaration is resolved without explicit opt-in. |

## 8. Acceptance-criteria traceability

| Criterion | Mechanism | Where enforced |
| --- | --- | --- |
| AC-301 | Final-set dependency closure, owner/digest collision rejection, order-independent construction and registration, resolution-time downgrade floors, deterministic versioned resolution, and ambiguity refusal. | §5 collision/compatibility matrix, `DomainPackRegistry`, and `require_approved_pack`. |
| AC-302 | `PackApproval` binds projection-facing declarations to OBS-1788 `(profile_id, profile_revision)` pairs; unapproved projection extensions are rejected. | §4 and §5; `DomainPack` construction. |
| AC-303 | Deterministic version-1 `legacy` pack derivation and frozen reference views preserve original OBS-1788 profile digests while translating bare vocabulary. | §6; `derive_legacy_pack`, `canonicalize_bare_reference`, and `migrate_profile_references`. |

## 9. Non-goals and later barriers

This Proposed B8 contract does not:

- create a domain-pack marketplace or any package-distribution mechanism;
- load untrusted code, modules, plugins, or pack-provided executables;
- create a new evidence store, SQLite table, or database migration;
- change OBS-1788 profile snapshots, canonical JSON, or digests;
- rewire legacy `TYPE_REGISTRY`, `ENRICHER_REGISTRY`, or `CustomType` paths, which remain fenced per B2;
- generalize evidence-keyed projection contracts (OBS-1971); or
- rebuild LadybugDB projection data (OBS-1972).

OBS-1971 evidence-keyed projection contract generalization and OBS-1972 LadybugDB projection rebuild MUST consume this registry when those later barriers are implemented. They MUST NOT treat B8 as authorization to alter legacy registries or OBS-1788 profile identity.
