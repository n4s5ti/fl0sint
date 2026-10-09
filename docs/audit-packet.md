# Audit packet manifest and validator

`scripts/audit_packet.py` is a development-only, stdlib-only checker for the evidence
packet each Linear issue leaves in `docs/audits/<ISSUE>/`. It does not ship in any
package, and no scraper runtime imports or runs it. A valid packet means that the evidence
is complete, current and internally consistent. It does **not** mean the change behaves
correctly, and the validator never accepts a review, approves labels or updates Linear.

## Commands

```bash
# One packet, against HEAD and the current working tree
python3 scripts/audit_packet.py validate docs/audits/DEF-90
# Every packet touched since a base ref (the same command CI runs)
python3 scripts/audit_packet.py changed --base origin/main
# Machine-readable report
python3 scripts/audit_packet.py --json --report /tmp/report.json validate docs/audits/DEF-90
```

Exit status: `0` valid, `1` invalid, `2` git/usage error. In `changed` mode, any
`docs/audits/<ISSUE>/` directory that the diff touches must contain `packet.json`. The only
exceptions are the frozen pre-schema packets in `LEGACY_PACKETS` (DEF-23, DEF-38 to DEF-47
and DEF-105). These are reported as `LEGACY … not validated` and never count as passed. A
change may add files to a legacy packet, but modifying or deleting existing legacy evidence
fails with `E_LEGACY_EVIDENCE_CHANGED`. Files placed directly in `docs/audits/` are listed
as `UNVALIDATED`.

## `packet.json` (`schema: fl0sint.audit-packet/v1`)

Artifact paths are relative to the packet directory. Every other path is relative to the
repository root.

| Field | Meaning and what the validator checks |
|---|---|
| `issue` | `DEF-<n>`. It must equal the directory name. |
| `base_commit`, `head_commit` | Full SHA-1 values. `base_commit` must be an ancestor of `head_commit`, and `head_commit` must be an ancestor of HEAD. Between `head_commit` and the revision that published the packet, only files inside `docs/audits/<ISSUE>/` directories may change. That revision is the last commit touching the packet; if the packet has uncommitted edits, it is HEAD plus tracked working-tree edits. Later commits on a stacked branch belong to their own packets and do not make this one stale. Each packet directory is gated separately, so packet directories are also excluded from the scope check. |
| `source_tree` | Must equal `git rev-parse <head_commit>^{tree}`. |
| `configuration` | `{path: sha256}`. Each digest is recomputed from `git show <head_commit>:<path>`. |
| `tool_versions` | Non-empty `{tool: version}`. |
| `capabilities` | `graph`, `model`, `browser`, `network` booleans: the enabled-capability manifest for this change. |
| `artifacts` | `{id: {path, sha256}}`. The file must exist inside the packet directory and match its digest. |
| `index` | `{mode, analyzed_commit, drift}`. `analyzed_commit` must equal `head_commit`. `drift` names a post-edit drift analysis whose status is `PASS`. |
| `analyses[]` | `{id, phase: pre/post, kind: blast/drift/hunt/doctor/index/lexical, target, analyzed_commit, command, exit_code, status, artifact}`. Pre-edit analyses must have run against `base_commit` and post-edit analyses against `head_commit`. Required: a pre-edit `blast`, plus a post-edit `blast` and `drift`. |
| `commands[]` | `{id, command, exit_code, status, artifact}`. These are the test, smoke and mutation runs. |
| `coverage_limitations[]` | `{ref, description}`. Every `UNKNOWN`, `DEGRADED` or `BLOCKED` result must be listed. |
| `required_cases[]`, `cases[]` | Each required case must be `PASS` or a permitted `NOT_APPLICABLE`. A `PASS` case may cite only `PASS` evidence. The optional `case_totals` must match the cases. |
| `scope` | `predicted_paths` (globs; a trailing `/` marks a prefix), `predicted_symbols`, `actual_symbols` and `explained_changes[{path or symbol, reason}]`. |
| `mutations[]` | `{id, path, description, killed, killed_by, restored_sha256}`. The path must be inside the predicted scope, the mutant must have been killed, and the digest must match `head_commit`. |
| `findings[]` | `{id, summary, origin: introduced/preexisting, blocking, classification, link}`. |
| `baseline_debt[]` | `{id, description, evidence}`. Preexisting failures are reported back so they stay visible. |
| `label_changes[]` | `{path, reason}` for every changed `fixtures/*` or `*/fixtures/*` file. It must not carry approval fields. |
| `review` | `{status: pending/accepted/changes-requested, reviewer, independent, limitation, evidence}`. |
| `rollback` | `{procedure, document}`. `document` is an artifact id. |

Status vocabulary: `PASS FAIL SKIP UNKNOWN DEGRADED BLOCKED NOT_APPLICABLE`. Both
`exit_code` and `status` are always recorded, because exit 0 is not success. The
validator also reads raw pip3r payloads, following the rules in `docs/audits/DEF-39/audit-procedure.md`
§10. A result declared `PASS` is rejected (`E_SEMANTIC_MISMATCH`) when its artifact shows
any of the following:

- a blast with `risk UNKNOWN`, non-`complete` completeness, `indexStatus != ready` or a `reason`
- a drift with a summary total other than 0
- a hunt with `coverage.commandsDiscovered == 0`
- an empty artifact (`BLOCKED`)
- a payload that cannot be parsed or is not the declared kind's pip3r shape, such as truncated
  JSON, an error body, or a drift without `mode: drift`

`NOT_APPLICABLE` is accepted only when `requires_capability` names a capability that the
packet declares `false`. Pre-edit and post-edit analyses cannot be marked not-applicable to
satisfy the requirements.

## Failure output

```text
INVALID docs/audits/DEF-90 at <HEAD>
  error E_STALE_HEAD: HEAD changed source after head_commit: scripts/audit_packet.py
  limitation result hunt-post: UNKNOWN
  limitation case G1: NOT_APPLICABLE
  human review required: <pkg>-enrichers/tests/fixtures/acquisition/manifest.json
  review: pending (reviewer=None, independent=True); acceptance is not decided here
```

| Code | Cause |
|---|---|
| `E_SCHEMA`, `E_REF`, `E_ISSUE` | A field is missing or has the wrong type, an id is unknown, or the issue does not match the directory. |
| `E_PACKET_MISSING`, `E_PACKET_PATH` | `packet.json` is absent, or the packet is outside the repository. |
| `E_COMMIT`, `E_SOURCE_DIGEST`, `E_CONFIG_DIGEST` | A revision or digest does not match git. |
| `E_STALE_HEAD`, `E_STALE_ANALYSIS`, `E_STALE_INDEX` | The evidence describes another revision, or the index freshness check is not a `PASS`. |
| `E_ARTIFACT_MISSING`, `E_ARTIFACT_DIGEST`, `E_ARTIFACT_PATH` | Evidence is missing, altered or outside the packet. |
| `E_PRE_MISSING`, `E_POST_MISSING` | A required Blast or drift report is missing. |
| `E_SEMANTIC_MISMATCH`, `E_EXIT_STATUS`, `E_UNDISCLOSED` | A PASS is contradicted by its payload or exit code, or a non-pass result is undisclosed. |
| `E_CASE_MISSING`, `E_CASE_NOT_PASS`, `E_CASE_EVIDENCE`, `E_AGGREGATE_MISMATCH`, `E_NOT_APPLICABLE` | A required case is missing or not PASS, its evidence is weak, its totals contradict the cases, or its not-applicable status is not permitted. |
| `E_SCOPE_PATH`, `E_SCOPE_SYMBOL` | A changed or untracked file, or a changed symbol, is outside the prediction and has no explanation. |
| `E_LABEL_UNDECLARED`, `E_LABEL_SELF_APPROVAL` | A fixture or label change is undeclared, or the packet tries to approve it. |
| `E_MUTATION_*`, `E_FINDING_*`, `E_REVIEW` | A mutant survived or its restoration is unproven, a blocking finding is open or unclassified, or an accepted review has no named reviewer and evidence. |
| `E_LEGACY_EVIDENCE_CHANGED` | A change modified or deleted frozen pre-schema evidence. |

## Regenerating stale evidence safely

1. Never re-index the shared checkout. Create a real worktree with `/usr/bin/git worktree add`.
   The shell wrapper in this environment may turn that command into a directory copy that has
   no `.git`. Build an isolated index there by following `docs/audits/DEF-39/audit-procedure.md` §6.
2. Commit the source change first. Then run the post-edit drift (require total 0), blast,
   hunt and tests against that commit, and write the output to a scratch directory outside the
   worktree. Files written into an indexed worktree appear as `file_unindexed` drift.
3. Copy the outputs into `docs/audits/<ISSUE>/raw/`, set `head_commit`, `source_tree` and the
   post-edit `analyzed_commit` values to the new commit, then recompute the artifact digests.
4. Commit the packet by itself. Committing source together with the packet, or between
   `head_commit` and the packet commit, makes it stale until steps 2–4 are repeated.

## Release-gate reviewer use

Download the `audit-packet-report` artifact from the **Tests / Audit packet validity** job, or
run the `changed` command locally. Treat `packet_valid: true` as permission to begin
reviewing, not as a verdict. Then do the following:

- Read `non_pass_results` and `coverage_limitations`. Results marked UNKNOWN, DEGRADED or
  BLOCKED are not passes. Cases marked `NOT_APPLICABLE` were waived by a disabled
  capability; confirm that each waiver is legitimate.
- Confirm that the `baseline_debt` entries are the same preexisting failures that the base run
  showed.
- Inspect each `requires_human_review` fixture or label path yourself.
- Record your own decision. The report's `review` block echoes what the packet claims, and its
  `acceptance` field is always `not decided by this validator`.
