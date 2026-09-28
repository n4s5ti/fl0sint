---
title: "Legacy execution fencing v1"
description: "Accepted OBS-1963 decision record for the forensic legacy-execution boundary."
version: "v1"
status: "Accepted"
issue: "OBS-1963"
---

# Legacy execution fencing v1

**Status:** Accepted — independently audited against AC-104/105/106.  
**Contract version:** `v1`  
**Runtime module:** `flowsint_core.core.forensics.fencing`  
**Scope:** Barrier 2 (B2) only. This record fences legacy mutable Neo4j/canvas execution from forensic-mode consumers and defines the sole admission boundary for legacy-derived material.

The key words **MUST**, **MUST NOT**, **REQUIRED**, **SHOULD**, and **MAY** are normative.

## Decision

B2 distinguishes the existing legacy canvas from the forensic target without changing legacy behavior by default:

- `LegacyExecutionMode.LEGACY_CANVAS` is the omitted/default mode. Existing callers retain their legacy canvas behavior.
- `LegacyExecutionMode.FORENSIC_CASE` is a forensic consumer context. It MUST NOT read from, write to, construct, or otherwise touch a legacy Neo4j/canvas execution path.
- A legacy-derived value becomes admissible to a forensic consumer only after it has been newly captured as a structured SQLite `EvidenceEnvelopeRecord` and is admitted through `admit_structured_legacy_evidence`.
- `forensic_case_scope(case_reference)` is the B2 case-admission mechanism. While its context is active, the effective mode is `FORENSIC_CASE` for legacy graph access; an explicit `LEGACY_CANVAS` argument MUST NOT downgrade that effective mode.

The fence is a boundary, not a migration. It does not make Neo4j, the legacy UI canvas, or any legacy mutable record forensic authority. Under B1, SQLite remains the sole forensic authority and an admissible ledger record is resolved only by `require_ledger_evidence(session, evidence_envelope_id)`.

## Stable concepts and dispositions

`flowsint_core.core.forensics.fencing` defines the following stable B2 concepts:

| Concept | Decision-record meaning |
| --- | --- |
| `LegacyExecutionMode` | Execution context: `LEGACY_CANVAS` or `FORENSIC_CASE`. |
| `LegacyPathDisposition` | Inventory classification: `FENCED`, `STRUCTURED_REINGESTION_REQUIRED`, or `OUT_OF_SCOPE`. |
| `LegacyPathInventoryEntry` / `LEGACY_PATH_INVENTORY_V1` | Frozen, bounded V1 inventory of the paths below and their disposition/rationale. |
| `LegacyFenceDecision` | Frozen decision describing an allowed legacy-canvas use or a fenced forensic-case request. |
| `LegacyExecutionFenced` | Stable, safe exception exposing `.decision` for a denied legacy execution request. |
| `require_legacy_graph_access(mode, operation, case_reference=None)` | Read/write fence for legacy graph access. |
| `forensic_case_scope(case_reference)` | Context-scoped case-admission mechanism that makes the effective legacy-execution mode non-downgradably `FORENSIC_CASE`. |
| `StructuredLegacyReingestionRequest` | Frozen request identifying an already-created structured evidence-envelope UUID. It does not carry a graph node or graph key. |
| `StructuredLegacyReingestionAdmission` | Frozen, read-only admission result containing only the authoritative ledger evidence UUID. |
| `admit_structured_legacy_evidence(session, request)` | Read-only admission path that resolves the evidence UUID through B1 `require_ledger_evidence`. |

### Execution-mode and case-admission matrix

| Execution mode | Case reference | Legacy graph/canvas read | Legacy graph/canvas write or construction | Forensic admission result |
| --- | --- | --- | --- | --- |
| `LEGACY_CANVAS` outside `forensic_case_scope` | Absent or legacy UI context | Allowed by the existing legacy path | Allowed by the existing legacy path | No forensic admission is implied. |
| `LEGACY_CANVAS` outside `forensic_case_scope` | Present | Allowed only as legacy behavior; the value remains non-forensic | Allowed only as legacy behavior | A case reference does not convert a legacy result into evidence. Re-ingestion is still required. |
| `FORENSIC_CASE` entered by `forensic_case_scope` | Required for a case-scoped request | Denied before legacy repository or canvas access | Denied before legacy repository or canvas access | Only a separately captured, existing structured SQLite envelope may be considered through the re-ingestion protocol. |
| Explicit `LEGACY_CANVAS` while `forensic_case_scope` is active | Present in the active scope | Denied; the active case scope sets the effective mode | Denied; the active case scope sets the effective mode | No admission. An explicit legacy mode cannot downgrade the forensic-case fence. |
| `FORENSIC_CASE` | Missing | Denied; a missing reference never selects a legacy fallback | Denied; a missing reference never selects a legacy fallback | No admission. The caller must supply an eligible, existing structured envelope to the admission API. |

