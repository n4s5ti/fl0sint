---
title: "Forensic observations, claims, and contradiction states v1"
description: "OBS-1968 append-only observation/claim records, claim relations, and contradiction-state assessments for the SQLite forensic ledger."
version: "observations-claims/v1"
status: "Accepted"
issue: "OBS-1968"
---

# Forensic observations, claims, and contradiction states v1

**Status:** **Accepted** — independent verification by B7Verifier (verifier ≠ author) returned overall PASS with high confidence on 2026-08-08: incompatible-claim retention, mutable-overwrite rejection (raw SQL + ORM), contradiction-state omission, hold states, and supersession-fork probes all passed; AC-210/AC-211/AC-212 individually satisfied. Two flagged gaps were remediated post-verification: `value_digest` is now recomputed and enforced at domain construction (with canonical-encoding round-trip), and a supersession `supersedes_id` naming a nonexistent target now raises a clean `forensic_claim_invalid_key` in all three inserts. No test counts are pinned here; see the OBS-1968 suites.
**Contract version:** `observations-claims/v1`
**Runtime modules:** `flowsint_core.core.forensics.observations`, `flowsint_core.core.forensics.observations_repository`; tables in `flowsint_core.core.models`
**Barrier:** B4 — append-only observation/claim records and contradiction-state assessments
**Scope:** Normative contract for the SQLite forensic observation/claim ledger, claim relations, contradiction-state assessments, and the projection-eligibility decisions they feed. It does not implement GraphProjectionService wiring, API routes, a Postgres alembic migration for these tables, identity adjudication, or intent assessments.

The key words **MUST**, **MUST NOT**, **REQUIRED**, **SHOULD**, and **MAY** are normative.

## 1. Decision

An observation is a record of what was reported, not a settled fact. The ledger stores reported claims, the relations observed between them, and the assessments that resolve those relations — and it never erases a competing observation. Contradiction, ambiguity, withdrawal, and insufficient support are first-class ledger states, not error conditions.

Resolution appends an assessment; it never mutates or deletes the observation it addresses. A superseded claim row remains queryable forever; only new projection is withheld. SQLite remains the sole forensic authority (B1): these tables are part of the authoritative ledger, and LadybugDB remains a rebuildable, evidence-keyed read model.

## 2. Claim identity and evidence linkage (AC-210)

Every claim is identified by a versioned key and is immutably linked to the evidence that supports it.

| Field | Rule |
| --- | --- |
| `claim_key` | `CHAR(64)`, unique; domain `obs1968/claim/v1` |
| `evidence_envelope_id` | NOT NULL FK → `evidence_envelope_records.id` (RESTRICT); immutable B1 evidence identity |
| `source_card_id` / `source_card_version` | NOT NULL; pins the exact source-card revision the claim was derived from |
| `source_record_id` | nullable FK → `forensic_source_records.source_record_id` (RESTRICT) when a B5 source record exists |
| `observed_value_json` | Text NOT NULL; canonical JSON (sorted keys, compact separators, ascii-escaped) preserved verbatim after insertion; `ObservedClaim` rejects any non-canonical encoding |
| `value_digest` | `CHAR(64)`; SHA-256 of the canonical `observed_value_json` bytes — recomputed and enforced at domain construction, a mismatched digest is unrepresentable as an `ObservedClaim` |
| `observed_at` | DateTime(timezone=True) NOT NULL; when the claim was reported |
| `valid_from` / `valid_to` | nullable DateTime(timezone=True); temporal scope of the claim's asserted validity |
| `subject_entity_key` | `CHAR(64)`, NOT NULL; the OBS-1788 stable entity key the claim is about |
| `object_entity_key` | `CHAR(64)` nullable; the OBS-1788 stable entity key the claim relates the subject to, when the predicate is relational |
| `predicate` | String; safe-name regex `^[a-z][a-z0-9_]{0,63}$` |
| `case_reference` | nullable String |

The claim key is additive identity, not a substitute for `evidence_envelope_id`: the envelope UUID remains the fact's ledger-evidence identity (B1), and the claim key accompanies it. `canonical_observed_value(value)` produces the ledger's canonical encoding; one value has exactly one canonical byte sequence, so `value_digest` and `claim_key` are stable per value.

