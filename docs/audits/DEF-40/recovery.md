# DEF-40 [B03] — Revert / disable / recovery

Scope of change: additive only. No tracked file is modified; no production import, configuration, schema, route, migration, graph, or service references the new files.

| Path | Kind |
|---|---|
| `scripts/acquisition_fixtures.py` | standalone CLI (`validate`, `smoke`, `serve`), stdlib only |
| `<pkg>-enrichers/tests/fixtures/acquisition/` | synthetic corpus: `manifest.json` plus 15 HTML artifacts |
| `<pkg>-enrichers/tests/enrichers/test_acquisition_fixtures.py` | package-local pytest module |
| `docs/audits/DEF-40/` | evidence packet |

## Disable without deleting evidence

Pytest discovery is the only automatic consumer. To take the tests out of `make test` while keeping the corpus, run `git rm` on `test_acquisition_fixtures.py` only. The CLI runs only when someone invokes it by hand.

## Full revert

`git revert <DEF-40 commit>` removes exactly the paths above. Unrelated work is not touched: the commit adds files and modifies none, so a revert cannot conflict with or undo any other change.

## Preserved across revert

- Input lineage, immutable evidence, and consumed budgets: none are created by this change. The corpus is authored data. Nothing is acquired and no execution records are written.
- The pre-edit evidence (`baseline.json`, `blast-before-*.json`, `drift-before.json`, `hunt-before.json`) stays in git history at the DEF-40 commit.
- Isolated graph indexes live in each worktree's `.gitnexus/`, which is gitignored and never shared. Delete the worktree to drop them. The shared checkout's `.gitnexus/` was never written.

## Label changes

Labels are authored truth. Any change to `manifest.json` labels or hashes must go through a reviewed commit. The loader never rewrites labels or hashes, and `test_trap_fixtures_keep_their_negative_verdicts` pins the negative verdicts on the trap fixtures.
