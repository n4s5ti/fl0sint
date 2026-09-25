# DEF-38 — Acceptance

Revision `59e2d670f674b8cf7b32f19ef9e7f8d895d4422f`. Self-review by subagent; **independent review pending**.

| Fixture | Verdict | Evidence |
|---|---|---|
| (a) Reviewer can reproduce the chosen revision and trace all reachable graph/network sinks from the entrypoint | **PASS** (manual trace) / **UNKNOWN** (tool-assisted) | Revision pinned and verified: `baseline.json → prd_pin` (base == origin/main, head == HEAD, merge-base == base, 13 ahead / 0 behind). Clean reproduction proven by creating a worktree at HEAD + `uv sync --frozen --all-packages` from the existing `uv.lock` (`tests.json → environment_setup`, `logs/uv-sync.log`). Sink enumeration with file:line for every graph write (G1–G5), log/DB/Redis sink (L1–L3, P1) and network call (N1–N3): `acquisition-path-trace.md §3`. Tool-assisted confirmation via pip3r Blast is DEGRADED — 3 of 4 runs `completeness=partial`, 0 upstream callers found, graph sink not resolved (`blast-before.json`) — so the tool leg is UNKNOWN, not PASS. |
| (b) First slice does not require Jev, MetaHarness, Neo4j or unused connector credentials | **PASS** (with one design constraint) | Jev / MetaHarness: 0 hits in tracked source at HEAD (`first-capability-manifest.json → disabled_paths_for_first_slice`). Connector credentials: `WebsiteToText` has no `vaultSecret` params (`to_text.py:85-108`); `EgressAuthorizer`/`destination_registry` only reached via `run_connector_template`. Neo4j: avoidable **only** by injecting `graph_service` into the ctor (`enricher_base.py:128,138-141`) — there is no env switch; `postprocess` guards on it (`to_text.py:320`). Postgres/Redis: avoidable by substituting `Logger` (precedent `flowsint-enrichers/tests/conftest.py`). `REDIS_URL` must still be *set* for import (`config.py:15`). |
| (c) Image/source mismatch is recorded as a deployment finding, not silently fixed | **PASS** | `deployment-findings.md` F1: `docker-compose.prod.yml` pulls `ghcr.io/reconurge/flowsint-*:latest` (upstream) while origin is `n4s5ti/fl0sint`; F2: HEAD is 25 commits past `v1.2.11`, version strings inconsistent. No file outside `docs/audits/DEF-38/` was changed (`git status` in the worktree shows only `docs/audits/DEF-38/`). |

## Additional checks
| Check | Verdict | Evidence |
|---|---|---|
| Test baseline captured per package | PASS | `tests.json`: types 54/54, core 382/382, enrichers 33/33, api 3 failed / 9 passed / 1 skipped (`tests/test_events_auth.py`, preexisting). Logs under `logs/pytest-*.log`. |
| Lint / typecheck baseline | NOT_RUN | no entrypoint in Makefile or CI; tools only declared as dev deps (`tests.json → lint, typecheck`). |
| Blast before | DEGRADED | `blast-before.json`; `--direction both` rejected by pip3r 1.2.6; `--no-auto-index` used; shared index untouched by this pass. |
| Dirty tree inventoried, untouched | PASS | `baseline.json → dirty_tree` (28 modified, 59 untracked); 9 of 16 traced files differ in the dirty checkout vs HEAD (`first-capability-manifest.json → implementation_digest.files`). |
| Worktrees listed, none pruned | PASS | `baseline.json → worktrees` (18 entries, 16 prunable). |
| `.gitnexus` not reindexed/deleted by this pass | PASS (with external event) | `--no-auto-index` on every call; index was rewritten by *another* process at 08:33:06Z, after this pass's last blast run at 08:32:03Z (`baseline.json → gitnexus_index`). |

## Rollback
The packet is additive files only under `docs/audits/DEF-38/` on branch `hermes-subagent/subagent-sa-0-1fc9dd03`. Removal = `rm -rf docs/audits/DEF-38/` (or drop the branch). No tracked source, lockfile, index, worktree or container state was modified. The worktree `.venv` created by `uv sync` is gitignored and disposable.
