"""B8/OBS-1969 -- Namespaced, versioned domain-pack registry for forensic vocabulary.

The domain-pack layer is the pure contract surface through which forensic
vocabulary (entity types, predicates, relationship semantics, projection
entity kinds, adapter schemas, and enrichers) is declared, versioned, owned,
and -- when it extends the approved projection surface -- explicitly
approved. B4 claim relation/assessment kinds are not pack-extensible in v1:
they remain closed enums owned by the observations layer.

Packs are immutable and content-addressed. The registry is fail-closed and
order-independent (AC-301): multiple versions of one namespace coexist,
default resolution always picks the highest registered version, historical
replay pins an exact version, and a required minimum version fails closed at
resolution time rather than at registration. Dependency closure is checked
over the final registered set and is satisfied when any registered version
of the dependency namespace is in range.

Legacy OBS-1788 vocabulary migrates additively into the single 'legacy'
namespace (AC-303) without rewriting any profile identity, snapshot, or
digest. Exception text never carries raw definition payloads (redaction
discipline) -- only stable codes, namespaces, symbol names, versions, and
profile identifiers.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from typing import Any, Iterable

from flowsint_core.core.projection.contracts import (
    ApprovedProjectionProfile,
    ProjectionKind,
    digest_snapshot,
)

DOMAIN_PACKS_CONTRACT_VERSION = "v1"
LEGACY_NAMESPACE = "legacy"
LEGACY_PACK_OWNER = "flowsint_legacy_migration"

_SAFE_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")

_ERROR_INVALID_NAMESPACE = "domain_pack_invalid_namespace"
_ERROR_NAMESPACE_CONFLICT = "domain_pack_namespace_conflict"
_ERROR_DIGEST_CONFLICT = "domain_pack_digest_conflict"
_ERROR_VERSION_DOWNGRADE = "domain_pack_version_downgrade"
_ERROR_MISSING_DEPENDENCY = "domain_pack_missing_dependency"
_ERROR_DEPENDENCY_INCOMPATIBLE = "domain_pack_dependency_incompatible"
_ERROR_DUPLICATE_SYMBOL = "domain_pack_duplicate_symbol"
_ERROR_SYMBOL_CONFLICT = "domain_pack_symbol_conflict"
_ERROR_UNKNOWN_SYMBOL = "domain_pack_unknown_symbol"
_ERROR_AMBIGUOUS_SYMBOL = "domain_pack_ambiguous_symbol"
_ERROR_UNAPPROVED_PROJECTION_EXTENSION = "domain_pack_unapproved_projection_extension"
_ERROR_DEPRECATED_SYMBOL = "domain_pack_deprecated_symbol"


class DomainPackError(RuntimeError):
    """A safe, stable domain-pack failure with a diagnostic code.

    Messages carry codes and ledger identifiers only -- never raw definition
    payloads.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.safe_message = message


class SymbolKind(StrEnum):
    """Kind of a declared forensic vocabulary symbol.

    B4 claim relation/assessment kinds are not pack-extensible in v1;
    they remain closed enums owned by the observations layer.
    """

    ENTITY_TYPE = "entity_type"
    PREDICATE = "predicate"
    RELATIONSHIP_SEMANTIC = "relationship_semantic"
    PROJECTION_ENTITY_KIND = "projection_entity_kind"
    ADAPTER_SCHEMA = "adapter_schema"
    ENRICHER = "enricher"


_PROJECTION_FACING_KINDS = frozenset(
    (SymbolKind.PROJECTION_ENTITY_KIND, SymbolKind.RELATIONSHIP_SEMANTIC)
)


@dataclass(frozen=True)
class CanonicalSymbol:
    """Fully-qualified identity of one declared forensic vocabulary symbol."""

    namespace: str
    kind: SymbolKind
    name: str

    def __post_init__(self) -> None:
        _require_safe_name(self.namespace, "namespace")
        if not isinstance(self.kind, SymbolKind):
            raise TypeError("kind must be a SymbolKind")
        _require_safe_name(self.name, "name")

    @property
    def canonical(self) -> str:
        return f"{self.namespace}:{self.kind.value}:{self.name}"


