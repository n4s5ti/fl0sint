# DEF-42 recovery: functional PASS, historical-evidence gate BLOCKED

User acceptance of DEF-105 resolved the external tool prerequisite. It did not waive
DEF-42's separately recorded missing pre-edit receipt. This recovery changes audit
records only; implementation remains commit `8de569a731fca084d05d89a6316daafb5a34caa3`.

## Current verification

- Explicit worktree `PYTHONPATH` and import-path assertion prevent the shared venv
  editable install from substituting main-checkout sources.
- Full enricher suite: baseline `cbad468d` **156 passed**; head `8de569a7`
  **165 passed**. Focused occurrence and acquisition fixtures: **33 passed**.
- Separate real-loopback HTTP smoke through public `execute`: slow source retains
  slow text; fast source retains fast text; capture sink receives both correct
  `HAS_INNER_TEXT` edges and one flush. Transport is real; graph storage is an
  injected recorder, not a live Neo4j deployment.
- Two in-memory mutations killed: assign every graph relationship to the first
  source; discard failed structured outcomes. Each caused its selected behavioral
  test to fail. Production source SHA-256 stayed unchanged. These are bounded
  mutation controls, not exhaustive mutation coverage.
- Independent Codex subprocess attacked the committed archive through public
  execute, scan/postprocess and execute_structured, including duplicate inputs,
  middle failure, retry, one-to-many extraction, cancellation and JSON survival.
  Functional PASS. Its sandbox prohibited socket creation: its HTTP tests failed
  at socket construction, not product assertions. Host real-HTTP results above are
  coordinator-run and are not attributed to that independent run.

## Impact comparison (retrospective, never pre-edit)

Both isolated baseline and head indexes were refreshed using installed repaired
Pip3r. Baseline/head and head-after-run drift totals are all zero. No shared index
was refreshed. Exact commands, exit status, timings and outputs are under `raw/`;
`impact-summary.json` contains bounded classifications.

Head class traversal, scan, postprocess, execute and execute_structured return
complete graph traversal in both directions. New WebsiteTextOccurrence upstream
remains partial/no-call-edges (constructor visibility), not PASS. Registry/dynamic
call coverage is not established by a complete indexed traversal.

Baseline execute/execute_structured are inherited, not defined in to_text.py.
Initial qualified-symbol-not-found receipts are retained as such, followed by
successful queries against core/enricher_base.py. This is corrected target selection,
not a product failure or a clean result for the original query.

Doctor's refactor advisory and Hunt's missing-CLI-target failure are preserved;
neither is a Python behavioral/security audit. LSP references could not execute:
pyright-langserver ENOENT. Source/caller tracing and independent public-path runtime
proof supplement those limitations, without relabeling them complete graph proof.

## Independent decision and remaining blocker

The independent report separates functional PASS from **DO NOT SHIP** under the
historical evidence contract. Reconstructing an old revision today establishes a
current baseline comparison, not that impact analysis was captured before editing.
The original omission cannot be repaired or backdated. Explicit user acceptance of
this retrospective substitute is required to move DEF-42 forward.

The independent archive initially lacked Git metadata; a follow-up binds all regular
archive files to `git archive 8de569a7`. Both original report and follow-up are
retained so that the initial limitation is not erased.

## Scope, provenance and recovery

Recovery scope: docs/audits/DEF-42/manifest.json and recovery/** only. No product,
configuration, dependency, or deployment change. `provenance.json` pins revisions,
source hashes, configuration equality, installed Pip3r and local state. This is an
honest post-implementation recovery scope, not a retrospective pre-edit plan.

Keep work/def-42-occurrences isolated until the evidence decision. Revert only this
documentation recovery commit to undo packet changes. If implementation must be
reverted later, preserve subsequent work and stored occurrence/evidence records;
do not replay operation identities or erase consumed resources. Temporary isolated
baseline and reviewer snapshots are retained for reproducibility; no destructive
cleanup of pre-existing worktrees was performed.
