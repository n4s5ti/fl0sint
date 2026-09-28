# DEF-105 acceptance — results against the repaired Pip3r runtime

All commands, argv, rc, wall time and output hashes are under `raw/` (one
`<name>.json` + `.stdout` + `.stderr` per command; `raw/summary.json` is the
machine summary; `matrix.json` is the per-root classification). Runtime provenance:
`baseline.json` (pip3r `82745f2`, rebuilt dists, gitnexus `1.6.10-rc.45`; the four
repaired packages are clean against HEAD; unrelated dirty paths listed, not used).

## 1. Fresh isolated index and traversal repair (DEF-106) — PASS

- Prior `.gitnexus` moved out of the worktree; `graphos --index` rebuilt 1156 files in
  35.3 s; content generation `sha256:51feb301…` (identical to the DEF-41 correction
  generation, as expected for unchanged sources). `--drift` total 0 before the matrix
  and again after it (`raw/drift*.stdout`). Shared checkout index untouched.
- `canonical_input_hash` upstream, depth 5, the previously interrupted repro:
  **risk HIGH, completeness complete, reasons [], warnings []; 105 symbols / 116
  edges / 90 files; edge types CALLS, HAS_METHOD, METHOD_OVERRIDES, EXTENDS.**
  Correction packet had 6 symbols / 5 edges then `query-interrupted`.
- CALLS-only control (`--relation-types CALLS`): complete, 17 symbols / 18 edges /
  8 files — the `METHOD_OVERRIDES` frontier row no longer poisons a CALLS-only query.
- Across all 49 Blast runs: zero `query-interrupted`, zero
  `unsupported-relation-types`, every `indexStatus: ready`, every `warnings: []`.
- Relation census of this index (`raw/cypher-relation-types.stdout`): 15 types incl.
  `METHOD_OVERRIDES` (596), `HANDLES_TOOL`, `HANDLES_ROUTE`, `ENTRY_POINT_OF`,
  `STEP_IN_PROCESS`, `MEMBER_OF`, `CONTAINS`, `ACCESSES`, `USES`. Under the repaired
  runtime a type outside the aligned schema is reported as
  `unsupported-relation-types` → partial/UNKNOWN with depth and frontier (DEF-106
  regression `never reports complete coverage when a frontier held unmodelled
  relation types`); none occurred here, so `complete` is a positive claim, not an
  absence of error. The deliberately-unsupported-edge proof lives in the Pip3r
  regression suite, not in this repository; it is cited, not re-derived.

## 2. Matrix: 24 roots × {upstream, downstream} — see `matrix.json`

| Graph verdict | Upstream | Downstream |
| --- | --- | --- |
| PASS (`complete`, edges traversed) | 8 | 24 |
| UNKNOWN (`partial`, `no-call-edges`) | 16 | 0 |

Upstream PASS: `canonical_input_hash`, `_digest`, `parse_request`, `parse_bundle`,
`persist_structured_result`, `reconstruct_structured_result`, `aggregate_status`,
`_connector_failure_diagnostic`.

Upstream UNKNOWN (all `no-call-edges`, no interruption): the nine model constructors,
the two `OutcomeStatus` enums, `build_bundle`, `serialize_request`,
`serialize_bundle`, `run_connector_template`, `ConnectorTestOutcome`.
Cause, verified against the index rather than assumed: gitnexus emits no CALLS edge
for Python class instantiation, for attribute calls through a module alias
(`a.build_bundle(...)`, `a.serialize_bundle(...)`), or for a Celery task invoked
through the registry; it does emit IMPORTS at file granularity. These remain graph
UNKNOWN. They are **not** promoted to PASS. Each is paired in `matrix.json` with:

- the exact source consumer inventory (`git grep -w`, production / test / example
  split; note `OutcomeStatus` exists in both modules so its word-grep count is the
  union), and
- the executed Python route below, which exercises every listed consumer file.

Downstream is complete for all 24 roots (helper CALLS, HAS_METHOD/HAS_PROPERTY,
EXTENDS), including `run_connector_template` (37 symbols / 51 edges / 9 files).

## 3. Import / alias coverage — graph PARTIAL, source inventory COMPLETE

