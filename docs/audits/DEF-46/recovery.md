# DEF-46 recovery

DEF-46 creates no Neo4j data: the capture repository is process-local and only returns unreviewed candidates. Recovery therefore has no graph-data cleanup step.

To remove this delivery after its repair commit is identified, use `git revert <DEF-46-repair-commit>` on `work/def-46-capture-sink`. Do not use a destructive reset: this checkout contains unrelated untracked directories that this delivery intentionally preserves.

The revert restores the previous implementation; it does **not** make current acquisition safe to use as a live fallback. The capture-only fence is intentional. Re-enabling live graph publication would require separate, reviewed scope and credentials, not a recovery action.
