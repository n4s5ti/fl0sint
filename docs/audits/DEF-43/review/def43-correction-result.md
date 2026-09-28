# DEF-43 correction result

## Corrected behavior

- Streaming `httpx.ReadError` and `httpx.ReadTimeout` failures now become typed `TOOL_ERROR` and `TIMEOUT` outcomes. Already delivered bytes and the failed dispatch remain accounted. Other occurrences complete, and operation cleanup has no escaped transport exception.
- Byte accounting records the complete delivered chunk, including bytes beyond the cap. Oversized content is still rejected with no body or text. A `Content-Length` rejection before streaming correctly records zero delivered bytes.
- Request and byte counters are owned by the operation ledger and keyed by occurrence ID, rather than coroutine-local variables. Deadline/cancellation during streaming, retry backoff, request dispatch, or semaphore waiting reconstructs each outcome from those retained counters. Operation request/byte totals reconcile with the sum of occurrence totals.
- `FetchParameters.max_bytes_per_input` is sealed into admission. WebsiteToText binds it to validated `max_response_bytes`, while retaining the pooled `count * max_response_bytes` operation cap.
- WebsiteToText reconstructs outcomes by admitted occurrence ID. Missing results become explicit `missing_fetch_result` failures; duplicate returned IDs fail the batch explicitly. Source references and ordered cardinality remain intact.
- WebsiteToText rejects boolean values for numeric `max_response_bytes`, `max_concurrency`, and `request_timeout` before arithmetic. Existing strict fetch parameter validation covers retry/redirect booleans.
- Explicit port `0` is rejected instead of being normalized through a falsy-port default.

## Reproduction evidence

The new deterministic tests failed before production edits as expected:

- delivered 9-byte overflow was reported as 8;
- streaming `ReadError` escaped `execute_fetch`;
- deadline outcomes reported per-occurrence request/byte counts as zero;
- `max_bytes_per_input` did not exist and WebsiteToText accepted pooled-only overflow;
- positional `zip(specs, outcomes)` dropped/misbound missing results;
- boolean WebsiteToText numeric parameters were accepted or failed only under unrelated field names.

Regression coverage also includes streaming `ReadTimeout`, deadline during retry backoff, and an undispatched occurrence waiting on the semaphore.

## Assessment and boundary

- Boolean multiplication was a real WebsiteToText vulnerability and is fixed.
- Port-zero truthiness normalization was real and is fixed by explicit rejection.
- Re-executing the same admitted operation creates a fresh in-process ledger. This runtime does not claim durable reservation, replay prevention, or distributed accounting. The admitted object is a trusted local single-execution capability; callers must execute it at most once. This limit is now documented without adding an unrelated distributed ledger.

## Checks run

- Deterministic core fetch and acquisition contract set: **58 passed, 3 socket tests deselected**.
- Final deterministic core fetch correction set: **13 passed, 3 socket tests deselected**.
- Full core suite excluding the three socket-only fetch fixtures: **954 passed, 3 deselected**.
- Final deterministic WebsiteToText set: **13 passed, 2 socket tests deselected**.
- Full enrichers suite excluding four socket-only tests: **167 passed, 4 deselected**.
- `py_compile` passed for both production modules.
- `git diff --check` passed.

The suites emitted the existing asynchronous logger database flush error after pytest completion. No logger file was touched, consistent with the coordinator's known unrelated failure.

## Remaining verification

Overall status is **unverified**, not claimed fixed: this child sandbox cannot bind sockets, and no distinct independent verifier exercised the final behavior here. The coordinator should rerun the real loopback redirect, invalid-TLS, stalled-stream, WebsiteToText loopback, and fixture-server tests on the host, then obtain independent review. The coordinator's prior real-suite results predate these corrections.

No commit, tracker change, external collection, graph indexing, or logger modification was performed.
