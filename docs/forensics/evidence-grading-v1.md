---
title: "LeadGen evidence maturity and Admiralty grading v1"
description: "Proposed B9 evidence-maturity and Admiralty grading contract for OBS-1970."
version: "evidence-grading/v1"
status: "Proposed"
issue: "OBS-1970"
---

# LeadGen evidence maturity and Admiralty grading v1

**Status:** **Proposed** — pending independent verification for OBS-1970  
**Contract version:** `evidence-grading/v1` (`GRADING_CONTRACT_VERSION = "v1"`; `RUBRIC_VERSION_LEADGEN_ADMIRALTY_V1 = "leadgen-admiralty/v1"`)  
**Runtime module:** `flowsint_core.core.forensics.grading`  
**Barrier:** B9 — LeadGen evidence maturity and Admiralty grading  
**Scope:** Normative pure-contract layer for frozen grade records, maturity ceilings, and explicit-source corroboration over B4 claims and B5 source cards. It does not persist or project grades.

The key words **MUST**, **MUST NOT**, **REQUIRED**, **SHOULD**, and **MAY** are normative.

## 1. Decision

Grades are forensic metadata over B4 `ObservedClaim` records and B5 `SourceCard` records. They are not a shortcut to truth, a replacement for source review, or a way to erase competing evidence. A grade records what was assessed under a named rubric, at a stated time, with cited evidence and source-card versions.

The four grade scales measure different things and MUST remain separate. The contract MUST NOT infer one scale from another or synthesize a truth, quality, or priority score from them. Grade records are append-only: a correction appends a superseding record with its own provenance, leaving the prior record queryable.

## 2. Scales and scope rules

### 2.1 Evidence maturity

| `EvidenceMaturity` member | Stored value | LeadGen definition |
| --- | --- | --- |
| `E0` | `e0` | Guessed or assumed. |
| `E1` | `e1` | Plausible but unverified. |
| `E2` | `e2` | Supported by one reconstructable source. |
| `E3` | `e3` | The same scoped claim is corroborated by at least two independent source families without an unresolved material contradiction. |

`maturity_rank` MUST map `E0`, `E1`, `E2`, and `E3` to `0`, `1`, `2`, and `3`, respectively. This rank orders maturity only; it is not a cross-scale conversion.

### 2.2 Source reliability

| `SourceReliability` member | Stored value | Meaning |
| --- | --- | --- |
| `A` | `a` | Source reliability grade A. |
| `B` | `b` | Source reliability grade B. |
| `C` | `c` | Source reliability grade C. |
| `D` | `d` | Source reliability grade D. |
| `E` | `e` | Source reliability grade E. |
| `F` | `f` | Cannot be judged. |

### 2.3 Information credibility

| `InformationCredibility` member | Stored value | Meaning |
| --- | --- | --- |
| `ONE` | `1` | Information credibility grade 1. |
| `TWO` | `2` | Information credibility grade 2. |
| `THREE` | `3` | Information credibility grade 3. |
| `FOUR` | `4` | Information credibility grade 4. |
| `FIVE` | `5` | Information credibility grade 5. |
| `SIX` | `6` | Cannot be judged. |

### 2.4 Analytic confidence

| `AnalyticConfidence` member | Stored value | Meaning |
| --- | --- | --- |
| `LOW` | `low` | Low analytic confidence. |
| `MODERATE` | `moderate` | Moderate analytic confidence. |
| `HIGH` | `high` | High analytic confidence. |

### 2.5 Scope-scale compatibility

`GradeScopeKind` is closed to `SOURCE`, `REPORT`, `CLAIM`, and `ASSESSMENT`. `GradeKind` is closed to evidence maturity, source reliability, information credibility, and analytic confidence. A grade MUST use only the following compatibility matrix; every other pairing fails closed under the scope/scale diagnostic in §8.1.

| Grade kind | Permitted scope kind | Prohibited examples |
| --- | --- | --- |
| Evidence maturity | `CLAIM` only | Source, report, and assessment |
| Source reliability | `SOURCE` only | Claim, report, and assessment |
| Information credibility | `REPORT` or `CLAIM` | Source and assessment |
| Analytic confidence | `ASSESSMENT` only | Source, report, and claim |

There is deliberately no opportunity or prospect scope. Nothing above these four target kinds can carry a grade, and grades MUST NOT inherit from a source, report, claim, or assessment to an opportunity or prospect.

