# DEF-45 independent-review remediation handoff

## Scope completed

- F1: candidate-time person attribution with heading-bounded context; later or sibling headings cannot rename earlier observations; malformed/generic containers stay unknown.
- F2: removed the arbitrary range-only observation resolver. `resolve_observation_span` now accepts a complete typed observation and trusted occurrence/input identity, then re-extracts and compares the canonical observation. Added `resolve_persisted_observation` for current-authority proof + typed metadata retrieval.
- F3: direct `GeneratedHypothesis` construction now enforces nonempty ID/value/basis/source IDs.
- F4: added shared conservative `url_policy.disclose_url`; fetch provenance and extracted link/form values drop ambiguous credential-like queries while exact retained raw spans stay unchanged.
- F5/F6: standalone live execution now calls graph/auth-free `execute_live_observed_extraction` with explicit `--runtime-config`; no WebsiteToText, registry, graph/noop, planner, DB, model, or hosted auth import.
- F7: added strict `observed-extraction/1.0` serialization/parser restoring the real Observation/result/enums/spans/diagnostics; `InputOutcome.metadata` now contains validated `PersistedMetadata`, and WebsiteTextOccurrence holds the real result type.
- Updated acquisition trust-boundary docs and changelog. Existing acquisition/1.0 source span payload was not changed.

## New files

- `flowsint-core/src/flowsint_execution/url_policy.py`
- `flowsint-core/tests/acquisition/test_observed_extraction_example.py`

## Self-check evidence

Commands used the requested explicit worktree PYTHONPATH, `/home/n4s5ti/Documents/dev/fl0sint/.venv/bin/python`, `AUTH_SECRET=def45-fixture-only`, and `REDIS_URL=redis://127.0.0.1:6379` unless the clean-environment subprocess intentionally omitted them.

- `final-core-regressions.txt`: 39 passed.
- `final-enricher-nosocket.txt`: 22 passed, 2 socket tests deselected.
- `core-nosocket.txt`: 56 passed, 3 socket tests deselected.
- `core-adjacent.txt`: 73 passed.
- `enricher-nosocket.txt`: 22 passed, 2 socket tests deselected.
- `git diff --check`: passed.
- `compileall`: passed.
- `known-auth-failure-confirmed.txt`: copied/read existing `/tmp/def45-host/example-live-clean.stderr`; SHA-256 `1ed802c86858c6bc58f2daa525908e7aff6279f57c80ca6a574e18d256710011`. The known failing command was not rerun.

Evidence directory: `/tmp/def45-review-fix-evidence`

## Remaining verification

Unverified pending the required distinct parent-host verifier. This sandbox denies socket creation. Three core loopback fetch tests and two enricher loopback tests fail at `ThreadingHTTPServer` construction with `PermissionError: [Errno 1]`, not an assertion. Parent should run the real `/seed -> /people/team.html?edition=2026` fixture and confirm final provenance, exact observation/context spans, saved/live equality, actual resource accounting, and clean-environment `--runtime-config` behavior.

No commit, index update, tracker change, sealed-fixture edit, or unrelated legacy producer repair was made.
