# Standalone acquisition contract

## Reviewed source retention and resolution

`flowsint_execution.artifacts` is the graph-free source-proof runtime.
`FilesystemArtifactStore` uses a deployment-selected trusted root, content-addresses
complete raw bytes by SHA-256, atomically writes objects, records separate immutable
occurrence snapshots, and verifies length and digest on every read. Serialized locators
are opaque; the resolver never accepts them as filesystem paths and rejects symlink reads.

Retention authority is runtime-owned. `RetentionAuthority` contains the issuer, reviewer,
reviewed policy digest, caller, scope, source family, and expiry, and issues an
operation-bound `RetentionDecision`. Request retention digests and template
`source_rights` cannot authorize retention. Missing authority or store produces HOLD after
a successful fetch is consumed, with no output or body retained. Missing or truncated
bodies, expired policy, missing objects, metadata mismatch, digest mismatch, and unsupported
UTF-8 mapping produce distinct REVIEW reasons and no bytes. Recovery requires reviewed
revalidation and a new immutable fetch snapshot; a digest alone is never sufficient.

`execute_fetch_with_source_proof` wraps the existing one-shot `execute_fetch`; it is not a
second fetcher. WebsiteToText scan, structured, and legacy paths use it. TemplateEnricher
captures its already bounded response through the same store. Production tasks and the
template-test API load `FLOWSINT_ARTIFACT_RUNTIME_CONFIG`; constructor injection remains a
test/local-call seam. WebsiteToText uses caller `website-to-text`, scope
`local-web-fetch`, source family `http`. A connector uses caller
`connector:<destination_id>:<endpoint_id>`, scope `enrich.read`, source family `connector`.
Store roots, headers, secrets, and bodies never enter bundles or reports. The config is an
operator-owned JSON document with `format_version: "1.0"`, an absolute `store_root`, and
reviewed `policies`. Each policy declares issuer, reviewer, policy ID, caller, scope,
source family, issuance/expiry, retention flags, and `content_digest`. The digest is the
SHA-256 of that policy object without `content_digest`, serialized with sorted keys and
compact separators. Missing, invalid, expired, ambiguous, or nonmatching policy fails
closed after fetch. Replacing the current policy prevents older proof resolution.

Raw offsets are zero-based half-open bytes into the exact retained UTF-8 response.
Normalized offsets are zero-based half-open Unicode code points. Tags and script/style
content are omitted, entities decoded, node whitespace collapsed and stripped, and one
space inserted between nonempty text nodes. Ordered segments map every emitted character,
including cross-node separators. Acquisition bundle `SpanReference` remains strict format
1.0. Normalized mappings use the separate strict `SourceProofSpanReference` and the
`source-proof/1.0` persisted proof envelope. Unknown proof versions are rejected.

```sh
cd flowsint-core
PYTHONPATH=src ../.venv/bin/python examples/source_proof.py \
  http://127.0.0.1:8765/fixture /tmp/def44-source-store
```

The example fetches a controlled fixture through the shared implementation, opens a fresh
store instance, resolves with explicit current authority, and prints a serialized
`AcquisitionBundle` plus a body-free resolution summary. A second consumer can pass the
returned `bundle` string directly to `parse_bundle`.

The value-only `flowsint_execution.acquisition` and `flowsint_execution.models` modules are the service-free Python boundary shipped in the `flowsint-core` wheel. They import only Python's standard library and Pydantic. The sibling `flowsint_execution.fetch` runtime uses the core package's existing `httpx` dependency but requires no database, graph, model, credential, or hosted-authentication service.

`flowsint_execution.models` owns the unchanged `InputOutcome`, `EvidenceEnvelope`, `StructuredExecutionResult`, `OutcomeStatus`, `RedactedDiagnostic`, and `canonical_input_hash` definitions. All repository callers use this namespace. The old `flowsint_core.core.execution` module is removed, not maintained as a compatibility shim. Existing workers serialize JSON, not Python module/class identities.

`flowsint_execution.acquisition` validates a versioned request and bundle. It does not fetch URLs, authorize access, admit operations, reserve resources, retrieve artifacts, verify source authenticity, accept assertions, or write a graph/database. A structurally valid declaration is not proof that its producer was authorized or truthful. Producers must use the shared acquisition/admission implementation when those components are enabled.

## Bounded HTTP admission and fetch

`flowsint_execution.fetch` is the single async HTTP path used by WebsiteToText and standalone local callers. A caller supplies an `AcquisitionRequest`, `FetchParameters`, and a separate `TrustedFetchPolicy`. Request fields are untrusted declarations. They cannot grant caller identity, scope, origins, capability, endpoint policy, or resources. Admission validates exact HTTP(S) origins including effective ports, rejects credential-bearing URLs, requires positive finite request/byte/time/concurrency allocations, and seals normalized inputs plus request, policy, and parameter digests. Execution rejects a copied or replaced admitted value whose seal no longer matches.

