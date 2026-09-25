# DEF-39 [B02] — Executable Blast / Hunt / Doctor audit procedure

Pinned to what was actually executed on 2026-09-25 against fl0sint @ `59e2d670` with
**pip3r 1.2.6** (`/home/n4s5ti/.local/bin/pip3r` → `~/Documents/dev/pip3r/packages/runtime/dist/bin/pip3r.js`, node v22.23.2).
`f0rg3 0.4.3` is installed but is a different CLI lineage (commander-style: `f0rg3 graph --blast <sym>`, `f0rg3 doctor --hunt`); it was
**not** executed against the shared index. Every flag below is copied from `--help` output in `help/`; none are invented.

## 0. Invariants (read first)

| Rule | Why |
|---|---|
| Always `export PIP3R_DISCLOSURE=off` and `--json` | raw JSON; several routes do real work only under `--json` |
| Always `--cwd <repo>` explicitly | blast/doctor resolve the index from cwd |
| **Always `--no-auto-index` on the shared repo** | `blast`/`Blast-native` default `--auto-index=true` and will rewrite `.gitnexus/` in place (observed: 42.65 s, `recoveryOutcome: published`, index rebuilt over the dirty tree). This is the single most important flag. |
| Never `pip3r graphos --index|--clean|--force` against the shared repo; never `f0rg3 graph --clean/--force` | shared coordination state |
| Never bare `pip3r doctor` or `--deep` | full gate suite (5+ min), empty JSON if interrupted |
| Run graph/blast/doctor **serially**, wrap in `timeout` | LadybugDB contention manufactures false timeouts |
| Redirect stdout to a file; never pipe to `head` | EPIPE crash |
| Exit code 0 ≠ success | missing symbol, missing index and unusable `--lbug-path` all exit 0; read `risk`/`completeness`/`reason` |
| Missing / degraded / interrupted output → **UNKNOWN or BLOCKED**, never PASS | |
| A lexical (`rg`) match report is **not** a graph impact report | it is a supplement, labelled as such |

## 1. Pre-flight (freshness + no-change baseline)

```bash
R=/home/n4s5ti/Documents/dev/fl0sint; S=<scratch>; export PIP3R_DISCLOSURE=off
git -C $R rev-parse HEAD                     > $S/head-pre.txt
git -C $R status --porcelain                 > $S/git-status-pre.txt
( cd $R && for f in .gitnexus/*; do [ -f "$f" ] && sha256sum "$f"; done ) > $S/gitnexus-sha256-pre.txt
python3 -c "import json;d=json.load(open('$R/.gitnexus/gitnexus.json'));print(d['indexedAt'],d['lastCommit'],d['stats'])"
ls $R/.gitnexus/pip3r-generation.dirty.json 2>/dev/null && echo "DIRTY MARKER PRESENT — expect --hunt to hang; do NOT reindex"
timeout 120 pip3r graphos --status --cwd $R --no-auto-index --json > $S/graphos-status.json
```
Record: `indexedAt`, `lastCommit` vs HEAD, dirty-entry count, `indexStatus` (`ready|stale|missing`).
If `lastCommit != HEAD` the index is stale relative to HEAD: results are **UNKNOWN-STALE** unless an isolated index is built (§6).

## 2. Symbol resolution (find a real target before blasting)

```bash
timeout 120 pip3r graphos --search <term>      --cwd $R --no-auto-index --json --limit 20 > $S/search.json
timeout 120 pip3r graphos --symbols <path-prefix> --cwd $R --no-auto-index --json          > $S/symbols.json   # --symbols takes a PATH prefix, not a name
timeout 120 pip3r graphos --refs <symbol>      --cwd $R --no-auto-index --json             > $S/refs.json      # callers + callees
timeout 120 pip3r graphos --cypher "MATCH (n) WHERE n.name = '<symbol>' RETURN n.id, n.filePath LIMIT 60" --cwd $R --no-auto-index --json > $S/defs.json
```
Known defect: `--refs` on a class with `METHOD_OVERRIDES` edges exits 1 (zod `invalid_enum_value`); use the `--cypher` form instead
(`MATCH (a)-[r]->(b) WHERE a.name='X' OR b.name='X' RETURN a.id, r.type, b.id` — note `type(r)` is not supported, use `r.type`).

