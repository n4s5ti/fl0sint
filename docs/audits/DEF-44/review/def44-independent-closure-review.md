# DEF44 independent closure review

## Verdict

**PASS.** F10-F13 are resolved in the immutable archive supplied as `at8fdfedb5`. I found no introduced critical, high, or medium defect. This was a same-vendor independent process. The archive intentionally has no usable Git metadata, so I could not independently derive or prove the supplied archive/commit label; parent whole-file comparison remains the archive-integrity control. I made no source, index, commit, or tracker changes.

The supported boundary assessed here is deployment-owned trusted local runtime configuration. I did not treat an untrusted user as able to select the artifact-store path or retention policy, and I did not expand scope to unrelated distributed authorization. For injected store threads, the required property is bounded caller return with no late emitted evidence. An already-started atomic write may finish and preserve immutable evidence without automatic retry.

## F10-F13 adjudication

- **F10 resolved.** Both persisted full-proof and span resolvers require trusted `operation_id`; span resolution also requires `occurrence_id`, and full-proof resolution checks it when supplied. My capture under `op-a`/`occ-a` resolved as `available`. `op-b` and `occ-b` each returned `review/authorization_mismatch` with no body. Span resolution likewise allowed the exact operation/occurrence and denied the wrong operation with no text.
- **F11 resolved.** The deadline begins before fetch and covers normalization/store work. A 200 ms injected store under a 50 ms allocation returned `timeout` in 0.0517 s with reported elapsed 0.0510 s and 15 consumed bytes. Cancellation returned `cancelled` in 0.0226 s with 15 bytes. Both returned no artifact, normalized value, or spans. After waiting beyond worker completion, the returned results still contained no evidence. Each already-started atomic write completed once, which is within the stated boundary and did not create a late success or reset counters.
- **F12 resolved.** `b"x " * 40_000` (80,000 bytes) normalized to one exact reproducible span and a 2,179-byte encoded proof. A separate attack of 50,000 fragmented HTML entities produced 50,000 spans and was rejected as `review/source_proof_too_large` before store invocation (`writes=0`), so no successful retention/report occurred past the proof bound.
- **F13 resolved.** The real standalone example construction path, with only transport replaced by an empty HTML response, returned an `available` bundle parseable by the public parser. Its outcome was `valid_no_result`, with zero candidates and a `controlled_fetch_complete` completion witness.

## Prior F1-F9 final adjudication

- **F1 repaired, no regression.** Exact-source normalization/span behavior remains covered by the acquisition suite. The independent F12 cases additionally reproduced the 80 KB body exactly from one span and exercised 50,000 distinct entity spans.
- **F2 repaired, no regression.** Normalized retention remains gated by deployment policy and the acquisition suite passed.
- **F3 repaired, no regression.** Runtime authority remains deployment-owned and loaded on operational paths. Constructor/test injection does not grant user policy authority.
- **F4 repaired, no regression.** Body-free persisted proofs resolve from a fresh runtime/store. My independent persisted proof resolved both the source and exact span.
- **F5 repaired, no regression.** Current-policy validation and fail-closed policy behavior remain exercised by the acquisition suite.
- **F6 repaired, no regression.** Descriptor-relative no-follow store behavior remains in final source and its acquisition tests passed.
- **F7 repaired, no regression.** Typed capture failure and resource accounting remain intact. My timeout and cancellation attacks retained the exact 15 consumed bytes and did not emit evidence.
- **F8 repaired, no regression.** Acquisition `SpanReference` remains separate from `SourceProofSpanReference`; the public contract tests in the passing acquisition suite cover the strict models.
- **F9 repaired, no regression.** The standalone example uses public bundle build/serialize/parse paths. F13 now confirms the zero-candidate branch is also valid.

## Commands and results

All Python commands used `/home/n4s5ti/Documents/dev/fl0sint/.venv/bin/python`, explicit `PYTHONPATH=$(printf '%s:' "$ARCHIVE"/*-*/src)`, `AUTH_SECRET=def44-fixture-only`, and `REDIS_URL=redis://127.0.0.1:6379`, with `ARCHIVE=/home/n4s5ti/.cache/def44-independent-closure`.

```text
python /tmp/def44-closure-repro.py
=> exit 0; all F10-F13 assertions passed

python -m pytest flowsint-core/tests/acquisition -q -k 'not real_loopback_cross_origin_redirect_never_hits_target and not real_untrusted_self_signed_tls_is_rejected and not real_stalled_body_obeys_total_deadline'
=> 90 passed, 3 deselected

python -m pytest flowsint-core/tests/templates/test_template_enricher.py -q
=> 16 passed (post-test PostgreSQL log sink unavailable message; exit 0)

python -m pytest flowsint-enrichers/tests/enrichers/test_website_to_text.py -q -k 'not legacy_public_execute_uses_loopback_occurrences_not_completion_zip and not public_scan_then_postprocess_captures_owned_occurrence_edges'
=> 21 passed, 2 deselected

python -m compileall -q flowsint-core/src/flowsint_execution flowsint-core/examples/source_proof.py flowsint-enrichers/src/flowsint_enrichers/website/to_text.py
=> exit 0
```

The five deselected tests are the known real-loopback cases that this sandbox cannot run. Per task instruction, the parent executes those actual loopback suites. They are not claimed as independently verified here.

## Exact SHA-256 evidence

```text
d6d608f40ca9fe1cd45618b49ec3b0e30679c8226b7b8a5766288b5ef07c739b  /tmp/def44-closure-repro.py
bf03e9be0b37251cb063d695b944194a3037bc7dae71755b5b3f09437d1a0c89  /tmp/def44-closure-repro.out
3714593211a8cdec5c1ef3c4ab0fd543b9f93846ece4dadbb42a6d358ad244a7  /tmp/def44-core-tests.out
8d7184a0c112a4c8ddf38ec6d2655ff8034c2f0039c1d613957bfc0947ec3c5b  /tmp/def44-template-tests.out
919d9b5450796446dc4d9fbbd317f98083b992aa47277e68ded6bc1d4d5032e8  /tmp/def44-website-tests.out
83018d0629555b5d4622615dd646b64494a953743040aad2737d40849649fa66  flowsint-core/src/flowsint_execution/artifacts.py
37e9257b56fc0f6eac06faae51d131aa2b9508bccab243a41c489ac3ec758fa1  flowsint-core/src/flowsint_execution/artifact_runtime.py
8d946a74f8c62c197d847b8f0b8938a8434ae906df66f9471dd0f5cd7c213096  flowsint-core/src/flowsint_execution/fetch.py
683376fc6f7841a858e239f09b42e2d361c16d29857b0d635cd5390afb5862ef  flowsint-core/examples/source_proof.py
c12e81eceaa3198ac6dc4c9a297dd0022dc5e3477b618d6fb8b798590b37d011  flowsint-enrichers/src/flowsint_enrichers/website/to_text.py
```

The report itself was written after these hashes. Re-running the behavioral repro changes timing values and therefore its output hash.
