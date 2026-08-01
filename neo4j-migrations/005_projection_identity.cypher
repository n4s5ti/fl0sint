// 005_projection_identity.cypher
// Immutable projection keys make graph retries safe after SQL-finalize crashes.

CREATE CONSTRAINT projection_entity_key_unique IF NOT EXISTS
FOR (node:ProjectionEntity) REQUIRE node.projection_key IS UNIQUE;

CREATE CONSTRAINT projection_entity_assertion_key_unique IF NOT EXISTS
FOR (node:ProjectionEntityAssertion) REQUIRE node.projection_key IS UNIQUE;

CREATE CONSTRAINT projection_observation_key_unique IF NOT EXISTS
FOR (node:ProjectionObservation) REQUIRE node.projection_key IS UNIQUE;

CREATE CONSTRAINT projection_claim_key_unique IF NOT EXISTS
FOR (node:ProjectionClaim) REQUIRE node.projection_key IS UNIQUE;