@dataclass(frozen=True)
class SymbolDeclaration:
    """One declared vocabulary symbol pinned to its canonical definition."""

    symbol: CanonicalSymbol
    definition_json: str
    definition_digest: str
    introduced_in_version: int
    deprecated: bool = False
    deprecated_reason: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.symbol, CanonicalSymbol):
            raise TypeError("symbol must be a CanonicalSymbol")
        _require_canonical_json(self.definition_json)
        _require_hex64(self.definition_digest, "definition_digest")
        _require_digest_match(self.definition_json, self.definition_digest)
        _require_positive_int(self.introduced_in_version, "introduced_in_version")
        if not isinstance(self.deprecated, bool):
            raise TypeError("deprecated must be a bool")
        if self.deprecated and not (
            isinstance(self.deprecated_reason, str) and self.deprecated_reason.strip()
        ):
            raise ValueError("deprecated_reason is required when deprecated")
        if not self.deprecated and self.deprecated_reason is not None:
            raise ValueError("deprecated_reason is only allowed when deprecated")


@dataclass(frozen=True)
class PackDependency:
    """A required sibling namespace pinned to a version range."""

    namespace: str
    minimum_version: int
    maximum_version: int | None = None

    def __post_init__(self) -> None:
        _require_safe_name(self.namespace, "dependency namespace")
        _require_positive_int(self.minimum_version, "minimum_version")
        if self.maximum_version is not None:
            _require_positive_int(self.maximum_version, "maximum_version")
            if self.maximum_version < self.minimum_version:
                raise ValueError("maximum_version must not precede minimum_version")


@dataclass(frozen=True)
class PackApproval:
    """Approved OBS-1788 projection profiles backing projection-facing symbols."""

    profiles: tuple[tuple[str, int], ...]

    def __post_init__(self) -> None:
        if not isinstance(self.profiles, tuple) or not self.profiles:
            raise ValueError("PackApproval requires at least one approved profile")
        seen: set[tuple[str, int]] = set()
        for pair in self.profiles:
            if (
                not isinstance(pair, tuple)
                or len(pair) != 2
                or not isinstance(pair[0], str)
                or not isinstance(pair[1], int)
                or isinstance(pair[1], bool)
            ):
                raise TypeError("approval profiles must be (profile_id, revision) pairs")
            profile_id, revision = pair
            _require_safe_name(profile_id, "profile_id")
            if revision < 1:
                raise ValueError("profile revision must be >= 1")
            if pair in seen:
                raise ValueError("approval profiles must be unique")
            seen.add(pair)


@dataclass(frozen=True)
class DomainPack:
    """One versioned, owned bundle of declared symbols for one namespace."""

    namespace: str
    owner: str
    version: int
    declarations: tuple[SymbolDeclaration, ...]
    dependencies: tuple[PackDependency, ...] = ()
    approval: PackApproval | None = None

    def __post_init__(self) -> None:
        _require_safe_name(self.namespace, "namespace")
        _require_nonempty_text(self.owner, "owner")
        _require_positive_int(self.version, "version")
        if not isinstance(self.declarations, tuple):
            raise TypeError("declarations must be a tuple of SymbolDeclaration")
        if not isinstance(self.dependencies, tuple):
            raise TypeError("dependencies must be a tuple of PackDependency")
        if self.approval is not None and not isinstance(self.approval, PackApproval):
            raise TypeError("approval must be a PackApproval or None")
        seen: set[tuple[SymbolKind, str]] = set()
        for declaration in self.declarations:
            if not isinstance(declaration, SymbolDeclaration):
                raise TypeError("declarations must contain only SymbolDeclaration")
            if declaration.symbol.namespace != self.namespace:
                raise DomainPackError(
                    _ERROR_SYMBOL_CONFLICT,
                    f"declaration symbol namespace {declaration.symbol.namespace!r} "
                    f"does not match pack namespace {self.namespace!r}",
                )
            key = (declaration.symbol.kind, declaration.symbol.name)
            if key in seen:
                raise DomainPackError(
                    _ERROR_DUPLICATE_SYMBOL,
                    f"pack {self.namespace!r} v{self.version} declares duplicate "
                    f"symbol {declaration.symbol.kind.value}:{declaration.symbol.name!r}",
                )
            seen.add(key)
            if declaration.introduced_in_version > self.version:
                raise ValueError(
                    "introduced_in_version must not exceed the pack version"
                )
            if (
                declaration.symbol.kind in _PROJECTION_FACING_KINDS
                and self.approval is None
            ):
                raise DomainPackError(
                    _ERROR_UNAPPROVED_PROJECTION_EXTENSION,
                    f"pack {self.namespace!r} v{self.version} extends the approved "
                    "projection surface without approval",
                )
        seen_dependencies: set[str] = set()
        for dependency in self.dependencies:
            if not isinstance(dependency, PackDependency):
                raise TypeError("dependencies must contain only PackDependency")
            if dependency.namespace == self.namespace:
                raise DomainPackError(
                    _ERROR_DEPENDENCY_INCOMPATIBLE,
                    f"pack {self.namespace!r} v{self.version} cannot depend on itself",
                )
            if dependency.namespace in seen_dependencies:
                raise DomainPackError(
                    _ERROR_DEPENDENCY_INCOMPATIBLE,
                    f"pack {self.namespace!r} v{self.version} declares duplicate "
                    f"dependency {dependency.namespace!r}",
                )
            seen_dependencies.add(dependency.namespace)

    def snapshot(self) -> dict[str, Any]:
        """Return the canonical, JSON-safe trusted definition of this pack."""
        return _pack_snapshot(self)

    def digest(self) -> str:
        return digest_snapshot(self.snapshot())


