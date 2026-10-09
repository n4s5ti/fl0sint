DEF-90 [S09] is ready for review on `work/def-90-packet-validator`: source is `b9af7f9b` (`feat(audit): validate audit packets locally and in CI`) on base `a59bdadf` (DEF-46), and the packet is in `docs/audits/DEF-90/`. Nothing has been pushed or merged.

**Deliverable**
* `scripts/audit_packet.py validate <packet-dir>` and `changed --base <ref>`: a stdlib-only development validator for `docs/audits/<ISSUE>/packet.json` (schema `fl0sint.audit-packet/v1`). It checks:
  * base/head/tree/config digests against git;
  * artifact sha256 and containment;
  * pre-edit analyses ran against base, and post-edit analyses and the index against head;
  * that no source changed after head;
  * the pip3r payload behind every PASS. Exit 0 is never treated as PASS; unknown or unparseable payloads are UNKNOWN, and every UNKNOWN/DEGRADED/BLOCKED result must be disclosed;
  * required cases, with aggregates never trusted;
  * NOT_APPLICABLE only for a declared-disabled capability;
  * predicted versus actual paths and symbols, including untracked files;
  * the mutation envelope and its restoration;
  * blocking findings;
  * fixture/label changes, which must be declared, cannot be self-approved, and are listed for human review;
  * rollback.
  It never approves review or labels, and never touches Linear.
* `.github/workflows/tests.yml`: a new **Audit packet validity** job. It checks out the PR head with full history, runs `changed --base <pull_request.base.sha | before>` (falling back to `HEAD^` for a zero SHA), and always uploads `audit-packet-report`. The validator tests run in the existing `test` job through `make test`.
* `docs/audit-packet.md` covers the manifest fields, failure codes and output, how to regenerate stale evidence safely, and how a release-gate reviewer uses the report.
* Legacy pre-schema packets (DEF-23, DEF-38 to DEF-47, DEF-105) are reported, never passed. A change may add files to them; modifying or deleting their existing evidence fails. DEF-47 is included so that its in-review packet does not block its merge.

**Evidence**
* 29 focused tests pass and cover all 8 required fixtures. F8 runs the real WebsiteToLinks capture crawl in a subprocess with an audit hook and a positive control.
* The mutation check killed 16 of 16 mutants and restored the file byte-identically.
* Enrichers: 185 passed at base and 214 at head.
* Types: 54 passed.
* Core (1 failure) and api (7 failures): the failures are preexisting and identical at base, and are recorded as baseline debt.
* Blast/drift: isolated index at base and at head, drift 0. The new symbols have no package-source callers, and the `load_manifest` blasts are identical before and after.
* The packet validates itself (VALID). Tampering with an artifact gives `E_ARTIFACT_DIGEST`, and setting F3 to SKIP gives `E_CASE_NOT_PASS`.
* CI: the parsed workflow trace passes all 8 checks, and the job's exact `run:` script passes in a clean clone.
* An independent `reviewer` subagent accepted over three rounds. All 7 of its findings and coordinator finding C1 were fixed and re-verified by the same reviewer.

**Not claimed**
* The hosted GitHub Actions run is UNKNOWN (T-CI-HOSTED) because nothing was pushed. Per the issue, Done needs the hosted CI path, so push the branch or open a PR and attach the job URL.
* Hunt coverage is 0: pre-edit BLOCKED, post-edit DEGRADED. This is disclosed and not counted as a pass.
