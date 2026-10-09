# DEF-47 acceptance evidence

| Criterion | Result | Exercised evidence |
| --- | --- | --- |
| C01: no model keys or graph/runtime services | PASS | The real CLI executed a loopback mixed batch in a child environment containing only `PATH`, `HOME`, and `PYTHONPATH=src`; it used the isolated worktree virtual environment. No auth, database, Redis, model, graph, or artifact-runtime variable was inherited. `raw/service-free-cli-proof.json` records return code `2`, `[success, failure]`, one observed link, matching report operation id, and empty stderr. |
| Mixed-success attribution | PASS | The success is retained while the HTTP 500 outcome remains a typed failure with its diagnostic. |
| Report/bundle agreement | PASS | The one versioned bundle produces JSON, JSONL, and Markdown; the exercised report contains the bundle operation id. |
| Direct second caller | PASS | `test_direct_function_preserves_mixed_results_and_source_references` calls `scrape_websites` without shelling out; the focused suite is 30 passed. |
| Existing graph path | PASS | `WebsiteToText` retains ordinary core imports and delegates only acquisition to `WebsiteTextExtractor`; the existing WebsiteToText tests are included in the focused suite. |
| No fake fallback | PASS | The standalone component is real admitted source acquisition/proof/extraction logic. A source scan found no fallback `Enricher`, logger, decorator, or import-error suppression in the website package. |

## Full-suite disclosure

`AUTH_SECRET=offline-test-secret REDIS_URL=redis://127.0.0.1:1 PYTHONPATH=src uv run --no-sync pytest -q` reported **187 passed, 3 failed**. The three failures are in the unrelated `TestScan` namechk assertions (mocked Hunter rows yield one account where tests expect two; legacy Celery logging is also emitted). DEF-47-focused coverage is green. `raw/namechk-baseline-comparison.json` reproduces all three failures on base `a59bdadf` in an isolated detached worktree with the same interpreter/dependency environment. The known core baseline failure for the missing `/tmp/def45-live-fixture.html` remains documented in `baseline.json`.

## Review state

The fallback implementation reviewed initially was removed after the coordinator identified it as inconsistent with the no-stub contract. The same independent reviewer re-reviewed the graph-free extraction component and reverified fixes F1–F5. Final verdict: **ACCEPT**, recorded in `review.json`.
