# DEF44 F10-F13 boundary fix result

## Result

F10-F13 are implemented and independently runtime-verified. No commit, index, or
tracker operation was performed. Website identities remain an explicit trusted-local
boundary.

## Red evidence

The starting independent review and reproducer were:

- `/tmp/def44-independent-final-review.md`
- `/tmp/def44-independent-final-repro.py`
- `/tmp/def44-independent-final-repro.out`

They recorded cross-operation resolution as `available`, post-fetch store time outside
the elapsed claim, 79,999 spans and a 34,700,616-byte proof for the 80 KB repeated-text
case, and an empty-example validation error.

## Green commands

All commands used the explicit source-tree `PYTHONPATH`,
`AUTH_SECRET=def44-fixture-only`, `REDIS_URL=redis://127.0.0.1:6379`, and
`/home/n4s5ti/Documents/dev/fl0sint/.venv/bin/python`.

```text
python -m pytest flowsint-core/tests/acquisition -q \
  -k 'not real_loopback_cross_origin_redirect_never_hits_target and not real_untrusted_self_signed_tls_is_rejected and not real_stalled_body_obeys_total_deadline'
=> 90 passed, 3 deselected

python -m pytest flowsint-core/tests/templates/test_template_enricher.py -q
=> 16 passed

python -m pytest flowsint-enrichers/tests/enrichers/test_website_to_text.py -q \
  -k 'not legacy_public_execute_uses_loopback_occurrences_not_completion_zip and not public_scan_then_postprocess_captures_owned_occurrence_edges'
=> 21 passed, 2 deselected

python -m compileall -q flowsint-core/src/flowsint_execution \
  flowsint-core/examples/source_proof.py \
  flowsint-enrichers/src/flowsint_enrichers/website/to_text.py
=> exit 0

git diff --check
=> exit 0
```

The excluded tests require loopback socket creation, which this sandbox denies. An
unfiltered core acquisition run produced 90 passes and only the three expected socket
permission failures. The unfiltered website run likewise produced 21 passes and only
the two expected socket permission failures.

## Independent verification

The distinct verifier ran the final runtime behavior without editing the worktree and
reported `VERIFIED`:

- F10: exact operation/occurrence resolved; wrong operation and wrong occurrence each
  returned `review/authorization_mismatch` with no body.
- F11: a 200 ms injected store under a 50 ms allocation returned `timeout` in 50.98 ms;
  cancellation returned `cancelled` in 21.69 ms. Both retained 15 consumed bytes and
  returned no artifact, normalized content, or spans. An already-started atomic write
  may finish, as documented, but cannot emit late evidence or reset the allocation.
- F12: `b"x " * 40_000` normalized to one exact reproducible span; the retained proof
  was 2,310 bytes against the 8,388,608-byte EvidenceEnvelope bound.
- F13: the real example construction path returned an available, publicly parseable
  `valid_no_result` bundle with zero candidates and a completion witness.

Verifier evidence:

- `/tmp/def44-boundary-evidence/independent-verifier.py`
- `/tmp/def44-boundary-evidence/independent-verifier.out`
- `/tmp/def44-boundary-evidence/independent-verifier.commands`
- `/tmp/def44-boundary-evidence/independent-verifier.verdict`

Verifier SHA-256:

```text
2e38f26211c88078fe9faf1181ec962ae5d29569c06bf6b2be851d0b17623ba0  independent-verifier.py
39bf80c91cbf3628e25382ec8bc41fac0400a6136bb4b1811dc8e351c0bc720f  independent-verifier.out
```
