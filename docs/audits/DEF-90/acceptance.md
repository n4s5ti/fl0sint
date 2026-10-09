# DEF-90 [S09] acceptance

- **Base:** `a59bdadf`. This is DEF-46, the latest accepted branch head. DEF-47 is still in review, so its branch was not used.
- **Head (source):** `e3b9416f`. The initial source was `b9af7f9b`; it was followed by stacked-branch staleness fixes `e8ea3afa` and `e3b9416f`. The packet is committed separately.
- **Worktree:** `/home/n4s5ti/Documents/dev/fl0sint-def90-packet`, branch `work/def-90-packet-validator`.
- **Machine-checked manifest:** `packet.json`. Run `python3 scripts/audit_packet.py validate docs/audits/DEF-90`; the result is VALID.

## Required fixtures

All fixtures run against real git state through `test_audit_packet.py`: 32 tests passed, and the mutation check killed 18 of 18 mutants.

| Case | Result | Test(s) |
|---|---|---|
| F1 Missing pre-edit report rejected | PASS | `test_missing_pre_edit_report_is_rejected`, `test_missing_or_altered_pre_edit_artifact_is_rejected`, `test_core_analysis_cannot_be_skipped_as_not_applicable` |
| F2 Stale HEAD or index metadata rejected | PASS | `test_source_change_after_head_commit_is_stale`, `test_uncommitted_source_edit_is_stale`, `test_stale_index_and_analysis_metadata_are_rejected`, `test_nonzero_drift_cannot_certify_index_freshness` |
| F3 Unknown or degraded result with exit 0 is not PASS | PASS | `test_unknown_blast_with_exit_zero_is_not_accepted_as_pass`, `test_degraded_hunt_with_exit_zero_is_not_pass`, `test_unrecognizable_payload_declared_pass_is_rejected[*]`, `test_pass_with_nonzero_exit_is_rejected` |
| F4 Untracked or new file outside scope flagged | PASS | `test_untracked_file_outside_scope_is_flagged`, `test_committed_change_outside_scope_and_unpredicted_symbol_are_flagged` |
| F5 Failing or skipped case not hidden by green totals | PASS | `test_required_case_failure_is_not_hidden_by_green_totals[FAIL,SKIP]`, `test_missing_required_case_is_rejected`, `test_case_id_cannot_shadow_a_result_id` |
| F6 Disabled graph/model features need no irrelevant checks | PASS | `test_disabled_capability_permits_only_its_own_checks_to_be_not_applicable` |
| F7 Coherent packet passes; review stays separate | PASS | `test_complete_packet_is_valid_and_review_stays_separate`. Also: this packet validates, and the dogfood negatives in `raw/post/dogfood-negative.txt` fail as expected. |
| F8 Scraper runtime never imports or invokes the validator | PASS | `test_scraper_runtime_does_not_import_or_invoke_validator[runtime,control]`. A subprocess audit hook watches the real WebsiteToLinks capture crawl on loopback. The control run proves the hook detects a validator import when one happens. |

Fixture and label surfacing are covered by `test_fixture_label_change_is_surfaced_and_cannot_self_approve`. The changed-scope CLI and frozen legacy evidence are covered by `test_changed_scope_cli_validates_packets_and_freezes_legacy_evidence`.

## Blast and drift

- **Pre-edit** (isolated index at base, drift 0):
  - `audit_packet`: UNKNOWN / target-symbol-unresolved. This is the expected absence result.
  - `load_manifest` upstream: LOW, complete.
  - `load_manifest` downstream: HIGH, complete.
  - `doctor --hunt`: BLOCKED (exit 1; no CLI target).
- **Post-edit** (index rebuilt at head, drift 0 before and after the analyses):
  - New symbols `validate_packet`, `validate_changed`, `main` and `observed_status`: complete and ready. Their upstream callers are only inside `scripts/audit_packet.py`; no package under `src` reaches them.
  - `load_manifest` blasts: identical before and after.
  - Hunt with `--hunt-target`: DEGRADED (0 commands discovered).
- **Graph gaps:** the CI YAML and the importlib test consumer are invisible to the graph (DEF39-F3). They are covered by the CI trace and the F8 runtime test.
- **Scope:** the planned paths match the actual paths exactly. No existing symbol was modified, and the new private helpers are explained.

## Tests (enrichers suite at base vs head, other packages run once)

- **enrichers:** 185 passed at base; 217 passed at head (185 + 32 new).
- **types:** 54 passed.
- **core:** 1 failure, preexisting: the `/tmp/def45-live-fixture.html` fixture.
- **api:** 7 failures, preexisting: `test_events_auth` ×3 and `test_sqlite_migrations` ×4.

The core and api failure sets are identical at base `a59bdadf` (in the main checkout, at the same commit) and at head. They are recorded as `baseline_debt` and are not hidden.

## Review

An independent `reviewer` subagent reviewed the change over five rounds and accepted it. It raised R1–R8, all fixed and re-verified by the same reviewer. Coordinator finding C1 (the gate would have blocked DEF-47) is fixed by adding DEF-47 to `LEGACY_PACKETS`. D48-1, from the hosted run on stacked PR #3, is fixed by measuring staleness to the packet's publishing revision. The reviewer is an AI subagent; Linear acceptance remains with the owner.

## Open and not claimed

- **T-CI-HOSTED: PASS.** PR #2 run 37883849516: the Audit packet validity job passed. The hosted Python tests job fails on dependency resolution; the same dependency error appears in the fetched logs for `main` b0c82d8d and def-43, so it is preexisting and recorded as baseline debt.
- **Hunt coverage of this change is 0** (DEGRADED/BLOCKED). It is disclosed, not passed.
