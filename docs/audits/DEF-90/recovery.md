# DEF-90 recovery and feature-disable

The change consists only of development tooling, so no runtime, data, graph, budget or
input lineage is involved.

| Path | Role |
|---|---|
| `scripts/audit_packet.py` | The stdlib validator. No package imports it. |
| `<pkg>-enrichers/tests/enrichers/test_audit_packet.py` | Its tests, which run in `make test`. |
| `.github/workflows/tests.yml` | The new `audit-packets` job. The existing `test` job is unchanged. |
| `docs/audit-packet.md`, `CHANGELOG.md` | Documentation. |
| `docs/audits/DEF-90/` | This packet. |

## Disable the CI gate only

Delete the `audit-packets:` job block, which is everything after the comment
`# Development evidence gate (DEF-90)`, from `.github/workflows/tests.yml`. The `test` job and
its triggers are untouched by DEF-90, so removing that block restores the pre-change
workflow exactly. Check the result with:
`git diff a59bdadf -- .github/workflows/tests.yml` (expect an empty diff).

## Full revert

```bash
git revert <packet commit> <source commit>   # newest first; preserves history
```

The revert removes only DEF-90 paths. Legacy packets (DEF-23 … DEF-47, DEF-105) and every
other evidence directory are untouched, because the validator never writes outside the
`--report` path it is given.

## Unrelated work preserved

The worktree `/home/n4s5ti/Documents/dev/fl0sint-def90-packet` is separate from the shared
checkout. The untracked user directories in the shared checkout (`fl0sint-def46-capture-sink/`,
`jev-ultrafast/`) were not touched. The isolated `.gitnexus/` index is gitignored and
belongs only to this worktree.
