# DEF-23 — P0 baseline, tooling procedure and controlled fixtures

## Release boundary

Source revision: `44f62863d208703982f8f50e6fcda30363a9a27d`, fast-forwarded into local `dev` from `02467e96`. This releases **baseline discovery, a documented audit procedure, and synthetic development fixtures**. It does not release a standalone production scraper, hosted execution, factual review engine, crawler, graph publication, or statistical evaluator. No remote push, deployment or live collection occurred.

Enabled: `python3 scripts/acquisition_fixtures.py validate|smoke|serve`; optional synthetic browser pages. The CLI imports only stdlib, binds its server to `127.0.0.1`, disables proxy inheritance, serves listed routes rather than filesystem paths, bounds bodies/delays/concurrency, and never writes execution records or graph data. Browser fixture support is not production browser-acquisition support.

## Required criterion results

| Criterion | Result and evidence |
|---|---|
| Children complete; versions reconciled | PASS for accepted P0 scope. DEF-38/39 prior closure explicitly accepted discovery/tool limitations; DEF-40 closure `68b08802-192d-4f2a-beea-1f9a8ecd1557` records merged runtime checks. `raw/source-pins.json` compares all 16 historical trace paths with current SHA-256; `source-reconciliation.md` adjudicates the nine changed files. Historical production runtime is not certified. |
| Public milestone interface works | PASS. `raw/validate.stdout`: 15 sources, 15 observations, 16 occurrences, 14 cases. `raw/smoke.stdout`: 12 HTTP cases, 15 passing occurrence results, two explicitly unrun browser cases. `raw/browser.json`: both browser pages subsequently exercised in real Chromium with visual confirmation and zero off-host requests. |
| Impact, drift, Hunt and Doctor evidence reviewed | PASS for evidence review, **not** universal tool assurance. `raw/commands.json` and `raw/refresh-commands.json` preserve exact argv/cwd/time/exit/output hashes. Initial drift was 19 audit-document entries, zero stale source symbols; final isolated drift is zero. Scope and tool limitations below remain UNKNOWN where stated. |
| Negative cases and blocking findings | PASS for fixture path. `raw/integration-checks.json`: focused 24 tests and full enrichers 156 tests passed. DEF-40 negative corpus tests cover tampering, identity, attribution, empty/failure distinction, route scope and trap verdicts; two-round fixes/dispositions in `../DEF-40/review.json`. P0 separate reviewer reproduced CLI output and identified release-document and source-reconciliation requirements addressed in this packet. |
| Source proof, identities, truth and spent resources | PASS within fixture scope. Retained byte spans and hashes verified; distinct repeated-input occurrence IDs survive shared content. Author labels are separate from extracted observations; stale roles remain unresolved and cross-company contacts rejected. Deterministic HTTP replay was independently reproduced. No production acceptance state or spent acquisition budget is created or modified by this fixture tool; those later-runtime guarantees are not claimed. |
| Capabilities, limits and unverified claims explicit | PASS. This release boundary, residual debt below, per-result NOT_RUN_BROWSER, synthetic provenance and development-only manifest expressly exclude unsupported claims. |
| Separate review and safe recovery | PASS under the previously accepted agent-review standard, not independent human review. `P0GateEvidenceReview` is a separate read-only reviewer context (resolved model anthropic/claude-fable-5), spawned by this implementing session; DEF-40 used another separate reviewer. Recovery below preserves history and unrelated data. |

## Tool interpretation and impact boundary

- Reviewed DEF-38/39 production-symbol reports remain UNKNOWN: dynamic registration and `WebsiteToText` no-call-edges/query-interrupted were not repaired.
- New fixture symbol `load_manifest` resolves `main -> load_manifest` with LOW/complete impact in the refreshed isolated index. The `importlib` pytest consumer is invisible to this graph: its existence and behavior are established by source tracing and actual pytest, not graph completeness.
- Doctor reports safetyScore 90 and `fragile_single`, but executes no test commands (`results: []`); semantic/test assurance from Doctor is UNKNOWN. This advisory does not override tests or graph gaps.
- Hunt discovers/probes zero commands and retains the historical pip3r root-help finding; coverage is UNKNOWN, never a clean security pass.
- Index operations target `.worktrees/def-40-b03`, not the shared index. Explicit refresh returned `planAction: noop`; the earlier isolated Doctor run may already have refreshed it. Final drift is independently observed zero; no assumption about which operation did the refresh is needed. Shared-index hashes match before/after both batches.
- DEF-40 added exactly 50 files under its four planned path envelopes; no existing production source or configuration was changed by that commit. Gate additions are evidence only under `docs/audits/DEF-23/`. The old copied directory and other worktrees remain untouched.

## Residual debt and evidence corrections

- DEF-38 historical pytest and uv-sync raw logs are missing from its committed packet. Its types/core/API counts are historical reported results, not independently reproduced here. The three historical API `test_events_auth.py` failures remain baseline debt; they were not rerun or fixed.
- DEF-39 shared-index mutation is a historical FAIL accepted by the prior user decision, not retroactively PASS. Current shared-index non-mutation has separate machine evidence.
- Legacy transport may swallow failures, mispair completion-order output, block on synchronous fallback, and disable QUIC TLS verification; these are baseline/downstream concerns, not implemented or endorsed by the fixture release.
- Current package tests retain known deprecation warnings and local Postgres teardown logging noise.
- DEF-40 mutation M1–M4 ran against the 22-test intermediate suite; M5 ran against 24 tests. This packet does not claim all mutations reran against the final suite.
- Historical browser `captured_at` is author-supplied, not a precise machine capture. Current `raw/browser.json` records the actual browser invocation time. Synthetic source dates and corpus `as_of` are authored data, not collection timestamps.
- Text-kind observation values remain free-form summaries, not independently validated factual values. All corpus labels require independent adjudication and origin/entity-grouped splitting before locked evaluation.
- No production audit-packet validator is enabled: DEF-90 is a future dependency. This packet receives structural/hash checks and separate substantive review, not a claimed implementation of DEF-90.

## Recovery

To disable fixtures without deleting evidence, stop invoking the CLI and omit its test module from the local test selection. To remove the additive implementation, use a reviewed `git revert 44f62863` after checking for downstream consumers and local edits; conflicts remain possible after later work, so do not force a revert. The gate-evidence commit can likewise be reverted separately. Preserve committed source artifacts and their hashes in history. No live ledger, input lineage or consumed production budget is rolled back. Do not delete the 5.6 GB historical copy without proving its complete unique-content and live-writer status.