## 3. Rubric crosswalk (AC-216)

This table is descriptive only. It identifies different questions that may be shown beside one another; it does not define a mathematical or semantic crosswalk.

| Scale | What it measures | What it does not measure | Why no automatic conversion exists |
| --- | --- | --- | --- |
| Evidence maturity | The claim's reconstruction and independent corroboration state. | A source's reliability, an individual report's credibility, or an analyst's confidence. | Multiple sources and independence do not establish source reliability or report credibility. |
| Source reliability | The assessed reliability of one source. | The support maturity of a claim, report credibility, or analytic confidence. | A reliable source can supply an uncorroborated claim. |
| Information credibility | The assessed credibility of information in a report or claim. | Source reliability, corroboration count, or analyst confidence. | Credible information may have a source of unknown reliability or no independent corroboration. |
| Analytic confidence | The confidence in a particular assessment. | Evidence maturity, source reliability, or information credibility. | Confidence describes an assessment, not a substitute for evidence provenance. |

The module exports no conversion function between these scales and MUST NOT calculate a combined score. `E3` never implies `A1`, and `A1` never implies `E3`.

## 4. Grade-assessment records (AC-213)

All records in this section are frozen and validate malformed construction with `TypeError` or `ValueError`; defined domain outcomes use the closed error registry in §8.1.

### 4.1 Scope and source-card version references

| Record | Fields | Contract |
| --- | --- | --- |
| `SourceCardVersionRef` | `card_id`, `version` | `card_id` is a UUID and `version` is an integer at least 1. It pins the B5 card version inspected. |
| `GradeScopeRef` | `kind`, `target_id`, `claim_key`, `source_card_version` | `kind` is a closed scope vocabulary member and `target_id` is a UUID. `claim_key` is a 64-character hexadecimal key and is REQUIRED if and only if the scope is `CLAIM`; otherwise it MUST be `None`. `source_card_version` is an integer at least 1 and is REQUIRED if and only if the scope is `SOURCE`; otherwise it MUST be `None`. |

### 4.2 `GradeAssessment`

A frozen `GradeAssessment` contains:

| Field | Contract |
| --- | --- |
| `id`, `grade_key` | UUID identity plus a 64-character hexadecimal key that MUST equal the `build_grade_key` recomputation. |
| `kind`, `scope` | A closed grade kind and a `GradeScopeRef` compatible under §2.5. |
| `grade_value`, `withheld_reason_code` | Exactly one MUST be set. A value MUST belong to that kind's own scale; a withheld reason MUST belong to `WITHHELD_REASON_CODES`. |
| `as_of`, `rubric_version` | `as_of` is timezone-aware and the rubric version is non-empty. |
| `evidence_envelope_ids` | Non-empty `frozenset[UUID]` of B4 evidence envelopes inspected, including for a withheld grade. |
| `source_card_refs` | Non-empty `frozenset[SourceCardVersionRef]` of B5 versions inspected, including for a withheld grade. A source-scoped grade MUST contain the ref whose `card_id` and version match its scope. |
| `independence_assessment_ids` | `frozenset[UUID]`. An assigned E3 grade MUST cite at least one independence assessment ID. |
| `rationale`, `graded_by_subject`, `recorded_at` | Non-empty rationale and grader subject; timezone-aware recorded time. |
| `supersedes_id`, `supersedes_reason` | Optional replacement link and reason, governed by §4.4. |

The `WITHHELD_REASON_CODES` set is closed:

| Withheld reason code | Meaning |
| --- | --- |
| `grade_withheld_unknown_rights` | Rights required for the assessment are unknown. |
| `grade_withheld_missing_locator` | The supporting artifact cannot be located reconstructably. |
| `grade_withheld_unresolved_contradiction` | A material contradiction remains unresolved. |
| `grade_withheld_insufficient_independence` | Independence proof is insufficient. |
| `grade_withheld_missing_source` | A required source is absent. |

### 4.3 Citation and key requirements

Every assessment, including a withheld assessment, MUST retain non-empty evidence-envelope IDs and source-card-version references. An E3 assignment MUST retain its independence basis. Every record MUST retain a non-empty rubric version so an evaluator can identify which rubric was applied.

