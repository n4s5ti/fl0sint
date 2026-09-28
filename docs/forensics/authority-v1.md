---
title: "Forensic authority v1"
description: "Normative authority and migration decision for OBS-1962."
version: "v1"
status: "Accepted"
issue: "OBS-1962"
---

# Forensic authority v1

**Status:** Accepted  
**Contract version:** `v1` (`AUTHORITY_CONTRACT_VERSION`)  
**Runtime module:** `flowsint_core.core.forensics.authority`  
**Scope:** The forensic target only. This contract governs ledger evidence, its rebuildable graph projection, artifact custody, derived views, and the legacy UI canvas. It does not implement LadybugDB, cases, claims tables, or historical-data migration.

The key words **MUST**, **MUST NOT**, **REQUIRED**, **SHOULD**, and **MAY** are normative.

## Decision

SQLite is the sole forensic authority. A forensic answer is admissible only when its lineage resolves to a SQLite ledger record. LadybugDB is a rebuildable, evidence-keyed read model and is not an authority. No Neo4j runtime is a forensic target.

The runtime exposes this decision through `FORENSIC_AUTHORITY` (`AuthorityMatrix`), `AuthoritySystem`, `AuthorityRole`, `AuthorityRule`, `MIGRATION_DECISIONS`, `ForensicLedgerSession`, `create_forensic_ledger_session_factory(bind: Engine) -> Callable[[], ForensicLedgerSession]`, `LedgerLineageError`, and `require_ledger_evidence(session, evidence_envelope_id)`.

## Authority matrix

| Identifier (`AuthoritySystem`) | Role (`AuthorityRole`) | Normative rule |
| --- | --- | --- |
| `sqlite_ledger` | `forensic_authority` | The sole durable forensic record and sole authority for evidence lineage, admissibility, and forensic answers. It MUST retain the immutable identity and lineage metadata for each evidence envelope. |
| `ladybugdb_projection` | `rebuildable_evidence_keyed_read_model` | A deterministic, disposable read model derived from SQLite. It MAY be deleted, rebuilt, stale, unavailable, or incomplete without changing forensic truth. This contract does not claim LadybugDB is implemented. |
| `legacy_neo4j_canvas` | `excluded_from_forensic_target` | A legacy visualization/UI canvas only. It is not the LadybugDB projection, is excluded from the forensic target, and MUST NOT provide forensic authority or an evidence store. |
| `artifact_store` | `evidence_payload_store` | Custody for artifact bytes addressed by ledger metadata. It MUST NOT establish evidence identity, provenance, admissibility, or forensic authority independently of `sqlite_ledger`. |
| `derived_views` | `rebuildable_derived_view` | Disposable reports, caches, exports, and materialized read views. They MUST be reproducible from authoritative inputs or identified as stale/non-authoritative. |

An implementation MUST NOT promote a row, node, edge, cache entry, artifact object, canvas element, or derived view to authority merely because it is available when the ledger is not.

## Immutable evidence-keyed projection

### Required identity

Every projected fact MUST carry the immutable UUID of its SQLite `EvidenceEnvelopeRecord` as `evidence_envelope_id`. That UUID is the current, required forensic identity for the fact. The projection MUST preserve it through rebuilds and MUST NOT replace it with a graph-generated identifier.

A future SQLite claim key MAY be associated with a projected fact when claims are introduced. A claim key is additive only: it MUST reference or accompany `evidence_envelope_id` and MUST NEVER substitute for the envelope UUID as the fact's ledger-evidence identity.

