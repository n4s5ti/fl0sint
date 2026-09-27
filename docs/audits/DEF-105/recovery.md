# DEF-105 recovery / revert

This packet adds files only under `docs/audits/DEF-105/` on branch
`work/def-105-gate-recovery`. No Fl0sint source, configuration, dependency or
shared index changed. Reverting is `git revert <packet commit>` or deleting the
directory; nothing else depends on it.

Analysis-side state that is reversible and how:

- Isolated worktree `fl0sint-def41-s01` `.gitnexus` was rebuilt. The previous index is
  preserved at `/tmp/def105/gitnexus-prev` (same content generation
  `sha256:51feb301…`); restoring it is `mv`. The shared checkout `.gitnexus` was not
  opened for writing.
- Temporary artifacts (`/tmp/def105/{raw,wheel,venv,example-out}`) are scratch; the
  packet `raw/` holds the retained copies.

Pip3r runtime: the repairs are commits in `/home/n4s5ti/Documents/dev/pip3r`
(`82745f2`). Falling back to the defective runtime (`f1ad5f51…`) would reintroduce
`query-interrupted`, `dead_code` deletion advice and the silent Hunt target; do not do
so for any DEF-41 gate decision. If a future Pip3r build changes relation semantics,
rerun `raw/run_matrix.py <outdir>` after a fresh isolated `graphos --index` and
compare `summary.json`.

Preserved unconditionally: the DEF-41 pre-edit packet, its correction packet
(`cbad468d`), emitted example bundles, input lineage and operation IDs. Nothing in
this issue re-issues IDs or rewrites historical evidence.
