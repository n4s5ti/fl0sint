# DEF-24 [P1-GATE] Audit and release Standalone source-preserving scraper — status: scheduled

## Release outcome

URL or mixed-success URL batch produces inspectable evidence and machine-readable output without Jev, graph or planner services.

[PRD v3](<https://linear.app/def2/document/fl0sint-prd-v3-authoritative-implementation-snapshot-ed5976aab5e6>) · [Mandatory execution/audit workflow](<https://linear.app/def2/document/execution-workflow-blast-hunt-doctor-audits-and-closure-f95edaa6216f>)

## Native blocking dependencies

* [DEF-23 (P0-GATE)](<https://linear.app/def2/issue/DEF-23/p0-gate-audit-and-release-baseline-blast-and-fixtures>)
* [DEF-41 (S01)](<https://linear.app/def2/issue/DEF-41/s01-define-the-versioned-acquisition-bundle-and-standalone-operation>)
* [DEF-42 (S02)](<https://linear.app/def2/issue/DEF-42/s02-repair-completion-order-attribution-and-preserve-failed-input>)
* [DEF-43 (S03)](<https://linear.app/def2/issue/DEF-43/s03-implement-bounded-fetch-admission-transport-validation-and>)
* [DEF-44 (S04)](<https://linear.app/def2/issue/DEF-44/s04-capture-retrievable-source-proof-and-stable-extraction-spans>)
* [DEF-45 (S05)](<https://linear.app/def2/issue/DEF-45/s05-extract-readable-text-observed-links-and-typed-contact-candidates>)
* [DEF-46 (S06)](<https://linear.app/def2/issue/DEF-46/s06-isolate-acquisition-from-graph-writes-with-a-complete-capture-sink>)
* [DEF-47 (S07)](<https://linear.app/def2/issue/DEF-47/s07-ship-the-standalone-scraper-clifunction-and-readable-evidence>)
* [DEF-48 (S08)](<https://linear.app/def2/issue/DEF-48/s08-audit-standalone-release-behavior-and-build-the-reproducible-smoke>)
* [DEF-90 (S09)](<https://linear.app/def2/issue/DEF-90/s09-enforce-audit-packet-validity-locally-and-in-the-existing-ci>)

## Required child deliverables

* [DEF-41 (S01)](<https://linear.app/def2/issue/DEF-41/s01-define-the-versioned-acquisition-bundle-and-standalone-operation>) — Define the versioned acquisition bundle and standalone operation contract
* [DEF-42 (S02)](<https://linear.app/def2/issue/DEF-42/s02-repair-completion-order-attribution-and-preserve-failed-input>) — Repair completion-order attribution and preserve failed input occurrences
* [DEF-43 (S03)](<https://linear.app/def2/issue/DEF-43/s03-implement-bounded-fetch-admission-transport-validation-and>) — Implement bounded fetch admission, transport validation and cancellation
* [DEF-44 (S04)](<https://linear.app/def2/issue/DEF-44/s04-capture-retrievable-source-proof-and-stable-extraction-spans>) — Capture retrievable source proof and stable extraction spans
* [DEF-45 (S05)](<https://linear.app/def2/issue/DEF-45/s05-extract-readable-text-observed-links-and-typed-contact-candidates>) — Extract readable text, observed links and typed contact candidates
* [DEF-46 (S06)](<https://linear.app/def2/issue/DEF-46/s06-isolate-acquisition-from-graph-writes-with-a-complete-capture-sink>) — Isolate acquisition from graph writes with a complete capture sink
* [DEF-47 (S07)](<https://linear.app/def2/issue/DEF-47/s07-ship-the-standalone-scraper-clifunction-and-readable-evidence>) — Ship the standalone scraper CLI/function and readable evidence report
* [DEF-48 (S08)](<https://linear.app/def2/issue/DEF-48/s08-audit-standalone-release-behavior-and-build-the-reproducible-smoke>) — Audit standalone release behavior and build the reproducible smoke harness
* [DEF-90 (S09)](<https://linear.app/def2/issue/DEF-90/s09-enforce-audit-packet-validity-locally-and-in-the-existing-ci>) — Enforce audit-packet validity locally and in the existing CI workflow

## Ordered release audit

1. Check every native blocker is Done with compatible source/configuration versions and complete child evidence. A merged PR or parent rollup is not sufficient.
2. Execute the component's public entrypoint against the documented success and failure fixtures. Inspect actual result bundles, statuses, source spans and resource accounting.
3. Review the union of pre-edit Blast envelopes against actual tracked/untracked changes. Rerun scoped impact/Hunt/Doctor using the verified tool manifest; flag stale/degraded index, missing dynamic/config edges and newly unwired exports.
4. Run applicable milestone cases: C01, C02 local, TA01/04/05/08/12 scoped to enabled path.
5. Try to falsify identity/source attribution, scope isolation, typed errors, resource bounds, replay and factual acceptance for the enabled paths. Disabled optional capabilities must be explicit and unreachable.
6. Use the audit-packet validator when available, then separately review substance. A machine-valid packet does not prove the tests are meaningful or that source claims are true.
7. Attach a versioned release manifest and PASS/FAIL/UNKNOWN result for every criterion, exact commands, artifact hashes, reviewer identity, residual baseline debt and recovery/revert procedure.
8. Mark Done only when all required evidence passes; downstream eligibility follows native blockers. This is not permission to deploy or collect live contact data.

## Acceptance checklist

- [ ] Every required child and native prerequisite is complete with reproducible evidence.
- [ ] The stated independent/milestone value works through the actual public interface.
- [ ] Blast before/after, predicted-versus-actual drift, Hunt and Doctor/test/contract evidence are reviewed.
- [ ] Negative cases pass and no introduced blocking finding remains; baseline debt and false positives are explained.
- [ ] Relevant source proof, input identity, scope, active accepted truth and spent resources remain correct.
- [ ] Enabled/disabled capabilities, limits and unverified claims are explicit.
- [ ] Separate review pass and safe rollback/recovery notes are linked.

If a tool or source is unavailable, record an exact blocker and leave the gate incomplete. Do not replace an unrun audit with a checkbox or claim all historical warnings were fixed. Creating this issue does not mean any code, test, merge or deployment is complete.
