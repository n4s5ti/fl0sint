# DEF-38 — Acquisition path trace: URL → text → sinks

Revision: `59e2d670f674b8cf7b32f19ef9e7f8d895d4422f` (branch `feat/graph-projection-connectors`).
All `file:line` references are HEAD content (read from a clean worktree), **not** the dirty main checkout. Symbols were located with `rg` + file reads; pip3r Blast was run but is DEGRADED (see `blast-before.json`) and was not relied on for this trace.

## 1. Entry points into the path

| # | Caller | Location | Reaches |
|---|--------|----------|---------|
| E1 | HTTP `POST /api/enrichers/{enricher_name}/launch` → `launch_enricher` | `flowsint-api/app/api/routes/enrichers.py:117-160` | reads input nodes from **Neo4j** (`GraphService.get_nodes_by_ids_for_task`, `service.py:156`), then `celery.send_task("run_enricher", …)` (`enrichers.py:144-152`) |
| E2 | Celery task `run_enricher` | `flowsint-core/src/flowsint_core/tasks/enricher.py:63-129` | creates a **Postgres** `Scan` row (`:76-82`), optional vault (`:86-93`), `ENRICHER_REGISTRY.get_enricher` (`:98`), `asyncio.run(enricher.execute(values=…))` (`:114`), writes `scan.details` to Postgres (`:116-118`) |
| E3 | Flow orchestrator | `flowsint-core/src/flowsint_core/core/orchestrator.py:430` `await enricher.execute(enricher_inputs)` | same downstream as E2 |
| E4 | Template test route | `flowsint-api/app/api/routes/enricher_templates.py:203` `execute_structured` | TemplateEnricher only — not WebsiteToText |
| E5 | Direct Python call | `Enricher.execute` (`enricher_base.py:632`) / `Enricher.execute_structured` (`:566`) | **smallest callable path** (below) |

Module import side effects that fire before any of the above:
- `flowsint_core/__init__.py` star-imports `core.celery`, `core.config`, `core.graph`, `core.logger`, `core.postgre_db` → `config.py:15` does `os.environ["REDIS_URL"]` (KeyError if unset), `postgre_db.py:15` calls `create_engine(DATABASE_URL)` (lazy, no connect), `celery.py:4` builds a Celery app (lazy), `graph/connection.py:200-214` builds the Neo4j driver only if `NEO4J_*` env are all set (lazy driver, no connect).
- `tasks/enricher.py:35-37` runs `load_all_enrichers()` and `db = next(get_db())` at import (opens a Postgres session object; connect is lazy).

## 2. Smallest callable path (E5)

```
Website(url=…)                                   flowsint-types/src/flowsint_types/website.py:10-17 (HttpUrl, primary)
  → WebsiteToText(sketch_id, scan_id, params, graph_service=?)  to_text.py:63 ; ctor Enricher.__init__ enricher_base.py:121-144
      ctor: create_graph_service(sketch_id, enable_batching=True) enricher_base.py:141  ← ONLY if graph_service not injected
             → Neo4jGraphRepository()  service.py:364 → Neo4jConnection.get_instance() repository.py:33 → connection.py:74-82
               (raises ValueError "Neo4j connection credentials are required" if NEO4J_* env missing, connection.py:59)
  → Enricher.execute(values)                       enricher_base.py:632
      Logger.info("started")                       :634  → SINK L (below)
      async_init()                                 :636 → build_params_model :151, resolve_params :155 (no vaultSecret params → no vault access)
      preprocess(values)                           :637 → _validate_single_input :392-406 (str → {url: str}); invalid dropped :431
      scan(preprocessed)                           :638 → WebsiteToText.scan to_text.py:113-136
          Semaphore(max_concurrency) :119
          _fetch_text_async(url, timeout, enable_quic)  to_text.py:141-189
              1. httpx.AsyncClient(http2=True, follow_redirects=True).get(url)  :151-164   → NETWORK N1
              2. (opt-in) _fetch_via_quic  :169-175 → :194-280  aioquic, verify_mode=False :204 → NETWORK N2
              3. requests.get(url, allow_redirects=True) :178-183 (sync, blocks loop)      → NETWORK N3
              on total failure: Logger.error(...) :185-188 → SINK L ; returns None
          _extract_text(html) :285-289  BeautifulSoup(html,"html.parser").get_text(" ", strip=True)
          Phrase(text=text) :128  (flowsint-types/src/flowsint_types/phrase.py:9-20)
      postprocess(results, preprocessed)           :639 → WebsiteToText.postprocess to_text.py:294-331
          GPU dedupe/batch (only if cupy/cudf importable) :301-315
          if self._graph_service:                  :320
              create_node(website) :321 → enricher_base.py:659-678 → GraphService.create_node_from_flowsint_type service.py:110-140 → repository.add_to_batch repository.py:84-103   → SINK G (batched)
              create_node(phrase) :323                                                                                                    → SINK G
              create_relationship(website, phrase, "HAS_INNER_TEXT") :324 → enricher_base.py:680-702 → service.py:160-193 → add_to_batch  → SINK G
              log_graph_message(...) :327 → enricher_base.py:704-711 → service.py:295-303 → Logger.graph_append                          → SINK L
      self._graph_service.flush()                  :642 → service.py:305-308 → repository.flush_batch repository.py:159-171 → Neo4jConnection.execute_batch connection.py:141-162  → SINK G (actual Cypher write)
      Logger.completed("finished")                 :645  → SINK L
      return processed  (List[Phrase])
      on ANY exception: Logger.error("errored") :653, return []  :657
```