`raw/cypher-imports-any.stdout`: the graph holds IMPORTS edges into `_execution`
from **8** files, one of which is the intra-package `acquisition.py → models.py`
edge, so **7** external importers are visible. The source inventory (`git grep -lE
'_execution(\.| import )' -- '*.py'`, `raw/import-inventory.txt`) has **15** files.
Missing from the graph (8): API `app/api/schemas/enricher_template.py`, API
`tests/test_template_egress.py`, `examples/acquisition_contract.py`, and five core
test modules (`test_structured_execution`, `test_execution_service`,
`test_graph_projection_service`, `test_template_execution_task`,
`test_template_enricher`). This is the DEF39-F3 class of gap (importer visibility)
and is recorded as a graph limitation, not resolved by DEF-106/107/108. All 15
files are exercised by the executed route.
Aliases present in the inventory: `OutcomeStatus as CanonicalStatus` (example, tests,
review script), `import acquisition as a` (tests, second caller, review script).
Lazy imports: `tasks/enricher.py` lines 169 and 207 (graph IMPORTS edge present;
CALLS to `canonical_input_hash` present in the depth-5 upstream result).

## 4. Doctor deletion advice (DEF-107) — negative controls PASS

`--verify-refactor build_bundle` (0 structural callers, executed example/test
callers): `findings: ["unwired_export"]`, `safetyScore 60`, wave 2 (moderate),
`recommendation: "verify consumers before deleting"`, warning "public interface:
importers, re-exports, aliases, decorators, and dynamic consumers are not indexed as
structural callers". Previously: `dead_code`, score 100, "safe to delete".
`serialize_bundle`: same moderate/verify outcome. `canonical_input_hash` and
`persist_structured_result` (structural callers present): wave 1, no warning.
No symbol received deletion advice.

## 5. Hunt targeting (DEF-108) and the Python audit route — honest, disclosed

- `doctor --hunt` with the Fl0sint cwd: **exit 1**, "`--hunt has no CLI probe
  target: …/fl0sint-def41-s01/bin/pip3r.mjs does not exist. --hunt probes a CLI
  executable, not the sources in --cwd`". Previously exit 0 with a spurious finding.
- `--hunt-target <installed pip3r> --scope <source dir>`: exit 1, selector rejected
  as a filesystem path.
- `--hunt-target <installed pip3r> --scope blast`: exit 0, 1 command probed, 0
  findings, `target` recorded. This is Pip3r self-probing; **it audits no Fl0sint
  Python and is classified N/A for DEF-41, not PASS.**
- `doctor --test`: `npm run test` → `skipped, script missing`. There is no Pip3r
  Python check runner; recorded as N/A.
- Executed Python route (`uv run --no-sync` with
  `UV_PROJECT_ENVIRONMENT=/home/n4s5ti/Documents/dev/fl0sint/.venv`; tests collected
  from the isolated worktree, `raw/pytest-*`, `raw/example-*`,
  `raw/verify-installed.*`). Disclosure (review finding): that shared venv holds an
  editable install, so the imported package bytes came from the main checkout's
  `src/`, not the worktree's. Impact nil: `git diff --name-only 1198f29e cbad468d`
  outside `docs/` is empty and `cmp` of `acquisition.py`/`models.py` between the
  two trees is identical; only the wheel-based second caller below imports
  worktree-built bytes.
  - core focused suite covering the acquisition contract and every integrated
    importer test module: **93 passed** (25.8 s);
  - API `tests/test_template_egress.py`: **2 passed**;
  - `examples/acquisition_contract.py`: exit 0, `success_with_output` retains one
    `unreviewed` candidate, `invalid_input` retains none;
  - wheel rebuilt from the worktree (`raw/wheel-sha256.txt`): SHA-256
    `bd3e8fbc60fa29649da7a47abec8005bccf84ae462b03b18444f7924d223d086`, byte-identical
    to the DEF-41 final wheel; DEF-41 second caller run against it in a fresh
    Pydantic-only venv (`env -i`, no neo4j/celery/sqlalchemy): `result: PASS`.
  Pre-existing psycopg/logger teardown noise in stderr is outside scope (as in
  DEF-41).

## 6. Drift comparison

Predicted: no tracked or untracked change in the analysis worktree; packet written
only in the `fl0sint` checkout on `work/def-105-gate-recovery`. Actual:
`git status --porcelain --untracked-files=all` in the worktree empty before and after
(`baseline.json`, `post-edit.json`); `--drift` total 0 after the matrix. The
`.gitnexus` rebuild is gitignored and reports no `file_unindexed` drift.

## Verdict for the DEF-41 gate

Tool-based impact evidence is no longer blocked by the three tool defects: traversal
completes with cause-carrying partials, deletion advice is guarded, Hunt fails
honestly. What remains UNKNOWN is structural, disclosed, and bounded by source
inventory plus executed consumers: (a) constructor / alias / registry consumers have
no CALLS edges, (b) 8 of 15 importers have no IMPORTS edge, (c) Hunt and Doctor
checks have no Python route. None of these are marked PASS. Whether that is
sufficient for DEF-41 closure is a review decision recorded in `review.json`; this
packet does not close DEF-41.
