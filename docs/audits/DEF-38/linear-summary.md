## DEF-38 [B01] — Baseline pinned, first acquisition path isolated (read-only pass)

**Packet:** `docs/audits/DEF-38/` on branch `hermes-subagent/subagent-sa-0-1fc9dd03` (worktree at HEAD). Self-review by subagent; independent review pending.

**Baseline**
- base `6c21c3a76c4098c29b743a8aed77c7c7faa31b83` = `origin/main` ✔ · head `59e2d670f674b8cf7b32f19ef9e7f8d895d4422f` = HEAD of `feat/graph-projection-connectors` ✔ (13 ahead / 0 behind; `git describe` = `v1.2.11-25-g59e2d67`).
- Main checkout is dirty: 28 modified + 59 untracked (inventoried in `baseline.json`, untouched). 9 of the 16 traced files differ in the dirty copy vs HEAD — digests in the manifest are HEAD-only.
- 18 git worktrees (16 prunable) listed, none pruned. Toolchain: uv 0.11.19, CPython 3.12.13 (venv), node 22.23.2, pip3r 1.2.6.

**Chosen path (smallest callable)**
`Website(url)` → `WebsiteToText` (`flowsint-enrichers/…/website/to_text.py:63`) → `Enricher.execute_structured` (`flowsint-core/…/enricher_base.py:566`) → `scan` → `_fetch_text_async` (httpx HTTP/2 → requests fallback, `to_text.py:141-189`) → `BeautifulSoup.get_text` → `Phrase`. Full trace with every sink at file:line: `acquisition-path-trace.md`; manifest (inputs, status contract, limits, retention, combined sha256 `6fc1b8c5…56e4`): `first-capability-manifest.json`.

**Disabled / excluded for the slice**
- Neo4j writes (`postprocess` create_node/relationship → `GraphService.flush` → `Neo4jConnection.execute_batch`) — via ctor `graph_service` injection; there is **no env switch** (`enricher_base.py:141`).
- Postgres log rows + Redis events (`LoggerSingleton._flush_batch/_emit_event`) — via `Logger` substitution (test-suite precedent). `REDIS_URL` must still be set for import.
- Celery `run_enricher` + Scan rows, API launch route (Neo4j read of inputs), QUIC/HTTP3 (TLS verify off), GPU cupy/cudf.
- Jev, MetaHarness: 0 hits in tracked source at HEAD. Connector/vault credentials: not used by this enricher.

**Test baseline** (`make test` equivalent, `uv run --frozen pytest -x -q`, env `AUTH_SECRET`/`REDIS_URL` dummies as in CI; fresh venv from existing `uv.lock`, no lock change)
- flowsint-types PASS 54/54 · flowsint-core PASS 382/382 · flowsint-enrichers PASS 33/33
- flowsint-api **FAIL**: 3 failed / 9 passed / 1 skipped — all `tests/test_events_auth.py` (`AttributeError: 'NoneType' object has no attribute 'rsplit'` at `app/api/deps.py:50`, token=None passed to `jwt.decode`). Preexisting, not fixed.
- Side effect observed: core/enrichers runs try a real Postgres connection (`localhost:5433`) from the Logger batch thread using the developer's untracked `.env` — test-isolation gap, recorded.
- Lint/typecheck: NOT_RUN — no entrypoint wired in Makefile/CI.

**Blast (pip3r 1.2.6): DEGRADED**
- `--direction both` rejected by the tool; ran upstream/downstream separately with `--no-auto-index` (default is auto-index=true). Index at run time: lastCommit == HEAD.
- 3 of 4 runs `completeness=partial` (`no-call-edges` / `query-interrupted`), `risk=UNKNOWN`, 0 upstream callers found although `rg` shows `run_enricher`, `orchestrator.py:430`. The one complete run (`Enricher.execute` downstream, risk HIGH, 23 edges) stays inside `enricher_base.py`/`logger.py` and never reaches the graph sink. Manual trace is authoritative.
- The shared `.gitnexus` index was rewritten by another process ~60 s after the last run (lastCommit now empty) — not caused by this pass.

**Deployment findings** (`deployment-findings.md`, recorded not fixed)
- F1 HIGH: `docker-compose.prod.yml` pulls `ghcr.io/reconurge/flowsint-{api,app}:latest` (upstream) while origin is `n4s5ti/fl0sint`; `make prod` never deploys fork source.
- F2 MEDIUM: `latest` tag; HEAD is 25 commits past `v1.2.11`; version strings 1.2.8 (pyproject) / 1.0.0 (package.json) / v1.2.11 (tags) disagree.
- F3 LOW: API image OCI source label points at upstream repo.

**Acceptance** (`acceptance.md`): (a) PASS manual / UNKNOWN tool-assisted · (b) PASS (Neo4j avoidable only via DI) · (c) PASS.

**Blockers / carry-forward**
1. Neo4j-free execution requires a null `GraphService` seam — decide where the slice injects it.
2. `Enricher.execute` swallows all errors → use `execute_structured` for an honest status contract.
3. Dirty working tree overlaps the traced files; commit or discard before building the slice, else digests won't match.
4. pip3r Blast unusable for impact analysis on this repo until Python attribute-call edges are indexed.

Rollback: delete `docs/audits/DEF-38/` (additive files only).