`build_grade_key(scope_kind, target_id, kind, recorded_at)` MUST hash the UTF-8 bytes of NUL-joined parts in this exact order:

1. domain: `obs1970/grade/v1`;
2. `scope_kind.value`;
3. `str(target_id)`;
4. `kind.value`;
5. `recorded_at` normalized to an aware UTC ISO-8601 string with `Z`.

The result is the lowercase SHA-256 hexadecimal digest. The following independently run recipe demonstrates the exact byte construction; it produced `b5a28e21d946582cb973356275426f7d94d7a239736122ee9afbe1b2df4ce0c3`:

```bash
uv run --no-sync python -c 'from datetime import datetime, UTC; from hashlib import sha256; from uuid import UUID; recorded_at = datetime(2026, 8, 9, 12, 34, 56, tzinfo=UTC); parts = ("obs1970/grade/v1", "claim", str(UUID("12345678-1234-5678-1234-567812345678")), "evidence_maturity", recorded_at.isoformat().replace("+00:00", "Z")); print(sha256("\0".join(parts).encode()).hexdigest())'
```

### 4.4 Supersession

A record MUST NOT supersede itself. `supersedes_reason` is REQUIRED if and only if `supersedes_id` is present. Supersession appends a new record; it MUST NOT overwrite or delete the superseded record.

## 5. Maturity ceilings (AC-214)

`derive_maturity_ceiling` evaluates one claim context from a `SourceReference | None`, an `ArtifactLocatorPolicy | None`, `frozenset[str] | None` rights, and a sequence of B4 `ContradictionState` values. It starts at E3 and caps the ceiling to E1 for each applicable condition below. `MaturityCeiling.reason_codes` is a sorted, deduplicated tuple and is empty if and only if the ceiling remains E3.

| Cap condition | Ceiling reason code |
| --- | --- |
| The source reference is absent or has `missing_source_reason`. | `grade_ceiling_missing_source` |
| The locator policy is absent or its mode is `OPEN`. `EXACT`, `PREFIX`, and `REGEX` are reconstructable. | `grade_ceiling_missing_locator` |
| Rights are absent or empty. | `grade_ceiling_unknown_rights` |
| Any contradiction state is `UNRESOLVED` or `AMBIGUOUS`. `RESOLVED_COMPATIBLE` and `RESOLVED_UPHELD` do not cap. | `grade_ceiling_unresolved_contradiction` |

`require_maturity_within_ceiling` MUST compare only maturity ranks. A requested rank above the derived ceiling raises `grade_exceeds_ceiling` and its safe message includes the cap reasons.

A capped record and a withheld record are distinct paths, but both MUST retain explicit uncertainty. A capped assessment records a valid value no higher than the ceiling and cites its inspected evidence; a withheld assessment records no value, one closed withheld reason, and the same mandatory citations. Neither path may silently imply that the missing source, locator, rights, or contradiction issue is resolved.

## 6. Independence and corroboration (AC-215/AC-217)

`require_corroborated_claim(claim_key, supports, memberships, independence, *, as_of)` is pure and evaluates a same-scoped-claim support set as follows:

1. It MUST validate `claim_key` as 64-character hexadecimal text. Every supported `ObservedClaim.claim_key` MUST equal that key; a different proposition or scope fails under the claim-scope diagnostic in §8.1.
2. It uses only family memberships active at `as_of`: `joined_at <= as_of` and either no `removed_at` or `removed_at > as_of`. It deduplicates supports by card ID, merges cards sharing any active family into a group, and makes each card with no active family its own singleton group.
3. It may establish independence only with an explicit B5 `IndependenceAssessment`. For cards in different groups, it calls `require_distinct_sources` with the argument order matching the assessment's stored card order and `evaluated_at=as_of`. A `SourceIndependenceError` means that pair does not qualify; it is not propagated as proof.
4. `UNKNOWN`, `RELATED`, and `COMMON_UPSTREAM` relationships, and missing or expired assessments, MUST NOT count as independent. Absence of an assessment MUST NEVER be interpreted as independence.
5. A cross-group edge exists only when at least one cross-group card pair qualifies. The independent-family count is the size of the largest deterministic set of group nodes whose every pair has such an edge (maximum clique). With fewer than two qualifying groups, the function fails under the insufficient-independence diagnostic in §8.1 and names the collapse reason safely.
6. Success returns a `CorroborationResult` containing `claim_key`, `independent_family_count`, `family_groups: tuple[frozenset[UUID], ...]`, and `qualifying_assessment_ids`. Each family group is its frozen card-ID set; the tuple MUST use deterministic sorted group order. An E3 assessment MUST require at least two independent families and retain those assessment IDs as its basis.

