---
title: "Forensic authorization policy v1"
description: "Accepted B3 forensic case-authorization and data-class policy for OBS-1964."
version: "forensic-data-class/v1"
status: "Accepted"
issue: "OBS-1964"
---

# Forensic authorization policy v1

**Status:** **Accepted** (verified 2026-08-09; independent PASS by B3Verifier, re-verified on the final tree by B3FinalVerifier — runtime probes: expired grant → hold, unspecified/mismatched source rights → redacted hold, research→action escalation → deny; current nine-file forensic suite: 216 passed; earlier 92/101-test citations superseded)
**Policy version:** `forensic-data-class/v1`  
**Barrier:** B3 — forensic case authorization and data-class policy  
**Scope:** Normative target for the B3 boundary helpers. It does not claim that B9 projection wiring, B11 connector adapters, B13 API wiring, B4 source cards, B5 immutable-ledger persistence, B12 case persistence, or B25 action execution exists.

The key words **MUST**, **MUST NOT**, **REQUIRED**, **SHOULD**, and **MAY** are normative.

## Decision

A forensic case authorizes access only through an exact, live B3 decision evaluated by one policy evaluator. Connector capability, a source's terms or rights, researcher role, possession of an artifact reference, and access to a projection are inputs to that evaluator; none is case authorization by itself.

B3 controls research-plane operations only. It never grants action-plane authority. A grant reconstructed from its fields, copied into another request, retargeted to another case, or used after expiry is invalid. A denial returns a redacted, auditable hold rather than sensitive policy, source, artifact, or identity detail.

SQLite remains the forensic authority under B1. B3 does not create a second evidence authority and does not alter B2's default legacy-canvas behavior.

## Versioned taxonomy and operation vocabulary

The taxonomy version is the literal string `forensic-data-class/v1`. A decision or grant with any other version is not applicable and MUST hold with `forensic_policy_version_mismatch`.

| Data class | Value | Ancestors | Minimum reviewer threshold | Prohibition / eligibility rule |
| --- | --- | --- | --- | --- |
| Public | `PUBLIC` | — | `CASE_OWNER` | An exact-class allow may permit research-plane access. |
| Licensed | `LICENSED` | `PUBLIC` | `CASE_OWNER` | An exact-class allow must also match source-rights values. |
| Personal | `PERSONAL` | `LICENSED`, `PUBLIC` | `PRIVACY_REVIEWER` or `COMPLIANCE_REVIEWER` | `CASE_OWNER` alone is insufficient. |
| Sensitive personal | `SENSITIVE_PERSONAL` | `PERSONAL`, `LICENSED`, `PUBLIC` | `COMPLIANCE_REVIEWER` | A privacy-only review is insufficient. |
| Restricted | `RESTRICTED` | `SENSITIVE_PERSONAL`, `PERSONAL`, `LICENSED`, `PUBLIC` | `COMPLIANCE_REVIEWER` | Only an `OVERRIDE_ALLOW` with a non-empty override reference may permit access. |

The operation vocabulary is closed in v1:

| Operation | Value | Research-plane meaning |
| --- | --- | --- |
| Collection | `COLLECTION` | Capture or re-ingest evidence into the authoritative evidence path. |
| Connector read | `CONNECTOR_READ` | Read from an approved source through a connector boundary. |
| Artifact read | `ARTIFACT_READ` | Read artifact bytes or represented content. |
| Projection read | `PROJECTION_READ` | View an evidence-backed projection result. |
| API view | `API_VIEW` | Return a policy-filtered research-plane response through an API. |
| Query | `QUERY` | Execute a research-plane query over eligible evidence. |

No v1 operation denotes an action-plane operation. `ACTION_EXECUTION` and similar values MUST be rejected by the explicit action-plane check with `forensic_action_plane_not_authorized`, even if a research grant would otherwise match.

### Inheritance and exactness

A decision applies only to its named case, taxonomy version, source scope, source-rights values, operation set, and time window. An `ALLOW` is exact-class only: an allow for `PUBLIC` does not authorize `LICENSED`, and an allow for any lower class does not authorize an ancestor or descendant class.