def build_symbol_key(namespace: str, kind: SymbolKind, name: str) -> str:
    """Stable content-addressed key for one declared symbol."""
    return _hash_parts("obs1969/symbol/v1", namespace, kind.value, name)


def build_pack_key(namespace: str, version: int, pack_digest: str) -> str:
    """Stable content-addressed key for one registered pack revision."""
    return _hash_parts("obs1969/pack/v1", namespace, str(version), pack_digest)


class DomainPackRegistry:
    """Fail-closed registry of versioned domain packs, order-independent (AC-301).

    Construction and register() share identical set-union semantics: multiple
    versions of one namespace coexist, an older version may always be added
    alongside a newer one (it never changes default resolution, which picks
    the highest registered version), and identical (namespace, version,
    digest) registrations are idempotent no-ops. Dependency closure is
    validated over the final registered set.
    """

    def __init__(self, packs: Iterable[DomainPack]) -> None:
        self._packs: dict[str, dict[int, DomainPack]] = {}
        for pack in packs:
            self._union_add(pack)
        self._check_dependency_closure()

    def _union_add(self, pack: DomainPack) -> None:
        if not isinstance(pack, DomainPack):
            raise TypeError("registry packs must be DomainPack instances")
        versions = self._packs.get(pack.namespace)
        if versions:
            existing_owner = next(iter(versions.values())).owner
            if existing_owner != pack.owner:
                raise DomainPackError(
                    _ERROR_NAMESPACE_CONFLICT,
                    f"namespace {pack.namespace!r} is owned by both "
                    f"{existing_owner!r} and {pack.owner!r}",
                )
            existing = versions.get(pack.version)
            if existing is not None:
                if existing.digest() != pack.digest():
                    raise DomainPackError(
                        _ERROR_DIGEST_CONFLICT,
                        f"namespace {pack.namespace!r} version {pack.version} "
                        "resolves to two different digests",
                    )
                return  # idempotent no-op
        self._packs.setdefault(pack.namespace, {})[pack.version] = pack

    def _check_dependency_closure(self) -> None:
        for pack in self._all_packs():
            for dependency in pack.dependencies:
                versions = self._packs.get(dependency.namespace)
                if not versions:
                    raise DomainPackError(
                        _ERROR_MISSING_DEPENDENCY,
                        f"pack {pack.namespace!r} v{pack.version} depends on "
                        f"unregistered namespace {dependency.namespace!r}",
                    )
                maximum = dependency.maximum_version
                if not any(
                    dependency.minimum_version <= version
                    and (maximum is None or version <= maximum)
                    for version in versions
                ):
                    raise DomainPackError(
                        _ERROR_DEPENDENCY_INCOMPATIBLE,
                        f"pack {pack.namespace!r} v{pack.version} dependency on "
                        f"{dependency.namespace!r} is unsatisfiable by any "
                        "registered version",
                    )

    def _all_packs(self) -> tuple[DomainPack, ...]:
        return tuple(
            self._packs[namespace][version]
            for namespace in sorted(self._packs)
            for version in sorted(self._packs[namespace])
        )

    def register(self, pack: DomainPack) -> None:
        """Union one pack into the registry with construction-identical semantics."""
        before = {
            namespace: dict(versions)
            for namespace, versions in self._packs.items()
        }
        self._union_add(pack)
        try:
            self._check_dependency_closure()
        except DomainPackError:
            self._packs.clear()
            self._packs.update(before)
            raise

    def resolve(
        self,
        namespace: str,
        kind: SymbolKind,
        name: str,
        *,
        version: int | None = None,
        minimum_version: int | None = None,
        allow_deprecated: bool = False,
    ) -> SymbolDeclaration:
        """Resolve one declared symbol, defaulting to the highest pack version."""
        if version is not None and minimum_version is not None:
            raise ValueError("version and minimum_version are mutually exclusive")
        if not isinstance(namespace, str) or not _SAFE_NAME.fullmatch(namespace):
            raise DomainPackError(
                _ERROR_INVALID_NAMESPACE,
                f"namespace {namespace!r} must match ^[a-z][a-z0-9_]{0,63}$",
            )
        if not isinstance(name, str) or not _SAFE_NAME.fullmatch(name):
            raise DomainPackError(
                _ERROR_INVALID_NAMESPACE,
                f"symbol name {name!r} must match ^[a-z][a-z0-9_]{0,63}$",
            )
        versions = self._packs.get(namespace)
        if not versions:
            raise DomainPackError(
                _ERROR_UNKNOWN_SYMBOL,
                f"no registered packs for namespace {namespace!r}",
            )
        best_version = max(versions)
        if minimum_version is not None and best_version < minimum_version:
            raise DomainPackError(
                _ERROR_VERSION_DOWNGRADE,
                f"namespace {namespace!r} best available version {best_version} "
                f"is below required minimum {minimum_version}",
            )
        target = best_version if version is None else version
        pack = versions.get(target)
        if pack is None:
            raise DomainPackError(
                _ERROR_UNKNOWN_SYMBOL,
                f"no registered version {target} of namespace {namespace!r}",
            )
        for declaration in pack.declarations:
            if (
                declaration.symbol.kind is kind
                and declaration.symbol.name == name
            ):
                if declaration.deprecated and not allow_deprecated:
                    raise DomainPackError(
                        _ERROR_DEPRECATED_SYMBOL,
                        f"symbol {kind.value}:{name!r} in namespace {namespace!r} "
                        "is deprecated",
                    )
                return declaration
        raise DomainPackError(
            _ERROR_UNKNOWN_SYMBOL,
            f"symbol {kind.value}:{name!r} is not declared in namespace {namespace!r}",
        )

    def resolve_unqualified(
        self,
        kind: SymbolKind,
        name: str,
        *,
        allow_deprecated: bool = False,
    ) -> SymbolDeclaration:
        """Resolve a symbol declared in exactly one namespace, else fail closed."""
        if not isinstance(name, str) or not _SAFE_NAME.fullmatch(name):
            raise DomainPackError(
                _ERROR_INVALID_NAMESPACE,
                f"symbol name {name!r} must match ^[a-z][a-z0-9_]{0,63}$",
            )
        hits: dict[str, SymbolDeclaration] = {}
        for namespace in sorted(self._packs):
            pack = self._packs[namespace][max(self._packs[namespace])]
            for declaration in pack.declarations:
                if (
                    declaration.symbol.kind is kind
                    and declaration.symbol.name == name
                ):
                    hits[namespace] = declaration
                    break
        if not hits:
            raise DomainPackError(
                _ERROR_UNKNOWN_SYMBOL,
                f"symbol {kind.value}:{name!r} is not declared in any namespace",
            )
        if len(hits) > 1:
            raise DomainPackError(
                _ERROR_AMBIGUOUS_SYMBOL,
                f"symbol {kind.value}:{name!r} is declared in more than one "
                "namespace",
            )
        declaration = next(iter(hits.values()))
        if declaration.deprecated and not allow_deprecated:
            raise DomainPackError(
                _ERROR_DEPRECATED_SYMBOL,
                f"symbol {kind.value}:{name!r} in namespace "
                f"{declaration.symbol.namespace!r} is deprecated",
            )
        return declaration

    def packs(self) -> tuple[DomainPack, ...]:
        """All registered packs, deterministically sorted by namespace/version."""
        return self._all_packs()


