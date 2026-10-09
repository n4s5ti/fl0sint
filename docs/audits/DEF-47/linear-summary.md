# DEF-47 Linear handoff

- **Issue:** DEF-47 — standalone scraper CLI/function/readable evidence report.
- **Lifecycle:** marked `running` before implementation; after same-reviewer acceptance, moved to `review` with the isolated branch commit.
- **Blocked-by state at pickup:** DEF-42 through DEF-46 were complete.
- **Scope delivered:** one keyed URL batch or single URL; caller-supplied scope, extraction, and bounded resource settings admitted before acquisition; one operation ID; a callable library function and a thin console CLI over that function; versioned JSON bundle, JSONL outcomes, Markdown report, and retained source artifacts.
- **Deliberate exclusions:** no UI, HTTP service, MCP wrapper, live-site collection, model/planner invocation, or accepted-contact assertions.
- **Legacy boundary:** DEF-46 logger/PostgreSQL/Redis/Celery behavior is not invoked by the standalone fallback. The CLI proof removes those settings and runs an offline loopback fixture.
- **Review gate:** independent review remains mandatory. `review.json` is intentionally pending until the coordinator supplies a reviewer and that reviewer accepts the exact committed content or re-verifies every fix.