`execute_structured` (`enricher_base.py:566-625`) is the per-input variant: same scan/postprocess per input (`:539-540`), same `flush()` in `finally` (`:611`), returns `StructuredExecutionResult` with `InputOutcome` status/diagnostic. `WebsiteToText` does not override any structured hook.

### Transitive imports of the smallest path (HEAD)
- `flowsint_enrichers.website.to_text` → `bs4`, `httpx` (lazy in-function), `requests` (lazy), `aioquic` (optional), `cupy`/`cudf` (optional), `flowsint_core.core.enricher_base`, `flowsint_core.core.logger`, `flowsint_types.{phrase,website}`, `flowsint_enrichers.registry`.
- `flowsint_core.core.enricher_base` → `httpx`, `pydantic`, `.execution` (pure), `.graph` (neo4j driver import), `.logger` (→ `..tasks.event` → `redis`, `..core.celery` → `celery`, `.config` → `os.environ["REDIS_URL"]`, `.connector_egress`; `.models` → SQLAlchemy; `.postgre_db` → `create_engine`), `.vault`.
- `flowsint_enrichers.registry` → `flowsint_core.core.enricher_base` only (plus dynamic `importlib` in `load_all_enrichers`, `registry.py:157`, which imports every enricher module — pulls in holehe/sherlock/maigret/etc. Not needed if `WebsiteToText` is imported directly).

### External services touched (and when)
| Service | Touched by | When | First-slice decision |
|---|---|---|---|
| Neo4j (bolt) | `Neo4jConnection` ctor `connection.py:44-71` (driver build, lazy TCP); `execute_batch` `connection.py:141` (real write) | ctor: at `Enricher.__init__` unless `graph_service` injected; write: at `flush()` if batch non-empty | **exclude** — inject a null/in-memory `GraphRepositoryProtocol`; `postprocess` already guards on `self._graph_service` (`to_text.py:320`) |
| Postgres | `LoggerSingleton._flush_batch` `logger.py:117-176` via `get_db()`; `run_enricher` Scan rows `tasks/enricher.py:76-82,116-118`; module-level `db = next(get_db())` `tasks/enricher.py:37` | background thread every 2 s once any `Logger.*` call queues a row | **exclude** — substitute `LoggerProtocol`/`TestLogger` (as `flowsint-enrichers/tests/conftest.py:18-25` does); do not import `flowsint_core.tasks.enricher` |
| Redis | `emit_event_task` `tasks/event.py:15-25` (`redis.from_url(os.environ["REDIS_URL"]).publish`), invoked via `LoggerSingleton._emit_event` `logger.py:181-198` `.apply()` (runs inline, not via broker) | on every `Logger.*` call | **exclude** — same substitution; `REDIS_URL` must still be *set* for import (`config.py:15`) |
| Celery broker | `celery.py:4-14` app object; `celery.send_task` in `enrichers.py:144` | only via E1/E2 | **exclude** (E5 bypasses) |
| Vault / connector credentials | `resolve_params` `enricher_base.py:166-201` only for `vaultSecret` params; `EgressAuthorizer`/`destination_registry` only in `run_connector_template` `tasks/enricher.py:188+` | never for `WebsiteToText` (no `vaultSecret` params, `to_text.py:85-108`) | **not required** |
| Jev | `rg -i jev` on tracked HEAD source: 0 hits (only untracked `docs/jev-fl0sint-unified-prd.md`) | — | **not present at HEAD** |
| MetaHarness | `rg -i metaharness`: 0 hits | — | **not present at HEAD** |
| Target website (HTTP/HTTPS/QUIC) | N1–N3 above | in `scan` | **reuse** (this *is* the capability); QUIC stays off |

