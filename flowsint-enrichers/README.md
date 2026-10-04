# Flowsint enrichers

The repository containing open-source enrichers for Flowsint.

## Standalone source-preserving scraper

`flowsint-standalone-scraper` is the supported standalone release surface. It uses the shared admitted extraction component without a graph, model, planner, PostgreSQL, Redis, or Celery service; the graph-backed `WebsiteToText` enricher delegates to that same component. It emits `bundle.json`, `outcomes.jsonl`, `report.md`, and source artifacts under the requested output directory.

`scope`, `extraction`, and resource limits are validated before any input is admitted. Every run allocates one operation ID; every bundle row, JSONL record, and report section carries it. Observed contacts and other candidates remain `unreviewed`; this tool does not make accepted-contact claims.

`report.md` is rendered from `bundle.json`; `outcomes.jsonl` is the same per-input outcome set in line-oriented form. Every successful result preserves source references, text, observed links, and unreviewed candidates. A failed row preserves its explicit diagnostic while successes are retained.

`0` means every input succeeded, `2` means the invocation completed with at least one failed input, and `64` means the invocation was invalid before admission. Invocation failures do not discard successful output.

### One URL

```bash
flowsint-standalone-scraper \
  --url http://127.0.0.1:8080/fixture.html \
  --output-dir ./scrape-output
```

### Keyed batch

A keyed batch is a JSON object, which preserves the caller's keys in every artifact:

```json
{
  "landing": "http://127.0.0.1:8080/fixture.html",
  "known-error": "http://127.0.0.1:8080/failure"
}
```

```bash
flowsint-standalone-scraper \
  --batch ./batch.json \
  --output-dir ./scrape-output \
  --scope local-web-fetch \
  --extraction observed-readable-text \
  --max-concurrency 2 \
  --request-timeout 10 \
  --max-response-bytes 1048576 \
  --max-redirects 3
```

### Callable library

```python
import asyncio
from flowsint_enrichers import ScrapeOptions, scrape_websites

bundle = asyncio.run(
    scrape_websites(
        {"landing": "http://127.0.0.1:8080/fixture.html"},
        output_dir="./scrape-output",
        options=ScrapeOptions(
            scope="local-web-fetch",
            extraction="observed-readable-text",
        ),
    )
)
print(bundle.exit_code)
```

### Offline fixture proof

The repository's offline loopback fixture covers direct function use, CLI admission errors, mixed success, and bundle/report agreement. The dummy values satisfy the legacy test package initializer only; the standalone scanner never connects to either service:

```bash
cd flowsint-enrichers
AUTH_SECRET=offline-test-secret REDIS_URL=redis://127.0.0.1:1 \
  PYTHONPATH=src uv run --no-sync pytest -q tests/enrichers/test_standalone.py
```
