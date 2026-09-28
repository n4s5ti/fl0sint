# DEF-40 [B03] — Acceptance fixtures and completion evidence

Base: `dev` @ `02467e96ac71708863776ad2732980344a9532e7`. Branch: `work/def-40-fixtures`. Worktree: `.worktrees/def-40-b03`. Tools: pip3r 1.2.6, Python 3.14.5 (CLI) and 3.12 (package venv).

The change is additive only. `post-edit.json` shows 0 tracked modifications, and every package/config hash matches `baseline.json`. No scraper, production, model, graph-write or live-data change is included.

Verdict scale: PASS means observed in the listed evidence. UNKNOWN means not observed, or observed only in degraded form. Paths are relative to `docs/audits/DEF-40/`.

## Acceptance fixtures

| Criterion | Verdict | Evidence |
|---|---|---|
| Known source spans and expected contacts can be checked without models or internet access | **PASS** | `raw/validate.json` verifies every observation's `[start,end)` byte span against retained UTF-8 bytes and sha256. `test_accepted_labels_are_grounded_in_retained_source_spans` requires the subject, company and value of every accepted label to appear in the quoted evidence. The CLI uses the stdlib only, serves on loopback only, and uses `ProxyHandler({})`. Browser off-host requests: `[]` (`browser-proof.json`). |
| Two identical URL inputs have distinct occurrence IDs; shared content can be deduplicated | **PASS** | `input-named` (index 1) and `input-named-repeat` (index 15) point to the same source/route and are both checked by `raw/smoke.json`. `copy-a`, `copy-b` and `named` have distinct routes but share one `origin_id` and one sha256. `test_duplicate_url_inputs_keep_distinct_occurrences_but_share_content`. Hashes are integrity markers, not identity. |
| Legitimate empty output and actual execution failure have different expected statuses | **PASS** | `success_empty` (`/empty`, 200, no observations), `failure_http` (`/failure`, 503) and `failure_timeout` (`/slow`, 750 ms against a 100 ms deadline) are enforced by the loader and observed over real HTTP in `raw/smoke.json`. Covered by `test_empty_success_and_execution_failures_are_distinct_labels` and mutation M3/M4 (`mutation-check.json`). |

## Ordered steps

1. **Static fixtures.** 12 HTTP cases (15 occurrence results) in `raw/smoke.json`, all `passed: true`. They cover static text, contact spans, malformed HTML, redirects, slow and failing pages, same-name people at different companies, generic inboxes, copied sources and stale roles.
2. **Dynamic fixtures (optional).** `render-only-contact` and `public-click-reveal-contact`. Smoke reports them as `NOT_RUN_BROWSER`. Real Chromium proof is in `browser-proof.json`: before and after text match, the target is visible, and there were 0 off-host requests.
3. **Stable IDs and expected observations.** Source, occurrence and observation IDs are authored in `manifest.json`. Extraction labels (`observations`) are kept separate from factual labels (`acceptance`: accepted, rejected or unresolved). The loader rejects orphan observations. `test_trap_fixtures_keep_their_negative_verdicts` pins the negative verdicts.
4. **Provenance, retention, hashes and outcomes.** Covered by the `provenance`, `retention` (policy sha256 checked) and per-artifact `sha256` fields. All data is synthetic: reserved `.example` domains and fictional people.
5. **Development vs. locked evaluation.** `development_only: true`. `provenance.reviewer_requirement` and `grouped_split_requirement` must be satisfied before any promotion. No statistical claim is made.

## Audit evidence

| Item | Verdict | Evidence |
|---|---|---|
| Base/head/package/config hashes | **PASS** | `baseline.json` and `post-edit.json` (deliverable sha256, config unchanged) |
| Pre-edit Blast (`WebsiteToText` up/down) | UNKNOWN, as expected | `blast-before-*.json`: `no-call-edges` / `query-interrupted` (DEF39-F2/F3 gap) |
| Post-edit Blast (`WebsiteToText`) | UNKNOWN, unchanged | `blast-after-up/down.json` are identical in risk and completeness to the pre-edit runs. Production callers are unaffected (no tracked change). |
| Post-edit Blast (new symbols) | **PASS**, with a gap | `load_manifest`/`smoke` LOW complete; `fixture_server` MEDIUM complete. The only callers are `main` and `smoke`. The pytest module loads the script through `importlib`, which the graph does not see; this is disclosed. |
| Drift: predicted vs. actual | **PASS** | `post-edit.json.drift_comparison`: 0 paths outside the planned scope and 0 tracked changes. `drift-after.json` shows total 0 after the isolated reindex. The shared `.gitnexus/` was never written. |
| Scoped Hunt | UNKNOWN (preexisting) | `hunt-before.json` and `hunt-after.json`: coverage 0 with the same self-contradictory pip3r finding (DEF39-F6). Hunt cannot probe this Python CLI. |
| Tests | **PASS** | `raw/pytest.txt`: 24 passed. `raw/enrichers-suite-{base,head}.txt`: 132 → 156 passed, 0 failures. The psycopg teardown log noise appears in both runs and is preexisting. |
| Test adequacy | **PASS** | `mutation-check.json`: 5 of 5 injected defects were killed. |
| Independent adversarial review | **PASS**, with a limitation | `review.json`: reviewer subagent, 2 rounds. R1 and R3a resolved; R2 and R3b accepted as documented. The review was spawned by the implementing session, so it is not independent human review. |
| Entrypoint callable; docs match output | **PASS** | `raw/cli-help.txt`, `raw/validate.json`, `raw/smoke.json`. `--port` outside `serve` is rejected (exit 2). |
| Revert/disable/recovery | **PASS** | `recovery.md` |

## Process note

Prior-session work used `.worktrees/def-40-fixtures`, which turned out to be a directory copy with no `.git` link. It contains a recursive self-copy of about 5.6 GB. Its untracked DEF-40 files were ported unchanged into a real `git worktree` at `.worktrees/def-40-b03`, and the fixes listed above were applied there. The pre-edit Blast, drift and hunt runs used the copy's isolated index at the same base content. The copy was left untouched.
