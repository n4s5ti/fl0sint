# DEF-38 — Scope plan for the first acquisition slice

Pinned revision: base `6c21c3a76c4098c29b743a8aed77c7c7faa31b83` (origin/main) → head `59e2d670f674b8cf7b32f19ef9e7f8d895d4422f` (HEAD of `feat/graph-projection-connectors`, 13 commits ahead, 0 behind). Both match the PRD pins (`baseline.json`).

## Chosen path
`website_to_text` — `WebsiteToText` (`flowsint-enrichers/src/flowsint_enrichers/website/to_text.py:63`) driven through `Enricher.execute_structured` (`flowsint-core/src/flowsint_core/core/enricher_base.py:566`) with an injected null `GraphService`. Full trace: `acquisition-path-trace.md`. Digest of the traced files: `first-capability-manifest.json` (combined sha256 `6fc1b8c5…56e4`).

Why this one: single-URL input (`Website.url`), no credentials, no vault params, output is a plain `Phrase`, transport is httpx with an optional sync fallback, and the only mandatory external couplings (Neo4j, Postgres, Redis) are reachable purely through two injectable seams (`graph_service` ctor arg; `Logger` module attribute).

## In scope (reuse)
| Component | Location | Note |
|---|---|---|
| `Website`, `Phrase` types | `flowsint-types/src/flowsint_types/{website,phrase}.py` | pydantic; `url: HttpUrl` primary |
| `Enricher.execute_structured` orchestration | `enricher_base.py:566-625` | per-input `InputOutcome` status/diagnostic |
| `Enricher.preprocess` / `_validate_single_input` | `enricher_base.py:392-442` | string → `{url: …}` coercion |
| `WebsiteToText.scan`, `_fetch_text_async` (httpx + requests fallback), `_extract_text` | `to_text.py:113-189, 285-289` | default params: `max_concurrency=10`, `request_timeout=10`, `enable_quic=False` |
| `EnricherRegistry` name lookup | `flowsint-enrichers/src/flowsint_enrichers/registry.py` | optional; direct import avoids `load_all_enrichers` importing every enricher |

## Out of scope (exclude / disable)
| Path | Seam used to disable | Evidence |
|---|---|---|
| Neo4j writes (G1–G4) | pass `graph_service=<null GraphService>` to ctor (`enricher_base.py:128,138-139`); `postprocess` guard `to_text.py:320` | `service.py:342-372` is the only Neo4j-backed factory |
| Neo4j reads of inputs (G5) | do not use `POST /api/enrichers/{name}/launch` | `enrichers.py:129` |
| Postgres logs + Redis events (L1–L3) | monkeypatch/inject `flowsint_core.core.logger.Logger` with a `LoggerProtocol` stub | `flowsint-enrichers/tests/conftest.py:18-25` precedent |
| Celery `run_enricher` + Scan rows (P1) | do not import `flowsint_core.tasks.enricher` | `tasks/enricher.py:35-37` has import-time side effects |
| QUIC/HTTP3 (N2) | keep `enable_quic=False` | `to_text.py:204` disables TLS verify |
| GPU cupy/cudf (X1) | do not install `gpu` extra | `pyproject.toml` optional-dependencies |
| Jev, MetaHarness | nothing to disable — absent at HEAD | `rg -i 'jev|metaharness'` → 0 tracked hits |
| Connector credentials / vault | nothing to disable — `WebsiteToText` has no `vaultSecret` params | `to_text.py:85-108` |

## Environment contract for the slice
- Required env at import: `REDIS_URL` (any value; `config.py:15` raises KeyError otherwise). `AUTH_SECRET` only if `flowsint_api` is imported.
- Must NOT be required: `NEO4J_URI_BOLT/USERNAME/PASSWORD` — achievable only via `graph_service` injection (there is no env switch; `enricher_base.py:141`).
- `DATABASE_URL` is read by `postgre_db.py:9` at import but no connection is made until a `Logger` flush, which the stub prevents.

## Reproduction recipe (for the reviewer)
```
git -C /home/n4s5ti/Documents/dev/fl0sint worktree add <dir> 59e2d670f674b8cf7b32f19ef9e7f8d895d4422f   # or checkout in a clean clone
cd <dir> && uv sync --frozen --all-packages
AUTH_SECRET=x REDIS_URL=redis://127.0.0.1:6379/0 make test           # baseline: see tests.json (flowsint-api has 3 preexisting failures)
```
Trace roots: `WebsiteToText` (`to_text.py:63`), `Enricher.execute` (`enricher_base.py:632`), `Enricher.execute_structured` (`:566`), `GraphService.flush` (`service.py:305`), `Neo4jConnection.execute_batch` (`connection.py:141`), `LoggerSingleton._flush_batch` (`logger.py:117`).

## Not done in this pass (by design)
- No code changes, no test fixes, no dependency upgrades, no live fetches, no index rebuild.
- Lint/typecheck not run — no entrypoint exists in Makefile/CI (`tests.json`).
- Frontend (`flowsint-app`) untouched.

## Risks carried forward
1. Dirty main checkout modifies 9 of the 16 traced files (`first-capability-manifest.json → implementation_digest.files[*].main_checkout_differs_from_HEAD`). Any slice built from the working tree will not match the HEAD digest; decide whether those changes get committed first.
2. `Enricher.execute` hides failures (returns `[]`); use `execute_structured`.
3. Legacy `postprocess` pairing bug (trace §4.5) if the slice ever uses `execute` with >1 URL.
4. pip3r Blast is DEGRADED for this repo (`blast-before.json`); impact analysis must stay manual until the index resolves Python attribute calls.
