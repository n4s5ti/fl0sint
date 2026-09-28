"""B8/OBS-1969 -- Domain-contract tests for the namespaced domain-pack registry.

Covers the CanonicalSymbol/SymbolDeclaration/PackDependency/PackApproval/
DomainPack validators, the content-addressed symbol/pack key builders, the
fail-closed order-independent DomainPackRegistry (construction conflicts,
register() set-union semantics, dependency closure, qualified and
unqualified resolution with version pins and minimum-version downgrades,
require_approved_pack), and the additive legacy migration surface
(AC-301/AC-303): deterministic derive_legacy_pack,
canonicalize_bare_reference, and migrate_profile_references digest
preservation.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from flowsint_core.core.forensics import (
    LEGACY_NAMESPACE,
    LEGACY_PACK_OWNER,
    CanonicalSymbol,
    DomainPack,
    DomainPackError,
    DomainPackRegistry,
    MigratedReference,
    PackApproval,
    PackDependency,
    ProfileMigrationView,
    SymbolDeclaration,
    SymbolKind,
    build_pack_key,
    build_symbol_key,
    canonicalize_bare_reference,
    derive_legacy_pack,
    migrate_profile_references,
    require_approved_pack,
)
from flowsint_core.core.projection.contracts import (
    ApprovedProjectionProfile,
    Cardinality,
    EntityRule,
    ObservationRule,
    ProjectionKind,
    RelationshipRule,
)

HEX = frozenset("0123456789abcdef")


def canonical_json(value: dict) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def digest(value_json: str) -> str:
    return hashlib.sha256(value_json.encode()).hexdigest()


def make_symbol(
    namespace: str = "alpha",
    kind: SymbolKind = SymbolKind.ENTITY_TYPE,
    name: str = "party",
) -> CanonicalSymbol:
    return CanonicalSymbol(namespace=namespace, kind=kind, name=name)


def make_declaration(
    kind: SymbolKind = SymbolKind.ENTITY_TYPE,
    name: str = "party",
    namespace: str = "alpha",
    *,
    introduced_in_version: int = 1,
    deprecated: bool = False,
    deprecated_reason: str | None = None,
) -> SymbolDeclaration:
    definition = canonical_json({"bare_name": name, "kind": kind.value})
    return SymbolDeclaration(
        symbol=make_symbol(namespace=namespace, kind=kind, name=name),
        definition_json=definition,
        definition_digest=digest(definition),
        introduced_in_version=introduced_in_version,
        deprecated=deprecated,
        deprecated_reason=deprecated_reason,
    )


def make_pack(
    namespace: str = "alpha",
    owner: str = "owner_a",
    version: int = 1,
    symbols: tuple[tuple[SymbolKind, str], ...] = (
        (SymbolKind.ENTITY_TYPE, "party"),
    ),
    dependencies: tuple[PackDependency, ...] = (),
    approval: PackApproval | None = None,
) -> DomainPack:
    return DomainPack(
        namespace=namespace,
        owner=owner,
        version=version,
        declarations=tuple(
            make_declaration(
                kind=kind, name=name, namespace=namespace, introduced_in_version=version
            )
            for kind, name in symbols
        ),
        dependencies=dependencies,
        approval=approval,
    )


def make_legacy_profile() -> ApprovedProjectionProfile:
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


# ── enum / error / contract constants ────────────────────────────────────────


def test_symbol_kinds_are_stable_contract_strings() -> None:
    assert {kind.value for kind in SymbolKind} == {
        "entity_type",
        "predicate",
        "relationship_semantic",
        "projection_entity_kind",
        "adapter_schema",
        "enricher",
    }


def test_contract_version_is_stable() -> None:
    from flowsint_core.core.forensics.domain_packs import DOMAIN_PACKS_CONTRACT_VERSION

    assert DOMAIN_PACKS_CONTRACT_VERSION == "v1"


def test_domain_pack_error_carries_stable_code_and_safe_message() -> None:
    error = DomainPackError("domain_pack_unknown_symbol", "safe message")
    assert isinstance(error, RuntimeError)
    assert error.code == "domain_pack_unknown_symbol"
    assert error.safe_message == "safe message"


# ── key builders ─────────────────────────────────────────────────────────────


def test_symbol_key_is_deterministic_hex64_and_domain_prefixed() -> None:
    key = build_symbol_key("alpha", SymbolKind.ENTITY_TYPE, "party")
    assert all(char in HEX for char in key)
    assert key == build_symbol_key("alpha", SymbolKind.ENTITY_TYPE, "party")
    assert key == hashlib.sha256(
        b"obs1969/symbol/v1\x00alpha\x00entity_type\x00party"
    ).hexdigest()


def test_pack_key_is_deterministic_hex64_and_domain_prefixed() -> None:
    pack_digest = "a" * 64
    key = build_pack_key("alpha", 1, pack_digest)
    assert all(char in HEX for char in key)
    assert key == build_pack_key("alpha", 1, pack_digest)
    assert key == hashlib.sha256(
        b"obs1969/pack/v1\x00alpha\x001\x00" + pack_digest.encode()
    ).hexdigest()


@pytest.mark.parametrize(
    "change",
    [
        {"namespace": "beta"},
        {"kind": SymbolKind.PREDICATE},
        {"name": "parcel"},
    ],
)
def test_symbol_key_is_sensitive_to_every_part(change: dict) -> None:
    base = {"namespace": "alpha", "kind": SymbolKind.ENTITY_TYPE, "name": "party"}
    assert build_symbol_key(**base) != build_symbol_key(**{**base, **change})


@pytest.mark.parametrize(
    "change",
    [
        {"namespace": "beta"},
        {"version": 2},
        {"pack_digest": "b" * 64},
    ],
)
def test_pack_key_is_sensitive_to_every_part(change: dict) -> None:
    base = {"namespace": "alpha", "version": 1, "pack_digest": "a" * 64}
    assert build_pack_key(**base) != build_pack_key(**{**base, **change})


def test_key_domains_are_distinct() -> None:
    symbol_key = build_symbol_key("alpha", SymbolKind.ENTITY_TYPE, "party")
    pack_key = build_pack_key("alpha", 1, "a" * 64)
    assert symbol_key != pack_key


# ── CanonicalSymbol validation ───────────────────────────────────────────────


def test_canonical_symbol_rejects_invalid_namespace_and_name() -> None:
    with pytest.raises(ValueError, match="namespace"):
        make_symbol(namespace="Bad")
    with pytest.raises(ValueError, match="name"):
        make_symbol(name="party-two")
    with pytest.raises(TypeError, match="kind"):
        make_symbol(kind="entity_type")  # type: ignore[arg-type]


def test_canonical_symbol_identity_includes_kind() -> None:
    as_type = make_symbol(name="party", kind=SymbolKind.ENTITY_TYPE)
    as_predicate = make_symbol(name="party", kind=SymbolKind.PREDICATE)
    assert as_type != as_predicate
    assert as_type.canonical == "alpha:entity_type:party"
    assert as_predicate.canonical == "alpha:predicate:party"


# ── SymbolDeclaration validation ─────────────────────────────────────────────


def test_declaration_recomputes_and_enforces_definition_digest() -> None:
    definition = canonical_json({"bare_name": "party", "kind": "entity_type"})
    with pytest.raises(ValueError, match="does not match sha256"):
        SymbolDeclaration(
            symbol=make_symbol(),
            definition_json=definition,
            definition_digest="a" * 64,
            introduced_in_version=1,
        )


def test_declaration_rejects_non_canonical_definition_json() -> None:
    non_canonical = '{"kind":"entity_type","bare_name":"party"}'  # unsorted keys
    with pytest.raises(ValueError, match="canonical JSON"):
        SymbolDeclaration(
            symbol=make_symbol(),
            definition_json=non_canonical,
            definition_digest=digest(non_canonical),
            introduced_in_version=1,
        )


def test_declaration_rejects_invalid_json_and_digest_format() -> None:
    definition = canonical_json({"bare_name": "party", "kind": "entity_type"})
    with pytest.raises(ValueError, match="valid JSON"):
        SymbolDeclaration(
            symbol=make_symbol(),
            definition_json=": not json",
            definition_digest=digest(": not json"),
            introduced_in_version=1,
        )
    with pytest.raises(ValueError, match="64-char hex"):
        SymbolDeclaration(
            symbol=make_symbol(),
            definition_json=definition,
            definition_digest="z" * 64,
            introduced_in_version=1,
        )


def test_declaration_rejects_invalid_introduced_version() -> None:
    definition = canonical_json({"bare_name": "party", "kind": "entity_type"})
    with pytest.raises(ValueError, match="introduced_in_version"):
        SymbolDeclaration(
            symbol=make_symbol(),
            definition_json=definition,
            definition_digest=digest(definition),
            introduced_in_version=0,
        )


def test_declaration_requires_deprecated_reason_iff_deprecated() -> None:
    definition = canonical_json({"bare_name": "party", "kind": "entity_type"})
    with pytest.raises(ValueError, match="deprecated_reason is required"):
        SymbolDeclaration(
            symbol=make_symbol(),
            definition_json=definition,
            definition_digest=digest(definition),
            introduced_in_version=1,
            deprecated=True,
        )
    with pytest.raises(ValueError, match="only allowed when deprecated"):
        SymbolDeclaration(
            symbol=make_symbol(),
            definition_json=definition,
            definition_digest=digest(definition),
            introduced_in_version=1,
            deprecated_reason="why",
        )


# ── PackDependency validation ────────────────────────────────────────────────


def test_pack_dependency_validates_namespace_and_range() -> None:
    with pytest.raises(ValueError, match="dependency namespace"):
        PackDependency("Bad", 1)
    with pytest.raises(ValueError, match="minimum_version"):
        PackDependency("beta", 0)
    with pytest.raises(ValueError, match="maximum_version"):
        PackDependency("beta", 3, maximum_version=2)
    assert PackDependency("beta", 3, maximum_version=None).maximum_version is None


# ── PackApproval validation ──────────────────────────────────────────────────


def test_pack_approval_rejects_empty_profiles() -> None:
    with pytest.raises(ValueError, match="at least one"):
        PackApproval(profiles=())


def test_pack_approval_rejects_invalid_profile_id_and_revision() -> None:
    with pytest.raises(ValueError, match="profile_id"):
        PackApproval(profiles=(("Bad", 1),))
    with pytest.raises(ValueError, match="revision"):
        PackApproval(profiles=(("legacy_profile", 0),))


def test_pack_approval_rejects_duplicate_profile_pairs() -> None:
    with pytest.raises(ValueError, match="unique"):
        PackApproval(profiles=(("legacy_profile", 2), ("legacy_profile", 2)))


# ── DomainPack validation ────────────────────────────────────────────────────


def test_pack_rejects_duplicate_symbol_within_pack() -> None:
    with pytest.raises(DomainPackError) as error:
        make_pack(
            symbols=(
                (SymbolKind.ENTITY_TYPE, "party"),
                (SymbolKind.ENTITY_TYPE, "party"),
            )
        )
    assert error.value.code == "domain_pack_duplicate_symbol"


def test_pack_rejects_declaration_namespace_conflict() -> None:
    with pytest.raises(DomainPackError) as error:
        DomainPack(
            namespace="alpha",
            owner="owner_a",
            version=1,
            declarations=(make_declaration(namespace="beta"),),
        )
    assert error.value.code == "domain_pack_symbol_conflict"


def test_pack_rejects_unapproved_projection_extension_without_approval() -> None:
    for kind in (SymbolKind.PROJECTION_ENTITY_KIND, SymbolKind.RELATIONSHIP_SEMANTIC):
        with pytest.raises(DomainPackError) as error:
            make_pack(symbols=((kind, "owns"),))
        assert error.value.code == "domain_pack_unapproved_projection_extension"


def test_pack_accepts_projection_extension_with_approval() -> None:
    pack = make_pack(
        symbols=(
            (SymbolKind.PROJECTION_ENTITY_KIND, "party"),
            (SymbolKind.RELATIONSHIP_SEMANTIC, "owns"),
        ),
        approval=PackApproval(profiles=(("legacy_profile", 2),)),
    )
    assert pack.approval is not None
    assert pack.digest() == digest(canonical_json(pack.snapshot()))


def test_pack_rejects_declaration_introduced_after_pack_version() -> None:
    with pytest.raises(ValueError, match="introduced_in_version"):
        DomainPack(
            namespace="alpha",
            owner="owner_a",
            version=1,
            declarations=(make_declaration(introduced_in_version=2),),
        )


def test_pack_rejects_self_dependency() -> None:
    with pytest.raises(DomainPackError) as error:
        make_pack(dependencies=(PackDependency("alpha", 1),))
    assert error.value.code == "domain_pack_dependency_incompatible"


def test_pack_rejects_duplicate_dependency_namespaces() -> None:
    with pytest.raises(DomainPackError) as error:
        make_pack(
            dependencies=(
                PackDependency("beta", 1),
                PackDependency("beta", 2),
            )
        )
    assert error.value.code == "domain_pack_dependency_incompatible"


def test_pack_rejects_invalid_version_and_owner() -> None:
    with pytest.raises(ValueError, match="version"):
        make_pack(version=0)
    with pytest.raises(ValueError, match="owner"):
        make_pack(owner="   ")


def test_pack_snapshot_is_json_safe_and_digest_stable() -> None:
    first = make_pack()
    second = make_pack()
    assert first.snapshot() == second.snapshot()
    assert first.digest() == second.digest()
    assert first.digest() == digest(canonical_json(first.snapshot()))
    assert json.loads(json.dumps(first.snapshot())) == first.snapshot()


# ── DomainPackRegistry construction conflicts ────────────────────────────────


def test_registry_rejects_namespace_owner_conflict() -> None:
    with pytest.raises(DomainPackError) as error:
        DomainPackRegistry(
            [
                make_pack(owner="owner_a", version=1),
                make_pack(owner="owner_b", version=2),
            ]
        )
    assert error.value.code == "domain_pack_namespace_conflict"


def test_registry_rejects_digest_conflict_for_same_version() -> None:
    first = make_pack(version=1, symbols=((SymbolKind.ENTITY_TYPE, "party"),))
    divergent = make_pack(version=1, symbols=((SymbolKind.ENTITY_TYPE, "parcel"),))
    with pytest.raises(DomainPackError) as error:
        DomainPackRegistry([first, divergent])
    assert error.value.code == "domain_pack_digest_conflict"


def test_registry_dedupes_identical_digest_idempotently() -> None:
    pack = make_pack()
    registry = DomainPackRegistry([pack, make_pack()])
    assert registry.packs() == (pack,)


# ── register() set-union semantics ───────────────────────────────────────────


def test_register_allows_older_version_alongside_newer() -> None:
    v1 = make_pack(version=1, symbols=((SymbolKind.ENTITY_TYPE, "party"),))
    v2 = make_pack(
        version=2,
        symbols=(
            (SymbolKind.ENTITY_TYPE, "party"),
            (SymbolKind.ENTITY_TYPE, "parcel"),
        ),
    )
    registry = DomainPackRegistry([v2])
    registry.register(v1)
    assert registry.packs() == (v1, v2)
    assert (
        registry.resolve("alpha", SymbolKind.ENTITY_TYPE, "party").introduced_in_version
        == 2
    )
    assert (
        registry.resolve("alpha", SymbolKind.ENTITY_TYPE, "party", version=1)
        .introduced_in_version
        == 1
    )


def test_register_rejects_equal_version_with_different_digest() -> None:
    first = make_pack(version=1, symbols=((SymbolKind.ENTITY_TYPE, "party"),))
    divergent = make_pack(version=1, symbols=((SymbolKind.ENTITY_TYPE, "parcel"),))
    registry = DomainPackRegistry([first])
    with pytest.raises(DomainPackError) as error:
        registry.register(divergent)
    assert error.value.code == "domain_pack_digest_conflict"
    assert registry.packs() == (first,)


def test_register_is_idempotent_for_identical_digest() -> None:
    pack = make_pack()
    registry = DomainPackRegistry([pack])
    registry.register(pack)
    registry.register(make_pack())  # equal construction -> equal digest
    assert registry.packs() == (pack,)


def test_register_rejects_owner_mismatch() -> None:
    registry = DomainPackRegistry([make_pack(owner="owner_a")])
    with pytest.raises(DomainPackError) as error:
        registry.register(make_pack(owner="owner_b", version=2))
    assert error.value.code == "domain_pack_namespace_conflict"
    assert registry.packs() == (make_pack(owner="owner_a"),)


def test_register_rejects_dependency_closure_violation() -> None:
    registry = DomainPackRegistry([make_pack(namespace="beta")])
    alpha = make_pack(dependencies=(PackDependency("beta", 2),))
    with pytest.raises(DomainPackError) as error:
        registry.register(alpha)
    assert error.value.code == "domain_pack_dependency_incompatible"
    assert registry.packs() == (make_pack(namespace="beta"),)  # rolled back


# ── order independence (AC-301) ──────────────────────────────────────────────


def test_registry_is_order_independent_across_construction_and_register() -> None:
    v1 = make_pack(version=1, symbols=((SymbolKind.ENTITY_TYPE, "party"),))
    v2 = make_pack(
        version=2,
        symbols=(
            (SymbolKind.ENTITY_TYPE, "party"),
            (SymbolKind.ENTITY_TYPE, "parcel"),
        ),
    )
    forward = DomainPackRegistry([v1, v2])
    backward = DomainPackRegistry([v2, v1])
    sequential = DomainPackRegistry([v1])
    sequential.register(v2)
    sequential_backward = DomainPackRegistry([v2])
    sequential_backward.register(v1)
    expected = (v1, v2)
    for registry in (forward, backward, sequential, sequential_backward):
        assert registry.packs() == expected
        assert (
            registry.resolve("alpha", SymbolKind.ENTITY_TYPE, "party")
            .introduced_in_version
            == 2
        )
        assert (
            registry.resolve("alpha", SymbolKind.ENTITY_TYPE, "party", version=1)
            .introduced_in_version
            == 1
        )


# ── dependency closure ───────────────────────────────────────────────────────


def test_registry_rejects_missing_dependency_namespace() -> None:
    alpha = make_pack(dependencies=(PackDependency("beta", 1),))
    with pytest.raises(DomainPackError) as error:
        DomainPackRegistry([alpha])
    assert error.value.code == "domain_pack_missing_dependency"


def test_registry_rejects_dependency_below_minimum() -> None:
    beta = make_pack(namespace="beta")
    alpha = make_pack(dependencies=(PackDependency("beta", 2),))
    with pytest.raises(DomainPackError) as error:
        DomainPackRegistry([alpha, beta])
    assert error.value.code == "domain_pack_dependency_incompatible"


def test_registry_rejects_dependency_above_maximum() -> None:
    beta = make_pack(namespace="beta", version=5)
    alpha = make_pack(dependencies=(PackDependency("beta", 1, maximum_version=3),))
    with pytest.raises(DomainPackError) as error:
        DomainPackRegistry([alpha, beta])
    assert error.value.code == "domain_pack_dependency_incompatible"


def test_dependency_closure_satisfied_by_any_registered_version() -> None:
    beta_v1 = make_pack(namespace="beta", version=1)
    beta_v5 = make_pack(namespace="beta", version=5)
    alpha = make_pack(dependencies=(PackDependency("beta", 2, maximum_version=6),))
    registry = DomainPackRegistry([alpha, beta_v1, beta_v5])
    assert len(registry.packs()) == 3


def test_registry_rejects_dependency_with_no_version_in_range() -> None:
    beta_v1 = make_pack(namespace="beta", version=1)
    beta_v5 = make_pack(namespace="beta", version=5)
    alpha = make_pack(dependencies=(PackDependency("beta", 2, maximum_version=4),))
    with pytest.raises(DomainPackError) as error:
        DomainPackRegistry([alpha, beta_v1, beta_v5])
    assert error.value.code == "domain_pack_dependency_incompatible"


# ── qualified resolution ─────────────────────────────────────────────────────


def test_resolution_defaults_to_highest_version() -> None:
    v1 = make_pack(version=1, symbols=((SymbolKind.ENTITY_TYPE, "party"),))
    v2 = make_pack(
        version=2,
        symbols=(
            (SymbolKind.ENTITY_TYPE, "party"),
            (SymbolKind.ENTITY_TYPE, "parcel"),
        ),
    )
    registry = DomainPackRegistry([v1, v2])
    assert (
        registry.resolve("alpha", SymbolKind.ENTITY_TYPE, "parcel").introduced_in_version
        == 2
    )
    assert (
        registry.resolve("alpha", SymbolKind.ENTITY_TYPE, "party").introduced_in_version
        == 2
    )


def test_resolution_pins_exact_version_for_historical_replay() -> None:
    v1 = make_pack(version=1, symbols=((SymbolKind.ENTITY_TYPE, "party"),))
    v2 = make_pack(version=2, symbols=((SymbolKind.ENTITY_TYPE, "parcel"),))
    registry = DomainPackRegistry([v1, v2])
    assert (
        registry.resolve("alpha", SymbolKind.ENTITY_TYPE, "party", version=1)
        .introduced_in_version
        == 1
    )
    with pytest.raises(DomainPackError) as error:
        registry.resolve("alpha", SymbolKind.ENTITY_TYPE, "parcel", version=1)
    assert error.value.code == "domain_pack_unknown_symbol"


def test_resolution_rejects_unknown_namespace_symbol_and_version() -> None:
    registry = DomainPackRegistry([make_pack()])
    with pytest.raises(DomainPackError) as error:
        registry.resolve("nope", SymbolKind.ENTITY_TYPE, "party")
    assert error.value.code == "domain_pack_unknown_symbol"
    with pytest.raises(DomainPackError) as error:
        registry.resolve("alpha", SymbolKind.ENTITY_TYPE, "nope")
    assert error.value.code == "domain_pack_unknown_symbol"
    with pytest.raises(DomainPackError) as error:
        registry.resolve("alpha", SymbolKind.ENTITY_TYPE, "party", version=7)
    assert error.value.code == "domain_pack_unknown_symbol"


def test_resolution_rejects_invalid_namespace_and_name_inputs() -> None:
    registry = DomainPackRegistry([make_pack()])
    with pytest.raises(DomainPackError) as error:
        registry.resolve("Bad", SymbolKind.ENTITY_TYPE, "party")
    assert error.value.code == "domain_pack_invalid_namespace"
    with pytest.raises(DomainPackError) as error:
        registry.resolve("alpha", SymbolKind.ENTITY_TYPE, "bad-name")
    assert error.value.code == "domain_pack_invalid_namespace"


def test_resolution_requires_version_and_minimum_version_mutually_exclusive() -> None:
    registry = DomainPackRegistry([make_pack()])
    with pytest.raises(ValueError, match="mutually exclusive"):
        registry.resolve(
            "alpha",
            SymbolKind.ENTITY_TYPE,
            "party",
            version=1,
            minimum_version=1,
        )


def test_resolution_fails_closed_on_minimum_version_downgrade() -> None:
    registry = DomainPackRegistry([make_pack(version=1)])
    with pytest.raises(DomainPackError) as error:
        registry.resolve("alpha", SymbolKind.ENTITY_TYPE, "party", minimum_version=2)
    assert error.value.code == "domain_pack_version_downgrade"
    assert (
        registry.resolve("alpha", SymbolKind.ENTITY_TYPE, "party", minimum_version=1)
        .symbol.name
        == "party"
    )


def test_resolution_gates_deprecated_symbols() -> None:
    retired = make_declaration(
        name="retired", deprecated=True, deprecated_reason="superseded"
    )
    pack = DomainPack(
        namespace="alpha",
        owner="owner_a",
        version=1,
        declarations=(make_declaration(), retired),
    )
    registry = DomainPackRegistry([pack])
    with pytest.raises(DomainPackError) as error:
        registry.resolve("alpha", SymbolKind.ENTITY_TYPE, "retired")
    assert error.value.code == "domain_pack_deprecated_symbol"
    resolved = registry.resolve(
        "alpha", SymbolKind.ENTITY_TYPE, "retired", allow_deprecated=True
    )
    assert resolved.symbol.name == "retired"
    assert (
        registry.resolve("alpha", SymbolKind.ENTITY_TYPE, "party").symbol.name
        == "party"
    )


# ── unqualified resolution ───────────────────────────────────────────────────


def test_unqualified_resolution_finds_unique_namespace() -> None:
    alpha = make_pack(symbols=((SymbolKind.ENTITY_TYPE, "party"),))
    beta = make_pack(
        namespace="beta",
        owner="owner_b",
        symbols=((SymbolKind.ENTITY_TYPE, "parcel"),),
    )
    registry = DomainPackRegistry([alpha, beta])
    assert (
        registry.resolve_unqualified(SymbolKind.ENTITY_TYPE, "party").symbol.namespace
        == "alpha"
    )
    assert (
        registry.resolve_unqualified(SymbolKind.ENTITY_TYPE, "parcel").symbol.namespace
        == "beta"
    )


def test_unqualified_resolution_is_ambiguous_across_namespaces() -> None:
    alpha = make_pack(symbols=((SymbolKind.ENTITY_TYPE, "party"),))
    beta = make_pack(
        namespace="beta",
        owner="owner_b",
        symbols=((SymbolKind.ENTITY_TYPE, "party"),),
    )
    registry = DomainPackRegistry([alpha, beta])
    with pytest.raises(DomainPackError) as error:
        registry.resolve_unqualified(SymbolKind.ENTITY_TYPE, "party")
    assert error.value.code == "domain_pack_ambiguous_symbol"


def test_unqualified_resolution_reports_unknown_symbol() -> None:
    registry = DomainPackRegistry([make_pack()])
    with pytest.raises(DomainPackError) as error:
        registry.resolve_unqualified(SymbolKind.ENTITY_TYPE, "nope")
    assert error.value.code == "domain_pack_unknown_symbol"


def test_unqualified_resolution_respects_deprecated_gate() -> None:
    retired = make_declaration(
        name="retired", deprecated=True, deprecated_reason="superseded"
    )
    pack = DomainPack(
        namespace="alpha",
        owner="owner_a",
        version=1,
        declarations=(retired,),
    )
    registry = DomainPackRegistry([pack])
    with pytest.raises(DomainPackError) as error:
        registry.resolve_unqualified(SymbolKind.ENTITY_TYPE, "retired")
    assert error.value.code == "domain_pack_deprecated_symbol"
    assert (
        registry.resolve_unqualified(
            SymbolKind.ENTITY_TYPE, "retired", allow_deprecated=True
        ).symbol.name
        == "retired"
    )


# ── require_approved_pack ────────────────────────────────────────────────────


def _approved_registry() -> DomainPackRegistry:
    v1 = make_pack(
        version=1,
        symbols=((SymbolKind.PROJECTION_ENTITY_KIND, "party"),),
        approval=PackApproval(profiles=(("legacy_profile", 2),)),
    )
    v2 = make_pack(
        version=2,
        symbols=((SymbolKind.PROJECTION_ENTITY_KIND, "party"),),
        approval=PackApproval(
            profiles=(("legacy_profile", 2), ("other_profile", 1))
        ),
    )
    return DomainPackRegistry([v1, v2])


def test_require_approved_pack_returns_highest_approved_version() -> None:
    pack = require_approved_pack(
        _approved_registry(), "alpha", profile_id="legacy_profile", profile_revision=2
    )
    assert pack.version == 2


def test_require_approved_pack_picks_highest_version_covering_the_profile() -> None:
    v1 = make_pack(
        version=1,
        symbols=((SymbolKind.PROJECTION_ENTITY_KIND, "party"),),
        approval=PackApproval(profiles=(("legacy_profile", 2),)),
    )
    v2 = make_pack(
        version=2,
        symbols=((SymbolKind.PROJECTION_ENTITY_KIND, "party"),),
        approval=PackApproval(profiles=(("other_profile", 1),)),
    )
    registry = DomainPackRegistry([v1, v2])
    pack = require_approved_pack(
        registry, "alpha", profile_id="legacy_profile", profile_revision=2
    )
    assert pack.version == 1


def test_require_approved_pack_rejects_unapproved_profile() -> None:
    registry = _approved_registry()
    with pytest.raises(DomainPackError) as error:
        require_approved_pack(
            registry, "alpha", profile_id="unapproved", profile_revision=1
        )
    assert error.value.code == "domain_pack_unapproved_projection_extension"
    with pytest.raises(DomainPackError) as error:
        require_approved_pack(
            registry, "absent", profile_id="legacy_profile", profile_revision=2
        )
    assert error.value.code == "domain_pack_unapproved_projection_extension"


def test_require_approved_pack_rejects_below_minimum_version() -> None:
    registry = _approved_registry()
    with pytest.raises(DomainPackError) as error:
        require_approved_pack(
            registry,
            "alpha",
            profile_id="legacy_profile",
            profile_revision=2,
            minimum_version=3,
        )
    assert error.value.code == "domain_pack_version_downgrade"
    pack = require_approved_pack(
        registry,
        "alpha",
        profile_id="legacy_profile",
        profile_revision=2,
        minimum_version=2,
    )
    assert pack.version == 2


# ── legacy migration: derive_legacy_pack (AC-303) ────────────────────────────


def test_derive_legacy_pack_is_deterministic_across_input_order() -> None:
    profile = make_legacy_profile()
    first = derive_legacy_pack(
        type_names=["party", "parcel", "instrument"],
        enricher_names=["web", "registry"],
        profiles=[profile],
    )
    second = derive_legacy_pack(
        type_names=["parcel", "instrument", "party"],
        enricher_names=["registry", "web"],
        profiles=[profile],
    )
    assert first.snapshot() == second.snapshot()
    assert first.digest() == second.digest()


def test_derive_legacy_pack_dedupes_identical_bare_names_from_multiple_sources() -> None:
    profile = make_legacy_profile()
    pack = derive_legacy_pack(
        type_names=["party", "parcel"],
        enricher_names=[],
        profiles=[profile],
    )
    entity_types = [
        declaration
        for declaration in pack.declarations
        if declaration.symbol.kind is SymbolKind.ENTITY_TYPE
    ]
    names = sorted(declaration.symbol.name for declaration in entity_types)
    assert names == ["parcel", "party"]  # aliases and type_names dedupe exactly
    assert all(
        declaration.definition_json
        == canonical_json(
            {"bare_name": declaration.symbol.name, "kind": "entity_type"}
        )
        for declaration in entity_types
    )


def test_derive_legacy_pack_rejects_regex_violating_bare_name() -> None:
    profile = make_legacy_profile()
    with pytest.raises(ValueError) as error:
        derive_legacy_pack(
            type_names=["bad-name"], enricher_names=[], profiles=[profile]
        )
    assert "bad-name" in str(error.value)


def test_derive_legacy_pack_approval_iff_projection_facing_declarations() -> None:
    with_profiles = derive_legacy_pack(
        type_names=["party"],
        enricher_names=[],
        profiles=[make_legacy_profile()],
    )
    assert with_profiles.approval == PackApproval(
        profiles=(("legacy_profile", 2),)
    )
    without = derive_legacy_pack(
        type_names=["party"], enricher_names=["web"], profiles=[]
    )
    assert without.approval is None
    assert without.digest() == digest(canonical_json(without.snapshot()))


def test_derive_legacy_pack_accepts_single_pass_profiles_iterable() -> None:
    materialized = derive_legacy_pack(
        type_names=["party"],
        enricher_names=["web"],
        profiles=[make_legacy_profile()],
    )
    from_generators = derive_legacy_pack(
        type_names=(name for name in ["party"]),
        enricher_names=(name for name in ["web"]),
        profiles=(profile for profile in [make_legacy_profile()]),
    )
    assert from_generators.approval == PackApproval(
        profiles=(("legacy_profile", 2),)
    )
    assert from_generators.snapshot() == materialized.snapshot()
    assert from_generators.digest() == materialized.digest()


def test_legacy_pack_carries_fixed_identity_and_version() -> None:
    pack = derive_legacy_pack(
        type_names=["party"],
        enricher_names=["web"],
        profiles=[make_legacy_profile()],
    )
    assert pack.namespace == LEGACY_NAMESPACE == "legacy"
    assert pack.owner == LEGACY_PACK_OWNER == "flowsint_legacy_migration"
    assert pack.version == 1
    assert all(
        declaration.introduced_in_version == 1 for declaration in pack.declarations
    )
    assert all(
        declaration.symbol.namespace == LEGACY_NAMESPACE
        for declaration in pack.declarations
    )
    assert all(
        declaration.definition_digest == digest(declaration.definition_json)
        for declaration in pack.declarations
    )
    kinds = {declaration.symbol.kind for declaration in pack.declarations}
    assert kinds == {
        SymbolKind.ENTITY_TYPE,
        SymbolKind.ENRICHER,
        SymbolKind.PROJECTION_ENTITY_KIND,
        SymbolKind.RELATIONSHIP_SEMANTIC,
    }


def test_legacy_pack_registers_and_resolves_in_registry() -> None:
    pack = derive_legacy_pack(
        type_names=["party"],
        enricher_names=["web"],
        profiles=[make_legacy_profile()],
    )
    registry = DomainPackRegistry([pack])
    resolved = registry.resolve("legacy", SymbolKind.ENTITY_TYPE, "party")
    assert resolved.symbol.canonical == "legacy:entity_type:party"
    assert (
        registry.resolve_unqualified(
            SymbolKind.RELATIONSHIP_SEMANTIC, "owns"
        ).symbol.namespace
        == LEGACY_NAMESPACE
    )


# ── legacy migration: canonicalize_bare_reference / migrate_profile_references ─


def test_canonicalize_bare_reference_qualifies_into_legacy_namespace() -> None:
    symbol = canonicalize_bare_reference(SymbolKind.ENTITY_TYPE, "party")
    assert symbol == CanonicalSymbol(
        namespace=LEGACY_NAMESPACE, kind=SymbolKind.ENTITY_TYPE, name="party"
    )
    assert symbol.canonical == "legacy:entity_type:party"
    assert canonicalize_bare_reference(
        SymbolKind.ENTITY_TYPE, "party"
    ) == canonicalize_bare_reference(SymbolKind.ENTITY_TYPE, "party")


def test_migrate_profile_references_preserves_digest_and_maps_all_bare_references() -> None:
    profile = make_legacy_profile()
    original_digest = profile.digest()
    view = migrate_profile_references(profile)
    assert isinstance(view, ProfileMigrationView)
    assert view.profile_id == "legacy_profile"
    assert view.profile_revision == 2
    assert view.profile_digest == original_digest
    assert profile.digest() == original_digest  # migration never mutates or re-derives
    covered = {(reference.kind, reference.bare_name) for reference in view.references}
    assert (SymbolKind.ENTITY_TYPE, "party") in covered
    assert (SymbolKind.ENTITY_TYPE, "parcel") in covered
    assert (SymbolKind.PROJECTION_ENTITY_KIND, "party") in covered
    assert (SymbolKind.PROJECTION_ENTITY_KIND, "parcel") in covered
    assert (SymbolKind.RELATIONSHIP_SEMANTIC, "owns") in covered
    for reference in view.references:
        assert isinstance(reference, MigratedReference)
        assert reference.symbol == canonicalize_bare_reference(
            reference.kind, reference.bare_name
        )
        assert reference.symbol.namespace == LEGACY_NAMESPACE


def test_migrate_profile_references_is_deterministic() -> None:
    profile = make_legacy_profile()
    first = migrate_profile_references(profile)
    second = migrate_profile_references(profile)
    assert first == second
    keys = [
        (reference.kind.value, reference.bare_name) for reference in first.references
    ]
    assert keys == sorted(keys)