Every initial dispatch, redirect, and retry charges one shared operation ledger before network dispatch. Redirect destinations are validated before dispatch and remain same-origin. Bodies are streamed against both the shared byte allocation and an optional sealed per-input limit with `Accept-Encoding: identity`; declared and chunked overflow return no body or text. Accounting records bytes actually delivered by the transport, including the chunk that crosses a limit, so measured overspend is not truncated. The total deadline covers semaphore waits, connection work, reads, redirects, retries, and backoff. An independent watcher decides the deadline instead of relying on cancellation acknowledgement from a child task. At the boundary, already-completed outcomes are retained, pending outcomes become timeouts, and task cancellation plus client close receive a secondary finite cleanup budget. Per-occurrence counters retain work consumed before interruption and their request/byte totals reconcile with the returned operation snapshot. The implementation uses only async `httpx` with TLS verification, `trust_env=False`, and manual redirects. It has no synchronous fallback, ambient proxy/cookie authority, or certificate-bypassing QUIC path.

Python cannot forcibly terminate a coroutine that suppresses cancellation. Fetch transports are therefore trusted, cooperative in-process components. If one does not stop within the cleanup budget, `execute_fetch` returns the typed deadline/cancellation result and leaves a supervised background cleanup task to observe its termination and close the client. This is a bounded caller-return guarantee, not a hard-kill guarantee for malicious Python code.

Typed results distinguish success (including decoded empty content), rate limiting, timeout, policy denial, HTTP failure, transport/decode/size failure, and cancellation. Results retain occurrence identity and measured requests, bytes, and elapsed time. Diagnostics are fixed, bounded messages and never include raw URL queries, response bodies, or provider exceptions. Bare `execute_fetch` remains transient; the reviewed `execute_fetch_with_source_proof` wrapper performs authorized capture and does not claim factual acceptance.

Source-proof resolution requires trusted `caller_id`, `scope`, `source_family`, and
`operation_id`; exact-span resolution also requires the trusted `occurrence_id`.
These values are supplied server-side from the authorized caller or investigation
context. Proof metadata and request-controlled scope values never select authority.
Local Website identities are an explicit trusted-local boundary, not hosted-user
authentication.

The fetch deadline covers receive, normalization, and capture. Blocking normalization
and store work runs on a bounded shared worker pool with cooperative checks before
storage and after normalization. A timeout or caller cancellation returns without
evidence even if an already-started atomic store commit later finishes; consumed
resources remain charged and the operation is never retried with a fresh allocation.

WebsiteToText creates its trusted local policy from the origins of explicitly supplied inputs. This is an in-process trust boundary, not hosted authentication. Its allocation defaults are finite, `max_response_bytes` is enforced independently for every input as well as through the pooled allocation, its redirects are same-origin, and its public `scan`, `execute_structured`, and legacy `execute` paths all invoke this same admitted fetch. Before associating any evidence, WebsiteToText requires the returned operation ID, exact occurrence count and ID set, unique occurrence IDs, and every occurrence's admitted `input_ref` to match. Missing, duplicate, unknown, wrong-operation, and wrong-input results fail the whole integration response as `invalid_fetch_result`; there is no positional fallback. A requested legacy QUIC path fails explicitly as `unsafe_transport_disabled`.

An admitted operation object is a trusted in-process execution capability, not remote authorization, a wire capability, or a durable reservation. Its immutable public fields carry private runtime claim state that is excluded from serialization and the operation seal. `execute_fetch` atomically claims that state before allocating a fresh ledger, so the same admitted runtime object, including an in-process `model_copy`, cannot reset its allocation through sequential or concurrent reuse. Serializing public fields and reconstructing a new model creates a new local object and is outside this object-scoped guarantee; serialized admitted models must not be treated as executable capability transfer. This enforcement has no global registry and is not a cross-process replay ledger. Callers needing wire transfer, distributed reservation, or idempotency must provide that outside this transport boundary.

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
# Observed extraction (`observed-extraction/1.0`)

Observed extraction is metadata layered on the unchanged acquisition/1.0 contract. The
authoritative value types and pure extractor are in
`flowsint_execution.observed_extraction`. A live WebsiteToText request extracts only
after the response has been admitted and retained, inside the existing source-proof
worker and its original elapsed allocation. Saved replay uses
`extraction_runtime.resolve_and_extract_observations(source_proof, ...)`; caller,
scope, source family, operation, and occurrence must match the persisted proof and the
current reviewed runtime policy.

Raw observation spans are byte offsets into the retained body and are resolved against
its exact length and SHA-256 digest. They are not normalized `SourceProofSpanReference`
ranges. Links and forms are unreviewed, non-executable metadata. They never create
Email, Phone, Individual, or graph-edge outputs. Relative references use the retained
final page URL. Non-sensitive query parameters are retained so fragment-only references
inherit the correct query. Empty attribute values are omitted because the contract
requires a nonempty exact raw span; query strings containing credential-like names
(`token`, `secret`, `password`, `key`, authorization codes, or signatures) are removed
as a whole. This conservative policy can discard benign parameters with those names.

HOLD and REVIEW resolution states produce no observations. A revoked or unavailable
current policy cannot be bypassed with an older proof. Both the source proof and the
machine-readable observation result are bounded before a success outcome is returned.
