# P1 recovery — audited source 907e94dab5292e12d81f4b34094656e22463bdcf

This audit performs no deployment, migration, live collection or database write. Release is not authorized by this packet.

1. Stop new standalone invocations and cancel any active invocation normally. Preserve its output directory, statuses and spent-resource accounting, including held/cancelled outcomes. Do not rewrite failed outcomes into successes or delete evidence to obtain a clean run.
2. Quarantine suspect bundles from downstream consumption. Retain original bundle.json, outcomes.jsonl, report.md and source-artifacts together. Verify copies with `python3 scripts/release_smoke.py verify --help`, then the documented `verify` invocation with the matching packaged interpreter supplied through `--python`. Without that interpreter, prose rendering is not verified. Hashes establish local consistency, not remote origin authenticity.
3. Restore a previously approved application environment through the operator's existing deployment rollback mechanism. No such production environment was inspected; no production rollback was executed. P1 was not deployed by this audit. Do not broadly revert merged main, rewrite shared history, or run database downgrade commands.
4. To investigate the audited version without changing an existing checkout, use `/usr/bin/git worktree add --detach <new-empty-path> 907e94dab5292e12d81f4b34094656e22463bdcf`, then `uv sync --frozen --all-packages` in that worktree. Use an absolute, new work directory for the release smoke harness. Exercise only loopback fixtures.
5. If code rollback becomes necessary, prepare a reviewed revert branch against the then-current main, map dependent DEF-41 through DEF-48 contracts first, and verify the entire standalone smoke path before deployment. Reverting the isolated DEF-24 documentation additions does not roll back scraper code.
6. Preserve the owner's LadybugDB-to-SQLite mirror policy. Standalone recovery requires neither database migration nor PostgreSQL. Do not remove or restore either database from this packet: database snapshots, mirror consistency and live restore are outside the inspected scope.

Recovery is an operator procedure, not a claim that a production rollback has been tested. Previously issued proof/retention expiry and spent resources are not reset by rollback.