A live ancestor `DENY` or `HOLD` applies to every descendant class for the same otherwise-matching decision scope. It cannot be defeated by a descendant allow. Consequently, a request for `SENSITIVE_PERSONAL` must consider matching `SENSITIVE_PERSONAL`, `PERSONAL`, `LICENSED`, and `PUBLIC` deny/hold decisions before it considers an exact sensitive-personal allow.

A decision naming multiple operations is equivalent to an explicit decision for each listed operation, but it remains exact: it does not cover an operation omitted from that set. An empty operation set is invalid and MUST hold.

## Case decisions

A case decision is an append-only policy record. It has this normative shape; field names are the B3 wire/storage contract, not a claim about an implemented persistence schema:

```json
{
  "decision_id": "UUID",
  "case_reference": "case identifier",
  "taxonomy_version": "forensic-data-class/v1",
  "data_class": "PERSONAL",
  "operations": ["ARTIFACT_READ", "QUERY"],
  "source_scope": "canonical source-scope value",
  "source_rights": {"license": "...", "allowed_use": "..."},
  "not_before": "RFC 3339 instant",
  "expires_at": "RFC 3339 instant",
  "state": "ALLOW",
  "reviewer_authority": "PRIVACY_REVIEWER",
  "rationale": "review rationale",
  "supersedes_decision_id": null,
  "override_reference": null
}
```

`decision_id`, `case_reference`, `taxonomy_version`, `data_class`, `operations`, `source_scope`, `source_rights`, `not_before`, `expires_at`, `state`, `reviewer_authority`, and `rationale` are REQUIRED. `supersedes_decision_id` and `override_reference` are nullable only where their state-specific rules permit it. Timestamps MUST be unambiguous RFC 3339 instants and `expires_at` MUST be later than `not_before`.

The decision state vocabulary is closed:

| State | Meaning |
| --- | --- |
| `ALLOW` | Exact-class research-plane authorization subject to threshold and all bindings. |
| `DENY` | Prohibits the matching class and all descendants. |
| `HOLD` | Prohibits the matching class and all descendants pending resolution. |
| `OVERRIDE_ALLOW` | Restricted-class exception only; requires compliance review and an override reference. |

A decision MUST name one exact case. Wildcard cases, wildcard classes, wildcard operations, and unbounded source scopes are prohibited. A rationale MUST state why this case, class, operation, source scope, and duration are appropriate without embedding secret material.

### Reviewer thresholds and overrides

- `PUBLIC` and `LICENSED` allows require `CASE_OWNER` or a stricter authority.
- `PERSONAL` allows require `PRIVACY_REVIEWER` or `COMPLIANCE_REVIEWER`.
- `SENSITIVE_PERSONAL` allows require `COMPLIANCE_REVIEWER`.
- `RESTRICTED` requires `COMPLIANCE_REVIEWER`, state `OVERRIDE_ALLOW`, and a non-empty `override_reference` that identifies the approved exception without disclosing protected contents.
- `OVERRIDE_ALLOW` is invalid for every class other than `RESTRICTED`.
- A reviewer authority lower than the class threshold produces `forensic_reviewer_authority_insufficient`; it never degrades to a lower-class allow.

Reviewer authority is a decision input. It is not a grant by itself and cannot substitute for a live exact decision.

### Append, supersede, expiry, and precedence

A decision is never modified in place. A replacement appends a new decision whose `supersedes_decision_id` names the replaced record. The replacement's own bindings and time window are evaluated independently.

A superseded decision is never active again. In particular, if a replacement expires, is malformed, or becomes inapplicable, its predecessor MUST NOT revive. The evaluator returns the applicable hold or denial instead.

For live, otherwise-matching decisions, precedence is deterministic:

1. `DENY`
2. `HOLD`
3. a valid exact-class allow

For `RESTRICTED`, the third category is available only to a valid exact-class `OVERRIDE_ALLOW` that satisfies the compliance and override-reference requirements. Thus `OVERRIDE_ALLOW` is not a separate precedence tier and cannot outrank a deny or hold.

