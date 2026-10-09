# DEF44 corrected immutable archive independent final review

## Verdict

**BLOCK / not verified.** The corrected archive materially repairs prior F1-F8, but it still has one high-severity authorization defect and three medium-severity defects. Persisted resolution does not require the trusted caller to supply the exact operation ID, source-proof processing can exceed the admitted operation deadline without accounting for that time, a successfully retained fragmented response can produce an evidence proof above the persistence model's 8 MiB limit, and the standalone example cannot emit a valid-empty acquisition bundle.

This was a same-vendor independent process. The archive intentionally contains no usable Git metadata, so I did not independently establish commit `a0eb7c3e`; the parent-provided whole-tree SHA check is the commit-integrity control. No archive source, index, commit, or tracker was changed.

## Findings

### F10 — High — persisted resolver lacks a trusted operation boundary

**Source:** `flowsint-core/src/flowsint_execution/artifact_runtime.py:170-200,202-235`; `flowsint-core/src/flowsint_execution/artifacts.py:134-159,304-341`; `flowsint-enrichers/src/flowsint_enrichers/website/to_text.py:42-43,352-369`; `flowsint-core/src/flowsint_core/core/template_enricher.py:278-325`.

`resolve_persisted_source_proof()` and `resolve_persisted_span()` accept trusted `caller_id`, `scope`, and `source_family`, but no trusted `operation_id`. The operation used by authorization is read from the proof itself. The record, decision, and proof are internally bound, but that only proves which operation created the artifact. It does not establish that the resolving request is authorized for that operation.

The independent repro captures an `operation-a` proof and resolves it successfully from a nominal different-operation caller context because there is no API parameter with which that context can express its operation. Result: `same_caller_scope_cross_operation: "available"`.

This is exploitable within the supported trust model whenever a trusted component obtains another persisted proof under the same deployment identity. Website uses fixed `website-to-text` / `local-web-fetch` across sketches and connector uses fixed endpoint caller / capability scope across investigations. Website and connector remain isolated from each other by caller, scope, and source family, but each surface lacks per-operation or per-investigation resolution isolation. Possession of proof metadata therefore supplies the missing operation selection, contrary to the documented invariant that possession grants nothing and exact operation must be supplied at resolution time.

**Minimal repro:** `/tmp/def44-independent-final-repro.py`, function `persisted_policy_and_operation`; evidence in `/tmp/def44-independent-final-repro.out`.

### F11 — Medium — source-proof work ignores the whole-operation elapsed budget and cancellation boundary

**Source:** `flowsint-core/src/flowsint_execution/fetch.py:621-706,709-816`; `flowsint-core/src/flowsint_execution/artifacts.py:269-302,395-425`.

`execute_fetch()` applies the operation deadline only through completion of network fetch. `execute_fetch_with_source_proof()` then synchronously normalizes and writes every successful body after that deadline has ended. Returned `actual_resources.elapsed_seconds` is the fetch ledger value and excludes this post-fetch work.

The independent repro uses the supported injected artifact-store seam with a 50 ms allocation and a 150 ms write. It returns `AVAILABLE`; wall time was about 174 ms while reported elapsed time was about 1 ms. Synchronous normalization and store writes also cannot observe task cancellation until they return to the event loop. This is not a demand for distributed authorization; it is a local admitted-operation accounting and cancellation defect.

**Minimal repro:** `/tmp/def44-independent-final-repro.py`, function `policy_and_elapsed`.

### F12 — Medium — retained fragmented responses can exceed the evidence/report proof bound

**Source:** `flowsint-core/src/flowsint_execution/artifacts.py:14-15,269-302`; `flowsint-core/src/flowsint_execution/artifact_runtime.py:154-168`; `flowsint-core/src/flowsint_execution/models.py:39-53`; `flowsint-enrichers/src/flowsint_enrichers/website/to_text.py:84-102,352-382`.

The store bounds canonical record metadata to 8 MiB, but persisted proof is a second JSON representation of the same spans plus other fields, then base64 encoded. `EvidenceEnvelope.artifact_reference` is independently limited to 8 MiB. These bounds are not coordinated before retention.

A 80,000-byte body containing 40,000 repeated `"x "` pairs produced 79,999 exact spans. Its record was accepted and the body retained, but the encoded proof was 34,700,616 bytes and `EvidenceEnvelope` rejected it. In the website path this happens after capture, so the structured operation fails after retaining an artifact that it cannot persist or report. A still larger fragmented body is typed as `artifact_store_invalid`, so the raw store cap itself is fail-closed; the defect is the accepted region between the record and evidence limits.

**Minimal repro:** `/tmp/def44-independent-final-repro.py`, function `compatibility_and_bounds`.

### F13 — Medium — standalone example rejects valid-empty acquisition

**Source:** `flowsint-core/examples/source_proof.py:62-100`.

For an empty normalized page, the example creates no spans, evidence, or candidates but always constructs `OccurrenceOutcome(status=SUCCESS_WITH_OUTPUT)`. The acquisition contract correctly rejects that status with zero candidates. The independent repro executes the example's real shared fetch/capture path with an injected empty HTML response and receives `ValidationError: success status disagrees with candidate cardinality` instead of a parseable `VALID_NO_RESULT` bundle.

