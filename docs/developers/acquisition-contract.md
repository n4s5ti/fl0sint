# Standalone acquisition contract

The value-only `flowsint_execution.acquisition` and `flowsint_execution.models` modules are the service-free Python boundary shipped in the `flowsint-core` wheel. They import only Python's standard library and Pydantic. The sibling `flowsint_execution.fetch` runtime uses the core package's existing `httpx` dependency but requires no database, graph, model, credential, or hosted-authentication service.

`flowsint_execution.models` owns the unchanged `InputOutcome`, `EvidenceEnvelope`, `StructuredExecutionResult`, `OutcomeStatus`, `RedactedDiagnostic`, and `canonical_input_hash` definitions. All repository callers use this namespace. The old `flowsint_core.core.execution` module is removed, not maintained as a compatibility shim. Existing workers serialize JSON, not Python module/class identities.

`flowsint_execution.acquisition` validates a versioned request and bundle. It does not fetch URLs, authorize access, admit operations, reserve resources, retrieve artifacts, verify source authenticity, accept assertions, or write a graph/database. A structurally valid declaration is not proof that its producer was authorized or truthful. Producers must use the shared acquisition/admission implementation when those components are enabled.

## Bounded HTTP admission and fetch

`flowsint_execution.fetch` is the single async HTTP path used by WebsiteToText and standalone local callers. A caller supplies an `AcquisitionRequest`, `FetchParameters`, and a separate `TrustedFetchPolicy`. Request fields are untrusted declarations. They cannot grant caller identity, scope, origins, capability, endpoint policy, or resources. Admission validates exact HTTP(S) origins including effective ports, rejects credential-bearing URLs, requires positive finite request/byte/time/concurrency allocations, and seals normalized inputs plus request, policy, and parameter digests. Execution rejects a copied or replaced admitted value whose seal no longer matches.

Every initial dispatch, redirect, and retry charges one shared operation ledger before network dispatch. Redirect destinations are validated before dispatch and remain same-origin. Bodies are streamed against both the shared byte allocation and an optional sealed per-input limit with `Accept-Encoding: identity`; declared and chunked overflow return no body or text. Accounting records bytes actually delivered by the transport, including the chunk that crosses a limit, so measured overspend is not truncated. The total deadline covers semaphore waits, connection work, reads, redirects, retries, and backoff. Per-occurrence counters live for the operation lifetime, so deadline and cancellation outcomes retain work consumed before interruption and their request/byte totals reconcile with the operation totals. The implementation uses only async `httpx` with TLS verification, `trust_env=False`, and manual redirects. It has no synchronous fallback, ambient proxy/cookie authority, or certificate-bypassing QUIC path.

Typed results distinguish success (including decoded empty content), rate limiting, timeout, policy denial, HTTP failure, transport/decode/size failure, and cancellation. Results retain occurrence identity and measured requests, bytes, and elapsed time. Diagnostics are fixed, bounded messages and never include raw URL queries, response bodies, or provider exceptions. Fetch does not retain a source artifact and does not claim extraction or factual acceptance.

WebsiteToText creates its trusted local policy from the origins of explicitly supplied inputs. This is an in-process trust boundary, not hosted authentication. Its allocation defaults are finite, `max_response_bytes` is enforced independently for every input as well as through the pooled allocation, its redirects are same-origin, and its public `scan`, `execute_structured`, and legacy `execute` paths all invoke this same admitted fetch. Results are joined back to sources by sealed occurrence ID; a missing occurrence becomes an explicit failure. A requested legacy QUIC path fails explicitly as `unsafe_transport_disabled`.

An admitted operation is a trusted in-process execution capability, not a durable reservation or replay token. `execute_fetch` does not coordinate replay prevention across calls or processes. Local callers must execute an admitted operation at most once; callers needing distributed reservation or idempotency must provide that outside this transport boundary.

## Ownership and attribution

A bundle describes one isolated invocation. Operation and bundle identities, occurrence ownership, origin digest, source references, timestamps, extraction version and measured resources travel together. Content-identical inputs may share `input_ref` but remain distinct occurrences. Completion order never decides ownership.

Optional imported lineage retains run, step, attempt, input index and originating seed/entity. Absent integrated/model/graph context means not applicable; it is never reconstructed as a model decision, confidence `0.0`, or accepted fact. `ExecutionService.persist_structured_result` is **not** a lossless bundle importer: its database schema cannot retain all portable identities and provenance. This release adds no importer, ledger or schema migration. A future importer must preserve immutable bundle/input/evidence identity and digest, deduplicate ingestion and return canonical execution references before integrated acceptance proceeds.

Acquisition success, observation retention and assertion acceptance are separate. Candidates remain explicitly unreviewed. `SUCCESS_WITH_OUTPUT` means valid acquired output, not factual acceptance. `VALID_NO_RESULT` requires a completion witness; an unexplained legacy empty list is `UNKNOWN_OUTCOME`. `PARTIAL` preserves successful and failed/held children. `RETENTION_HOLD` preserves its diagnostic, attribution and measured resources without retaining prohibited material or receiving factual-success credit.

## Public API and minimal caller