## 3. Ledger state model

Three append-only tables model observations, their relations, and the assessments that resolve them.

### 3.1 `forensic_claim_records`

A reported claim. UUID PK `id`; all columns per §2. Supersession fields:

- `supersedes_id` — nullable self-FK, with a `UniqueConstraint` (fork prevention: at most one successor per superseded claim).
- `supersedes_reason` — Text, REQUIRED iff `supersedes_id` is set (CHECK).
- `recorded_at` — DateTime(timezone=True) NOT NULL.

### 3.2 `forensic_claim_relation_records`

An observed relation between two claims. UUID PK `id`; `relation_key` `CHAR(64)` unique with domain `obs1968/relation/v1`; `recorded_at` NOT NULL.

- `claim_id` and `related_claim_id` — NOT NULL FKs → `forensic_claim_records.id` (RESTRICT); CHECK `claim_id != related_claim_id`.
- The pair is stored in normalized order: the lexicographically smaller UUID hex first. The domain layer normalizes; the repository rejects a relation whose pair is not normalized.
- `kind` — CHECK constraint over `{contradicts, compatible, duplicate_report}` (`ClaimRelationKind`).
- `rationale` — Text NOT NULL with a CHECK that it is non-empty.
- `evidence_envelope_id` — NOT NULL FK → `evidence_envelope_records.id` (RESTRICT); the evidence that establishes the relation.
- `UNIQUE (claim_id, related_claim_id, kind)`.
- `supersedes_id` unique self-FK + `supersedes_reason` CHECK, as in §3.1.

### 3.3 `forensic_claim_assessment_records`

An assessment of a claim or of a relation. UUID PK `id`; `assessment_key` `CHAR(64)` unique with domain `obs1968/assessment/v1`; `recorded_at` NOT NULL.

Exactly one scope, enforced by CHECK:

- `claim_id` — nullable FK → `forensic_claim_records.id`, for claim-scoped kinds: `withdrawn`, `insufficient_support`.
- `relation_id` — nullable FK → `forensic_claim_relation_records.id`, for relation-scoped kinds: `unresolved`, `ambiguous`, `resolved_compatible`, `resolved_upheld`.

The CHECK guarantees the XOR: an assessment names exactly one of `claim_id` or `relation_id`, and the kind matches the scope it names.

- `kind` — CHECK over the six `ClaimAssessmentKind` values.
- `rationale` — Text NOT NULL with a non-empty CHECK.
- `evidence_envelope_id` — NOT NULL FK → `evidence_envelope_records.id` (RESTRICT).
- `supersedes_id` unique self-FK + `supersedes_reason` CHECK.

Assessment kinds and their scopes:

| Kind | Scope | Meaning |
| --- | --- | --- |
| `withdrawn` | claim | The claim is retracted; it projects no longer |
| `insufficient_support` | claim | The claim lacks enough evidence; it projects no longer |
| `unresolved` | relation | The contradiction stands unresolved |
| `ambiguous` | relation | The relation's meaning cannot be decided from evidence |
| `resolved_compatible` | relation | Contradicting claims are reconciled as compatible |
| `resolved_upheld` | relation | One side of the contradiction is upheld |

## 4. Supersession rules (AC-211)

Supersession is new-row supersession, never in-place mutation:

- A replacement appends a new row whose `supersedes_id` names the replaced row and whose `supersedes_reason` is REQUIRED.
- The `UniqueConstraint` on `supersedes_id` enforces fork prevention: a row can be superseded at most once. The repository checks this before persisting and raises `forensic_claim_supersession_fork` (a clean domain error, before the SQL unique failure) when a fork is attempted. A `supersedes_id` naming a row that does not exist raises `forensic_claim_invalid_key` before the fork check (and before any SQL FK failure) in all three inserts.
- Assessments supersede ONLY assessments. `supersedes_id` is the sole supersession field on an assessment and its FK targets `forensic_claim_assessment_records` alone — the scope FKs (`claim_id`, `relation_id`) name what an assessment is ABOUT, never what it supersedes — so there is structurally no way for an assessment or resolution to supersede a claim. A claim's fate changes only through claim-scoped assessments (`withdrawn`, `insufficient_support`) appended alongside it, or through a superseding claim row.
- A superseded row is never active again and MUST NOT revive; it remains queryable in the ledger forever.
- Contradictory claims MUST be simultaneously queryable (AC-211): `load_claims_for_subject` returns every claim row for a subject, including superseded and withdrawn rows.