`ForensicLedgerSession` is the required forensic-ledger session type. The only admissible way to create one is `create_forensic_ledger_session_factory(bind: Engine) -> Callable[[], ForensicLedgerSession]`. The factory MUST first require that `bind` is an `Engine` and that its dialect name is the literal string `"sqlite"`; it then records each exact created session object and its exact factory `Engine` in a module-private, lock-protected `WeakKeyDictionary` registry. No capability or token is stored on, supplied to, or accepted from a `Session`. Before a consumer admits a projected fact as forensic, it MUST call `require_ledger_evidence(session, evidence_envelope_id)` with `ForensicLedgerSession | None`. It MUST reject `None` before calling `get_bind()`. The guard builds the `EvidenceEnvelopeRecord` query, resolves `session.get_bind(mapper=EvidenceEnvelopeRecord, clause=statement)`, and MUST require that result to be the registered `Engine` by object identity before a query executes. It then executes only with `session.scalar(statement, bind_arguments={"bind": registered_bind})`; public mapper/table bind redirects MUST be rejected before query. Any generic, unregistered, copied, direct-subclass, or rebound session, including one bound to a separate SQLite database with the same schema and matching UUID row, MUST fail closed before any evidence query or other I/O with `LedgerLineageError` code `forensic_ledger_backend_not_sqlite`. An admissible registered session MUST also fail closed before any evidence query or other I/O when unbound, including when `session.get_bind()` raises `UnboundExecutionError`, or when `session.get_bind().dialect.name` is not the literal string `"sqlite"`, with that same code. The repository's generic and PostgreSQL sessions are not authorized forensic ledger sessions. A missing UUID or graph-only lineage MUST raise `LedgerLineageError` with `forensic_evidence_key_missing`; an absent matching SQLite record MUST raise `LedgerLineageError` with `forensic_evidence_not_found`. A graph-only fact may be displayed as non-forensic UI data, but it MUST NOT be returned or persisted as forensic evidence.

### B1 scope and staged enforcement

B1 governs new forensic consumers only. The current generic `GraphProjectionService`/Neo4j path is legacy and non-forensic; its runtime fence is explicitly OBS-1963/B2, not B1.

The immutable `EvidenceEnvelopeRecord` UUID requirement in this contract is the B1-ratified target. B1 does not claim to enforce append-only persistence: current persistence still permits mutation. Database and write-model append-only enforcement is explicitly OBS-1966/B5.

### Artifact metadata and bytes

The SQLite ledger records immutable evidence-envelope metadata, including the artifact reference and integrity metadata required to identify the evidence. The `artifact_store` holds the corresponding bytes. Artifact bytes without a matching immutable SQLite envelope are not forensic evidence. Conversely, the ledger remains the authority for a recorded envelope even when bytes are unavailable, deleted under policy, or fail integrity verification; such conditions MUST be recorded as availability or integrity state, not repaired by inventing a second evidence store.

## Deterministic rebuild contract

A projection rebuild MUST use only these inputs:

1. A defined SQLite ledger snapshot, including the eligible immutable `EvidenceEnvelopeRecord` rows and lineage/disposition records.
2. The `v1` authority rules and projection schema/version used for that build.
3. Verified artifact metadata from the ledger; bytes MAY be read only when the projection logic requires them and their recorded integrity check succeeds.

The builder MUST select the same eligible ledger records for the same snapshot and order its work deterministically by immutable `evidence_envelope_id` UUID. Within a fact, it MUST use a canonical field and relationship ordering. It MUST NOT depend on existing graph contents, Neo4j/canvas state, cache state, insertion timing, or graph-generated identifiers.

For the same snapshot, authority rules, and projection schema/version, two clean rebuilds are equivalent only if they yield the same set of projected facts and relationships, the same `evidence_envelope_id` on every fact, and the same canonical fact/relationship content. Storage layout, physical record IDs, and non-forensic operational timestamps are not equivalence inputs.

A rebuild MUST publish atomically. A partially constructed projection MUST NOT be advertised as complete or used for forensic answers.

### Deletion and replacement

Evidence envelopes are immutable. A correction or replacement MUST NOT mutate an existing envelope, rewrite its UUID, or overwrite a projected fact in place.

To replace evidence:

1. Ingest a new `EvidenceEnvelopeRecord` with a new immutable UUID and append lineage that identifies the predecessor and the reason for supersession.
2. Retain the predecessor and its history in SQLite.
3. Rebuild the projection from the applicable ledger snapshot; the result carries the old and/or new envelope UUIDs according to the recorded disposition.

To delete artifact bytes under an authorized retention, legal, or security policy:

1. Append the SQLite disposition and the byte-availability/integrity outcome before removing the bytes.
2. Remove bytes only from `artifact_store`; do not delete or alter the evidence envelope, its UUID, digest metadata, or lineage in SQLite.
3. Rebuild or invalidate affected projections and derived views from the ledger state. A view that cannot represent the disposition MUST be marked unavailable, not silently repaired from graph state.

## Stale, partial, and failed projections

