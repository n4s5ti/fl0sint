# DEF44/S04 independent adversarial review

## Verdict

**BLOCK / not verified.** The archive does not satisfy S04. The implementation retains and resolves complete bytes in isolated constructor-injected tests, and identical byte bodies deduplicate while occurrence records remain distinct. The central source-proof claim fails, however: emitted spans are not exact raw-byte-to-normalized mappings, `NormalizedSource.reproduce()` is tautological, and there is no API that resolves an emitted span from retained bytes. Enabled WebsiteToText and TemplateEnricher task/API paths also do not configure a store or retention authority.

The archive's `.git` directory is empty. I could not independently establish that it is commit `0882e3a2`. `docs/audits/DEF-44/baseline.json` instead names base `b0c82d8db2b2480bc2b2aa91a02ba0fdb74111fd`. File hashes below identify the reviewed snapshot.

## Findings

### F1 — Critical — span proof is false and has no resolver

**Source:** `flowsint-core/src/flowsint_execution/artifacts.py:21-24,109-129,335-374`; `flowsint-core/src/flowsint_execution/fetch.py:743-779`; `flowsint-core/src/flowsint_execution/acquisition.py:184-226`.

`_TOKEN.finditer()` does not account for unmatched source gaps. `byte_cursor` advances by matched token lengths rather than using `match.start()`/`match.end()` converted to byte offsets. The tag regex also terminates at a quoted `>` and cannot parse literal `<` text safely. Separator spans are invented over gaps/tags. `reproduce(body)` only decodes the supplied body and then joins stored `emitted_text`; it never derives text from the supplied bytes. It returns the original normalized text for an unrelated valid UTF-8 body.

Observed direct results from `/tmp/def44-review-repro.py`:

- `<p title="1 > 0">ok</p>` normalizes to `0">ok`, not `ok`.
- `alpha < beta <b>gamma</b>` emits `gamma` for raw range `[15,20)`, whose exact bytes are `>gamm`.
- `<style>x < y</style><p>ok</p>` emits `ok` from raw bytes `>o`.
- `<p>a < b</p>` gives overlapping/inconsistent spans and maps emitted `b` to raw bytes `< `.
- entities, multibyte Unicode, repeated text, and zero-width text produce metadata, but the implementation never proves those emitted values from the cited bytes.
- `reproduce(b"<p>UNRELATED</p>")` still returns the original normalized text.

No span-resolution function exists. `resolve_source()` returns only the whole body (`artifacts.py:284-332`). Stored normalized metadata is neither validated nor returned. Consequently no caller can ask the API to resolve an arbitrary emitted normalized range to a substring proven from the exact retained bytes.

**Minimal repro:** run `/tmp/def44-review-repro.py`; inspect `normalization`. The script asserts the quoted-attribute failure, the `>gamm` raw slice, and reproduction from unrelated bytes.

**Tests falsely reassuring:** `flowsint-core/tests/acquisition/test_artifact_capture.py:127-135` asserts `extracted.reproduce(body) == extracted.text`, but that assertion passes for every decodable body because `body` is unused after decoding. The shared-fetch test at lines 143-168 proves whole-body retrieval and that spans exist, not that any span resolves correctly.

### F2 — High — `retain_normalized_text=false` still emits prohibited normalized output

**Source:** `flowsint-core/src/flowsint_execution/artifacts.py:67-68,278`; `flowsint-core/src/flowsint_execution/fetch.py:743-779`; `flowsint-enrichers/src/flowsint_enrichers/website/to_text.py:317-330`.

The flag only suppresses the `normalized` field in the store record. `execute_fetch_with_source_proof()` still returns `NormalizedSource` and span references, and WebsiteToText converts that text to a `Phrase` success output.

**Observed:** the repro issued a live decision with `retain_normalized_text=False`. The record contained `"normalized": null`, while the public source-proof result returned `PROHIBITED NORMALIZED OUTPUT` plus one span.

**Minimal repro:** run the script and inspect `retain_normalized_text_false`.

### F3 — High — enabled task/API paths cannot configure source retention

**Source:** `flowsint-enrichers/src/flowsint_enrichers/website/to_text.py:95-98,252-281`; `flowsint-core/src/flowsint_core/tasks/enricher.py:101-114,300-312`; `flowsint-api/app/api/routes/enricher_templates.py:197-203`; `flowsint-core/src/flowsint_core/core/template_enricher.py:56-87,274-302,382-396`; `flowsint-enrichers/src/flowsint_enrichers/registry.py:44-49`.