## 3. Blast — BEFORE the edit

```bash
SYM=build_params_model               # or file:symbol to disambiguate, e.g. flowsint-core/src/flowsint_core/core/enricher_base.py:execute
timeout 120 pip3r blast --cwd $R --no-auto-index --json                       $SYM > $S/blast-before-up.json
timeout 120 pip3r blast --cwd $R --no-auto-index --json --direction downstream $SYM > $S/blast-before-down.json
# optional narrowing: --relation-types CALLS   --max-depth N (default 3)   --include-tests   --min-confidence 0.7 (default)   --risk-only
# --direction both is REJECTED (exit 252). Run upstream and downstream separately.
```
`pip3r Blast-native` accepts the identical flag set and produced byte-identical JSON in every paired run (`diff` exit 0); either is acceptable, pin which one you used.

### 3.1 Observed JSON top-level schema (pip3r 1.2.6)

Always present: `symbol, repo, lbugPath, rootPath, direction, risk, target, symbols, edges, files, affected_modules, affected_processes, byDepth, summary, warnings, completeness, completenessReasons, indexStatus`.
Conditional: `source_generation` (only when auto-index ran in-process), `reason` + `hint` (unresolved target / missing or unusable index).
**No `contentType` / `schema` / `version` key was observed.** The contract id `application/vnd.pip3r.blast+json;v=1` exists only as `outputContracts` metadata in `packages/blast/src/plugin.ts`; treat contract version as *declared, not self-describing*.

| Field | Values observed |
|---|---|
| `risk` | `LOW`, `CRITICAL`, `UNKNOWN` (enum also has MEDIUM, HIGH) |
| `completeness` | `complete`, `partial` |
| `completenessReasons` | `query-interrupted`, `ambiguous-symbol`, `target-symbol-unresolved`, `index-unavailable`, `no-call-edges` (enum also: `stale-index`, `lock-timeout`, `match-truncated`, `qualified-symbol-not-found`, `import-fallback`) |
| `indexStatus` | `ready`, `missing` (also `stale` per source) |
| `byDepth.d=N.label` | `WILL BREAK` (d=1), `LIKELY AFFECTED` (d=2), … |
| `edges[]` | `{from, to, type, confidence}`; CALLS edges seen at 0.85, HAS_METHOD at 1 |
| `summary` | `rootSymbols, totalSymbols, totalEdges, filesMatched, truncated, direct, processes, modules` |

### 3.2 Interpretation rules
- `risk == UNKNOWN` or `completeness == partial` → the report is **not** a clean impact verdict. Record the `completenessReasons` verbatim.
- `query-interrupted` was returned on **every** upstream run against the fl0sint index (including `--relation-types CALLS`), while the same build reports `complete` on a 2-file disposable repo. Cause in source: `queryBlastRadius` sets `partial=true` when an edge query throws (`packages/graphos/src/index.ts:434`). Until root-caused, treat fl0sint upstream blasts as **partial evidence**, and corroborate with `graphos --refs` / cypher.
- `target-symbol-unresolved` (missing symbol) and `index-unavailable` (no/unusable index) both still **exit 0** — gate on the JSON, not the exit code.
- `rootSymbols > 1` → ambiguous; re-run with `file:symbol`.
- `no-call-edges` on a class that is instantiated through a registry (e.g. `ENRICHER_REGISTRY.get_enricher`) means the graph cannot see dynamic dispatch → go to §7.
- A `LOW / complete / 0 edges` on a symbol you know is used dynamically (observed for `get_enricher --direction downstream`) is a **false-comfort** result; downgrade to UNKNOWN with the §7 supplement attached.