- A projection that predates its declared SQLite snapshot is **stale**. It MUST be labeled stale/non-authoritative and MUST NOT be the only basis for a forensic answer.
- A partial rebuild is **incomplete**. It MUST remain unpublished for forensic use; the last complete projection, if retained, is stale rather than authoritative.
- A rebuild failure, deletion, corruption, or unavailability of `ladybugdb_projection` MUST leave SQLite authoritative. Operators MUST rebuild from the defined SQLite snapshot and MUST NOT recover forensic facts from graph-only content, Neo4j, the canvas, or derived views.
- An artifact-byte integrity failure or missing bytes MUST produce the ledger-recorded availability/integrity outcome. It MUST NOT create a new evidence identity, promote a projection, or authorize graph-only recovery.

## Neo4j and legacy canvas exclusion

`legacy_neo4j_canvas` names the legacy UI canvas boundary, not a forensic graph backend. Neo4j is explicitly excluded from the forensic target: it has no required runtime, storage, migration destination, authority role, or recovery role under `v1`.

The legacy canvas MAY render non-forensic visual state. It MUST NOT be described as the LadybugDB projection, hold a duplicate evidence store, serve graph-only facts as evidence, or participate in forensic rebuild/recovery.

## Migration decision record

`MIGRATION_DECISIONS` is immutable. Its decisions for OBS-1782, OBS-1787, and OBS-1788 preserve a single SQLite forensic authority: no decision authorizes historical graph/canvas migration in place or a second forensic evidence authority.

| Issue | Decision | Required migration behavior | Prohibited behavior |
| --- | --- | --- | --- |
| OBS-1782 | `preserve_structured_sqlite_evidence` | Preserve and reuse the durable structured SQLite evidence ledger. New or re-ingested source evidence MUST append new immutable `EvidenceEnvelopeRecord` UUIDs; any projection derives only from SQLite. | Replacing the SQLite ledger; copying ledger evidence into a second forensic store; treating a graph identifier as evidence identity; rewriting history in place. |
| OBS-1787 | `rebuild_from_sqlite_without_neo4j_authority` | Replace or rebuild the legacy projection only from SQLite ledger evidence. Neo4j remains excluded from forensic authority. | Maintaining Neo4j or a graph as authority; backfilling a graph as if it were ledger evidence; in-place historical migration. |
| OBS-1788 | `preserve_registry_egress_boundary` | Preserve the registry-backed connector egress and policy boundary. Connector-derived evidence MUST enter SQLite before it can be projected or viewed. | Direct graph writes; bypassing the registry egress/policy boundary; duplicating evidence stores; using a graph identifier as evidence identity. |

A migration MAY add a projection, metadata, or view only when it has a ledger evidence envelope. It MUST NOT create duplicate stores that can independently answer forensic provenance questions. Historical graph/canvas data remains out of scope; the existing structured SQLite ledger is preserved and reused.

## Open transition boundaries

| Independent review finding | Staged owner | Boundary retained by B1 |
| --- | --- | --- |
| IAR-002 | OBS-1963/B2 | The generic `GraphProjectionService`/Neo4j path remains legacy and non-forensic. B1 does not provide its runtime fence. |
| IAR-003 | OBS-1966/B5 | `EvidenceEnvelopeRecord` UUID immutability is the required target, while persistence/write-model append-only enforcement remains a B5 responsibility. |

## Authority-matrix review checklist

| Acceptance criterion | Review question | Required evidence in this artifact |
| --- | --- | --- |
| AC-101 | Does the contract name SQLite as the sole forensic authority and LadybugDB as a rebuildable read model? | The decision and authority matrix identify `sqlite_ledger` as `forensic_authority` and `ladybugdb_projection` as `rebuildable_evidence_keyed_read_model`. |
| AC-102 | Does every projected fact retain immutable SQLite evidence identity and have a defined rebuild path? | The immutable evidence-keyed projection, deterministic rebuild, deletion/replacement, and stale/partial/failure sections require `EvidenceEnvelopeRecord` UUID lineage and SQLite-only recovery. |
| AC-103 | Does the contract exclude Neo4j from the target while distinguishing the legacy UI canvas from the forensic projection? | The matrix and Neo4j exclusion section define `legacy_neo4j_canvas` as `excluded_from_forensic_target`, exclude Neo4j from the target, and forbid graph/canvas duplicate evidence stores. |

A review passes only when all three criteria are satisfied without treating a non-ledger system as authority. The minimum reversal probe is: delete or corrupt `ladybugdb_projection`, retain SQLite, rebuild the read model from SQLite, and confirm that graph-only output is rejected by the ledger-lineage guard.
