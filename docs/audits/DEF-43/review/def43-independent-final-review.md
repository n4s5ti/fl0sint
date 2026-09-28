# DEF-43 independent final verification of immutable archive b1474b07

## Verdict

**PASS within the stated trust and sandbox limits.** I independently reproduced the prior F1/F2 cases against `/home/n4s5ti/.cache/def43-independent-final`. Both defects are resolved. I found no introduced critical, high, or medium defect in the reviewed fetch and WebsiteToText scope.

The archive was treated as immutable and no source file was edited. It deliberately has no Git metadata, so I did not report that as a defect or independently assert commit ancestry. The parent can compare the hashes below with commit `b1474b07`.

## Exact source hashes

SHA-256:

- `flowsint-core/src/flowsint_execution/fetch.py`: `043505e3b658b28c06c75cbabf2fe0f68e37cbbbccdcd488f5b82aefe5ffba28`
- `flowsint-enrichers/src/flowsint_enrichers/website/to_text.py`: `ace3c84ad3b00703a3ca4383aef4acaf4d752683ee179cf56f99c79b324fd21d`
- `flowsint-core/tests/acquisition/test_fetch.py`: `644aedb672ca2184e2e8726c5ac94c0bb22941b174d4787bbdf6352f2e2d946d`
- `flowsint-enrichers/tests/enrichers/test_website_to_text.py`: `304e6ad77ff4c8e300f60c9f02bd6623cbd63891ae176aff498bceba3cd3cc24`
- `docs/developers/acquisition-contract.md`: `d7b421c46d3b4b9d73a30b0ba28dd076abaae241bc32eff0d3075bef363a5552`
- `docs/audits/DEF-43/scope-plan.md`: `44bdfdb6c634789de0fe2b6b56fb269d307aff0f3e087a5a93f53bca3a41f25c`

## Prior findings

### F1 resolved: cancellation-resistant streaming has bounded caller return and cannot become success

`fetch.py:586-663` uses an independent `asyncio.wait(..., timeout=timeout)` boundary rather than waiting for child cancellation acknowledgement. Tasks pending at that boundary are cancelled, cleanup gets a separate finite budget of `min(0.05, max(0.01, timeout))`, and the boundary snapshot is returned as `TIMEOUT` or `CANCELLED`. A cancellation-suppressing child that outlives cleanup is supervised with a result-consuming callback while client close completes in that background cleanup.

The prior reproducer was adapted only to assert the intended fixed behavior. With a stream that consumes `CancelledError` and then sleeps 0.25 seconds, actual output was:

```text
deadline_seconds=0.03 elapsed_seconds=0.064 status=timeout
```

Assertions required elapsed time below 0.15 seconds and status `TIMEOUT`; both passed. The extra post-return sleep allowed the supervised cleanup to finish without an unhandled-task warning. The targeted suite also passed the explicit parent-cancellation-resistant-stream test.

This verifies bounded caller return and no timeout success. It does not claim a hard kill of hostile arbitrary Python, which is impossible here. The production claim is appropriately limited to cooperative real `httpx` and trusted in-process transports.

### F2 resolved: WebsiteToText rejects identity mismatches

`to_text.py:270-294` requires all of the following before associating results: exact operation ID, exact outcome count, unique occurrence IDs, exact occurrence ID set, and the admitted `input_ref` for every outcome. Any mismatch fails the whole integration response with `invalid_fetch_result`.

The prior substituted-result reproducer supplied both a wrong operation ID and wrong input reference. Actual output was:

```text
substituted_identity_status=failure diagnostic=invalid_fetch_result outputs=0
```

The targeted parameterized tests also passed for wrong operation, wrong input reference, missing, unknown, duplicate, and extra outcomes.

## New single-use admission claim

`AdmittedFetchOperation` owns a private `_ExecutionClaim` containing a `threading.Lock` and claimed bit. `execute_fetch` validates the seal and atomically claims the object before creating a ledger or client. Pydantic `model_copy()` preserves that private claim object, so it does not reset the capability.

The adapted prior replay case executed the original object and then its in-process `model_copy`. Actual output was:

```text
single_use_hits=1 first_requests=1 replay_error='admitted operation has already been claimed for execution'
```

The suite independently passed sequential reuse and concurrent-race cases; exactly one concurrent execution wins and only one dispatch occurs.

This correctly supports only the documented object-scoped, process-local, single-use property. Serialization/reconstruction can create a new object and is outside the claim. No global durable ledger, cross-process replay protection, wire capability, remote authorization, or idempotency guarantee was credited.

## Behavioral test results

Interpreter: `/home/n4s5ti/Documents/dev/fl0sint/.venv/bin/python`.

Every run used explicit archive source paths only:

```text
PYTHONPATH=/home/n4s5ti/.cache/def43-independent-final/flowsint-app/src:/home/n4s5ti/.cache/def43-independent-final/flowsint-core/src:/home/n4s5ti/.cache/def43-independent-final/flowsint-enrichers/src:/home/n4s5ti/.cache/def43-independent-final/flowsint-types/src:/home/n4s5ti/.cache/def43-independent-final/flowsint-mcp-server/src
```

- Adapted deterministic F1/F2/replay reproducer: passed all assertions, exit 0.
- Core targeted suite `tests/acquisition/test_fetch.py`: **17 passed, 3 failed**. Every failure occurred at `socket.socket()` with sandbox `PermissionError: [Errno 1] Operation not permitted` in the real loopback redirect, self-signed TLS, and stalled-body tests.
- Enricher targeted suite `tests/enrichers/test_website_to_text.py`: **19 passed, 2 failed**. Both failures occurred at `socket.socket()` with the same sandbox denial in loopback integration tests.
- Thus all 36 non-socket targeted tests passed. The five socket-backed paths were not exercised here and remain for the host coordinator.

## Scope assessment and limits

Source inspection and deterministic behavior support the stated dispatch accounting, byte accounting, same-origin redirect check, strict TLS client construction (`verify=True`, `trust_env=False`), typed deadline/cancellation behavior, object-local single-use claim, and result identity binding. No critical/high/medium defect was demonstrated in these paths.

This sandbox cannot create network sockets, so it cannot independently verify real loopback dispatch, TLS handshake rejection, or a real stalled socket body. Those failures are environmental and not product failures. Host coordinator results are required for those runtime claims. The adapted reproducer is `/tmp/def43-final-review-repro.py`; it uses `httpx.MockTransport` and does not establish a socket.
