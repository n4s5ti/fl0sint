# DEF-45 independent audit of immutable archive `20cc2596`

## Verdict

**BLOCK / not accepted.** The reviewed source has seven introduced S05/S04-integration defects. Four are high severity: person attribution can be fabricated across temporal and multi-person boundaries, raw candidate spans have no observation ownership/value verification, credential aliases leak through retained URLs, and the documented `--runtime-config` live path does not apply that configuration. Three medium defects break the promised graph-free standalone entry point, generated-hypothesis validation, and typed persisted observation metadata.

This was a same-vendor independent Codex process. I audited the immutable archive at `/home/n4s5ti/.cache/def45-independent`; I did not use stale native handoffs. No archive source, index, Git state, commit, or tracker was changed. The archive intentionally lacks Git metadata. A read-only comparison against `/home/n4s5ti/Documents/dev/fl0sint` showed that every implementation hash below exactly equals `git show 20cc2596:<path>`.

## Findings

### F1 — High — mutable card context fabricates person associations

`flowsint-core/src/flowsint_execution/observed_extraction.py:207-227, 252-256, 349-365, 491-505`

Each candidate retains a mutable `_Frame`, while all headings/name labels in that frame continue appending to `person_name_parts`. Attribution happens only after the whole document is parsed. Therefore an email occurring before a later heading is retroactively attributed to that later person. If one explicit parent contains two people, both contacts are attributed to the synthetic name `Ada One Bo Two`. This violates the strict separation and same-page multi-person ownership requirements. The repro demonstrates both cases through `extract_observations`.

### F2 — High — observation span resolution verifies bytes, not candidate ownership

`flowsint-core/src/flowsint_execution/observed_extraction.py:191-204`

`resolve_observation_span` accepts only body, artifact, an arbitrary `RawSpan`, and an optional kind that is type-checked but otherwise ignored. It has no observation ID, occurrence ID, snapshot binding from the observation, expected value/quote, or context ownership. Any in-bounds retained range can therefore be resolved while labeled as any observation kind. The persisted proof contains only normalized source spans, not observation spans (`flowsint-core/src/flowsint_execution/artifact_runtime.py:66-82, 210-247`). The repro resolves `beta@example.test` using an arbitrary range labeled `GENERAL_INBOX` without an owning observation.

### F3 — Medium — `GeneratedHypothesis` bypasses its own validation

`flowsint-core/src/flowsint_execution/observed_extraction.py:135-149`

Validation exists only in `GeneratedHypothesis.create`; the public frozen dataclass has no `__post_init__`. `GeneratedHypothesis("", "", "", ())` succeeds. This permits empty/unbound generated values to bypass the separation enforced by the factory.

### F4 — High — retained URL redaction leaks credential aliases

`flowsint-core/src/flowsint_execution/fetch.py:876-887`

The sensitive-name set uses exact decoded parameter names. Common aliases including `client_secret`, `auth_token`, `passwd`, and `pwd` are retained verbatim, including their secret values, in requested/final artifact context and persisted source proof. Case folding and percent-encoded canonical `api_key` work, and benign `view=full&page=2` remains intact, so the defect is specifically incomplete credential-name coverage rather than indiscriminate query removal.

### F5 — Medium — standalone graph-free `--url` example has an auth/graph prerequisite

`flowsint-core/examples/observed_extraction.py:5-16`

The live example imports `flowsint_enrichers.website.to_text`, which imports the core package and its auth initialization before any URL admission. With `AUTH_SECRET` absent, the documented standalone command terminates with `ValueError: AUTH_SECRET environment variable is not set`. `_NoGraph` does not remove this import-time dependency. This contradicts the directly callable graph-free standalone requirement.

### F6 — High — `--runtime-config` is ignored for live execution

`flowsint-core/examples/observed_extraction.py:11-23, 35-44`; `flowsint-enrichers/src/flowsint_enrichers/website/to_text.py:301-309`

`--runtime-config` is passed only to saved-proof replay. `_live` accepts only the URL, constructs `WebsiteToText` without a store, authority, or config path, and WebsiteToText calls `load_artifact_runtime` without `config_path`. Thus `--url ... --runtime-config /reviewed/config.json` does not apply that config; live success depends on a separate environment variable. The CLI option promises behavior it does not provide.

### F7 — Medium — integrated observation metadata is untyped and has no persisted parser

`flowsint-enrichers/src/flowsint_enrichers/website/to_text.py:72-85, 87-116, 370-398`; `flowsint-core/src/flowsint_execution/models.py:65-85`

`observation_result` is typed as `object`, converted with `asdict`, and placed into `InputOutcome.metadata: tuple[Any, ...]`. The size check serializes the runtime type but there is no versioned strict metadata model or parse/restore boundary that validates exact `Observation`, enum, span, diagnostic, and count types. This does not satisfy the required persisted typed result contract and permits downstream ad-hoc dict trust.

## Positive and negative scope results

