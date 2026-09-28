"""System-owned contracts for durable, approved graph projection."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from hashlib import sha256
import json
import re
from typing import Any, Literal
from unicodedata import normalize


_PROFILE_ID = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_SAFE_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class ProjectionKind(StrEnum):
    """Fixed entity labels supported by the projection graph."""

    PARCEL = "parcel"
    PARTY = "party"
    INSTRUMENT = "instrument"


class ProjectionError(RuntimeError):
    """A projection failure with a safe, stable diagnostic code."""

    def __init__(self, code: str, safe_message: str):
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message


class ProjectionNotApproved(ProjectionError):
    def __init__(self) -> None:
        super().__init__(
            "projection_profile_not_approved",
            "The requested graph projection profile is not approved",
        )


class ProjectionValidationError(ProjectionError):
    """Raised before graph I/O when persisted evidence cannot be projected."""


@dataclass(frozen=True)
class Cardinality:
    minimum: int = 0
    maximum: int | None = None

    def __post_init__(self) -> None:
        if self.minimum < 0 or (self.maximum is not None and self.maximum < self.minimum):
            raise ValueError("Projection cardinality bounds are invalid")

    def validate(self, count: int) -> None:
        if count < self.minimum or (self.maximum is not None and count > self.maximum):
            raise ProjectionValidationError(
                "projection_cardinality_invalid",
                "Persisted evidence does not meet an approved cardinality",
            )


@dataclass(frozen=True)
class ObservationRule:
    """A scalar field emitted as an immutable observation."""

    field_name: str
    value_pointer: str
    value_type: Literal["string", "integer", "number", "boolean"]

    def __post_init__(self) -> None:
        _require_safe_name(self.field_name, "field name")
        _require_pointer(self.value_pointer)


@dataclass(frozen=True)
class EntityRule:
    """Approved source-record identity and scalar fields for one entity kind."""

    alias: str
    kind: ProjectionKind
    items_pointer: str
    source_record_id_pointer: str
    effective_from_pointer: str
    effective_to_pointer: str | None
    observations: tuple[ObservationRule, ...]
    cardinality: Cardinality

    def __post_init__(self) -> None:
        _require_safe_name(self.alias, "entity alias")
        _require_pointer(self.items_pointer)
        _require_pointer(self.source_record_id_pointer)
        _require_pointer(self.effective_from_pointer)
        if self.effective_to_pointer is not None:
            _require_pointer(self.effective_to_pointer)
        if len({rule.field_name for rule in self.observations}) != len(self.observations):
            raise ValueError("Observation field names must be unique within an entity rule")


@dataclass(frozen=True)
class RelationshipRule:
    """Approved assertion semantics between declared entity aliases."""

    rule_id: str
    semantic: str
    items_pointer: str
    from_alias: str
    from_source_record_id_pointer: str
    to_alias: str
    to_source_record_id_pointer: str
    effective_from_pointer: str
    effective_to_pointer: str | None
    cardinality: Cardinality

    def __post_init__(self) -> None:
        _require_safe_name(self.rule_id, "relationship rule ID")
        _require_safe_name(self.semantic, "relationship semantic")
        _require_pointer(self.items_pointer)
        _require_safe_name(self.from_alias, "relationship source alias")
        _require_pointer(self.from_source_record_id_pointer)
        _require_safe_name(self.to_alias, "relationship target alias")
        _require_pointer(self.to_source_record_id_pointer)
        _require_pointer(self.effective_from_pointer)
        if self.effective_to_pointer is not None:
            _require_pointer(self.effective_to_pointer)


@dataclass(frozen=True)
class ApprovedProjectionProfile:
    """An immutable profile authored in system code, never by a template."""

    profile_id: str
    revision: int
    source_id: str
    entities: tuple[EntityRule, ...]
    relationships: tuple[RelationshipRule, ...] = ()

    def __post_init__(self) -> None:
        _require_profile_id(self.profile_id)
        if self.revision < 1:
            raise ValueError("Projection profile revision must be positive")
        if (
            not self.source_id.strip()
            or self.source_id != self.source_id.strip()
            or "\0" in self.source_id
        ):
            raise ValueError("Projection profile source_id is invalid")
        aliases = {rule.alias for rule in self.entities}
        if not aliases or len(aliases) != len(self.entities):
            raise ValueError("Projection profiles need uniquely named entity rules")
        if len({rule.rule_id for rule in self.relationships}) != len(self.relationships):
            raise ValueError("Relationship rule IDs must be unique within a projection profile")
        for relationship in self.relationships:
            if relationship.from_alias not in aliases or relationship.to_alias not in aliases:
                raise ValueError("Relationship rules must reference declared entity aliases")

    def snapshot(self) -> dict[str, Any]:
        """Return the canonical, JSON-safe trusted definition stored with a job."""
        return _profile_snapshot(self)

    def digest(self) -> str:
        return digest_snapshot(self.snapshot())

    @classmethod
    def from_snapshot(cls, snapshot: dict[str, Any]) -> "ApprovedProjectionProfile":
        try:
            entities = tuple(
                EntityRule(
                    alias=item["alias"],
                    kind=ProjectionKind(item["kind"]),
                    items_pointer=item["items_pointer"],
                    source_record_id_pointer=item["source_record_id_pointer"],
                    effective_from_pointer=item["effective_from_pointer"],
                    effective_to_pointer=item.get("effective_to_pointer"),
                    observations=tuple(
                        ObservationRule(
                            field_name=observation["field_name"],
                            value_pointer=observation["value_pointer"],
                            value_type=observation["value_type"],
                        )
                        for observation in item["observations"]
                    ),
                    cardinality=Cardinality(**item["cardinality"]),
                )
                for item in snapshot["entities"]
            )
            relationships = tuple(
                RelationshipRule(
                    rule_id=item["rule_id"],
                    semantic=item["semantic"],
                    items_pointer=item["items_pointer"],
                    from_alias=item["from_alias"],
                    from_source_record_id_pointer=item["from_source_record_id_pointer"],
                    to_alias=item["to_alias"],
                    to_source_record_id_pointer=item["to_source_record_id_pointer"],
                    effective_from_pointer=item["effective_from_pointer"],
                    effective_to_pointer=item.get("effective_to_pointer"),
                    cardinality=Cardinality(**item["cardinality"]),
                )
                for item in snapshot.get("relationships", [])
            )
            return cls(
                profile_id=snapshot["profile_id"],
                revision=snapshot["revision"],
                source_id=snapshot["source_id"],
                entities=entities,
                relationships=relationships,
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ProjectionValidationError(
                "projection_profile_digest_mismatch",
                "The persisted projection profile is invalid",
            ) from error


@dataclass(frozen=True)
class ProjectionBinding:
    """Frozen, approved profile data persisted with each projection job."""

    profile_id: str
    revision: int
    digest: str
    snapshot: dict[str, Any]


class ApprovedProjectionRegistry:
    """Exact-match registry populated only by deployment-owned system code."""

    def __init__(self, profiles: tuple[ApprovedProjectionProfile, ...] = ()) -> None:
        self._profiles = {(profile.profile_id, profile.revision): profile for profile in profiles}
        if len(self._profiles) != len(profiles):
            raise ValueError("Projection registry profile keys must be unique")

    def resolve(self, reference: Any | None) -> ProjectionBinding | None:
        if reference is None:
            return None
        profile_id = getattr(reference, "profile_id", None)
        revision = getattr(reference, "revision", None)
        profile = self._profiles.get((profile_id, revision))
        if profile is None:
            raise ProjectionNotApproved()
        snapshot = profile.snapshot()
        return ProjectionBinding(
            profile_id=profile.profile_id,
            revision=profile.revision,
            digest=digest_snapshot(snapshot),
            snapshot=snapshot,
        )

    def validate_binding(self, binding: ProjectionBinding) -> ApprovedProjectionProfile:
        profile = self._profiles.get((binding.profile_id, binding.revision))
        if profile is None:
            raise ProjectionNotApproved()
        expected_snapshot = profile.snapshot()
        expected_digest = digest_snapshot(expected_snapshot)
        if (
            binding.digest != expected_digest
            or binding.snapshot != expected_snapshot
            or binding.digest != digest_snapshot(binding.snapshot)
        ):
            raise ProjectionValidationError(
                "projection_profile_digest_mismatch",
                "The persisted projection profile does not match its approval",
            )
        return profile


def digest_snapshot(snapshot: dict[str, Any]) -> str:
    return sha256(
        json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()


def stable_entity_key(source_id: str, kind: ProjectionKind, source_record_id: str) -> str:
    """Versioned source-scoped identity; source boundaries are never merged."""
    return _hash_parts("obs1788/entity/v1", source_id, kind.value, source_record_id)


def stable_entity_assertion_key(
    profile_id: str,
    profile_revision: int,
    evidence_record_id: str,
    output_index: int,
    entity_alias: str,
    item_index: int,
    entity_key: str,
) -> str:
    return _hash_parts(
        "obs1788/entity-assertion/v1",
        profile_id,
        str(profile_revision),
        evidence_record_id,
        str(output_index),
        entity_alias,
        str(item_index),
        entity_key,
    )


def stable_observation_key(
    profile_id: str,
    profile_revision: int,
    evidence_record_id: str,
    output_index: int,
    entity_alias: str,
    item_index: int,
    field_name: str,
) -> str:
    return _hash_parts(
        "obs1788/observation/v2",
        profile_id,
        str(profile_revision),
        evidence_record_id,
        str(output_index),
        entity_alias,
        str(item_index),
        field_name,
    )


def stable_claim_key(
    profile_id: str,
    profile_revision: int,
    evidence_record_id: str,
    output_index: int,
    relationship_rule_id: str,
    semantic: str,
    item_index: int,
) -> str:
    return _hash_parts(
        "obs1788/claim/v2",
        profile_id,
        str(profile_revision),
        evidence_record_id,
        str(output_index),
        relationship_rule_id,
        semantic,
        str(item_index),
    )


def canonical_source_record_id(value: Any) -> str:
    if isinstance(value, bool) or value is None:
        raise ProjectionValidationError(
            "projection_identifier_invalid",
            "A projection source record identifier is invalid",
        )
    value = normalize("NFC", str(value)).strip()
    if not value or "\0" in value:
        raise ProjectionValidationError(
            "projection_identifier_invalid",
            "A projection source record identifier is invalid",
        )
    return value


def resolve_pointer(document: Any, pointer: str) -> Any:
    """Resolve an RFC 6901 pointer without permissive flattening."""
    _require_pointer(pointer)
    current = document
    if pointer == "":
        return current
    for encoded_part in pointer[1:].split("/"):
        part = encoded_part.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdecimal() and int(part) < len(current):
            current = current[int(part)]
        else:
            raise ProjectionValidationError(
                "projection_evidence_invalid",
                "Persisted evidence is missing an approved projection field",
            )
    return current


def validate_scalar(value: Any, value_type: str) -> Any:
    valid = (
        (value_type == "string" and isinstance(value, str))
        or (value_type == "integer" and isinstance(value, int) and not isinstance(value, bool))
        or (value_type == "number" and isinstance(value, (int, float)) and not isinstance(value, bool))
        or (value_type == "boolean" and isinstance(value, bool))
    )
    if not valid:
        raise ProjectionValidationError(
            "projection_evidence_invalid",
            "Persisted evidence has an invalid approved projection field type",
        )
    return value


def _profile_snapshot(profile: ApprovedProjectionProfile) -> dict[str, Any]:
    return {
        "profile_id": profile.profile_id,
        "revision": profile.revision,
        "source_id": profile.source_id,
        "entities": [
            {
                "alias": rule.alias,
                "kind": rule.kind.value,
                "items_pointer": rule.items_pointer,
                "source_record_id_pointer": rule.source_record_id_pointer,
                "effective_from_pointer": rule.effective_from_pointer,
                "effective_to_pointer": rule.effective_to_pointer,
                "observations": [
                    {
                        "field_name": observation.field_name,
                        "value_pointer": observation.value_pointer,
                        "value_type": observation.value_type,
                    }
                    for observation in rule.observations
                ],
                "cardinality": asdict(rule.cardinality),
            }
            for rule in profile.entities
        ],
        "relationships": [
            {
                "rule_id": rule.rule_id,
                "semantic": rule.semantic,
                "items_pointer": rule.items_pointer,
                "from_alias": rule.from_alias,
                "from_source_record_id_pointer": rule.from_source_record_id_pointer,
                "to_alias": rule.to_alias,
                "to_source_record_id_pointer": rule.to_source_record_id_pointer,
                "effective_from_pointer": rule.effective_from_pointer,
                "effective_to_pointer": rule.effective_to_pointer,
                "cardinality": asdict(rule.cardinality),
            }
            for rule in profile.relationships
        ],
    }


def _hash_parts(domain: str, *parts: str) -> str:
    return sha256((domain + "\0" + "\0".join(parts)).encode()).hexdigest()


def _require_pointer(pointer: str) -> None:
    if not isinstance(pointer, str) or (pointer and not pointer.startswith("/")):
        raise ValueError("Projection JSON pointers must use RFC 6901 syntax")


def _require_safe_name(value: str, description: str) -> None:
    if not _SAFE_NAME.fullmatch(value):
        raise ValueError(f"Projection {description} is invalid")


def _require_profile_id(value: str) -> None:
    if not _PROFILE_ID.fullmatch(value):
        raise ValueError("Projection profile_id is invalid")