Retention is constructor-only. The legacy WebsiteToText task asks the registry for an enricher without `artifact_store` or `retention_authority`. The connector Celery task and template test API construct `TemplateEnricher` without either dependency. These real paths therefore fetch, consume resources, then return HOLD and retain no body/output. The only enabled successful paths are direct tests/examples that manually inject both objects. No reviewed runtime configuration factory or dependency provider exists in production source.

This is a delivery blocker, not a demand for hosted/distributed authentication. The trusted local Python caller may supply authority, but the intended public task/API callers never do.

### F4 — High — persisted metadata cannot retrieve the retained source

**Source:** `flowsint-core/src/flowsint_core/core/template_enricher.py:300-325`; `flowsint-core/src/flowsint_execution/models.py:34-53`; `flowsint-core/src/flowsint_core/core/services/execution_service.py:448-481`; `flowsint-enrichers/src/flowsint_enrichers/website/to_text.py:66-85,317-330,516-517`.

TemplateEnricher discards the `ArtifactReference` and persists only `content_digest` plus locator `sha256:<digest>`. `resolve_source()` requires the snapshot ID, full artifact reference, exact context, and decision. Those are unavailable after persistence. WebsiteToText transiently places artifact/span objects on `WebsiteTextOccurrence`, but `to_input_outcome()` drops them, so structured/task persistence retains neither.

The stored locator proves neither occurrence nor snapshot and is not a resolvable handle. This fails “metadata persisted retrieves source” even if deployment injection is later added.

### F5 — High — policy issuer/reviewer identity, future issuance, and revocation are not enforced

**Source:** `flowsint-core/src/flowsint_execution/artifacts.py:56-76,92-106,157-167,260-280,284-332`.

`_bound()` checks expiry and operation/caller/scope/source family only. It does not reject `issued_at > now`. Resolver comparison checks `policy_digest`, but not `issuer_id`, `reviewer_id`, or `policy_id`. Store record tampering of those fields is accepted. A replacement decision with the same digest but different issuer/reviewer/policy ID also resolves. There is no current-authority/revocation input at resolve time, so a revoked decision remains usable until expiry.

**Observed:** the repro captures successfully with a decision issued two hours in the future; resolves after changing stored issuer/reviewer/policy/normalized metadata; and resolves with a decision relabeled to different issuer, reviewer, and policy IDs.

**Minimal repro:** inspect `resolver_store.future_issued_capture`, `tampered_record_resolve`, and `relabeled_policy_resolve`.

### F6 — High — trusted-root containment is bypassed through symlink ancestors

**Source:** `flowsint-core/src/flowsint_execution/artifacts.py:170-221`.

The store rejects only a symlink at the final object/record path. It does not reject symlink ancestors or use directory FDs with no-follow semantics. Replacing/creating `objects/<digest-prefix>` as a symlink redirects both write and read outside the trusted root. The digest prevents filename traversal but does not provide root containment.

**Observed:** the repro creates the digest-prefix directory as a symlink to a sibling temporary directory. `capture_source()` writes the body there, and `resolve_source()` follows the same ancestor and returns it as AVAILABLE.

**Minimal repro:** inspect `resolver_store.symlink_ancestor_external_write`.

### F7 — Medium — store failures escape typed state and lose consumed accounting at integration boundaries

**Source:** `flowsint-core/src/flowsint_execution/artifacts.py:224-281`; `flowsint-core/src/flowsint_execution/fetch.py:743-760`; `flowsint-enrichers/src/flowsint_enrichers/website/to_text.py:270-285`.

`capture_source()` does not catch `store.write()` failures. The source-proof wrapper therefore raises instead of returning HOLD/REVIEW with the already-consumed fetch resources. WebsiteToText catches the broad exception and creates generic failures without the fetched outcome's resource accounting. Read failures are typed, but write failures are not.

**Observed:** a store whose `write()` raises `OSError` propagates `OSError:fixture write failure` from `capture_source()`.

### F8 — Medium — strict v1 wire compatibility was broken without a version change

**Source:** `flowsint-core/src/flowsint_execution/acquisition.py:184-226,372-380`; `docs/developers/acquisition-contract.md:28-34,122-125`.

`SpanReference` gained normalized-offset and encoding fields while `format_version` remains strict literal `1.0`. An existing strict v1 consumer with `extra="forbid"` rejects a newly emitted v1 span. The documentation acknowledges that strict older consumers “must upgrade,” which is a wire-breaking change under the same version, not backward compatibility.