## 7. Display and query rules

`summarize_grades(records, *, scope_kind, target_id)` filters by the scope identity and respects supersession within the supplied records: a record that another input record supersedes is not live. For each `GradeKind`, it selects the latest live record using `(recorded_at, str(id))` as a deterministic maximum.

`GradeSummary` contains four separate optional fields—`maturity`, `reliability`, `credibility`, and `confidence`—alongside `scope_kind`, `target_id`, and `limitations`. It MUST NOT merge these fields or calculate a combined score. `limitations` is the sorted union of any withheld reason codes on the four selected records, so unavailable evidence remains visible to readers.

## 8. Stable-code registry

`GradeAssessmentError` is a `RuntimeError` subclass with stable `code` and `safe_message`. Consumers MUST use the stable code rather than exception text.

### 8.1 Assessment error codes

| Stable error code | Trigger condition |
| --- | --- |
| `grade_invalid_scale_value` | A supplied grade value is not a member of the selected kind's scale. |
| `grade_scope_scale_mismatch` | A grade kind is used with an incompatible scope kind. |
| `grade_missing_citation` | Evidence-envelope IDs or source-card-version references are empty. |
| `grade_invalid_withheld_reason` | A withheld reason is outside the closed set. |
| `grade_e3_missing_independence_basis` | An E3 value lacks cited independence assessment IDs. |
| `grade_exceeds_ceiling` | Requested maturity is above its derived ceiling. |
| `grade_claim_scope_mismatch` | A supplied support claim has another claim key. |
| `grade_insufficient_independence` | Fewer than two independent family groups qualify. |

The module exports this exact closed set as `ASSESSMENT_ERROR_CODES`; constructing a `GradeAssessmentError` with a code outside the set fails with `ValueError`, so no additional public assessment code can exist at runtime.

### 8.2 Ceiling reason codes

| Ceiling reason code | Cap condition |
| --- | --- |
| `grade_ceiling_missing_source` | Source reference absent or missing-source reason present. |
| `grade_ceiling_missing_locator` | Locator absent or open. |
| `grade_ceiling_unknown_rights` | Rights absent or empty. |
| `grade_ceiling_unresolved_contradiction` | An unresolved or ambiguous contradiction exists. |

### 8.3 Withheld reason codes

| Withheld reason code | Withheld condition |
| --- | --- |
| `grade_withheld_unknown_rights` | Rights are unknown. |
| `grade_withheld_missing_locator` | Locator is missing. |
| `grade_withheld_unresolved_contradiction` | Contradiction remains unresolved. |
| `grade_withheld_insufficient_independence` | Independence support is insufficient. |
| `grade_withheld_missing_source` | Required source is missing. |

## 9. Acceptance-criteria traceability

| Criterion | Mechanism | Where enforced |
| --- | --- | --- |
| AC-213 | Frozen grade records require citations, version pins, rubric version, and E3 independence basis. | §4 |
| AC-214 | Pure maturity-ceiling derivation caps missing/reconstruction/rights/contradiction conditions and rejects excess maturity. | §5 |
| AC-215 | Corroboration accepts only explicit valid independence evidence; unknown or absent relationships never qualify. | §6 |
| AC-216 | Four separate scales, compatible scopes, separate summary fields, and structural absence of conversion or opportunity inheritance. | §§2–3, §7 |
| AC-217 | Corroboration validates one claim key, groups active families, and counts only pairwise-qualified independent groups. | §6 |

## 10. Non-goals and later barriers

This B9 contract does not:

- rank prospects or opportunities;
- direct collection work;
- infer identity;
- suppress contradiction evidence;
- plan intent or action;
- create SQLite models, migrations, repositories, or persistence; or
- provide a combined score, crosswalk, or automatic grade conversion.

Persistence and query surfaces are later barriers. They MUST consume this contract, preserve its append-only provenance and four-scale separation, and MUST NOT reinterpret an assessment as prospect ranking, collection direction, identity inference, contradiction resolution, or intent planning.
