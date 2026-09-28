# DEF-43 independent audit of candidate 353a8389

## Verdict

**FAIL: two reproducible consumer-facing defects.** The implementation is not verified against the required bounded streaming cancellation/deadline and occurrence-binding properties.

This review used an independent Codex process, but the same model vendor as the authoring process. Native task-based independent verification was unavailable. The archive's `.git` directory is empty/inaccessible as a repository, so `git rev-parse HEAD` returned `fatal: not a git repository`; I could not independently prove that the supplied tree is commit `353a8389`. I audited only `/home/n4s5ti/.cache/def43-independent` and made no repository edits or tracker updates.

## Exact audited hashes

SHA-256:

- `docs/audits/DEF-43/scope-plan.md`: `44bdfdb6c634789de0fe2b6b56fb269d307aff0f3e087a5a93f53bca3a41f25c`
- `docs/developers/acquisition-contract.md`: `b8f0f553c5b27801ac9f12450a044836016dadc7f7080de65bd8bb0102468a21`
- `flowsint-core/src/flowsint_execution/fetch.py`: `5c33b3f1a19581c0689a3d9efbc0c6d35de3880c5e010a17622ab4b9b1b555bc`
- `flowsint-enrichers/src/flowsint_enrichers/website/to_text.py`: `201d7a5871bd140309165bd65cf45c5feb9fcea6376027445b65a4f3279dad65`
- `flowsint-core/tests/acquisition/test_fetch.py`: `3e05ae1153265ce032121e3099c2854680c1da43538da04cb6ef4ce5ce8521e7`
- `flowsint-enrichers/tests/enrichers/test_website_to_text.py`: `19b14dd9db028f148154ef2a152e4077f58009c8f95060a99d67512afe53da71`

## Findings

### F1 — High — A cancellation-resistant stream defeats the whole-operation deadline and returns success

Path/lines: `flowsint-core/src/flowsint_execution/fetch.py:532-562, 564-594`.

`asyncio.timeout()` cancels the gather at the deadline, but a stream/transport can consume `CancelledError`, delay, and finish normally. In that case the timeout block completes after the sealed deadline and the occurrence is returned as `success`. Cleanup has no secondary bound. The same unbounded `gather()` pattern is used after explicit parent cancellation at lines 595-618. This violates both the whole-operation time allocation and bounded cancellation, specifically during streaming.

Reproduction:

```sh
ARCHIVE=/home/n4s5ti/.cache/def43-independent
PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH="$ARCHIVE/flowsint-app/src:$ARCHIVE/flowsint-core/src:$ARCHIVE/flowsint-enrichers/src:$ARCHIVE/flowsint-types/src:$ARCHIVE/flowsint-mcp-server/src" \
/home/n4s5ti/Documents/dev/fl0sint/.venv/bin/python /tmp/def43-review-repro.py
```

Actual relevant output:

```text
deadline_seconds=0.03 elapsed_seconds=0.282 status=success
```

This uses an `httpx.AsyncByteStream`, the actual streaming interface accepted by the public standalone function. It is deterministic and needs no socket bind.

### F2 — Medium — WebsiteToText binds results only by occurrence ID and accepts substituted operation/input identity

Path/lines: `flowsint-enrichers/src/flowsint_enrichers/website/to_text.py:270-286, 302-329`.

The integrated consumer keys results by `occurrence_id` but does not require `FetchResult.operation_id == operation.operation_id`, does not require `result.input_ref == admitted.input_ref`, and does not reject unknown extra occurrence IDs. A faulty or replaced fetch result with the expected occurrence ID is therefore attributed to the requested Website and emitted as success even when both operation ID and input reference are wrong. This defeats the claimed occurrence join/binding at the integration seam.

Same reproduction command. Actual relevant output:

```text
substituted_input_ref_status=success text=substituted content
```

The reproducer supplies `operation_id="wrong-operation"` and `input_ref="c" * 64`; WebsiteToText attributes the substituted text to the requested source.

## Required-property adjudication

