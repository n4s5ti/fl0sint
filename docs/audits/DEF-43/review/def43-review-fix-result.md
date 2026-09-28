# DEF-43 independent review fix result

## Verdict

PASS for the requested object-scoped fixes. An independent read-only verifier exercised the final behavior after all edits. Real loopback socket paths remain unverified in this sandbox because socket creation is denied before product code runs.

Baseline commit: `353a8389788ad12a85226bc4666e922e424cb30d`

## Changes

- F1: `execute_fetch` now uses an independent `asyncio.wait` deadline watcher. Tasks pending at the boundary are typed as timeout even if they suppress cancellation; tasks completed before the boundary retain their real outcomes.
- Cancellation and client cleanup have a secondary finite budget. Remaining cooperative transport cleanup is supervised in the background with exception consumption. Documentation states that Python cannot hard-kill a malicious coroutine and limits the guarantee to bounded caller return with trusted cooperative transports.
- Actual request/byte accounting is preserved in returned timeout/cancellation snapshots. No late completion is accepted as timeout success.
- Each admitted runtime object has private, concurrency-safe, process-local single-use claim state excluded from serialization and seals. The same object and its in-process `model_copy` cannot reset allocation through sequential or concurrent reuse. No global registry or distributed replay claim was added.
- F2: WebsiteToText validates operation ID, exact outcome cardinality, unique exact occurrence IDs, and each occurrence's admitted `input_ref` before associating evidence. Missing, extra, unknown, duplicate, wrong-operation, and wrong-input results fail as `invalid_fetch_result`; there is no positional fallback.
- Existing developer documentation and changelog were updated.

## Red/green evidence

Before production edits, the added regressions failed as expected:

- cancellation-resistant deadline returned after about 0.282 seconds as `success`;
- sequential replay did not raise and concurrent execution produced two results;
- missing/substituted operation and input bindings were accepted or partially associated.

After the final edits:

- deterministic core suite: `17 passed, 3 deselected`;
- deterministic WebsiteToText suite: `19 passed, 2 deselected`;
- `git diff --check`: passed;
- changed Python modules compiled successfully.

The full targeted files were also run without deselection:

- core: `17 passed, 3 failed`;
- enricher: `19 passed, 2 failed`.

All five failures are environmental `PermissionError: [Errno 1] Operation not permitted` at `socket.socket()` for loopback redirect/TLS/stalled-body and loopback integration cases. They occur before the product runtime path.

## Independent re-review

The independent verifier returned PASS after the final edits. Its direct parent-cancellation probe used a stream that consumed cancellation for 250 ms and observed:

- caller return in 0.051 seconds;
- typed `cancelled` outcome;
- one request accounted;
- background cleanup eventually completed;
- zero remaining non-current tasks after scheduler drain.

It also confirmed the cancellation-resistant deadline result, one-winner sequential/concurrent claim behavior, private claim exclusion from `model_dump`, and all F2 identity/cardinality variants.

## Final file hashes

- `CHANGELOG.md`: `51cfa3c84196c5ed691a09caeb6d69a72ac908e4f7e87f42fad0f0520a9a7b3d`
- `docs/developers/acquisition-contract.md`: `d7b421c46d3b4b9d73a30b0ba28dd076abaae241bc32eff0d3075bef363a5552`
- `flowsint-core/src/flowsint_execution/fetch.py`: `043505e3b658b28c06c75cbabf2fe0f68e37cbbbccdcd488f5b82aefe5ffba28`
- `flowsint-core/tests/acquisition/test_fetch.py`: `644aedb672ca2184e2e8726c5ac94c0bb22941b174d4787bbdf6352f2e2d946d`
- `flowsint-enrichers/src/flowsint_enrichers/website/to_text.py`: `ace3c84ad3b00703a3ca4383aef4acaf4d752683ee179cf56f99c79b324fd21d`
- `flowsint-enrichers/tests/enrichers/test_website_to_text.py`: `304e6ad77ff4c8e300f60c9f02bd6623cbd63891ae176aff498bceba3cd3cc24`

No commit or tracker action was performed. Source edits are confined to the requested worktree; this receipt is the explicitly requested `/tmp` output.
