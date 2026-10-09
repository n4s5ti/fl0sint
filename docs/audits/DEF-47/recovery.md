# DEF-47 recovery

To disable the release, revert the DEF-47 commit as one unit; do not delete a caller's output directory. Existing `bundle.json`, `outcomes.jsonl`, `report.md`, and `source-artifacts/` are immutable evidence for already-consumed budgets and must remain intact.

For a failed or partial batch, preserve its operation ID, input keys/order, diagnostics, source proofs, and successful artifacts. Correct the caller's inputs or local fixture, then start a new invocation. Never rewrite a bundle, reuse its operation ID, or convert unreviewed candidates into accepted contacts during recovery.

The standalone command performs no graph write, model call, PostgreSQL, Redis, or Celery operation. Disabling it therefore does not require any infrastructure rollback.
