## DEF-40 [B03]: controlled source fixtures and evidence/test manifest

**Base:** `dev` @ `02467e96`. **Branch:** `work/def-40-fixtures` (worktree `.worktrees/def-40-b03`). **Packet:** `docs/audits/DEF-40/`. This change only adds files; it modifies 0 tracked files, and package/config hashes are unchanged.

**Deliverable**
* `scripts/acquisition_fixtures.py validate|smoke|serve`: a stdlib-only fixture loader, loopback allowlist server and real-HTTP smoke test. It is not a scraper.
* `…-enrichers/tests/fixtures/acquisition/`: a synthetic corpus of 15 sources, 15 byte-span observations, 16 occurrences and 14 cases. Every artifact has a sha256, there is a retention policy hash, the corpus is marked `development_only`, and all domains use `.example`.
* `…-enrichers/tests/enrichers/test_acquisition_fixtures.py`: 24 tests, found by the existing pytest discovery.

**Acceptance fixtures (all PASS)**
* Spans and contacts can be checked offline. Byte spans and hashes are validated. Every accepted label's subject, company and value are grounded in the quoted evidence. No model or internet access is used, and the browser made 0 off-host requests.
* Duplicate URL inputs keep distinct occurrence IDs. `input-named` and `input-named-repeat` have separate IDs and indexes but the same route. Copied pages share one `origin_id` and one sha256, so they are not independent evidence.
* Empty output and failure are different statuses. `success_empty` (200, no observations), `failure_http` (503) and `failure_timeout` (750 ms against a 100 ms deadline) were all observed over real HTTP.

**Verification**
* `validate` exit 0. `smoke` exit 0: 15 of 15 HTTP occurrence results passed, and 2 browser cases are listed in `not_run_browser_cases`. Browser proof in `browser-proof.json`: the render and click cases passed in real Chromium.
* The new module passed 24 tests. Full enrichers suite: 132 passed before, 156 passed after, 0 failures.
* Mutation check: 5 of 5 injected defects were caught (hash check disabled, lax routes, empty page reported as nonempty, timeout reported as http, verdict flip).

**Blast/Hunt/Drift:** `WebsiteToText` up/down gave UNKNOWN before and after (`no-call-edges` / `query-interrupted`), a known gap (DEF39-F2/F3). The new symbols returned complete results with callers limited to `main`/`smoke`. The pytest `importlib` consumer is not visible to the graph. Drift after the isolated reindex is 0, and 0 changed paths fall outside the plan. Hunt coverage is 0, which is a preexisting limitation (DEF39-F6). The shared `.gitnexus/` was never written.

**Review:** an independent reviewer subagent ran 2 rounds (not independent human review). It confirmed a 29-case tamper battery, server behaviour (loopback only, 404/501, bounded concurrency) and smoke falsification. Findings: R1 (verdict flips on trap labels went undetected) is resolved by a trap-verdict pin test. R3a (orphan observations were accepted) is resolved: the loader now rejects them. R2 (smoke's top-level `passed` omits browser cases) and R3b (free-form values on text observations) are accepted and documented. No findings remain open.

**Revert:** `git revert` of the single additive commit; see `recovery.md`.

**Note:** the prior session's `.worktrees/def-40-fixtures` is a plain copy with no `.git` link and contains a recursive self-copy of about 5.6 GB. Its files were ported to a real worktree, and the copy was left untouched for your cleanup decision.