- Dispatch/redirect scope: source tracing and deterministic tests show every initial request, redirect, and retry calls the shared `charge_request`; redirect authority is validated before the next dispatch and restricted to the original effective origin. Unsupported schemes and credential-bearing URL authorities are rejected by `_origin` during admission/redirect processing.
- TLS/scheme: `AsyncClient(verify=True, trust_env=False)` and strict `http`/`https` origin parsing are present. The real self-signed TLS test could not run because this sandbox denies socket creation. A coordinator can run the command below. The existing TLS assertion is weak because it accepts any `TOOL_ERROR`, not specifically certificate verification failure.
- Shared allocations/accounting: deterministic tests passed for shared request allocation, per-occurrence and pooled bytes, oversize chunk accounting, retry/backoff accounting, and semaphore wait accounting. Delivered bytes before stream failure are retained. Oversized chunks may make measured consumption exceed the allocation, but no oversized body is returned and the overspend is honestly recorded, matching the documented policy.
- Status distinctions: deterministic tests passed for valid empty success, rate limit, timeout, HTTP error, decode/tool error, and policy denial. F1 shows a deadline can nevertheless be mislabeled success when cancellation is consumed.
- Digest/seal binding: request, policy, parameters, allocation, admitted inputs, and their digests are included in the seal; replacement fails validation. Admission revalidates constructed models. No defect reproduced here.
- Replay: the same sealed admitted operation executes twice (`replay_hits=2 first_requests=1 second_requests=1`). This is **not filed as a defect** because `acquisition-contract.md` explicitly defines the operation as a trusted in-process capability, says replay prevention is not coordinated across calls/processes, and requires the local trusted caller to execute at most once. This implementation makes no hosted authorization, durable reservation, distributed receipt, or distributed idempotency claim. Consumers requiring those properties remain responsible for them.
- Graph-free/identity of implementation: the standalone caller and WebsiteToText import and invoke the same `flowsint_execution.fetch.execute_fetch`; fetch has no graph/service dependency. WebsiteToText graph use occurs after acquisition. F2 is in the integration consumer, not a second transport implementation.
- Trusted-caller scope: accepted only as a local in-process boundary. WebsiteToText derives allowed origins from explicitly supplied inputs, which is not authorization of an untrusted hosted user. No hosted authorization or distributed receipt was observed or credited.

## Tests and actual results

Interpreter: `/home/n4s5ti/Documents/dev/fl0sint/.venv/bin/python`. All runs used only this archive's `flowsint-app/src`, `flowsint-core/src`, `flowsint-enrichers/src`, `flowsint-types/src`, and `flowsint-mcp-server/src` on `PYTHONPATH`; no editable installed source was used.

Core targeted suite: **13 passed, 3 failed**. All three failures were sandbox `PermissionError: [Errno 1] Operation not permitted` at `socket.socket()` for loopback redirect, self-signed TLS, and real stalled-body tests. These are environmental, not product failures.

Enricher targeted suite: **13 passed, 2 failed**. Both failures were the same sandbox socket-bind denial for loopback integration tests.

Deterministic reproducer: exited 0 with:

```text
deadline_seconds=0.03 elapsed_seconds=0.282 status=success
replay_hits=2 first_requests=1 second_requests=1
substituted_input_ref_status=success text=substituted content
```

Coordinator socket command, if run in an environment permitting loopback binds:

```sh
cd /home/n4s5ti/.cache/def43-independent/flowsint-core
ARCHIVE=/home/n4s5ti/.cache/def43-independent \
AUTH_SECRET=a REDIS_URL=redis://127.0.0.1:6379 PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH="$ARCHIVE/flowsint-app/src:$ARCHIVE/flowsint-core/src:$ARCHIVE/flowsint-enrichers/src:$ARCHIVE/flowsint-types/src:$ARCHIVE/flowsint-mcp-server/src" \
/home/n4s5ti/Documents/dev/fl0sint/.venv/bin/python -m pytest -q -p no:cacheprovider \
tests/acquisition/test_fetch.py::test_real_loopback_cross_origin_redirect_never_hits_target \
tests/acquisition/test_fetch.py::test_real_untrusted_self_signed_tls_is_rejected \
tests/acquisition/test_fetch.py::test_real_stalled_body_obeys_total_deadline
```

## Vacuous or misleading test coverage

- `test_cancellation_returns_typed_accounting` and the deadline streaming tests use cooperative awaitables. They do not test a stream that consumes cancellation, so they miss F1 and overstate bounded cleanup.
- `test_real_untrusted_self_signed_tls_is_rejected` proves only `TOOL_ERROR`; an unrelated connection/transport failure satisfies it. It should also prove that the server was reached far enough for the TLS handshake and that the failure class corresponds to certificate verification without exposing unsafe diagnostic details.
- `test_occurrence_reconstruction_uses_id_and_marks_missing_result` tests missing IDs only. It constructs a `FetchResult` without asserting operation ID, input reference, exact result cardinality, or rejection of unknown IDs, so it misses F2.
- Socket-backed tests are not vacuous, but could not execute in this verifier sandbox. Deterministic transports are sufficient for F1/F2 and most policy/accounting claims, not for a verified real TLS claim.

## Resolved uncertainties and remaining limits

URL authority probes covering backslashes, encoded separators, fragments containing `@`, explicit port zero, unsupported schemes, credentials, and effective ports did not expose a dispatch-authority mismatch between `urllib.parse` admission and `httpx` for accepted cases. This is not a proof over all parser inputs.

The historical logger periodic-flush failure was not treated as introduced, per instruction. I did not rerun full host suites: supplied baseline records state initial host core `949 passed, 1 periodic-flush failure` and enrichers `166 passed`; this review focused on candidate-critical paths. Real socket behavior, including self-signed TLS rejection, remains unverified in this sandbox pending the coordinator command above.
