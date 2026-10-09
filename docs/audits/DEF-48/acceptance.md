# DEF-48 [S08] acceptance

- **Base:** `e40aa006`, which is DEF-47 head `76f85321` merged with DEF-90 `aee6633f`. Both are in review.
- **Head:** `d5b56a71`.
- **Packet:** `packet.json` is machine-checked by `python3 scripts/audit_packet.py validate docs/audits/DEF-48`.

## Documented invocation

```bash
python3 scripts/release_smoke.py --report smoke.json run --revision <rev>
python3 scripts/release_smoke.py verify <output-dir> --python <venv>/bin/python
AUTH_SECRET=… REDIS_URL=… python3 scripts/release_smoke.py suites   # with `uv sync --frozen --all-packages`
```

## Acceptance fixtures

| Case | Result | Evidence |
|---|---|---|
| All P1 component cases pass from the documented invocation | PASS | S1–S8 PASS on wheels built from `git archive d5b56a71`, installed into a fresh venv, with the scraper run under `PATH`/`HOME` only |
| Broken lineage or missing source proof fails the harness | PASS | <ul><li>S8: the negative controls were rejected.</li><li>22 focused tests, covering forged values, text, links, lineage, proofs, bodies, digests, JSONL and report.</li><li>Mutation check: 21 of 21 killed.</li></ul> |
| Baseline unrelated failures remain visible | PASS | `suites`: no new failures. The 11 known failures (core 1, enrichers 3, api 7) are listed as `baseline_debt`, with provenance in `docs/release/p1-baseline-failures.json`. |
| Outputs replay locally without the website | PASS | <ul><li>S7: all bundles re-verified and re-rendered after the fixture server stopped, with 0 sockets.</li><li>`verify --python`: VALID.</li></ul> |

## Package and resources

- **Wheels:** `flowsint_core-1.2.8-py3-none-any.whl` `fb48403fd56a`, `flowsint_enrichers-1.2.8-py3-none-any.whl` `184214671887`, `flowsint_types-1.2.8-py3-none-any.whl` `cb73176b1d96`
- **Source tree:** `9117f4c588b7`; uv.lock `e62c31644702`; Python: Python 3.12.13
- **Build and install:** 1.69 s. This step is the only one that uses the network (package registry or uv cache).

| Run | Wall time | Peak RSS (VmHWM) | Output size |
|---|---|---|---|
| single | 1.705 s | 81224 KiB | 28626 B |
| batch | 0.719 s | 64588 KiB | 152834 B |
| limit | 0.834 s | 63972 KiB | 2870 B |
| cancel | 0.91 s | 64032 KiB | 2852 B |
| invalid | 0.499 s | 51904 KiB | 0 B |

Example outputs are in `examples/`.

## Not claimed

- **Hunt coverage:** 0. Pre-edit hunt is BLOCKED and post-edit hunt is DEGRADED; both are disclosed.
- **Offline verification:** it proves that output is grounded in the retained bytes, not that the extractor produced it. S2 covers extraction live.
- **Hosted Python tests job:** blocked by DEF-130.