An exact-class allow cannot override an ancestor deny or hold. Equal-precedence contradictory records, including two active records that produce irreconcilable outcomes within the same case/class/operation/source/rights scope, MUST return `forensic_policy_conflict_hold`; selection by recency, record ID, request order, or reviewer identity is prohibited.

## One evaluator and boundary helpers

Every B3 boundary helper MUST call one canonical evaluator. A helper MUST NOT implement local policy shortcuts, interpret connector capability as authority, or issue its own ad hoc grant. The evaluator receives at least: exact case reference, taxonomy version, requested class, operation, evidence identifier when known, source scope, source-rights values, requested time, caller identity, and applicable decision/grant records.

| Boundary helper | Required B3 request | Later wiring explicitly not claimed |
| --- | --- | --- |
| Collection | `COLLECTION` evaluation and matching collection grant | B12 case persistence; B2 structured re-ingestion wiring is a B3 requirement |
| Connector read | `CONNECTOR_READ` evaluation | B11 connector adapter integration |
| Artifact access | `ARTIFACT_READ` evaluation | Artifact-store route integration |
| Projection visibility | `PROJECTION_READ` evaluation | B9 projection implementation or visibility filter |
| API view | `API_VIEW` evaluation | B13 API implementation |
| Query | `QUERY` evaluation | Query service integration |

The collection row is B3's required B2 structured re-ingestion wiring, not a later barrier. The remaining rows identify later wiring not claimed by this policy. A B3 helper returns either a bound grant or a redacted hold.

### Source rights and connector capability are non-authority inputs

`source_scope` and `source_rights` bind what an approved decision/grant may cover. The evaluator MUST require exact canonical equality between request and decision/grant values. A missing, changed, malformed, or mismatched source-rights value returns `forensic_source_rights_mismatch_hold`; a source-scope mismatch returns `forensic_source_scope_mismatch_hold`.

Connector capabilities describe what a connector can technically do. They MAY constrain a request but MUST NOT create, widen, infer, or replace case authorization. Likewise, source rights may make access ineligible but MUST NOT authorize an otherwise unauthorized case.

### Research-plane/action-plane separation

A research grant MUST be passed to an explicit action-plane check before any action-plane use. That check MUST always deny it with `forensic_action_plane_not_authorized`. It MUST run before action dispatch, task publication, mutation, external write, or other side effect. No decision state, reviewer authority, override reference, connector capability, source right, or grant field in this policy authorizes B25 action execution.

## Grants and anti-forgery semantics

A grant is a capability issued only by the canonical evaluator after a valid authorization decision. A grant is not a serializable reconstruction of the input decision. It MUST be identity-registered in evaluator-owned state and validated by identity, not value equality.

A normative grant shape is:

```json
{
  "grant_id": "opaque UUID or opaque token identifier",
  "case_reference": "case identifier",
  "taxonomy_version": "forensic-data-class/v1",
  "data_class": "LICENSED",
  "operation": "COLLECTION",
  "evidence_envelope_id": "UUID or null before collection",
  "source_scope": "canonical source-scope value",
  "source_rights": {"license": "...", "allowed_use": "..."},
  "subject_id": "registered caller identity",
  "issued_at": "RFC 3339 instant",
  "expires_at": "RFC 3339 instant",
  "decision_id": "UUID",
  "override_reference": null
}
```

A valid grant MUST bind exactly one case, class, operation, subject identity, taxonomy version, source scope, source-rights value set, evidence identifier (or the explicit pre-collection null state), issuance decision, and expiry. It MUST be identity-registered and non-forgeable by field reconstruction, copying, serialization, deserialization, or changing any bound value. The evaluator MUST reject an unregistered identity, a copied/reconstructed object, an expired grant, or a mismatch with `forensic_grant_invalid_hold` or `forensic_grant_expired_hold`, without disclosing registration internals.

A collection grant is valid only before the SQL read it authorizes and for the exact collection request. It cannot be reused for connector read, artifact access, projection visibility, API view, query, or action-plane access. A post-collection evidence identifier may be attached only through evaluator-owned validation after the persisted record is read.