Import from `flowsint_execution.acquisition`; there are no package-root re-exports. `build_bundle` validates the reference graph and computes its digest. `serialize_request` / `serialize_bundle` validate Python values **before** JSON encoding and then validate the wire representation. `parse_request` / `parse_bundle` report `ContractError.code` and safe `field_paths`. These functions, not unchecked `model_construct` / `model_copy` or raw Pydantic dumping, are the interchange boundary. Models are frozen but nested JSON values are not deeply immutable.

```python
from flowsint_execution.acquisition import (
    AcquisitionRequest, InputOccurrence, Resources, serialize_request,
)
from flowsint_execution.models import canonical_input_hash

value = "source.json"
request = AcquisitionRequest(
    operation_id="local-operation-1", caller_id="local-caller", scope="local-fixture",
    capability_digest="a" * 64,  # Substitute the selected capability's real digest.
    inputs=(InputOccurrence(occurrence_id="input-1", input_ref=canonical_input_hash(value),
                            type_tag="record_locator", value=value),),
    allocation=Resources(requests=0, bytes=1024),
)
wire_request = serialize_request(request)
```

The [executable local example](../../flowsint-core/examples/acquisition_contract.py) constructs successful and failed responses. From `flowsint-core`, with the repository environment installed:

```sh
PYTHONPATH=src ../.venv/bin/python examples/acquisition_contract.py /path/to/new-output-directory
```

It writes `request.json`, `success.json`, `failed.json`, and `source.json`. Response summaries are `success_with_output` with `["unreviewed"]`, and `invalid_input` with no candidates. Operation IDs, timestamps and digests vary. It refuses to overwrite an existing output directory. This is a controlled local-fixture contract example, not the P1 network scraper. The failed response is an explicit caller-supplied rejection; it performs no fetch.

A second local caller needs only the installed namespace and Pydantic:

```python
from pathlib import Path
from flowsint_execution.acquisition import parse_bundle
bundle = parse_bundle(Path("success.json").read_bytes())
assert bundle.candidates[0].disposition == "unreviewed"
```

## Wire semantics

- Required request: operation/caller/scope, capability digest, explicit input occurrences, allocation. Optional parent operation, Need reference and endpoint/projection/retention policy digests remain null when not applicable. Type tags identify producer input schemas; this contract validates finite JSON shape and content hash, not every domain-specific type such as an email address.
- `input_ref` uses canonical hashing from `models`; `occurrence_id` is independent. Optional `ImportedLineage` is all-or-nothing: run, step, positive attempt, nonnegative input index and seed/entity ID. Parsing does not regenerate identities.
- Each requested occurrence has exactly one outcome, irrespective of order. Granular wire statuses are lowercase enum values. Optional `underlying_status` retains canonical success/failure/hold; contradictory terminal mappings are rejected. Partial outcomes retain distinct terminal children, their underlying statuses/diagnostics and exactly their successful output references.
- Artifact → span → evidence → candidate references must resolve. Evidence/candidates retain occurrence ownership. Artifact/span sharing across duplicate inputs is allowed; candidate ownership is not. Unreferenced proof records are rejected. Successful empty outcomes may retain directly referenced artifacts. Failed, unknown and held outcomes cannot carry material. A hold's reason is its required redacted diagnostic; consumed resources remain recorded.
- Artifact locators are mandatory. Requested/final HTTP(S) URLs appear together when applicable; credentials in URLs are rejected. Timestamps are timezone-aware and normalized to UTC. Byte spans are nonempty half-open ranges bounded by artifact size; field pointers use JSON Pointer syntax. Evidence retains extraction method/version and declared subject attribution. The contract does not fetch locators or authorize retrieval.
- Resource dimensions are independently typed, finite and nonnegative: requests, pages, bytes, elapsed seconds, model calls/tokens, browser actions, concurrency, fanout and USD spend. Null means unmeasured/not applicable, not zero. Measured overspend is preserved; enforcement belongs to runtime admission/accounting.
- `origin_digest` is SHA-256 of the normalized complete payload excluding only its own field. Encoding is Python JSON with sorted object keys, escaped non-ASCII, compact separators, no nonfinite numbers and model defaults included. Arrays retain order. This detects corruption, not authenticity: a producer can compute a new digest.
- Missing/future versions use `unsupported_format_version`; malformed schema/reference/digest uses `malformed_contract`; malformed JSON uses `invalid_json`; duplicate JSON keys use `duplicate_json_key`. Diagnostics omit rejected values and untrusted extra-key names. Do not log raw Pydantic validation exceptions at an untrusted boundary.

## Compatibility and recovery

Format `1.0` is the only supported wire format. Missing/unsupported versions and unknown fields are rejected, not silently dropped. Format version is not deployment policy identity: applicable capability and policy content digests identify implementation/configuration separately.

Before downstream adoption, revert the DEF-41 implementation commit as a unit to restore canonical imports and wheel metadata. After adoption, stop the producer and retain existing bundles unchanged; migrate consumers explicitly rather than silently downgrading documents. Never rewrite origin digests, collapse duplicate occurrences, delete evidence, reset consumed resources or mint replacement operation IDs to conceal uncertain outcomes. Rollback does not authorize re-execution.