`FORENSIC_CASE` is not a permission or authorization decision. B2 uses it only to prevent a forensic consumer from traversing a legacy mutable path. Authorization policy, case lifecycle, and directives remain outside this record.

## Read/write fence

Every legacy graph/canvas operation, whether it would read, write, construct a repository, queue a legacy execution, or mutate legacy state, MUST call `require_legacy_graph_access` before that action. For `FORENSIC_CASE`, the guard MUST fail closed with `LegacyExecutionFenced` and the exact safe denial code:

```text
legacy_execution_forensic_case_fenced
```

The failure MUST be safe to return or log: it identifies the denied boundary without exposing connection settings, graph node identifiers, Cypher, serialized legacy payloads, or provider credentials. The decision MAY retain the requested operation and case reference only as redacted, non-authorizing diagnostic context.

The direct `GraphService` constructor and `create_graph_service` factory MUST apply this check before constructing or touching a Neo4j repository. This ordering is load-bearing: a forensic denial has no Neo4j connection, query, mutation, queueing, or other legacy execution side effect.

`forensic_case_scope(case_reference)` applies the fence to pre-existing `GraphService` instances as well as newly constructed services. Per-operation graph-service guards MUST use the effective scoped mode, so an explicit `LEGACY_CANVAS` mode cannot bypass an active forensic-case scope. Legacy API routes invoked outside that scope remain legacy-only; their graph-derived input or output still requires structured re-ingestion before any forensic use.

## Structured legacy re-ingestion protocol

Re-ingestion is the only path by which material that originated from a legacy workflow can be presented to a forensic consumer. It is not graph import, graph adoption, or a graph-to-ledger conversion.

1. A collector captures the material again as a **new** structured SQLite `EvidenceEnvelopeRecord`; it MUST NOT point the request at a pre-existing legacy graph node, edge, element ID, canvas object, cache entry, scan detail, or task result.
2. Before admission, the referenced SQLite row MUST already exist and satisfy the structured provenance contract: nonempty `destination_id`, `endpoint_id`, and `policy_version`; `capability` exactly `enrich.read`; and `source` exactly `connector:<destination_id>:<endpoint_id>`. It MUST also have nonempty `source_rights`, a canonical lowercase 64-hex `artifact_sha256`, and `artifact_reference` exactly `body:sha256:<same digest>`. A graph/canvas/arbitrary locator or source value is not provenance or an artifact reference and MUST be denied as provenance-invalid or artifact-invalid, respectively.
3. The caller supplies a `StructuredLegacyReingestionRequest` that identifies that evidence-envelope UUID. The request contains no legacy node/key adoption field.
4. `admit_structured_legacy_evidence` first resolves that UUID through B1 `require_ledger_evidence(session, evidence_envelope_id)`. B1 therefore requires a registered `ForensicLedgerSession` produced by `create_forensic_ledger_session_factory`, bound to SQLite, and rejects non-ledger or unregistered sessions before evidence query I/O.
5. The admission then verifies the required structured-row fields. On success it returns `StructuredLegacyReingestionAdmission` containing **only** the ledger `evidence_envelope_id` UUID.

An admission is read-only. It MUST NOT create, update, delete, attach, mutate, query, or otherwise adopt any legacy graph/canvas object. It MUST NOT use a legacy graph node ID, edge ID, element ID, key, or mutable canvas state as forensic identity or lineage. **Graph node adoption and graph mutation are forbidden.**

`admit_structured_legacy_evidence` invokes B1 `require_ledger_evidence` as required, but it translates any resulting `LedgerLineageError` to the B2-safe `structured_legacy_reingestion_evidence_unavailable` denial. B2 re-ingestion denials use only these stable, safe codes:

- `structured_legacy_reingestion_request_invalid`
- `structured_legacy_reingestion_evidence_unavailable`
- `structured_legacy_reingestion_provenance_missing`
- `structured_legacy_reingestion_provenance_invalid`
- `structured_legacy_reingestion_rights_missing`
- `structured_legacy_reingestion_artifact_missing`
- `structured_legacy_reingestion_artifact_invalid`

These codes identify only the failed admission category; they MUST NOT disclose graph identifiers, artifact content, credentials, or internal storage details.

A dirty, deleted, or pending evidence instance is ineligible for re-ingestion and MUST be denied without flushing, expunging, rolling back, or otherwise discarding caller-owned session state.

## Bounded legacy-path inventory V1

This inventory is exhaustive for the B2 boundary. A classification describes the forensic disposition; it does not remove or rewrite the legacy default behavior.

| Surface | Legacy role | Disposition | B2 requirement and rationale |
| --- | --- | --- | --- |
| `GraphService(...)` direct construction | Direct legacy graph service access | `FENCED` | `FORENSIC_CASE` MUST be rejected before the injected repository is touched. `LEGACY_CANVAS` remains the default. |
| `create_graph_service(...)` factory | Neo4j repository construction path | `FENCED` | `FORENSIC_CASE` MUST be rejected before `Neo4jGraphRepository` is constructed or touched. |
| `Neo4jGraphRepository` constructor and every public method | Central legacy Neo4j choke point | `FENCED` | `legacy_execution_class_boundary` guards construction plus every public read, write, batch, list-mutation, and flush method before singleton connection, query, write, or batch/list mutation. This catches direct and future un-inventoried legacy-repository callers. |
| `POST /enrichers/{enricher_name}/launch` / `dispatch_legacy_task` | Sanctioned legacy API publication reads Neo4j element IDs and publishes legacy enrichment | `STRUCTURED_REINGESTION_REQUIRED` | The sanctioned route publication uses `dispatch_legacy_task` and is guarded for an active forensic-case scope, so a forensic caller cannot enqueue it. Outside that scope it remains legacy-only; its graph-derived inputs and results require separately captured and admitted structured SQLite evidence. Scope context does not propagate as authorization across the queue. |
| `LegacyExecutionTask.apply_async` (including `.delay`) | Reusable sanctioned task-publication boundary | `FENCED` | The `apply_async` guard MUST reject an active forensic-case scope before broker publication; `.delay` cannot bypass it. |
| `run_enricher` / `Enricher.execute` | Legacy asynchronous enricher execution | `STRUCTURED_REINGESTION_REQUIRED` | The `run_enricher` task body is guarded for an active forensic-case scope. Queue context does not authorize a task; legacy execution output, scan state, and serialized values cannot be admitted directly. Re-capture as a new structured envelope first. |
| Raw broker injection | Unsanctioned task-message injection | `OUT_OF_SCOPE` | It is not authorization and is outside the sanctioned application API; B2 does not describe it as a way to obtain forensic execution or admission. |
| Flow API canvas routes (including flow create/update/delete/launch) | Legacy mutable flow-canvas control surface | `FENCED` | A forensic-case consumer MUST NOT use these routes to read, mutate, or launch legacy canvas execution. Existing legacy-canvas use remains unchanged. |
| `FlowService` mutation/launch methods / `run_flow` | Legacy flow schema, launch service, and task body | `FENCED` | A forensic-case caller MUST be stopped at the boundary rather than mutating or executing the legacy flow model; the `run_flow` task body is separately guarded. |
| Sketch API canvas routes / `update_sketch_timestamp` publication | Legacy mutable sketch/graph control surface and timestamp scheduling | `FENCED` | A forensic-case consumer MUST NOT retrieve, add, relate, delete, or schedule a legacy sketch update. The guard runs before `BackgroundTasks.add_task`; the timestamp task body is separately guarded. |
| `SketchService` graph and sketch mutation methods | Legacy sketch/canvas service, including graph operations | `FENCED` | A forensic-case caller MUST be stopped before Neo4j/canvas reads, writes, and cleanup. |
| Investigation API CRUD routes | Legacy mutable investigation control surface | `FENCED` | In a forensic-case scope, public read/create/delete paths MUST reject before permission checks, repository access, or commit. |
| `InvestigationService` public methods and factory, including direct delete | Legacy investigation service and graph-cleanup path | `FENCED` | Every public method and factory is guarded before permission checks, repository access, commit, or Neo4j graph cleanup. The direct delete wrapper rejects before its body, so it cannot remap or bypass the canonical fence. |
| `ProjectionGraphRepository` | Approved evidence-keyed projection path | `OUT_OF_SCOPE` | This is a separate B1 projection path, not a legacy canvas input or an alternate forensic authority. |
| `Neo4jConnection` in the approved evidence-keyed projection path | Projection infrastructure | `OUT_OF_SCOPE` | It is excluded from the B2 legacy-execution inventory because the approved projection is a separate B1 path. |
| Approved projection path from SQLite ledger evidence | Rebuildable forensic read-model input | `OUT_OF_SCOPE` | This is not a legacy input. It starts with B1-admissible SQLite evidence UUIDs and must not be described as the legacy UI canvas or a Neo4j migration path. |