## 4. Blast — AFTER the edit
Re-run exactly the §3 commands into `$S/blast-after-*.json`, then:
```bash
diff <(python3 raw/summarize_blast.py $S/blast-before-up.json) <(python3 raw/summarize_blast.py $S/blast-after-up.json)
```
`raw/summarize_blast.py` strips `content` bodies so the diff is readable. Any change in `byDepth`, `edges_total`, `risk`, or `completenessReasons` must be explained in the review note.
Note: without `--no-auto-index` an after-edit blast on a repo with uncommitted edits *will* rebuild the index; on the shared repo that is forbidden — build the isolated index (§6) instead.

## 5. Doctor / Hunt / Drift

```bash
timeout 300 pip3r doctor  --cwd $R --verify-refactor $SYM --json > $S/doctor-verify.json   # ~2 s
timeout 300 pip3r doctor  --cwd $R --hunt --json                 > $S/doctor-hunt.json     # ~2 s here; if it hangs with 0 bytes → DEGRADED, do not reindex
timeout 300 pip3r graphos --drift --cwd $R --no-auto-index --json > $S/graphos-drift.json  # ~1 s
```
Observed shapes:
- `doctor` JSON: `cwd, packageName, packageManager, requestedChecks[], results[], graph{status,repo,lbugPath,isOpen,discoveredTables,fileCount,indexStatus,warnings}` plus `refactor{concept,symbols[{name,filePath,safetyScore,wave,findings,recommendation}],waves,warnings,summary}` or `hunt{findings[],counts,durationMs,coverage{commandsDiscovered,commandsProbed,jsonContractsExercised,delegatedSurfacesCaptured,spawns,discoveryCap,commands}}`.
- `--verify-refactor` gave `safetyScore 100 / safe to modify` for a symbol whose blast was `UNKNOWN / query-interrupted`. **Doctor does not propagate blast completeness**; do not let a green refactor score override a partial blast.
- `--hunt` probes the *pip3r CLI itself* (contract hunting), not fl0sint code. `coverage.commandsDiscovered == 0` ⇒ DEGRADED. Its single finding ("Root --help must exit 0", evidence `1`) was contradicted by a direct `pip3r --help` (exit 0).
- `--drift` JSON: `mode:"drift", result{repo,lbugPath,changed_symbols[],summary{total,file_modified,file_removed,file_unindexed,symbol_stale}}, indexStatus, warnings`. `total == 0` only means "index matches the working tree at query time"; it says nothing about HEAD.

Exit codes observed: `0` for every completed run including error bodies; `252` for CLI argument errors (`--direction both`, `graph <subcommand>` forms); `1` for `graphos --refs` schema-validation failure. `124` would be `timeout` expiry — **not observed** in this run. Cancellation/SIGINT behaviour: **UNKNOWN** (not exercised; source shows `signal?.throwIfAborted()` checkpoints in blast).

## 6. Isolated index (non-destructive path for stale/dirty shared repos)

Demonstrated on a 2-file disposable git repo (`raw/disposable-*.json`):
```bash
D=<disposable clone or worktree>            # NEVER the shared checkout
timeout 600 pip3r graphos --index --cwd $D --json > $S/index.json     # 19.5 s for 2 files (indexLatencyMs 18465); creates $D/.gitnexus/{lbug,gitnexus.json,meta.json,pip3r-generation.json,pip3r-content-manifest.json,pip3r-workspace-edges.json,index.lock.reclaim*}
timeout 120 pip3r blast --cwd $D --no-auto-index --json helper > $S/blast.json   # LOW / complete / caller→helper, top→caller
```
Freshness is **not** tied to HEAD by the producer: `gitnexus.json.lastCommit` is written as `""` by `pip3r-graphos` (the 2026-08-09 shared index had it populated by a different producer). Freshness is tied to the content-hash `generation` in `pip3r-generation.json` plus `graphos --drift`:
- after a new commit, `--drift` reported `file_modified 1, symbol_stale 2`, but `blast --no-auto-index` still returned `LOW / complete / indexStatus ready` with the new function absent — **a stale index is served without a stale flag**. Always run `--drift` immediately before a `--no-auto-index` blast and require `summary.total == 0`.
- `blast` without `--no-auto-index` rebuilt the isolated index in 11.8 s and then showed the new symbol at d=3, with `source_generation` populated.

