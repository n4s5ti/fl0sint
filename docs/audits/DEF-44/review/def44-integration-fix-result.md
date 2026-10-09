# DEF-44 S04 integration fix result

Candidate: `0882e3a2`

## Implemented

- Added deployment-owned artifact runtime loading from `FLOWSINT_ARTIFACT_RUNTIME_CONFIG`. The reviewed policy document binds issuer, reviewer, content digest, current expiry, caller, scope, and source family to one trusted absolute store root. Missing, invalid, expired, ambiguous, revoked, or nonmatching configuration fails closed.
- Wired WebsiteToText registry construction and TemplateEnricher production construction to the same runtime loader. Existing trusted constructor injection remains available for local tests; request/template/source-rights values cannot select a store or mint authority.
- Added strict `source-proof/1.0` persisted metadata with operation, occurrence, input, exact retrieval timestamp, full artifact identity, normalized spans, and retention decision. Encoding carries no body, normalized text, store path/capability, headers, or URL query/userinfo.
- Added strict proof decode plus whole-source and span consumers that re-check the current configured authority before calling the artifact resolver.
- WebsiteToText structured conversion now preserves resolvable evidence metadata. Connector evidence now preserves the same metadata instead of a digest locator, and uses the exact capture retrieval timestamp.
- The shared fetch wrapper validates operation ID and the complete occurrence/input binding before the first capture. Invalid results retain measured resources and return typed review state.
- Restored acquisition format 1.0 `SpanReference` to its old strict shape. Normalized mapping moved to separately versioned `SourceProofSpanReference`; unknown proof versions are rejected.
- Updated the standalone example to return a serialized `AcquisitionBundle`, parse it with the public consumer, and demonstrate explicit-authority resolution.
- Updated acquisition documentation and changelog with runtime setup, proof resolution, compatibility, and trust limits.

## Integration-owned files touched

- `flowsint-core/src/flowsint_execution/artifact_runtime.py`
- `flowsint-core/src/flowsint_execution/acquisition.py`
- `flowsint-core/src/flowsint_execution/fetch.py`
- `flowsint-core/src/flowsint_execution/models.py`
- `flowsint-core/src/flowsint_core/core/template_enricher.py`
- `flowsint-enrichers/src/flowsint_enrichers/website/to_text.py`
- `flowsint-core/examples/source_proof.py`
- `flowsint-core/tests/acquisition/test_artifact_runtime.py`
- `flowsint-core/tests/templates/test_template_enricher.py`
- `flowsint-enrichers/tests/enrichers/test_website_to_text.py`
- `docs/developers/acquisition-contract.md`
- `CHANGELOG.md`

Concurrent core-agent edits to `artifacts.py` and `test_artifact_capture.py` were not authored here.

## Self-check evidence

All commands used explicit worktree `PYTHONPATH`, `/home/n4s5ti/Documents/dev/fl0sint/.venv/bin/python`, `AUTH_SECRET=def44-fixture-only`, and `REDIS_URL=redis://127.0.0.1:6379`.

- Core contract/runtime/template process: `67 passed`.
- Core artifact/contract/fetch process excluding three socket-only cases: `79 passed, 3 deselected`.
- Template runtime integration process after final changes: `23 passed`.
- WebsiteToText process: `20 passed, 2 failed`; both failures were loopback socket creation denied by the sandbox. The added registry/runtime/capture/resolver test separately passed with mock transport and real store/resolver.
- Final WebsiteToText registry proof roundtrip: `1 passed`.
- `compileall`: passed for the example and modified production modules.
- `git diff --check`: passed.

## Genuine blockers / verification status

- Loopback socket binds fail with `PermissionError: [Errno 1] Operation not permitted`; the parent must run the three core real-network cases and two WebsiteToText loopback cases.
- `flowsint-api/tests/test_template_egress.py` produced no output and did not complete under the bounded local run, matching the prior review behavior.
- These are author self-checks. No distinct independent verifier exercised the final combined behavior in this session, so the integration work is **unverified**, not claimed fixed or verified.
- No commit, push, tracker action, or index operation was performed.
