# DEF-46 acceptance record

## Scope

`WebsiteToLinks` is the first enabled acquisition path. It now selects a capture-only `GraphService` when no service is supplied, runs its actual `reconspread.Crawler` path, and returns unreviewed captured candidates with `execution_id`, `input_index`, and `source_url` lineage. An explicitly injected non-capture service is rejected; capture mode never silently falls back to Neo4j.

The prior `56e5febe` implementation was not relied on as verification. The before state was reconstructed from `7f6f3c83` during repair; see `baseline.json`.

## Required fixtures

| Requirement | Actual test | Evidence |
| --- | --- | --- |
| More than the auto-flush threshold produces no live writes | `test_enabled_crawler_auto_flushes_unreviewed_candidates_without_writes` | Real loopback crawler produces more candidates than its batch threshold; capture batch flush events are local, all candidates remain `unreviewed`, and no graph element ID is emitted. |
| A direct discovery callback followed by a partial-scan exception is captured | `test_real_discovery_callback_keeps_partial_candidates_after_exception` | The actual callback has already captured candidates when its logging call is made to raise; the real scan exception path retains partial candidates and exact source lineage. |
| Unsupported graph operation fails explicitly | `test_enabled_crawler_rejects_unsupported_graph_intent_explicitly` and capture-repository parameterized coverage | `query`, node reads, deletes, updates, and related non-capture operations raise `NotImplementedError`; no empty read stub or live delegation exists. |
| Scraper runs without Neo4j service or credentials | `test_enabled_crawler_runs_without_neo4j_credentials_or_connection` | Environment credentials are removed and `Neo4jConnection.get_instance` is patched to fail if reached; actual `WebsiteToLinks.execute()` succeeds through the loopback crawler. |

The runtime command for all four fixtures was:

```bash
cd flowsint-enrichers
NO_PROXY=127.0.0.1,localhost,.localhost \
no_proxy=127.0.0.1,localhost,.localhost \
UV_PROJECT_ENVIRONMENT=/home/n4s5ti/Documents/dev/fl0sint/.venv \
uv run --no-sync pytest tests/enrichers/test_website_to_links_capture.py -q
```

Observed result after the execution-scope repair: `5 passed`. The raw result is `raw/runtime-crawler-pytest.stdout`.

## Additional focused proof

```bash
cd flowsint-core
UV_PROJECT_ENVIRONMENT=/home/n4s5ti/Documents/dev/fl0sint/.venv \
uv run --no-sync pytest tests/test_capture_repository.py -q
```

Observed result after the forensic-fence repair: `7 passed`. This verifies retained candidates, local auto-flush, explicit unsupported operations, capture-factory selection, and that capture mode does not bypass forensic legacy-graph fencing.

## Safety properties

- Candidate creation returns no synthetic live graph identifier.
- The capture repository has no graph read implementation. Unsupported calls record an `unsupported_publication_intent` and then fail explicitly.
- `create_graph_service(capture_only=True)` does not import the logging class or construct a Neo4j repository.
- `WebsiteToLinks.postprocess()` does not publish data. `Enricher.execute()`'s final flush only flushes the local capture batch.
- The loopback site is addressed as `test.localhost`, avoiding domain validation of a host:port string while retaining a local-only request.
## External-service boundary disclosure

The acceptance fixtures silence the existing legacy `Logger` only so the loopback acquisition proof can isolate graph behavior. Without that test-local patch, the legacy logger attempts PostgreSQL and Redis/Celery connections during discovery. This delivery proves **Neo4j isolation only**; it does not claim the legacy acquisition surface is free of all external-service behavior. Removing or replacing that logger/event path belongs to the standalone runtime work in DEF-47 and is deliberately out of scope for DEF-46.

## Final gate

Full-suite, compile, diagnostics, post-edit impact, mutation, and independent-review evidence are recorded in the companion DEF-46 packet files. DEF-46 must not be moved to Done by this delivery; an accepted external review is still required.
