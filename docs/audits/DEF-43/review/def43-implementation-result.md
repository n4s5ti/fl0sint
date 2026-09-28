# DEF-43 implementation result

## Implemented

- Added `flowsint_execution.fetch`, an independently callable admitted async HTTP runtime using only `httpx` with verified TLS, `trust_env=False`, manual redirects, identity encoding, streamed byte enforcement, and a total operation deadline.
- Bound trusted caller/scope/origin policy, validated request values, strict finite allocation, and bounded parameters into immutable request/policy/parameter digests plus an operation seal. Copied/replaced admitted values are rejected before dispatch.
- Added one concurrency-safe operation ledger. Every initial request, redirect, and retry is charged before dispatch; body bytes are charged during streaming. Typed results retain input identity, status, bounded redacted diagnostics, and request/byte/elapsed accounting.
- Replaced WebsiteToText's redirect-following client, certificate-disabled QUIC, and synchronous requests fallback with the shared admitted fetch. All public scan/structured/legacy paths use it. Legacy QUIC requests fail explicitly. Legacy list execution raises on typed failures instead of converting denial/failure into a successful empty list.
- Preserved ordered S02 occurrence ownership, duplicate cardinality, successful empty output, graph attribution, and the final successful legacy `list[Phrase]` adapter. Added accounting and fetch status to occurrence envelopes.
- Added mock-transport behavior tests plus deterministic real loopback HTTP, stalled-response, cross-origin redirect, and generated self-signed TLS fixtures for host execution. Updated acquisition/enricher developer contracts and changelog.

## Tests run in this sandbox

- RED captured: `flowsint-core/tests/acquisition/test_fetch.py` initially failed collection with `ModuleNotFoundError: flowsint_execution.fetch`.
- RED captured: legacy execute denial test initially failed because no exception was raised.
- `flowsint-core`: targeted fetch + acquisition contract, socket fixtures excluded: **52 passed, 3 deselected**.
- `flowsint-enrichers`: WebsiteToText occurrence/public adapter tests, two loopback tests excluded: **8 passed, 2 deselected**.
- Full `flowsint-core/tests`, three new socket fixtures excluded: **947 passed, 3 deselected**.
- Full `flowsint-enrichers/tests`, two WebsiteToText loopback tests excluded: **161 passed, 2 existing acquisition-fixture socket failures, 2 deselected**. Both failures were `PermissionError: [Errno 1] Operation not permitted` at `socket.socket`, not product assertions.
- `py_compile` passed for both production modules. `git diff --check` passed.

## Host test command

Run outside the socket-restricted sandbox:

```sh
cd /home/n4s5ti/Documents/dev/fl0sint-def43-s03
AUTH_SECRET=test-only REDIS_URL=redis://127.0.0.1:6379/15 \
PYTHONPATH="$PWD/flowsint-core/src:$PWD/flowsint-enrichers/src:$PWD/flowsint-types/src" \
/home/n4s5ti/Documents/dev/fl0sint/.venv/bin/python -m pytest \
  flowsint-core/tests/acquisition/test_fetch.py \
  flowsint-core/tests/acquisition/test_contract.py \
  flowsint-enrichers/tests/enrichers/test_website_to_text.py \
  flowsint-enrichers/tests/enrichers/test_acquisition_fixtures.py -q
```

## Remaining risks / verification state

- **Unverified overall**: real loopback HTTP/TLS/stall tests could not run in this sandbox because socket creation is denied. The coordinator must run the host command and independent review before claiming DEF-43 verified or fixed.
- No external live collection, graph indexing, commit, push, merge, tracker update, or historical audit-evidence edit was performed.
