# Tooling remediation after review handoff

## Repaired

- Pyright launchers used a deleted interpreter: `/home/n4s5ti/.local/share/mise/installs/python/3.14.2/bin/python3.14`. Backed up both launchers under `~/.local/state/tool-repairs/def45/`, then installed Pyright 1.1.414 with `UV_TOOL_BIN_DIR=~/.local/bin uv tool install --python 3.12 --force pyright`. The launchers now belong to a uv-managed tool environment. Version execution and direct JSON-RPC initialize/didOpen/references passed; the known `resolve_observation_span` call at line335 was returned. This is a real reference request, not proof of complete project-wide reference coverage.
- Installed the frontend's existing pnpm lockfile using `pnpm install --frozen-lockfile --ignore-scripts`. TypeScript 5.9.3 runs. No tracked dependency or production source file changed.
- Corrected verification targeting: invoke Doctor typecheck at the app directory, not the root (current root invocation skips the missing root script). Actual app typecheck now reports 115 diagnostics across 45 files, not `tsc not found`. This matches the existing baseline defect OBS-1805; its record was updated. Missing Tiptap imports and incompatible command types remain application/dependency defects, not repaired tool failures.
- Replaced the inappropriate library Hunt invocation with actual scoped `doctor --verify-refactor extract_observations --json`. It resolved the correct Python symbol, two structural callers, eight flows, and a high-fanout advisory. This is structural evidence, not runtime correctness proof; the previous host smoke and tests remain authoritative.

## Still unresolved

OMP 18.3.1's mounted LSP tool continues to return immediate `posix_spawn ENOENT` for the repaired, directly executable launcher. Reload and stop/recreate of the session-owned mux did not recover it. Direct Python and Bun launches work, including Bun with an empty environment; therefore the delegated report's assertion that environment replacement caused this failure was rejected. No source-grounded harness root cause has been established. `strace` attachment was denied by the host's ptrace policy; that security setting was not changed. The existing OBS-114 record and tool QA received evidence. A new harness session may help, but that is unverified; the running coding session was not terminated or replaced.

Hunt's help/catalog/JSON-contract probe discovers zero command surfaces on this flat argparse example. Zero findings is NOT acceptance. Its generic ability to spawn executable targets does not imply Python-library semantic auditing. The delegate's blanket claim that Hunt supports only Node is too broad and is not adopted. Do not probe the unrelated Pip3r CLI and count that as extraction coverage.

## Receipts

`raw/tooling/` contains direct Pyright protocol proof, actual app/root Doctor reports, and the scoped refactor result. The original acceptance packet remains a historical record; this addendum supersedes only the missing-tool diagnosis. S05 remains in review. No frontend errors were suppressed, no harness binary was patched, and no push/merge/deployment occurred.