def require_approved_pack(
    registry: DomainPackRegistry,
    namespace: str,
    *,
    profile_id: str,
    profile_revision: int,
    minimum_version: int | None = None,
) -> DomainPack:
    """Return the highest pack version approved for the given profile.

    Fails closed with domain_pack_unapproved_projection_extension when no
    registered version of the namespace is approved for the profile, and
    with domain_pack_version_downgrade when the best approved version is
    below the required minimum.
    """
    if not isinstance(registry, DomainPackRegistry):
        raise TypeError("registry must be a DomainPackRegistry")
    if not isinstance(namespace, str) or not _SAFE_NAME.fullmatch(namespace):
        raise DomainPackError(
            _ERROR_INVALID_NAMESPACE,
            f"namespace {namespace!r} must match ^[a-z][a-z0-9_]{0,63}$",
        )
    _require_safe_name(profile_id, "profile_id")
    _require_positive_int(profile_revision, "profile_revision")
    if minimum_version is not None:
        _require_positive_int(minimum_version, "minimum_version")
    approved: dict[int, DomainPack] = {}
    for version, pack in registry._packs.get(namespace, {}).items():
        if pack.approval is not None and any(
            pair[0] == profile_id and pair[1] == profile_revision
            for pair in pack.approval.profiles
        ):
            approved[version] = pack
    if not approved:
        raise DomainPackError(
            _ERROR_UNAPPROVED_PROJECTION_EXTENSION,
            f"no pack of namespace {namespace!r} is approved for profile "
            f"{profile_id!r} revision {profile_revision}",
        )
    best = max(approved)
    if minimum_version is not None and best < minimum_version:
        raise DomainPackError(
            _ERROR_VERSION_DOWNGRADE,
            f"best approved version {best} of namespace {namespace!r} is below "
            f"required minimum {minimum_version}",
        )
    return approved[best]