### F9 — Medium — standalone example is bespoke proof JSON, not a consumable acquisition bundle

**Source:** `flowsint-core/examples/source_proof.py:19-59`.

The example exercises the shared fetch/capture implementation, which is useful, but returns an ad hoc dictionary containing digest, normalized text, spans, and byte count. It never constructs or serializes an `AcquisitionBundle`, so an acquisition-contract consumer cannot consume its result. It also exposes normalized text while describing its result as body-free metadata.

## Confirmed positive behavior

- Complete bodies are content-addressed by SHA-256 and checked for length/digest on resolve (`artifacts.py:185-221,244-281,330-332`).
- Missing policy/store produces HOLD on the shared fetch path; missing body, truncated input, missing object, expired policy/artifact, metadata mismatch, and digest mismatch have typed reasons in isolated calls.
- Different operation/caller/scope values are denied by `_bound()`.
- Duplicate byte bodies share one object while separate randomized snapshot records and occurrence entries are appended. Existing test `test_identical_bodies_share_content_but_keep_occurrence_lineage` passed.
- Template `source_rights` no longer grants retention. Secret headers are assembled only for dispatch, and reviewed report serializers do not include headers/body. I found no secret value in the requested report/persistence fields.

These positives do not cure F1-F9.

## Test and runtime evidence

Environment used for every Python invocation:

```sh
ARCHIVE=/home/n4s5ti/.cache/def44-independent
PYTHONPATH=$(printf '%s:' "$ARCHIVE"/*-*/src)
AUTH_SECRET=def44-fixture-only
REDIS_URL=redis://127.0.0.1:6379
/home/n4s5ti/Documents/dev/fl0sint/.venv/bin/python ...
```

- Repro script: PASS, all adversarial assertions executed and JSON evidence emitted.
- Core targeted tests: `68 passed` (`test_artifact_capture.py`, `test_contract.py`, `test_template_enricher.py`). These are author self-checks and do not override the adversarial failures.
- WebsiteToText tests: `20 passed, 2 failed`; both failures were `PermissionError: [Errno 1] Operation not permitted` while binding loopback sockets. Per the provided constraint, real HTTP verification needs the host coordinator.
- API `test_template_egress.py`: produced no output and did not finish within about 90 seconds; interrupted. It is not counted as passing.
- No graph indexing, repository edits, commits, tracker actions, or live external collection were performed.

## SHA-256 evidence

```text
edf3483fa6133cb85c50e07eed96c74351d9d9993b45c6e8a36588304da43df6  flowsint-core/src/flowsint_execution/artifacts.py
787a4c8dba42f93d67d4c1bf154f786ce3d9d9e1ce3786ae6b134456344443b8  flowsint-core/src/flowsint_execution/fetch.py
52318d5a3995541033dd298a731f383090710efecccba81167515536186901ae  flowsint-core/src/flowsint_execution/acquisition.py
ca80436cce3333e7e672c90ae339b7b97d46414bd80ee75385676b83d542dee3  flowsint-enrichers/src/flowsint_enrichers/website/to_text.py
071289b92b3345e74082720127fb4139aa1b1e8bbe95970484e7e5679e66a826  flowsint-core/src/flowsint_core/core/template_enricher.py
141c1b647475768273e616285fe726974dc9339499226b4da3c74dbe630b5229  flowsint-core/src/flowsint_core/tasks/enricher.py
57159cb3c7141567952d99c00a92364909295ff975e14d729a9f666941b061b9  flowsint-api/app/api/routes/enricher_templates.py
2c97fee2406d7549e5364ac64ef7524f6785c4f07d53cee1a56c59ac075a87d7  flowsint-core/examples/source_proof.py
49bad8589f7b6a14d018e9b5f183b66ca966538678c0a5e73a534aefb6feef00  flowsint-core/tests/acquisition/test_artifact_capture.py
5e7e340522240294184ecf91d669622baeb0bbe67f69e74cea31a6b284d43aa9  /tmp/def44-map.md
fa105a4a7787c08daeef47b5ac98cfb0d1b5097da3a6fe1024f1cc2ff9bb80d4  /tmp/def44-review-repro.py
fe6a933f1deee1659ad70d965a2ab99cedbefa24f743f6e8b9cf8fb3466bcadb  /tmp/def44-review-repro.out
```

The two `/tmp` hashes above were calculated before this report was written. Re-running the repro changes only the temporary escape-path string in its output, so hash the new output if a new evidence run is retained.