- Pure extraction is deterministic for the tested source, excludes script/style/template/noscript/svg text, keeps duplicate raw ranges distinct, binds the final URL to the artifact, resolves relative query links, and keeps emitted link/form execution state non-executable.
- Generic and role inbox local-parts override person-card association, so `info@` is not promoted to an executive contact in the tested case.
- Live extraction is invoked inside the existing fetch/source-proof worker and checks the elapsed deadline before and after extraction. I found no second fetch and no graph mutation by observation extraction.
- Saved replay requires current reviewed authority and returns no result for non-AVAILABLE states. It reuses retained bytes and the pure extractor.
- Legacy WebsiteToLinks/WebsiteToCrawler, Phrase converters, and FireEnrich producers were treated as mapped preexisting/out-of-scope risks and were not invoked. I do not demand their rewrite.
- The existing untyped `metadata` field is older infrastructure, but F7 is introduced behavior because DEF-45 chose it as the new observation serialization boundary.
- Sandbox policy denies socket creation. Real loopback live behavior, redirects, cancellation timing against a real server, and full saved/live runtime parity are therefore **unverified here** and require the parent-host run requested by the audit prompt. I do not infer PASS from mocked/model tests.

## Exact reviewed source hashes

```text
dfba3437848215ee15eb0446b5bdfc7eaf869c33e5b45f56e4f81c17a3434fd0  flowsint-core/src/flowsint_execution/observed_extraction.py
de9ebe74a7c12ebb0f9904203de04d68685673aafb8c348d99e3e4845ea6973c  flowsint-core/src/flowsint_execution/extraction_runtime.py
1101aa4abdb8e5614127ed3b7e674dfd5a4aaa7aad27cdd1d89e9c796e60ba11  flowsint-core/src/flowsint_execution/fetch.py
013d0b68e918f61896ab2a106d92f25db5a742d45a8e5de097722d1f3b622d2a  flowsint-core/examples/observed_extraction.py
27ad027e22e4462d204a7cdcbe97609b6ab2ce5c64a52b1bf965ce174528f718  flowsint-enrichers/src/flowsint_enrichers/website/to_text.py
a493d3a19fb2b4575131dc3d22f9befe93083609ed8c6ff83160bbba22f4be42  flowsint-core/src/flowsint_execution/models.py
37e9257b56fc0f6eac06faae51d131aa2b9508bccab243a41c489ac3ec758fa1  flowsint-core/src/flowsint_execution/artifact_runtime.py
```

## Commands and observed results

All Python commands used `/home/n4s5ti/Documents/dev/fl0sint/.venv/bin/python`, explicit archive-only `PYTHONPATH`, `AUTH_SECRET=def45-fixture-only`, and `REDIS_URL=redis://127.0.0.1:6379`, except the deliberate F5 clean-auth subprocess.

```sh
PYTHONPATH="/home/n4s5ti/.cache/def45-independent/flowsint-core/src:/home/n4s5ti/.cache/def45-independent/flowsint-enrichers/src:/home/n4s5ti/.cache/def45-independent/flowsint-types/src:/home/n4s5ti/.cache/def45-independent/flowsint-mcp-server/src:/home/n4s5ti/.cache/def45-independent/flowsint-app/src" \
AUTH_SECRET=def45-fixture-only REDIS_URL=redis://127.0.0.1:6379 \
/home/n4s5ti/Documents/dev/fl0sint/.venv/bin/python /tmp/def45-review-repro.py
# Seven defect probes reproduced; all seven archive hashes matched.
```

```sh
cd /home/n4s5ti/.cache/def45-independent/flowsint-core
PYTHONPATH="$PWD/src:$PWD/../flowsint-enrichers/src:$PWD/../flowsint-types/src:$PWD/../flowsint-mcp-server/src:$PWD/../flowsint-app/src" \
AUTH_SECRET=def45-fixture-only REDIS_URL=redis://127.0.0.1:6379 \
/home/n4s5ti/Documents/dev/fl0sint/.venv/bin/python -m pytest -q \
  tests/acquisition/test_observed_extraction.py tests/acquisition/test_extraction_runtime.py
# 20 passed, 2 warnings
```

```sh
cd /home/n4s5ti/.cache/def45-independent/flowsint-enrichers
PYTHONPATH="$PWD/../flowsint-core/src:$PWD/src:$PWD/../flowsint-types/src:$PWD/../flowsint-mcp-server/src:$PWD/../flowsint-app/src" \
AUTH_SECRET=def45-fixture-only REDIS_URL=redis://127.0.0.1:6379 \
/home/n4s5ti/Documents/dev/fl0sint/.venv/bin/python -m pytest -q tests/enrichers/test_website_to_text.py
# 22 passed; 2 failed only when ThreadingHTTPServer socket creation raised PermissionError [Errno 1]
```

The first combined core/enricher pytest invocation was discarded because pytest imported both `tests.conftest` packages in one process and raised `ImportPathMismatchError`; the required separate-process reruns above are the relevant receipts.
