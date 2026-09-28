"""Fixed-label Neo4j writer for deterministic projection operations."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Protocol

from ..graph.connection import Neo4jConnection
from .contracts import ProjectionKind


class ProjectionGraphWriter(Protocol):
    def execute_write(self, query: str, parameters: dict[str, Any] | None = None) -> Any: ...


class ProjectionGraphError(RuntimeError):
    """Base class for graph-driver failures whose details must not be persisted."""


class ProjectionGraphUnavailable(ProjectionGraphError):
    pass


@dataclass(frozen=True)
class ProjectionEntityOperation:
    projection_key: str
    source_id: str
    entity_kind: ProjectionKind
    source_record_id: str
    ingested_at: str


@dataclass(frozen=True)
class ProjectionEntityAssertionOperation:
    projection_key: str
    entity_key: str
    flow_run_id: str
    step_run_id: str
    evidence_record_id: str
    profile_id: str
    profile_revision: int
    output_index: int
    entity_alias: str
    item_index: int
    provenance_json: str


@dataclass(frozen=True)
class ProjectionObservationOperation:
    projection_key: str
    entity_key: str
    field_name: str
    value: str | int | float | bool
    value_type: str
    effective_from: str
    effective_to: str | None
    flow_run_id: str
    step_run_id: str
    evidence_record_id: str
    profile_id: str
    profile_revision: int
    output_index: int
    entity_alias: str
    item_index: int
    provenance_json: str


@dataclass(frozen=True)
class ProjectionClaimOperation:
    projection_key: str
    from_entity_key: str
    to_entity_key: str
    semantic: str
    relationship_rule_id: str
    effective_from: str
    effective_to: str | None
    flow_run_id: str
    step_run_id: str
    evidence_record_id: str
    profile_id: str
    profile_revision: int
    output_index: int
    relationship_item_index: int
    provenance_json: str


@dataclass(frozen=True)
class ProjectionBatch:
    entities: tuple[ProjectionEntityOperation, ...]
    entity_assertions: tuple[ProjectionEntityAssertionOperation, ...]
    observations: tuple[ProjectionObservationOperation, ...]
    claims: tuple[ProjectionClaimOperation, ...]


class ProjectionGraphRepository:
    """Writes only trusted labels and fixed relationship types in one transaction."""

    def __init__(self, connection: ProjectionGraphWriter | None = None) -> None:
        self._connection = connection or Neo4jConnection.get_instance()

    def write_projection(self, batch: ProjectionBatch) -> None:
        """MERGE deterministic entity, fact, and claim keys without mutable SET +=."""
        try:
            self._connection.execute_write(
                _PROJECTION_WRITE,
                {
                    "entities": [
                        {
                            **asdict(entity),
                            "entity_kind": entity.entity_kind.value,
                        }
                        for entity in batch.entities
                    ],
                    "entity_assertions": [
                        asdict(assertion) for assertion in batch.entity_assertions
                    ],
                    "observations": [asdict(observation) for observation in batch.observations],
                    "claims": [asdict(claim) for claim in batch.claims],
                },
            )
        except ProjectionGraphError:
            raise
        except Exception as error:
            raise ProjectionGraphUnavailable("Projection graph write unavailable") from error


_PROJECTION_WRITE = """
CALL {
    WITH $entities AS entities
    UNWIND entities AS entity
    MERGE (e:ProjectionEntity {projection_key: entity.projection_key})
    ON CREATE SET
        e.source_id = entity.source_id,
        e.entity_kind = entity.entity_kind,
        e.source_record_id = entity.source_record_id,
        e.first_observed_at = entity.ingested_at
    FOREACH (_ IN CASE entity.entity_kind WHEN 'parcel' THEN [1] ELSE [] END |
        SET e:ProjectedParcel
    )
    FOREACH (_ IN CASE entity.entity_kind WHEN 'party' THEN [1] ELSE [] END |
        SET e:ProjectedParty
    )
    FOREACH (_ IN CASE entity.entity_kind WHEN 'instrument' THEN [1] ELSE [] END |
        SET e:ProjectedInstrument
    )
    RETURN count(e) AS entity_count
}
CALL {
    WITH $entity_assertions AS entity_assertions
    UNWIND entity_assertions AS assertion
    MATCH (e:ProjectionEntity {projection_key: assertion.entity_key})
    MERGE (a:ProjectionEntityAssertion {projection_key: assertion.projection_key})
    ON CREATE SET
        a.flow_run_id = assertion.flow_run_id,
        a.step_run_id = assertion.step_run_id,
        a.evidence_record_id = assertion.evidence_record_id,
        a.profile_id = assertion.profile_id,
        a.profile_revision = assertion.profile_revision,
        a.output_index = assertion.output_index,
        a.entity_alias = assertion.entity_alias,
        a.item_index = assertion.item_index,
        a.provenance_json = assertion.provenance_json
    MERGE (a)-[:ASSERTS_ENTITY]->(e)
    RETURN count(a) AS entity_assertion_count
}
CALL {
    WITH $observations AS observations
    UNWIND observations AS observation
    MATCH (e:ProjectionEntity {projection_key: observation.entity_key})
    MERGE (o:ProjectionObservation {projection_key: observation.projection_key})
    ON CREATE SET
        o.field_name = observation.field_name,
        o.value = observation.value,
        o.value_type = observation.value_type,
        o.effective_from = observation.effective_from,
        o.effective_to = observation.effective_to,
        o.flow_run_id = observation.flow_run_id,
        o.step_run_id = observation.step_run_id,
        o.evidence_record_id = observation.evidence_record_id,
        o.profile_id = observation.profile_id,
        o.profile_revision = observation.profile_revision,
        o.output_index = observation.output_index,
        o.entity_alias = observation.entity_alias,
        o.item_index = observation.item_index,
        o.provenance_json = observation.provenance_json
    MERGE (o)-[:OBSERVES]->(e)
    RETURN count(o) AS observation_count
}
CALL {
    WITH $claims AS claims
    UNWIND claims AS claim
    MATCH (from:ProjectionEntity {projection_key: claim.from_entity_key})
    MATCH (to:ProjectionEntity {projection_key: claim.to_entity_key})
    MERGE (c:ProjectionClaim {projection_key: claim.projection_key})
    ON CREATE SET
        c.semantic = claim.semantic,
        c.relationship_rule_id = claim.relationship_rule_id,
        c.effective_from = claim.effective_from,
        c.effective_to = claim.effective_to,
        c.flow_run_id = claim.flow_run_id,
        c.step_run_id = claim.step_run_id,
        c.evidence_record_id = claim.evidence_record_id,
        c.profile_id = claim.profile_id,
        c.profile_revision = claim.profile_revision,
        c.output_index = claim.output_index,
        c.relationship_item_index = claim.relationship_item_index,
        c.provenance_json = claim.provenance_json
    MERGE (c)-[:SUBJECT]->(from)
    MERGE (c)-[:OBJECT]->(to)
    MERGE (from)-[:CLAIMS {projection_key: claim.projection_key}]->(to)
    RETURN count(c) AS claim_count
}
RETURN entity_count, entity_assertion_count, observation_count, claim_count
"""