Nonempty example construction uses `build_bundle`, `serialize_bundle`, and `parse_bundle`, so prior F9 is repaired for output-bearing pages only.

**Minimal repro:** `/tmp/def44-independent-final-repro.py`, function `example_bundle`.

## Prior F1-F9 adjudication

- **F1 repaired:** literal `<`, quoted `>`, comments, Unicode, entities, repeated text, and script/style cases normalized correctly. Every emitted span resolved by re-normalizing retained bytes. Unrelated bytes and a corrupted mapping were denied.
- **F2 repaired:** `retain_normalized_text=false` returns `normalized_retention_prohibited`, no normalized result, and no spans.
- **F3 repaired:** deployment-owned runtime configuration is loaded on each WebsiteToText acquisition and each TemplateEnricher response capture. Website registry construction and TemplateEnricher's real constructor path recognize it. Defaults fail closed, while constructor injection remains a test/local seam rather than the only usable path. Template `source_rights` does not select authority.
- **F4 repaired subject to F10/F12:** structured evidence persists body-free source-proof metadata, and a fresh resolver with current runtime configuration can resolve bytes/spans without private object state.
- **F5 repaired:** future decisions, expired current policy, replacement identity, and missing/revoked current configuration fail closed. Resolution rereads current config, so changes after construction/capture are observed.
- **F6 repaired:** descriptor-relative `O_NOFOLLOW` traversal rejects symlink ancestors; the external directory remained empty in the repro. Descriptor traversal also closes the reviewed ancestor race class.
- **F7 repaired:** store write exceptions become typed HOLD/REVIEW results, and the wrapper retained consumed byte accounting (`8` bytes in the repro).
- **F8 repaired:** strict acquisition `SpanReference` 1.0 retains its original five fields. Normalized mapping uses separate `SourceProofSpanReference` and `source-proof/1.0`.
- **F9 partially repaired:** the example builds and parses the acquisition bundle for output-bearing responses, but F13 blocks valid-empty behavior.

## Additional boundary results

- Runtime loader does not cache production authority in constructors. Website and TemplateEnricher call `load_artifact_runtime()` at acquisition/capture time; persisted resolution loads it again. Replacing or removing config after capture caused `current_policy_mismatch` or `current_policy_unavailable`.
- Website proof URLs are passed through `_redacted_location()`, which removes userinfo, query, and fragment. Connector registry rejects base URL credentials/query/fragment, and neither proof path serializes request headers. Reviewed report serializers include proof/digest identifiers but not raw headers or vault values. No credential/header leak was reproduced.
- Website and connector policies are isolated by distinct caller, scope, and source family. F10 concerns cross-operation use within one such fixed surface, not arbitrary cross-surface access.
- `source_rights` remains descriptive evidence metadata and cannot create a retention authority.
- The 8 MiB evidence field and Text database column prevent unbounded database insertion, but F12 shows retention can succeed before proof persistence rejects the value.

## Test evidence

All commands used:

```text
ARCHIVE=/home/n4s5ti/.cache/def44-independent-final
PYTHONPATH=$(printf '%s:' "$ARCHIVE"/*-*/src)
AUTH_SECRET=def44-fixture-only
REDIS_URL=redis://127.0.0.1:6379
/home/n4s5ti/Documents/dev/fl0sint/.venv/bin/python ...
```

- Independent repro: exit 0, all repair and attack assertions executed.
- Core artifact/runtime/contract tests: `70 passed`.
- TemplateEnricher tests: `16 passed`. A post-test logging attempt reported unavailable PostgreSQL; test exit was still 0 and the tested behavior had completed.
- WebsiteToText tests: `21 passed, 2 failed`; both failures were solely loopback socket creation denied by the sandbox (`PermissionError: [Errno 1] Operation not permitted`). Per request, these require the parent host run and are not counted as product failures or independent runtime verification.
- A combined core/enricher pytest invocation first hit pytest's duplicate `tests.conftest` import-path collision; suites were rerun separately.

Author tests are self-checks. The verdict and findings above rely on the separate `/tmp` repro and direct corrected-source inspection.

## Evidence files and hashes

```text
e252fe141a0dea4e3f53017180c5a8104f0e3d81ce3ee9c8c8a5335bc6a24d9d  /tmp/def44-independent-final-repro.py
38d650a28c9c27b763bb96e39e1dd93430037dab39bd48ca789e6d38d596c42f  /tmp/def44-independent-final-repro.out
aefe4316c15a30a173db4545476a38278e2881bde215ccfe85b593197da0a26a  flowsint-core/src/flowsint_execution/artifacts.py
498f60b8dc56f9203c09755465e7f00dcc1bff9954bdca2ac6ccc177d5fb9b98  flowsint-core/src/flowsint_execution/artifact_runtime.py
559a37cbd9e55686500576c5061ba185592b5ce9ba1d699c08f69bf55b5c67b6  flowsint-core/src/flowsint_execution/fetch.py
a96160e509f2071e9c8850f43041eb8e347db688d6a4b30f45faee8c8ee2005f  flowsint-core/examples/source_proof.py
```

The report itself was written after these hashes. Re-running the repro changes timing fields and therefore the output hash.