## B2 structured legacy re-ingestion ordering

B2 remains the sole admission boundary for legacy-derived material. This policy adds a required ordering to its structured re-ingestion path without allowing legacy graph/canvas access:

1. Validate the request shape and exact case/class/source-rights inputs without SQL or legacy execution.
2. Evaluate `COLLECTION` with the canonical evaluator and obtain a matching, live collection grant **before any SQL**.
3. Perform B2's existing SQLite ledger resolution/read only; do not construct, connect to, read from, write to, or adopt legacy graph/canvas state.
4. After the read, compare the persisted evidence source scope and source-rights values with both the request and the grant. They MUST match exactly.
5. If validation succeeds, return only B2's existing ledger UUID admission result. The grant neither changes that result nor adds a second identifier.

A missing, expired, reconstructed, mismatched-case/class/operation/evidence/source/rights/version, or action-plane collection grant MUST hold before SQL. A post-read source or rights mismatch MUST hold and MUST NOT admit the evidence. B3 does not alter B1 SQLite-authority semantics or broaden any legacy execution path.

## Safe holds and auditable decision records

A policy failure is a `HOLD` result unless a live governing decision produces `DENY`. Safe failure codes are stable, machine-readable, and redact case internals, evidence contents, source credentials, artifact locations, registered-object details, and reviewer identities.

| Condition | Required result code | Result state |
| --- | --- | --- |
| Missing or malformed decision/grant/request binding | `forensic_policy_missing_hold` | `HOLD` |
| Decision or grant expired, not yet valid, or superseded | `forensic_policy_expired_hold` or `forensic_grant_expired_hold` | `HOLD` |
| Source-scope mismatch | `forensic_source_scope_mismatch_hold` | `HOLD` |
| Source-rights missing, malformed, or mismatch | `forensic_source_rights_mismatch_hold` | `HOLD` |
| Taxonomy version mismatch | `forensic_policy_version_mismatch` | `HOLD` |
| Reviewer threshold insufficient | `forensic_reviewer_authority_insufficient` | `HOLD` |
| Equal-precedence or binding conflict | `forensic_policy_conflict_hold` | `HOLD` |
| Invalid restricted override | `forensic_override_invalid_hold` | `HOLD` |
| Unregistered, copied, reconstructed, or mismatched grant | `forensic_grant_invalid_hold` | `HOLD` |
| Live governing denial | `forensic_policy_denied` | `DENY` |
| Research grant presented to action plane | `forensic_action_plane_not_authorized` | `DENY` |

An auditable evaluation record MUST capture the following redacted fields:

```json
{
  "evaluation_id": "UUID",
  "evaluated_at": "RFC 3339 instant",
  "case_reference": "case identifier or redacted stable reference",
  "taxonomy_version": "forensic-data-class/v1",
  "requested_data_class": "PERSONAL",
  "requested_operation": "QUERY",
  "source_scope_fingerprint": "non-reversible canonical fingerprint",
  "source_rights_fingerprint": "non-reversible canonical fingerprint",
  "evidence_envelope_id": "UUID or null",
  "subject_id": "registered subject reference",
  "considered_decision_ids": ["UUID"],
  "grant_id": "opaque identifier or null",
  "outcome": "HOLD",
  "code": "forensic_policy_conflict_hold",
  "rationale_reference": "redacted policy rationale reference"
}
```

The audit record MUST identify the evaluated boundary and preserve the actual outcome, but MUST NOT persist raw credentials, artifact bytes, query text, connector payloads, unredacted source-rights terms when sensitive, or the data itself merely to explain a denial.

## Denial and negative-test matrix