def canonicalize_bare_reference(kind: SymbolKind, bare_name: str) -> CanonicalSymbol:
    """Qualify a bare legacy vocabulary name into the legacy namespace (pure)."""
    return CanonicalSymbol(namespace=LEGACY_NAMESPACE, kind=kind, name=bare_name)


def derive_legacy_pack(
    *,
    type_names: Iterable[str],
    enricher_names: Iterable[str],
    profiles: Iterable[ApprovedProjectionProfile],
) -> DomainPack:
    """Deterministically wrap bare legacy vocabulary into the version-1 legacy pack.

    Additive (AC-303): type_names become ENTITY_TYPE declarations, enricher
    names become ENRICHER declarations, and each profile contributes its
    entity aliases (ENTITY_TYPE), relationship semantics
    (RELATIONSHIP_SEMANTIC), and entity-kind values
    (PROJECTION_ENTITY_KIND). Identical bare names from multiple sources
    dedupe exactly; the pack carries a PackApproval over the passed profiles
    exactly when projection-facing declarations exist.
    """

    def declare(kind: SymbolKind, bare_name: object) -> None:
        if not isinstance(bare_name, str) or not _SAFE_NAME.fullmatch(bare_name):
            raise ValueError(
                f"legacy bare name {bare_name!r} does not match "
                "^[a-z][a-z0-9_]{0,63}$"
            )
        symbol = canonicalize_bare_reference(kind, bare_name)
        declarations[symbol] = _canonical_json(
            {"bare_name": bare_name, "kind": kind.value}
        )

    declarations: dict[CanonicalSymbol, str] = {}
    profiles = tuple(profiles)
    for name in type_names:
        declare(SymbolKind.ENTITY_TYPE, name)
    for name in enricher_names:
        declare(SymbolKind.ENRICHER, name)
    for profile in profiles:
        for entity in profile.entities:
            declare(SymbolKind.ENTITY_TYPE, entity.alias)
            declare(SymbolKind.PROJECTION_ENTITY_KIND, entity.kind.value)
        for relationship in profile.relationships:
            declare(SymbolKind.RELATIONSHIP_SEMANTIC, relationship.semantic)

    profile_pairs = tuple(
        sorted({(profile.profile_id, profile.revision) for profile in profiles})
    )
    has_projection_facing = any(
        symbol.kind in _PROJECTION_FACING_KINDS for symbol in declarations
    )
    approval = (
        PackApproval(profiles=profile_pairs)
        if profile_pairs and has_projection_facing
        else None
    )
    ordered = sorted(
        declarations.items(), key=lambda item: (item[0].kind.value, item[0].name)
    )
    return DomainPack(
        namespace=LEGACY_NAMESPACE,
        owner=LEGACY_PACK_OWNER,
        version=1,
        declarations=tuple(
            SymbolDeclaration(
                symbol=symbol,
                definition_json=definition,
                definition_digest=sha256(definition.encode()).hexdigest(),
                introduced_in_version=1,
            )
            for symbol, definition in ordered
        ),
        approval=approval,
    )


@dataclass(frozen=True)
class MigratedReference:
    """One bare legacy reference mapped to its legacy-namespace symbol."""

    kind: SymbolKind
    bare_name: str
    symbol: CanonicalSymbol


