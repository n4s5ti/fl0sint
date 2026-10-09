# DEF-48 recovery

DEF-48 adds development tooling only. It changes no shipped package source, configuration or dependency.

| Path | Role |
|---|---|
| `scripts/release_smoke.py` | Harness (stdlib). Nothing imports it except its own test. |
| `<pkg>-enrichers/tests/enrichers/test_release_smoke.py` | Its tests. |
| `docs/release/p1-release-checklist.md`, `docs/release/p1-baseline-failures.json` | Release gate list and failure baseline. |
| `CHANGELOG.md` | Entry. |
| `docs/audits/DEF-48/` | This packet. |

**Revert:** `git revert <packet commits> <source commit>`, newest first. This removes only the paths above.

**Scratch data:** harness runs write only to `--work-dir` (default: a new temp dir). That directory holds the wheels, the venv, the outputs and the audit logs, and it can be deleted freely. Nothing is written to the repository, the graph or any service.

**Baseline file:** removing `p1-baseline-failures.json` makes every preexisting failure report as `new_failures`. That fails closed and never hides a failure.

**Branch base:** DEF-48 is stacked on DEF-47 (`76f85321`, in review) merged with DEF-90 (`aee6633f`, in review). If DEF-47 changes before acceptance, rebase this branch and re-run `release_smoke.py run`.