| Threat or negative probe | Expected B3 outcome | Required assertion |
| --- | --- | --- |
| No decision, grant, case, class, operation, source, or rights binding | `forensic_policy_missing_hold` | No protected read, SQL, connector call, artifact read, projection view, API response, query, or action side effect occurs. |
| Allow for ancestor/lower class used for a descendant/higher class | `forensic_policy_missing_hold` | Allow is exact-class only. |
| Ancestor deny or hold plus descendant allow | `forensic_policy_denied` or governing hold | Ancestor prohibition wins. |
| Two live conflicting decisions | `forensic_policy_conflict_hold` | No recency/order tie-break authorizes access. |
| Superseded decision whose replacement expires | `forensic_policy_expired_hold` | The predecessor never revives. |
| Expired decision or grant | applicable expiry hold | No boundary helper proceeds. |
| `CASE_OWNER` attempts personal; privacy reviewer attempts sensitive | `forensic_reviewer_authority_insufficient` | Threshold cannot be inferred from another role. |
| Restricted allow lacks compliance authority, `OVERRIDE_ALLOW`, or override reference | `forensic_override_invalid_hold` | Restricted access never falls back to ordinary allow. |
| Request changes case, class, operation, evidence, source, rights, subject, or version after grant issue | `forensic_grant_invalid_hold` or precise mismatch hold | Bound value is exact. |
| Reconstructed/copied grant with equivalent fields | `forensic_grant_invalid_hold` | Registration/identity validation rejects it. |
| Connector advertises capability or source reports rights but no live case decision | `forensic_policy_missing_hold` | Capability and rights are not authority. |
| Research grant offered to action dispatch | `forensic_action_plane_not_authorized` | Explicit check runs before every action-plane side effect. |
| B2 legacy re-ingestion lacks matching collection grant | applicable hold | The denial occurs before any SQL or legacy operation. |
| B2 persisted evidence source/rights disagree after ledger read | precise source/rights hold | B2 returns no admission beyond its existing UUID-only result. |

## Acceptance mapping

| Criterion | This policy's required evidence |
| --- | --- |
| AC-107 | A case cannot collect, reveal, or project a data class without a current explicit authorization decision. |
| AC-108 | Missing, expired, or conflicting rights/classification yields an auditable hold, never implicit access. |
| AC-109 | Action capabilities remain separately approved and cannot be inferred from research authorization. |

## Audit and acceptance recipe

An implementation review MUST establish behavior, not merely inspect declarations:

1. Construct current explicit decisions across every class, reviewer threshold, operation, and source/rights binding. Attempt collection, reveal, and projection of each class without a current decision; verify every attempt holds.
2. Probe exact-class allow, ancestor deny/hold inheritance, equal-precedence conflict, restricted override requirements, expiry, supersession, and non-revival.
3. Mutate one otherwise-valid authorization to expired and another to an unspecified source-rights value. Exercise both collection and read paths for each mutation; verify redacted auditable holds, with the collection denial before SQL.
4. Issue a grant and attempt reconstruction, copying, serialization round-trip, expired reuse, and every bound-field substitution. Verify fail-closed redacted results.
5. Exercise case/collection entry, connector read, artifact read, projection visibility, API view, and query through the same evaluator; verify a connector capability or source right alone never authorizes a request.
6. Instrument B2 structured re-ingestion. With a missing or invalid collection grant, verify zero SQL and zero legacy graph/canvas side effects. With a valid grant, verify post-read persisted source/rights equality and UUID-only admission.
7. Present a valid research grant to the action-plane check and verify `forensic_action_plane_not_authorized` before dispatch or mutation.
8. Inspect audit records for a stable code and required redacted fields while confirming they contain no protected payload, credential, artifact bytes, raw query, or registration secret.

## Non-goals and later barriers

This Proposed B3 policy does not:

- implement B4 source cards or a source-card data model;
- implement B5 immutable ledger persistence, a decision ledger schema, or an immutable audit store;
- implement B9 projection construction or projection visibility wiring;
- implement B11 connector adapters or prove third-party connector behavior;
- implement B12 durable case persistence or case lifecycle;
- implement B13 API endpoints, authentication, or response filtering;
- implement B25 action execution, approvals, dispatch, or mutation authority;
- change B1 SQLite-authority semantics; or
- broaden B2 legacy graph/canvas execution or replace B2's UUID-only admission result.

A later barrier may wire a listed boundary only by preserving this policy's one-evaluator, fail-closed contract. It MUST NOT infer authorization from connector capability, source rights, a research grant, or a later read model.
