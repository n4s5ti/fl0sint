# P1 release checklist: standalone source-preserving scraper

Run this checklist from a committed revision. Each step must produce the stated result.
Record any other result in the release audit packet instead of overriding it.

## Gates that apply to P1

| # | Gate | Command | Required result |
|---|---|---|---|
| 1 | Packaged release smoke | `python3 scripts/release_smoke.py --report <dir>/smoke.json run --revision <rev>` | Exit 0 and cases S1–S8 PASS (see below). |
| 2 | Fixture corpus integrity | `python3 scripts/acquisition_fixtures.py validate` | Exit 0. |
| 3 | Package suites against the baseline | `AUTH_SECRET=… REDIS_URL=redis://127.0.0.1:6379/0 python3 scripts/release_smoke.py --report <dir>/suites.json suites`, run with the repository's synced environment (`uv sync --frozen --all-packages`) | No `new_failures`. Every `baseline_debt` entry is listed in the packet. Each `baseline_resolved` entry is removed from `docs/release/p1-baseline-failures.json` in the same change. |
| 4 | Offline replay of shipped examples | `python3 scripts/release_smoke.py verify <output-dir> --python <venv>/bin/python` | `VALID`, with `report_rerendered: true`. No network is used, and the original website is never contacted. Without `--python`, `report.md` prose is not verified and the tool says so. |
| 5 | Audit packet | `python3 scripts/audit_packet.py validate docs/audits/<ISSUE>` | `VALID`. Its `non_pass_results` are reviewed by a person. |
| 6 | Hosted CI | **Tests / Audit packet validity** on the release PR | Pass. **Tests / Python tests** is currently blocked by DEF-130 (dependency resolution). Until DEF-130 lands, record that as a known failure and rely on gate 3. |

`release_smoke.py run` builds wheels from `git archive <rev>`, not from the working tree. It
installs them, together with the `uv.lock` export, into a fresh virtual environment.
**Installation is the only network step**: it reads the package registry or the uv cache.
Every scraper invocation runs with `PATH`/`HOME` only. An in-process audit hook **records**
every Python import and `socket.connect`; it does not block them. S3 fails on any record of
a service module or a non-loopback connection, and it also fails if the hook recorded nothing.
The hook cannot see non-Python child processes. The standalone scraper starts none.

| Case | What it proves |
|---|---|
| S1 | A single page runs through the installed `*-standalone-scraper` entrypoint with exit 0 and a verified bundle. |
| S2 | In a mixed batch, the exit code is 2 and the bundle is checked for: <ul><li>byte spans against the retained bodies</li><li>observation and proof lineage (`occurrence_id`, `operation_id`, URLs, proof `input_ref`)</li><li>retained bytes equal to the fixture sha256</li><li>a redirect resolving to its final URL</li><li>an empty page that is a success with proof, versus a 503 that is an explicit error</li><li>duplicate URLs kept as distinct snapshots</li><li>JSONL and report agreeing with the bundle</li></ul> |
| S3 | No graph, database, queue, model or browser module is imported, and no connection goes anywhere except the loopback fixture. |
| S4 | `--max-response-bytes` is enforced as `body_too_large`. |
| S5 | SIGINT during a fetch records `hold`/`cancelled` and retains the consumed request budget. |
| S6 | An invalid scope exits 64 before admission and writes no output. |
| S7 | Every bundle replays offline after the fixture server has stopped, and the report re-renders exactly. |
| S8 | Negative controls: broken lineage, a missing source proof and a missing retained body are each rejected. |

The report also records:

- the wheel sha256 values, source tree, `uv.lock` and lock-export digests, and interpreter;
- wall and CPU time for each run, peak RSS (`VmHWM`), and output bytes.

## Deferred gates (not P1)

These gates must not be invoked, stubbed or marked passed for the P1 release:

| Gate | Milestone | Why it is deferred |
|---|---|---|
| Browser render/interaction and session budgets | P3 (DEF-27) | P1 uses HTTP fetch only. The `dynamic` and `click` fixtures exist but are outside the P1 scope. |
| Graph projection, acceptance publication and retraction | G1 (DEF-29), P4 (DEF-28) | P1 output stays `unreviewed` and never writes to a graph. S3 checks that no graph module is imported. |
| Model/Jev decisions, calibration, bandit, learning, training | P5–P8 (DEF-34 … DEF-37) | No model is called. S3 checks this. |
| Crawler frontier and resume | P2A (DEF-25) | Only single-hop fetch is supported. |
| Hosted execution, tenancy and receipts | H1 (DEF-32) | P1 is local. |

## Known limitations

- `outcome.input_ref` hashes the `Website` input object, while the proof and observations use
  `input_ref` = hash of the URL. Join outcomes, proofs and observations on `occurrence_id`
  (`input-<position>`); the harness enforces this.
- Bundles from `failure` and `hold` outcomes carry no source proof by design. The harness
  requires their diagnostic code to be present and their content to be empty.
- Offline `verify` proves that every text, link and candidate in a bundle is grounded in the
  retained bytes and correctly attributed. It cannot prove the shipped extractor produced
  them. A forger could add an observation that is fully grounded but was never extracted.
  Detecting that requires re-running the extractor, which S2 does live against the fixtures.
