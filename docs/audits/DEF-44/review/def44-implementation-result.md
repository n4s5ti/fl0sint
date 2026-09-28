# DEF44/S04 authoring result

## API and behavior

- `flowsint_execution.artifacts` adds `FilesystemArtifactStore`, `ArtifactContext`,
  `RetentionAuthority`, `RetentionDecision`, `capture_source`, `resolve_source`, and
  deterministic `normalize_html` mapping.
- The store content-addresses complete raw bytes, writes objects atomically, persists
  per-occurrence snapshot/authorization/retrieval metadata, survives a fresh store instance,
  rejects symlink reads, and rechecks metadata, byte length, and SHA-256 on resolution.
- Resolution requires exact operation, occurrence, caller, scope, source family, reviewed
  policy digest, and unexpired policy/artifact. HOLD and REVIEW reasons are distinct:
  `retention_policy_unavailable`, `artifact_store_unavailable`, `body_missing`,
  `truncated_body`, `policy_expired`, `artifact_expired`, `artifact_missing`,
  `authorization_mismatch`, `metadata_mismatch`, `digest_mismatch`, and
  `unsupported_source_encoding`.
- Strict UTF-8 HTML normalization emits ordered half-open raw-byte to Unicode-code-point
  ranges for entity decoding, whitespace collapse, tag removal, repeated text, and
  cross-node separators. Retained normalized text/mapping is governed by the reviewed
  runtime decision.
- `execute_fetch_with_source_proof` wraps the existing one-shot `execute_fetch`; there is no
  second fetcher. It captures before transient bodies are discarded and returns body-free
  artifact/span references. Requested and actual final locations are sanitized of query,
  fragment, and userinfo before retention.
- WebsiteToText scan, legacy execute, and structured execute use the shared wrapper. Missing
  runtime retention configuration consumes the fetch then HOLDs; legacy execute raises.
- TemplateEnricher accepts the same explicit store/authority injection, captures its bounded
  buffer through the same store, and no longer treats template `source_rights` as authority.
  Existing task/API constructor paths intentionally supply no implicit authority and fail
  closed with HOLD until deployment configures it.
- `SpanReference` adds optional exact normalized range/unit/encoding fields. Existing 1.0
  fields are unchanged; documentation explicitly warns strict old consumers to upgrade for
  producers emitting the additive fields.

## Repository files changed

- `CHANGELOG.md`
- `docs/developers/acquisition-contract.md`
- `docs/developers/managing-enrichers.mdx`
- `flowsint-core/src/flowsint_execution/acquisition.py`
- `flowsint-core/src/flowsint_execution/fetch.py`
- `flowsint-core/src/flowsint_execution/artifacts.py` (new, planned implementation)
- `flowsint-core/src/flowsint_core/core/template_enricher.py`
- `flowsint-enrichers/src/flowsint_enrichers/website/to_text.py`
- `flowsint-core/examples/source_proof.py` (new, requested standalone runtime example)
- `flowsint-core/tests/acquisition/test_artifact_capture.py` (new)
- `flowsint-core/tests/templates/test_template_enricher.py`
- `flowsint-enrichers/tests/enrichers/test_website_to_text.py`

No files outside the planned/requested implementation, tests, example, and documentation
sets were added. No commit, push, merge, tracker, or index operation was performed.

## Author tests and evidence

All commands used `/home/n4s5ti/Documents/dev/fl0sint/.venv/bin/python`,
`AUTH_SECRET=def44-fixture-only`, `REDIS_URL=redis://127.0.0.1:6379`, and explicit worktree
`PYTHONPATH` values.

- Initial RED: `pytest flowsint-core/tests/acquisition/test_artifact_capture.py -q` failed
  during collection with missing `flowsint_execution.artifacts`, as expected. Evidence:
  `/tmp/def44-author-evidence/01-feature-red.txt`.
- Final broad core author suite, excluding the three known socket-required host tests:
  `966 passed, 3 deselected`. Evidence:
  `/tmp/def44-author-evidence/10-core-suite.txt`.
- Final broad enricher author suite, excluding four known socket-required fixture/loopback
  tests: `174 passed, 4 deselected`. Evidence:
  `/tmp/def44-author-evidence/11-enricher-suite.txt`.
- Final focused core/acquisition/template/service suite after the requested/final-location
  change: `98 passed, 3 deselected`. Evidence:
  `/tmp/def44-author-evidence/12-final-core-focused.txt`.
- Final WebsiteToText suite after that change, excluding its two loopback tests:
  `20 passed, 2 deselected`. Evidence:
  `/tmp/def44-author-evidence/13-final-website-focused.txt`.
- `git diff --check` passed. Python compilation passed for all changed production/example
  modules. The local `ruff` module is unavailable, and the installed `black` launcher has
  the same missing Python 3.14.2 interpreter problem already recorded for local tooling.

These are author self-checks, not independent verification. Parent-owned host smoke and
immutable independent audit remain required before calling the work verified.

## Mutation proof

Each mutation copied `flowsint_execution` and the behavioral fixture into a fresh
`/tmp/def44-mutation-*` directory, placed that copy first on `PYTHONPATH`, and ran only the
relevant real behavior test. All three negative fixtures failed with exit 1:

- Authorization binding bypass (`_bound` forced to allow): expected
  `authorization_mismatch`, got `metadata_mismatch`.
  `/tmp/def44-author-evidence/20-mutation-auth.txt`.
- Digest verification bypass: tampered bytes were exposed instead of REVIEW/no body.
  `/tmp/def44-author-evidence/21-mutation-digest.txt`.
- Missing-policy HOLD bypass: the mutated path dereferenced absent authority rather than
  returning typed HOLD/no retention.
  `/tmp/def44-author-evidence/22-mutation-retention.txt`.

## Host smoke invocation

Start a controlled local HTTP fixture, then from `flowsint-core` run:

```sh
AUTH_SECRET=def44-fixture-only \
REDIS_URL=redis://127.0.0.1:6379 \
PYTHONPATH=/home/n4s5ti/Documents/dev/fl0sint-def44-source/flowsint-core/src \
/home/n4s5ti/Documents/dev/fl0sint/.venv/bin/python \
examples/source_proof.py http://127.0.0.1:<port>/fixture /tmp/def44-host-store
```

Expected JSON has `state: "available"`, the raw source digest, exact normalized text and
span metadata, and `resolved_bytes` equal to the fixture body length. The example opens a
fresh store instance for resolution and imports no graph/planner/database modules or keys.

## Remaining concerns for parent audit

- Sandbox socket policy prevented the seven real loopback/TLS/fixture tests listed above;
  parent owns those host runs, including the standalone example.
- Deployment wiring intentionally grants no default retention authority. Existing task and
  API call sites therefore HOLD after successful connector fetch until the parent supplies a
  reviewed `FilesystemArtifactStore` and matching `RetentionAuthority` through deployment
  composition. No template or environment-secret fallback was added.
- No independent verifier ran in this authoring slice, per the requested ownership split.