@dataclass(frozen=True)
class ProfileMigrationView:
    """Preserved identity and legacy-symbol mapping of one migrated profile."""

    profile_id: str
    profile_revision: int
    profile_digest: str
    references: tuple[MigratedReference, ...]


def migrate_profile_references(
    profile: ApprovedProjectionProfile,
) -> ProfileMigrationView:
    """Map a profile's bare references to legacy symbols without touching it.

    Additive (AC-303): the view carries the profile's original digest
    byte-stable; the profile snapshot is never mutated or re-derived.
    """
    references: dict[tuple[SymbolKind, str], MigratedReference] = {}
    for entity in profile.entities:
        references[(SymbolKind.ENTITY_TYPE, entity.alias)] = MigratedReference(
            kind=SymbolKind.ENTITY_TYPE,
            bare_name=entity.alias,
            symbol=canonicalize_bare_reference(SymbolKind.ENTITY_TYPE, entity.alias),
        )
        references[
            (SymbolKind.PROJECTION_ENTITY_KIND, entity.kind.value)
        ] = MigratedReference(
            kind=SymbolKind.PROJECTION_ENTITY_KIND,
            bare_name=entity.kind.value,
            symbol=canonicalize_bare_reference(
                SymbolKind.PROJECTION_ENTITY_KIND, entity.kind.value
            ),
        )
    for relationship in profile.relationships:
        references[
            (SymbolKind.RELATIONSHIP_SEMANTIC, relationship.semantic)
        ] = MigratedReference(
            kind=SymbolKind.RELATIONSHIP_SEMANTIC,
            bare_name=relationship.semantic,
            symbol=canonicalize_bare_reference(
                SymbolKind.RELATIONSHIP_SEMANTIC, relationship.semantic
            ),
        )
    ordered = tuple(
        references[key]
        for key in sorted(references, key=lambda item: (item[0].value, item[1]))
    )
    return ProfileMigrationView(
        profile_id=profile.profile_id,
        profile_revision=profile.revision,
        profile_digest=profile.digest(),
        references=ordered,
    )


# ── Helpers ───────────────────────────────────────────────────────────────────


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _pack_snapshot(pack: DomainPack) -> dict[str, Any]:
    return {
        "namespace": pack.namespace,
        "owner": pack.owner,
        "version": pack.version,
        "declarations": [
            {
                "symbol": {
                    "namespace": declaration.symbol.namespace,
                    "kind": declaration.symbol.kind.value,
                    "name": declaration.symbol.name,
                },
                "definition_json": declaration.definition_json,
                "definition_digest": declaration.definition_digest,
                "introduced_in_version": declaration.introduced_in_version,
                "deprecated": declaration.deprecated,
                "deprecated_reason": declaration.deprecated_reason,
            }
            for declaration in pack.declarations
        ],
        "dependencies": [
            {
                "namespace": dependency.namespace,
                "minimum_version": dependency.minimum_version,
                "maximum_version": dependency.maximum_version,
            }
            for dependency in pack.dependencies
        ],
        "approval": (
            {
                "profiles": [
                    {"profile_id": pair[0], "revision": pair[1]}
                    for pair in pack.approval.profiles
                ]
            }
            if pack.approval is not None
            else None
        ),
    }


def _hash_parts(domain: str, *parts: str) -> str:
    return sha256((domain + "\0" + "\0".join(parts)).encode()).hexdigest()


def _require_safe_name(value: object, description: str) -> None:
    if not isinstance(value, str) or not _SAFE_NAME.fullmatch(value):
        raise ValueError(f"{description} must match ^[a-z][a-z0-9_]{0,63}$")


def _require_hex64(value: object, name: str) -> None:
    if not isinstance(value, str) or not _HEX64.fullmatch(value):
        raise ValueError(f"{name} must be a 64-char hex string")


def _require_positive_int(value: object, description: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{description} must be a positive integer")


def _require_nonempty_text(value: object, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} is required")


def _require_canonical_json(value: object) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError("definition_json must be a non-empty JSON string")
    try:
        parsed = json.loads(value)
    except (ValueError, TypeError) as error:
        raise ValueError("definition_json must be valid JSON") from error
    if value != _canonical_json(parsed):
        raise ValueError(
            "definition_json must be canonical JSON"
            " (sorted keys, compact separators, ascii-escaped)"
        )


def _require_digest_match(value_json: str, digest: str) -> None:
    computed = sha256(value_json.encode()).hexdigest()
    if digest != computed:
        raise ValueError("definition_digest does not match sha256 of definition_json")