For fl0sint: `git worktree add <path> <sha>` (or a clone) + `pip3r graphos --index --cwd <path>` gives a HEAD-pinned index without touching `.gitnexus/` in the shared checkout. Expect minutes, not seconds, for 900+ files (not measured here).

## 7. Supplemental lexical / import / config tracing (for graph gaps)

Use when blast returns `no-call-edges`, `target-symbol-unresolved`, `index-unavailable`, or a suspicious `LOW/complete/0 edges` on a dynamically-used symbol. Label the output **"lexical supplement — not a graph impact report"**.
```bash
rg -n --type py '\b<Symbol>\b' $R --glob '!.worktrees/**' --glob '!node_modules/**'          # direct references
rg -n 'from .* import .*\b<Symbol>\b|import .*\b<Symbol>\b' $R --type py                     # import sites
rg -n '@flowsint_enricher|ENRICHER_REGISTRY\.(get_enricher|register)|importlib\.import_module|getattr\(' $R --type py   # dynamic registration / dispatch
rg -n '<symbol_or_name>' $R --glob '*.{yaml,yml,toml,json,ini,cypher,j2,jinja*,sql}'          # config / templates / Cypher
rg -n 'entry-points|entry_points|\[project\.scripts\]' $R --glob 'pyproject.toml'             # packaging-time registration
```
Limitations: lexical hits include comments/strings/tests, miss aliased imports and `getattr`/string-keyed lookups (e.g. `ENRICHER_MAP["module"]` in `scripts/enrich_sheet.py`), cannot rank by depth, and give no confidence score. Record hit counts and files, then state the verdict as UNKNOWN-with-supplement, never PASS.

## 8. Post-flight (no-source-change proof)

```bash
git -C $R rev-parse HEAD > $S/head-post.txt;  diff $S/head-pre.txt $S/head-post.txt
git -C $R status --porcelain > $S/git-status-post.txt;  diff $S/git-status-pre.txt $S/git-status-post.txt
( cd $R && for f in .gitnexus/*; do [ -f "$f" ] && sha256sum "$f"; done ) > $S/gitnexus-sha256-post.txt
diff $S/gitnexus-sha256-pre.txt $S/gitnexus-sha256-post.txt
```
Any diff in the `.gitnexus/` hashes = shared coordination state changed → report it verbatim (as happened in this run, see `no-source-change/`).

## 9. Artifact layout
`docs/audits/<ISSUE>/` → `tooling-manifest.json`, `case-results.json`, `audit-procedure.md`, `acceptance.md`, `linear-summary.md`, `help/` (verbatim `--help`), `raw/` (every JSON + stderr + `runlog.txt` with exit/wall/cmd), `no-source-change/` (pre/post hashes and `git status`).

## 10. UNKNOWN / BLOCKED rules
- No output, 0-byte JSON, `timeout` exit 124, or a hang → **BLOCKED** (record the command and the timeout); never reindex the shared repo to recover.
- `risk UNKNOWN`, `completeness partial`, `indexStatus != ready`, `lastCommit != HEAD`, drift `total > 0`, `coverage.commandsDiscovered == 0` → **UNKNOWN** for that fixture.
- A result that could not be reproduced twice serially → UNKNOWN.
- Anything asserted only by a tool's own self-description (help text, contract ids, safetyScore) without a matching observed payload → UNKNOWN.
