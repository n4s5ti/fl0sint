# DEF-44 recovery

Disable new retention by removing/revoking the applicable reviewed policy in the deployment-owned runtime configuration, or unset `FLOWSINT_ARTIFACT_RUNTIME_CONFIG`. Successful acquisition then returns HOLD without pretending that discarded bytes are support. Already-consumed requests/bytes remain reported. Never grant authority through template source_rights or request parameters.

Preserve the artifact store, immutable occurrence records, existing source-proof strings and audit packet. Do not delete shared content objects merely because one occurrence expires; different occurrences may share bytes. Unavailable, expired, digest-tampered or truncated evidence requires explicit REVIEW/revalidation. A deliberate re-fetch creates a new operation/snapshot under a new allocation; no automatic replay or overwrite of previous source identity.

Current-policy revocation prevents future resolution, including previously issued proof. Revocation does not rewrite evidence history. In-flight atomic writes may finish; their completion cannot emit late success after the caller deadline/cancellation. Keep those immutable records rather than retrying and double-spending the original allocation.

Code recovery before deployment: revert `8fdfedb5`, `a0eb7c3e`, then `0882e3a2` in reverse order on an isolated integration branch, retaining `d81cadff` and the evidence packet. Do not reset other worktrees or discard user changes. Reverting source-proof support removes authorized resolution; it does not make old hash-only references sufficient evidence. Do not deploy the previous source_rights retention gate as a security fix.

No schema migration, database backfill, deployed configuration change, push, merge or production artifact creation occurred in this ticket. The timed-out wrapper worktree copy was left untouched; only the valid native worktree was used.