The `Neo4jGraphRepository` class fence is a defense for direct and future un-inventoried legacy repository callers. It does not replace service, route, or task decorators: those guards remain REQUIRED to stop pre-graph SQL, task-publication, scheduling, and other side effects before a repository call is reached.

## No-side-effect invariant

For a `FORENSIC_CASE` denial, B2 MUST establish the denial before any legacy side effect. In particular, the attempted path MUST NOT:

- construct or connect a Neo4j repository, including through a direct pre-created `Neo4jGraphRepository`;
- invoke a direct repository public read, write, batch/list mutation, or flush method, or read a graph node, edge, element ID, or canvas state;
- execute Cypher, publish or enqueue a legacy task through `dispatch_legacy_task`, `LegacyExecutionTask.apply_async`, or `.delay`, invoke `Enricher.execute`, run a legacy task body, or call an external enricher;
- schedule `update_sketch_timestamp` through `BackgroundTasks.add_task`, or execute its task body;
- add, update, delete, attach, or otherwise mutate legacy graph/canvas state;
- write a forensic result, projection input, or evidence envelope from the denied legacy request; or
- perform an Investigation API/`InvestigationService` permission check, repository operation, commit, or Neo4j graph cleanup.

For a successful re-ingestion admission, B2 performs only the B1 ledger resolution and row-field validation described above. It does not mutate SQLite evidence, the legacy graph/canvas, or a projection.

## Acceptance-criterion mapping

| Criterion | B2 evidence required by independent audit |
| --- | --- |
| AC-104 | Direct `GraphService` construction and `create_graph_service` reject `FORENSIC_CASE` with `legacy_execution_forensic_case_fenced` before a Neo4j repository is constructed or touched. `Neo4jGraphRepository` construction and direct pre-created-instance read/write/batch/list-mutation/flush calls are fenced before singleton connection or graph side effects. `dispatch_legacy_task`, `LegacyExecutionTask.apply_async`, and `.delay` reject an active forensic-case scope before broker publication. Investigation API CRUD, `InvestigationService` public methods, its factory, and direct delete reject before permission/repository/commit/cleanup work; omitted mode preserves the legacy canvas path. |
| AC-105 | Every inventory row is classified exactly once, legacy enricher results cannot enter a forensic consumer directly, and an admission yields only an existing B1-resolved SQLite evidence UUID with exact structured connector provenance (`destination_id`, `endpoint_id`, `policy_version`, `enrich.read`, and canonical source), source rights, canonical digest, and matching body locator. A deleted evidence instance is safely denied without discarding caller state. |
| AC-106 | The rejection and re-ingestion probes show no legacy graph/canvas read, write, task publication/execution, timestamp scheduling/execution, investigation permission/repository/commit/cleanup work, external execution, graph-node adoption, or graph mutation. `run_enricher`, `run_flow`, and `update_sketch_timestamp` task bodies are independently guarded; the approved projection starts from B1 ledger evidence rather than legacy UI state. |

## Mutation and re-ingestion audit recipe

An independent audit MUST observe behavior, not rely on source inspection alone:

1. Instrument `Neo4jGraphRepository` construction, singleton connection, and each public direct read/write/batch/list-mutation/flush method, as well as the legacy graph read/write methods. Pre-create a repository outside the forensic scope, then under `FORENSIC_CASE` request direct `GraphService` construction and factory creation, attempt repository construction, and exercise the pre-created repository's representative read, write, batch/list mutation, and flush methods. Verify `legacy_execution_forensic_case_fenced` and zero connection/query/write/batch/list side effects after the forensic-scope requests.
2. Instrument `dispatch_legacy_task`, `LegacyExecutionTask.apply_async`, `.delay`, broker publication, the `run_enricher` and `run_flow` task bodies, and `Enricher.execute`. Under an active forensic-case scope, verify a forensic caller cannot publish through the sanctioned route/helper or reusable task API and each guarded task body denies before execution. Verify queue context does not propagate as authorization. Treat raw broker injection as outside the sanctioned application API, not as authorization. Outside the scope, verify the route remains legacy-only and no direct output is admitted as forensic evidence.
3. Instrument flow and sketch canvas route/service mutations plus `BackgroundTasks.add_task` and the `update_sketch_timestamp` task body. In `FORENSIC_CASE`, attempt representative read, create/update, relationship, delete, launch, and timestamp paths; verify the fence occurs before scheduling and produces zero legacy state changes.
4. Instrument Investigation API read/create/delete routes, `InvestigationService` public methods and factory, the direct delete wrapper/body, permission checks, repositories, commits, and Neo4j graph cleanup. Under `FORENSIC_CASE`, attempt representative read/create/delete calls; verify each fence occurs before all instruments, and verify direct delete cannot remap the canonical fence.
5. Create a valid new SQLite evidence envelope through the ordinary structured capture path with nonempty `destination_id`, `endpoint_id`, `policy_version`, `source_rights`, `capability` `enrich.read`, `source` `connector:<destination_id>:<endpoint_id>`, a canonical lowercase 64-hex digest, and matching `body:sha256:<same digest>` locator. Resolve it using the registered B1 SQLite `ForensicLedgerSession`; verify admission returns only the same evidence UUID and causes no writes.
6. Mutate one prerequisite at a time: substitute an unregistered/non-SQLite session, an absent UUID, missing or mismatched connector provenance, missing source rights, an invalid/noncanonical digest, or a mismatched/arbitrary locator. Verify the applicable stable safe denial, no graph access, and no state change.

7. Present a dirty or pending evidence instance to admission. Verify its safe denial occurs without a flush, expunge, rollback, or other loss of caller-owned session state.
8. Include a deleted evidence instance in the admission probes. Verify its safe denial occurs without a flush, expunge, rollback, or other loss of caller-owned session state.
9. Try to provide or recover a graph node/key/element ID in place of the envelope UUID. Verify it cannot be admitted, attached, or returned as forensic identity.

## Transitions

| Transition | Required condition | B2 result |
| --- | --- | --- |
| Legacy canvas use → legacy canvas use | Omitted mode or explicit `LEGACY_CANVAS` | Existing behavior is preserved; no forensic status is implied. |
| Legacy-derived material → forensic consumer | Newly captured structured SQLite envelope already exists and passes B1 plus B2 field validation | Admission returns only the ledger evidence UUID. |
| `FORENSIC_CASE` → any legacy graph/canvas execution | Any read, write, construction, mutation, task launch, or execution attempt | Fail closed with `legacy_execution_forensic_case_fenced` before side effects. |
| SQLite ledger evidence → approved forensic projection | Evidence UUID is B1-admissible | Not a legacy-input transition; outside B2's legacy inventory enforcement. |

## Non-goals

B2 does not:

- implement B3 authorization policy, permissions, or case authorization;
- implement B4 source cards, source-family policy, or source validation beyond the B2 admission prerequisites;
- implement B5 evidence immutability or change any persistence guarantees;
- define cases, directives, claims, LadybugDB, graph-projection implementation, action-plane behavior, or historical graph migration;
- authorize legacy paths, grant access based on a case reference, or make Neo4j/canvas data forensic authority; or
- change legacy-canvas behavior for callers that omit the execution mode.

## Independent-audit gate

This record is **Accepted** after independent static and runtime audits confirmed the AC-104/105/106 evidence above, including pre-side-effect `FORENSIC_CASE` denials, UUID-only structured re-ingestion, legacy positive controls, and continued operation of the separate approved projection path. Any normative or runtime-boundary change requires a fresh independent audit.