## 3. Reachable graph / network sinks from `Enricher.execute` (E5 root)

| Sink | Symbol chain | Reachable from E5? | Decision | Reason |
|---|---|---|---|---|
| G1 Neo4j node MERGE (Website) | `postprocess :321 → create_node → create_node_from_flowsint_type → add_to_batch → flush_batch → execute_batch` | yes (default ctor) | **exclude** | first slice must not require Neo4j; inject null repository |
| G2 Neo4j node MERGE (Phrase) | `postprocess :323 → …` | yes | **exclude** | same |
| G3 Neo4j `HAS_INNER_TEXT` relationship | `postprocess :324-326 → create_relationship → service.py:160 → add_to_batch(relationship)` | yes | **exclude** | same |
| G4 Neo4j batch flush | `execute :642 / execute_structured :611 → GraphService.flush → flush_batch → execute_batch` | yes (always called) | **exclude** | with null repository this is a no-op |
| G5 Neo4j read of inputs | `enrichers.py:129 get_nodes_by_ids_for_task` | no (E1 only) | **exclude** | inputs supplied directly as `Website`/str |
| L1 Postgres `logs` rows | `Logger.info/error/completed/warn → _log → queue → _flush_batch → get_db()` | yes (`:634,:645,:653`; `to_text.py:185,307,365`) | **exclude** | substitute logger protocol |
| L2 Redis pub/sub event | `_log → _emit_event → emit_event_task.apply` | yes | **exclude** | same |
| L3 Graph log message | `postprocess :327 log_graph_message → GraphService.log_graph_message → Logger.graph_append` | yes (only inside `if self._graph_service`) | **exclude** | guarded by null graph service |
| P1 Postgres `Scan` row | `run_enricher :76-82, :116-118` | no (E2 only) | **exclude** | E5 bypasses Celery |
| N1 httpx GET (HTTP/1.1+2, redirects) | `_fetch_text_async :151-164` | yes | **reuse** | primary transport |
| N2 aioquic HTTP/3, TLS verify off | `_fetch_text_async :169-175 → _fetch_via_quic :194-280` | only if `enable_quic=True` | **exclude** | opt-in; `verify_mode=False` (`:204`) is a security finding |
| N3 requests GET (sync fallback) | `_fetch_text_async :178-183` | yes, on httpx failure | **reuse with caveat** | blocks the event loop; acceptable for first slice, flag for later |
| X1 GPU cupy/cudf | `postprocess :301-304` | only if importable | **exclude** | optional extra; CPU path is default |

## 4. Observations recorded (not fixed)
1. `Enricher.__init__` (`enricher_base.py:141`) eagerly constructs a Neo4j-backed `GraphService` unless one is injected — the *only* way to run without Neo4j credentials is dependency injection; there is no env flag.
2. `Enricher.execute` swallows every exception and returns `[]` (`:651-657`); only `execute_structured` reports per-input status. The first slice should target `execute_structured` for an honest status contract.
3. `build_params_model` (`:40`) maps `int` params to `str`; `max_concurrency`/`request_timeout` values passed as strings would break `asyncio.Semaphore` / `httpx` timeout arithmetic if supplied by a caller (defaults are ints and are used untouched).
4. `_http_client` is a class-level attribute (`to_text.py:70`) mutated per instance (`:154`) — shared across event loops; a preexisting hazard, out of scope.
5. `postprocess` zips `original_input` with `results` (`:318`) although `scan` drops failed URLs and reorders via `as_completed` (`:132-135`) — Website↔Phrase pairing is not guaranteed under the legacy path. Under `execute_structured` each input is processed alone (`enricher_base.py:539-540`), so pairing is correct there.
6. QUIC path disables certificate verification (`:204`).
