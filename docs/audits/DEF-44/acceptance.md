# DEF-44 / S04 acceptance

Verdict: REVIEW. Implementation and required fixtures complete; user review remains required before Done. No push, merge, deployment, external collection or shared-checkout changes.

Base: `b0c82d8db2b2480bc2b2aa91a02ba0fdb74111fd`. Genuine pre-edit scope/impact receipts: `d81cadff`, committed before production mutation. Implementation: `0882e3a2`; independent-review corrections: `a0eb7c3e`, `8fdfedb5`. Branch: `work/def-44-retained-source`, native isolated worktree `/home/n4s5ti/Documents/dev/fl0sint-def44-source`.

## Delivered boundary

One graph/service-free filesystem artifact store and resolver supports shared standalone HTTP acquisition, WebsiteToText and TemplateEnricher. Complete source bytes are content-addressed; immutable per-occurrence records retain retrieval metadata. Opaque, body-free source-proof metadata survives structured evidence conversion and existing persistence. Trusted runtime policy is loaded from `FLOWSINT_ARTIFACT_RUNTIME_CONFIG`; arbitrary template `source_rights` never grants retention. Unconfigured deployments HOLD after acquisition rather than treating discarded material as evidence.

Resolver authorization requires trusted caller/scope/operation and current reviewed policy; span resolution additionally requires occurrence identity. Possession of proof metadata grants no operation authority. Reads validate body length/digest, record identity, policy identity/expiry, and exact source mapping. Descriptor-relative no-follow I/O prevents symlink-ancestor escapes. Store roots and credentials are not serialized into evidence.

HTML parsing maps original UTF-8 byte ranges to Unicode-code-point ranges with deterministic entity/whitespace/tag transforms. Resolution re-parses retained bytes; it does not echo stored normalized strings. Strict acquisition 1.0 spans remain unchanged; normalized mappings use a separately versioned source-proof contract. Metadata limits are checked before retention, not deferred to report serialization. Dense plain text coalesces into compact exact spans.

The original elapsed allocation includes normalization and storage. Cancellation/deadline returns no late source evidence and preserves consumed counters. Already-started atomic writes may finish and remain immutable; they never become late success or trigger automatic retry. This is a trusted local runtime, not remote policy administration or process isolation for malicious injected Python code.

## Required acceptance fixtures

| Criterion | Observed proof | Result |
| --- | --- | --- |
| Every emitted span resolves to exact fixture text and digest | Real HTTP fixture with quoted `>`, Unicode, entity decoding and repeated text: 22 resolved spans over two occurrences reconstruct `Café & tea Repeat Repeat < 3`; exact 89-byte source digest and fresh resolver roundtrip | PASS |
| Another scope cannot retrieve snapshot | Real public persisted resolver denied different operation and different scope; independent tests also deny caller/occurrence mismatch, stale policy and tampered identity | PASS |
| Missing, tampered, truncated and retention hold distinguishable | Behavioral artifact tests exercise typed missing-body, truncated-body, digest mismatch, missing/expired artifact, future/expired/current policy and HOLD; no bytes/text leak | PASS |
| Duplicate content shares bytes without lost lineage | Two real HTTP occurrences retained different snapshot/occurrence identities with exactly one content object | PASS |
| Missing retention remains post-acquisition HOLD | Runtime policy revocation followed by real acquisition returned HOLD with 1 request and 89 consumed bytes | PASS |
| Standalone use needs no graph/planner/keys | Real loopback example returned public parseable AcquisitionBundle with exact digest and fresh-store resolution; graph/planner credentials absent | PASS |
| Valid empty stays valid empty | Independent real shared example path returned `valid_no_result`, zero candidates and completion witness | PASS |
| Bounded normalization/storage | Independent 200ms injected write under 50ms allocation returned timeout in about 52ms; cancellation returned about 23ms, both retained 15 consumed bytes without artifact/text/spans | PASS |
| Proof-size bound | 80KB repeated text became one reproducible span and 2,179-byte proof; 50,000-entity fragmented fixture rejected before any store write | PASS |

## Host verification

Final full core: **990 passed**. Full enrichers: **179 passed**. API template egress: **2 passed**. Real HTTP/TLS tests included; no socket deselections on host. Standalone real-HTTP example and integrated persisted-span smoke passed. Commands, outputs and assertion receipts are in `raw/host/`; reproducible smoke scripts are retained as audit evidence rather than application scaffolding.

Baseline under explicit synthetic test environment: 961 core / 177 enrichers passed. First pristine worktree runs lacked ignored `AUTH_SECRET` and failed collection; those receipts remain under raw/pre/initial. No production secret was copied. PostgreSQL logger shutdown noise is preexisting and recorded, not hidden or interpreted as successful database verification.

## Independent audit and mutation proof

Separate Codex processes reviewed committed archives, not the author's dirty tree. First audit found F1–F9; second identified F10–F13. All were corrected; final independent closure verdict PASS, with no introduced critical/high/medium finding. Review is same-vendor independent-process, not cross-vendor. Reviewer sockets were sandbox-blocked; host tests provide actual network proof. Whole-file archive comparison ties the final reviewed tree to `8fdfedb5`.

Isolated mutations killed by behavioral fixtures: raw-span corruption; authorization bypass leaking bytes; future-policy admission bypass; digest verification bypass exposing tampered bytes. Earlier weak mutation receipts are preserved separately and are not substituted for the stronger final core-correction receipts. These mutations were run during core correction; final host/independent suites re-exercised the resulting tests.

## Analysis coverage and limits

Fresh isolated pre/post indexes; final source index drift **zero**. All raw upstream/downstream results remain linked, including UNKNOWN/partial answers. Initial unmapped SourceArtifact/SourceSpan/enrich queries were retried using actual ArtifactReference/SpanReference/task symbols. Source mapping and runtime checks supplement dynamic registry/Celery edges. LSP unavailable due missing configured pyright executable; no complete-reference claim.

Scoped Doctor on `capture_source`: 1 symbol / 11 flows. Scoped Hunt exited 1 because this Python library has no CLI probe target; **unsupported, not PASS**. No unrelated installed CLI was probed to manufacture coverage. Prior hosted ghunt/httpx dependency-resolution conflict remains out of scope; this packet does not claim hosted CI green.

See `post-edit.json` for expected-versus-actual paths/configuration hashes, `review.json` for adjudication and archive provenance, `mutation-check.json`, `recovery.md`, and checksums.
