# DEF-43 acceptance and review handoff

Implementation: `353a8389`; independent-review corrections: `b1474b07`. Source baseline: `cb5cc295`; genuine pre-edit scope and impact evidence committed as `7ece2072` before production changes. No deployment, merge, push, shared-checkout reindex, or external collection.

## Exercised acceptance

- Shared graph-free `admit_fetch` / `execute_fetch` runtime used by WebsiteToText. Caller/scope/policy digest admission, sealed parameters, HTTP(S)-only validated origins, same-origin redirect admission before dispatch, verified TLS, no ambient proxy trust, experimental QUIC rejected.
- Shared request/byte/elapsed/concurrency limits include redirects, retries and failures. Per-input response cap also enforced. Delivered overflow bytes are counted honestly, but oversized bodies never become successful evidence.
- Streaming read errors and timeouts are typed. Empty successful content remains distinct from rate-limit, timeout, decode/parse failure and policy denial.
- Cancellation/deadline retains per-occurrence counters. Independent deadline watcher prevents cancellation-resistant late success; bounded cleanup protects caller return. Admission objects are single-use in-process; no distributed reservation or cross-process replay guarantee.
- Integration requires matching operation identity, exact occurrence set/cardinality and input references before associating results with Websites. Legacy list adapter raises typed failure instead of silently returning an empty success.
- Final host suites: **961 core passed; 177 enrichers passed**. See raw/post test outputs and command metadata. Real socket fixtures include cross-origin redirect denial, self-signed TLS rejection and stalled receive deadline.
- Real integrated redirect smoke: target dispatches **0**, graph edges **0**, `WebsiteFetchError`. Historical before smoke dispatched the other origin and emitted evidence.
- Additional real HTTP cancellation during receive: **1 request, 3 bytes**, typed cancelled, no output body; observed cancellation return about **0.00053 seconds**. See raw/post/stream-cancellation.stdout.

## Independent audit and corrections

Independent Codex subprocess audited a committed archive, found F1 (cancellation-resistant late success) and F2 (operation/input identity substitution), and independently reverified the corrected archive. Final verdict: PASS within stated trust/sandbox limits; no introduced critical/high/medium finding in reviewed scope. Exact reports, reproducer, author red/green accounts and archive provenance are preserved under review/ and raw/. Same-vendor review is not cross-vendor independence. Reviewer sandbox could not bind sockets; host verification above supplies that evidence.

## Tool limits and reconciled results

Final isolated index drift: zero. Pre/post Blast receipts are retained for roots and callers, including UNKNOWN/partial graph answers; exit zero is not proof of complete impact coverage. LSP reference discovery was unavailable because configured pyright-langserver was missing. Source tracing, graph evidence, independent review and runtime tests supplement that gap.

Doctor file-path scope returned zero symbols; it is not accepted as coverage. Retried symbol scope `execute_fetch` found one symbol and 18 flows. Doctor is structural evidence, not Python correctness proof. Hunt CLI probing is not applicable to this Python library/API surface; no Hunt PASS is claimed.

Initial pre-correction host suite had one logger periodic-flush timing failure (949 passes). Logger was not modified. Later complete source-candidate runs passed; the original failure output is retained rather than erased.

## Recovery and boundaries

Revert the two implementation commits together to return to the sealed pre-edit source; retain audit evidence. No migration, database, production policy or deployment was changed. Docs and changelog reflect new failure semantics; downstream callers must consume structured outcomes or handle WebsiteFetchError.

Trusted local callers supply policy. This is not hosted authorization. Python cannot force-kill malicious transport coroutines; hard isolation of untrusted transports requires process isolation. Bounded return and honest accounting apply to the supported cooperative HTTP transport, with cancellation-resistant fixtures testing caller-boundary safety. S04 source-artifact capture and S05 extraction expansion remain out of scope.
