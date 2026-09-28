# DEF-42 independent audit

## Verdict

**Overall: DO NOT SHIP.**

- **Functional: PASS** for the five requested DEF-42 behavior claims on the supplied archive. The independent attack exercised public `execute`, public `scan` plus `postprocess`, and public `execute_structured`; all assertions passed.
- **Evidence contract: BLOCKED.** `docs/audits/DEF-42/manifest.json` admits that no immutable historical PRE-EDIT Blast receipt exists. A retrospective baseline/head reconstruction is retrospective evidence, not historical pre-edit evidence. It cannot cure or backdate the omission. If the historical receipt is mandatory acceptance evidence, an explicit user waiver is required.
- **Archive provenance: UNCONFIRMED.** The supplied `./repo` is an extracted source tree without `.git` metadata. `git -C ./repo rev-parse HEAD` therefore failed, so this environment cannot independently bind the tree to claimed commit `8de569a7`. The audited `website/to_text.py` SHA-256 is `2f23716b57523737f8991e21ae2621559616d8226ceaf731c714257ba0e809bc`, matching the manifest.

No product source was changed. The only created artifacts are this report, `SUMMARY.txt`, and disposable `attack_def42.py`, all under `/home/n4s5ti/.cache/def42-independent`.

## Concrete findings

### F1. Slow A / fast B ownership: PASS

`WebsiteToText._scan_occurrence_specs` creates one task from an immutable `(index, source, input_ref)` specification and gathers results in specification order. Each `_fetch_occurrence` constructs its own `WebsiteTextOccurrence`; graph capture consumes `occurrence.source` and `occurrence.outputs` together.

The independent attack delayed A, completed B immediately, and called public `execute([A, B])`. It observed outputs `A, B` and graph edges `A -> A`, `B -> B`. It separately called public `scan([A, B])` and `postprocess(envelopes)`.

### F2. Failed A / successful B isolation: PASS

The independent attack returned `None` for A and `"B"` for B through the transport boundary. Public `scan` returned `[FAILURE, SUCCESS]`; public `postprocess` returned only B and recorded only `B -> B`. No B text was attached to A.

### F3. Per-occurrence identity: PASS

The attack covered two equal duplicate inputs, a middle failure, out-of-order completion, one-to-many output, and reinvocation retry. Outcome cardinality and order matched input occurrences; both duplicates retained separate outcomes; the middle failure retained an empty output tuple; every successful one-to-many occurrence generated two edges to its own source; a first-call failure retried successfully on the second invocation with the same canonical input reference.

The implementation does not deduplicate inputs. `execute_structured` allocates an outcome slot per original list position, validates each independently, and restores each occurrence by `occurrence.index`.

### F4. Parent cancellation: PASS

The attack started two inputs with concurrency one, cancelled the parent after the first transport entered, and awaited the public `execute_structured` task. It returned two terminal `HOLD` outcomes with `cancelled` diagnostics. `_scan_occurrence_specs` cancels child tasks, awaits them with `return_exceptions=True`, and converts cancelled positions into held occurrences.

### F5. Graph and JSON survival: PASS

The attack observed the successful source/output pair in the graph recorder and then round-tripped `StructuredExecutionResult.model_dump_json()` through `json.loads`. The successful output text and failed input's `transport_failed` diagnostic survived serialization.

### F6. Producer/caller consistency: PASS with a compatibility boundary

The changed producer and consumers are internally consistent:

- `scan` publicly produces `WebsiteTextOccurrence` envelopes.
- `postprocess` requires those envelopes and rejects detached `Phrase` values with `TypeError`.
- the overridden legacy `execute` calls that `scan` and one-argument `postprocess` pair;
- `execute_structured` directly creates occurrence specs and passes envelopes to the same postprocessor;
- the enabled generic `run_enricher` task invokes `enricher.execute(...)`, so it reaches the override;
- repository search found no other non-test `WebsiteToText` or `WebsiteTextOccurrence` caller.

This is an intentional direct-caller API break: external callers that previously passed flat phrases into `postprocess`, or expected `scan` to return phrases, must migrate to occurrence envelopes. The focused test explicitly asserts rejection of detached phrases. No enabled in-repository omission was found.

### F7. Test execution limitations and failed attacks

The focused suite result was `7 passed, 2 failed`. Both failures occurred while constructing `ThreadingHTTPServer(("127.0.0.1", 0), ...)`, before product behavior, with `PermissionError: [Errno 1] Operation not permitted`.

The related acquisition fixture suite result was `22 passed, 2 failed`. Again, both failures occurred at loopback socket construction with the same permission error.

The first disposable attack invocation also failed before behavior because the harness passed `Enricher` constructor arguments positionally, causing the graph recorder to be interpreted as configuration and triggering `ValueError: Neo4j connection credentials are required`. After changing the scratch harness to the constructor's documented keyword arguments, the same attack passed every assertion. This was a harness defect, not a product defect.

Because socket creation is prohibited by the execution environment and approval escalation is unavailable, this audit did not independently reproduce the historical real-HTTP loopback result. The five identity claims were nevertheless executed through public runtime methods with only `_fetch_text_async` replaced by deterministic asynchronous behavior. Existing audit documents were treated as historical claims, not proof or instructions.

## Evidence-contract adjudication

The manifest says:

> No immutable pre-edit Blast receipt was preserved. This is a local evidence omission, not solely an external tool defect; it cannot be backdated.

It also instructs that any reconstructed baseline be labeled retrospective, never historical. The coordinator's future repaired-tooling results were not available and were not claimed as read.

Therefore:

1. Retrospective baseline/head graph evidence can support a current comparison.
2. It does **not** establish that an immutable PRE-EDIT receipt existed before editing.
3. It does **not** satisfy a contract specifically requiring historical pre-edit evidence.
4. The missing historical receipt requires explicit user waiver if that contract remains an acceptance gate.

Functional PASS and evidence acceptance are separate. Functional behavior passing does not erase the evidence omission.

## Exact commands and outputs

All Python commands used the mandated interpreter and an explicit archive-only `PYTHONPATH`:

```text
/home/n4s5ti/.cache/def42-independent/repo/flowsint-core/src:/home/n4s5ti/.cache/def42-independent/repo/flowsint-enrichers/src:/home/n4s5ti/.cache/def42-independent/repo/flowsint-types/src
```

### Provenance attempt

```sh
git -C ./repo rev-parse HEAD
```

```text
fatal: not a git repository (or any parent up to mount point /)
Stopping at filesystem boundary (GIT_DISCOVERY_ACROSS_FILESYSTEM not set).
```

### Focused tests, first collection attempt

```sh
env PYTHONPATH=/home/n4s5ti/.cache/def42-independent/repo/flowsint-core/src:/home/n4s5ti/.cache/def42-independent/repo/flowsint-enrichers/src:/home/n4s5ti/.cache/def42-independent/repo/flowsint-types/src /home/n4s5ti/Documents/dev/fl0sint/.venv/bin/python -m pytest -q repo/flowsint-enrichers/tests/enrichers/test_website_to_text.py
```

```text
ERROR ... ValueError: AUTH_SECRET environment variable is not set.
1 error in 0.23s
```

Adding the test-only `AUTH_SECRET` exposed the next required import-time setting:

```text
ERROR ... KeyError: 'REDIS_URL'
1 error in 0.51s
```

### Focused tests, runnable environment

```sh
env AUTH_SECRET=def42-independent-test-only REDIS_URL=redis://127.0.0.1:1/15 PYTHONPATH=/home/n4s5ti/.cache/def42-independent/repo/flowsint-core/src:/home/n4s5ti/.cache/def42-independent/repo/flowsint-enrichers/src:/home/n4s5ti/.cache/def42-independent/repo/flowsint-types/src /home/n4s5ti/Documents/dev/fl0sint/.venv/bin/python -m pytest -q repo/flowsint-enrichers/tests/enrichers/test_website_to_text.py
```

```text
F.....F.. [100%]
2 failed, 7 passed, 2 warnings in 2.91s
Both failures: PermissionError: [Errno 1] Operation not permitted at socket.socket(...).
```

### Related fixture tests

```sh
env AUTH_SECRET=def42-independent-test-only REDIS_URL=redis://127.0.0.1:1/15 PYTHONPATH=/home/n4s5ti/.cache/def42-independent/repo/flowsint-core/src:/home/n4s5ti/.cache/def42-independent/repo/flowsint-enrichers/src:/home/n4s5ti/.cache/def42-independent/repo/flowsint-types/src /home/n4s5ti/Documents/dev/fl0sint/.venv/bin/python -m pytest -q repo/flowsint-enrichers/tests/enrichers/test_acquisition_fixtures.py
```

```text
....FF.................. [100%]
2 failed, 22 passed, 2 warnings in 2.78s
Both failures: PermissionError: [Errno 1] Operation not permitted at socket.socket(...).
```

### Independent public-runtime attack

First run before the harness constructor correction:

```text
ValueError: Neo4j connection credentials are required
exit code 1
```

Final command:

```sh
env AUTH_SECRET=def42-independent-test-only REDIS_URL=redis://127.0.0.1:1/15 PYTHONPATH=/home/n4s5ti/.cache/def42-independent/repo/flowsint-core/src:/home/n4s5ti/.cache/def42-independent/repo/flowsint-enrichers/src:/home/n4s5ti/.cache/def42-independent/repo/flowsint-types/src /home/n4s5ti/Documents/dev/fl0sint/.venv/bin/python /home/n4s5ti/.cache/def42-independent/attack_def42.py
```

```text
PASS claims-1-2 public execute and scan/postprocess
PASS claim-3 duplicates/middle-failure/retry/one-to-many
PASS claim-4 parent cancellation retains HOLD outcomes
PASS claim-5 graph capture and JSON serialization
ALL INDEPENDENT ATTACKS PASSED
```

### Hash checks

```sh
sha256sum repo/flowsint-enrichers/src/flowsint_enrichers/website/to_text.py repo/flowsint-enrichers/tests/enrichers/test_website_to_text.py repo/docs/audits/DEF-42/manifest.json
```

```text
2f23716b57523737f8991e21ae2621559616d8226ceaf731c714257ba0e809bc  repo/flowsint-enrichers/src/flowsint_enrichers/website/to_text.py
25421d0e63a3cee4bb4cdd9b18ca349c3d887e8ecb2a4834b682eb6019afcece  repo/flowsint-enrichers/tests/enrichers/test_website_to_text.py
dd6841d7e9337ce9873dc3f4e0c53ad8b3ed08a1e3ee03e34f878216075df7fa  repo/docs/audits/DEF-42/manifest.json
```

## Paths

- Implementation: `/home/n4s5ti/.cache/def42-independent/repo/flowsint-enrichers/src/flowsint_enrichers/website/to_text.py`
- Existing focused tests: `/home/n4s5ti/.cache/def42-independent/repo/flowsint-enrichers/tests/enrichers/test_website_to_text.py`
- Evidence manifest: `/home/n4s5ti/.cache/def42-independent/repo/docs/audits/DEF-42/manifest.json`
- Disposable attack: `/home/n4s5ti/.cache/def42-independent/attack_def42.py`
- Summary: `/home/n4s5ti/.cache/def42-independent/SUMMARY.txt`