## 5. Contradiction-state derivation

`derive_contradiction_state(relation, assessments) -> ContradictionState` derives the state of one relation from its live assessments:

1. A `contradicts` relation with no live relation-scoped assessment defaults to `UNRESOLVED`.
2. Otherwise the latest non-superseded relation-scoped assessment governs: `ambiguous`, `resolved_compatible`, or `resolved_upheld` maps to the same-named `ContradictionState` value.

`ContradictionState` is `{unresolved, ambiguous, resolved_compatible, resolved_upheld}`. Only relation-scoped assessments are resolving; claim-scoped assessments (`withdrawn`, `insufficient_support`) never resolve a contradiction state.

## 6. Projection eligibility and hold matrix (AC-212)

`evaluate_projection_eligibility(claim, *, superseded, assessments, relations_with_states) -> ProjectionEligibility` evaluates ONE claim at a time. `ProjectionEligibility` is a frozen dataclass: `claim_id`, `eligible: bool`, `hold_code: str | None`, `contradiction_state: ContradictionState | None`, `claim_key`. It never returns a merged or picked-one result — both sides of an unresolved contradiction project.

| Recorded state | Projection decision | Qualifier |
| --- | --- | --- |
| Recorded, unrelated to any contradiction | ELIGIBLE | `contradiction_state=None` |
| Unresolved contradiction (`contradicts`, no live resolving assessment) | ELIGIBLE for BOTH claims | `contradiction_state=unresolved` — never collapsed, merged, or picked |
| Withdrawn | HOLD | `forensic_claim_withdrawn_hold` |
| Superseded | HOLD | `forensic_claim_superseded_hold` |
| Insufficient support | HOLD | `forensic_claim_insufficient_support_hold` |

Holds withhold NEW projection only: they never delete, hide, or alter ledger rows, and held claims remain queryable forever. `evaluate_subject_projection_eligibility(session, subject_entity_key)` returns a tuple with one decision per claim of the subject — a per-claim decision set, never a collapse.

## 7. Mapping onto OBS-1788 qualified claims

- Each claim row projects under its own distinct projection key, built from the same versioned-domain-key construction as OBS-1788 `stable_claim_key` (SHA-256 over a domain prefix and NUL-joined parts); the claim's `claim_key` accompanies the projected fact alongside the immutable `evidence_envelope_id`.
- Projection is MERGE-only with ON CREATE semantics: a rebuild never overwrites an existing projected fact in place and never overwrites a source observation.
- LadybugDB stays a rebuildable, evidence-keyed read model (B1). Deleting or rebuilding it never changes forensic truth; the ledger is the sole authority.

## 8. Append-only mutation contract

Dual enforcement, mirroring `forensic_artifact_records` in `models.py`:

1. DDL triggers registered with `event.listen(table, "after_create", ...)`, `trg_`-prefixed and snake_case, issuing `RAISE(ABORT, ...)` on `BEFORE UPDATE` and `BEFORE DELETE` for each of the three tables (SQLite), with the equivalent Postgres trigger function for the `postgresql` dialect.
2. ORM guards: `before_update`/`before_delete` listeners on each mapped class raising `TypeError` with an append-only message.

An `UPDATE` or `DELETE` therefore surfaces as an `IntegrityError` (trigger path) or the ORM guard's `TypeError` (ORM path); either way the row is immutable. All mutation in the repository layer is insert-only.

## 9. Repository contract

`flowsint_core.core.forensics.observations_repository` mirrors the B5 `forensics/repository.py` session-function style:

| Function | Behavior |
| --- | --- |
| `insert_claim_record(session, claim)` | Verifies the evidence envelope exists first (clean `forensic_claim_missing_evidence` before any SQL FK failure), verifies a named supersession target exists (`forensic_claim_invalid_key`), fork-checks `supersedes_id`, persists, returns the ORM row |
| `insert_claim_relation_record(session, relation)` | Both claims exist, self-relation check (`forensic_claim_relation_self`), duplicate-pair check (`forensic_claim_relation_duplicate`), evidence-envelope check, supersession-target + fork checks, normalized-pair enforcement |
| `insert_claim_assessment_record(session, assessment)` | Scope target (claim or relation) exists, evidence-envelope check, supersession-target + fork checks, kind/scope match |
| `load_claims_for_subject(session, subject_entity_key)` | Every claim row for the subject, including superseded and withdrawn — contradictory claims simultaneously queryable (AC-211) |
| `load_contradiction_state(session, relation_id)` | Relations plus latest assessments → `ContradictionState` |
| `load_subject_contradictions(session, subject_entity_key)` | All relations involving the subject's claims with their derived states |
| `evaluate_subject_projection_eligibility(session, subject_entity_key)` | `tuple[ProjectionEligibility, ...]`, one per claim, never collapsing |

Missing-rationale violations raise `forensic_claim_missing_rationale`; scope/kind mismatches raise `forensic_claim_assessment_scope_mismatch`; malformed keys, missing scope targets, and missing supersession targets raise `forensic_claim_invalid_key`.

## 10. Error codes and redaction

`ForensicObservationError(code, message)` uses the closed code set:

| Code | Meaning |
| --- | --- |
| `forensic_claim_missing_evidence` | Evidence envelope does not exist |
| `forensic_claim_supersession_fork` | A row is already superseded; a second successor was attempted |
| `forensic_claim_relation_self` | A relation names the same claim on both sides |
| `forensic_claim_relation_duplicate` | The normalized (claim_id, related_claim_id, kind) pair already exists |
| `forensic_claim_missing_rationale` | Required rationale is absent or empty |
| `forensic_claim_assessment_scope_mismatch` | Assessment kind does not match its scope, or both/neither scope target set |
| `forensic_claim_invalid_key` | A record key is malformed, or a referenced claim/relation/assessment (scope or supersession target) does not exist. Predicate safe-name violations are rejected earlier, at domain construction, with `ValueError` |

Redaction discipline: exception text NEVER embeds `observed_value_json` bodies or `rationale` bodies — codes and safe identifiers only.

## 11. Acceptance-criteria traceability

| Criterion | Mechanism | Where enforced |
| --- | --- | --- |
| AC-210 (claim identity + evidence linkage) | `evidence_envelope_id` NOT NULL FK RESTRICT; `source_card_id` + `source_card_version` NOT NULL; `observed_value_json` canonical-encoding round-trip + `value_digest` recomputation at construction; temporal scope (`observed_at`, `valid_from`/`valid_to`); versioned `claim_key` | Table DDL (§3.1, §2); `ObservedClaim.__post_init__` validation; `insert_claim_record` evidence check |
| AC-211 (supersession; contradictory claims simultaneously queryable) | New-row supersession with unique fork prevention and REQUIRED reason; assessments supersede ONLY assessments (no structural path to supersede a claim); `load_claims_for_subject` returns all rows incl. superseded/withdrawn | UniqueConstraint + CHECK (§3.1–3.3); FK topology (§4); repository loader (§9) |
| AC-212 (unresolved contradictions project on both sides) | Per-claim `evaluate_projection_eligibility`; unresolved contradiction → ELIGIBLE with `contradiction_state=unresolved` for both sides; holds only for withdrawn/superseded/insufficient_support | Domain decision (§6); `derive_contradiction_state` (§5); per-claim tuple from the repository (§9) |

## 12. Explicitly not claimed

This OBS-1968 contract does not:

- wire GraphProjectionService or `graph_repository` to these tables, or change either;
- expose API routes for observations, claims, relations, or assessments;
- provide a Postgres alembic migration for the three new tables (the same gap already exists for the B5 forensic tables; the DDL triggers carry the `postgresql` dialect for parity, but no migration is claimed);
- perform identity adjudication (mapping observed entity keys to real-world identities);
- perform intent assessments or any assessment beyond the six closed kinds;
- merge claims, deduplicate claims, or pick one side of a contradiction;
- alter any existing table or the `forensic_artifact_records`/`forensic_source_records` schema.

A later barrier may wire projection only by consuming `ProjectionEligibility` decisions and preserving the append-only, per-claim, never-collapse contract defined here.
