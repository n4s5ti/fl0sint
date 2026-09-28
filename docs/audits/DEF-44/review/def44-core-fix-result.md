# DEF-44 core artifact correction result

Scope changed only by this workstream:

- `flowsint-core/src/flowsint_execution/artifacts.py`
- `flowsint-core/tests/acquisition/test_artifact_capture.py`

The concurrent integration worktree contains sibling-owned changes in other files. This workstream did not edit, stage, commit, or revert them.

## Implemented

- Replaced regex normalization with `HTMLParser(convert_charrefs=False)` and raw character-to-UTF-8-byte position accounting.
- Added recomputed `NormalizedSource.reproduce(body)` digest/text/mapping validation.
- Added typed `resolve_span(...) -> SpanResolveResult`, using sibling-owned `SourceProofSpanReference` after its strict-v1 wire split.
- Added current-authority enforcement for source/span reads, including complete policy identity/flags, future issuance, expiry, replacement/revocation behavior, and exact context binding.
- Enforced `retain_normalized_text=False` as a typed HOLD for normalized capture while permitting explicit raw-only capture.
- Persisted and checked policy ID, issuer, reviewer, issuance/expiry, and retention flags. Stored normalized metadata is recomputed from retained bytes before use.
- Reworked filesystem I/O around directory descriptors, `O_NOFOLLOW`, exclusive 0600 creation, bounded regular-file reads, content deduplication, and immutable occurrence records.
- Converted capture store failures to typed HOLD/REVIEW outcomes.

## Self-check evidence

- `core-no-socket-pytest-final.out`: 80 passed, 3 socket tests deselected, 2 pytest warnings. After the pass summary, the concurrent sibling-owned fetch cleanup emitted two `Task was destroyed but it is pending!` messages; this workstream does not classify that broader integration run as clean.
- `py-compile.out`: empty, exit 0.
- `sha256.txt`: final owned-file and API-contract hashes.
- `final-mutation-raw_span/pytest.out`: failures after isolated raw mapping corruption.
- `final-mutation-authorization/pytest.out`: 3 failures after bypassing current authority, including assertions showing leaked `b'secret'` bytes.
- `final2-mutation-policy/pytest.out`: failure after bypassing future issuance (`retained` instead of `policy_not_yet_valid`).
- `final2-mutation-digest/pytest.out`: failure after bypassing digest verification, including an assertion showing returned `b'tampered'` bytes.

## Honest limitations

- The three real loopback fetch tests fail in this sandbox at socket construction with `PermissionError: [Errno 1] Operation not permitted`; they require the parent host verifier.
- These checks were run by the implementing session and are self-checks, not independent verification. Per the repository rule, this result is **unverified** until a distinct parent verifier exercises the final integrated behavior.
- `/tmp/def44-review-repro.py` encodes assertions for the prior broken behavior, so it is not treated as a passing post-fix oracle. Its scenarios are replaced by exact-output and resolver assertions in the owned test file.
