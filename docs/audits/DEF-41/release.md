# DEF-41 / S01 acceptance

Decision corrected on 2026-09-27: **BLOCKED — acceptance withdrawn**, pending [DEF-105](https://linear.app/def2/issue/DEF-105). The prior implementation and behavioral results remain recorded below, but they do not satisfy the required impact-verification gate. The former acceptance is preserved in Git at `1198f29e`; it must not be used to authorize downstream work. No deployment, live collection, graph writes, admission runtime, database migration or bundle importer was added.

- Baseline: `1b48c428e2e32a5179005df288ac669bdae82409` (accepted P0 gate DEF-23).
- Implementation: `b7828cc968860f8376ac2ff251523fb2e8dfa391`.
- Public namespace: `flowsint_execution.acquisition`; canonical models moved unchanged to `flowsint_execution.models`.
- Reviewed acquisition source SHA-256: `faaec3e705ac4db321053e292d34bc6fc58b3d1d05585ca14ee8435433b1d10c`.
- Canonical models SHA-256: `dfbff64db850c5c99b0cd619571244f11e6a1081d261793f475b1fed7ece65d4`; Git recorded a 100% rename.
- Final wheel SHA-256: `bd3e8fbc60fa29649da7a47abec8005bccf84ae462b03b18444f7924d223d086`. Its three contract-package members equal the committed source bytes; the old module is absent. See `raw/verification/wheel-provenance.json`.

## Required behavior

| Criterion | Result | Evidence |
| --- | --- | --- |
| Explicit caller/scope/capability/allocation/operation; optional parent/Need | PASS | Strict request models; independent reviewer exercised allocation omission and context roundtrip |
| Every input occurrence retained; zero/one/many outputs; duplicate hashes independent | PASS | 46 behavioral tests, independent duplicate/cross-owner probes, completion-order fixture |
| Success/valid-empty/partial/typed failures/unknown/retention hold preserve underlying status | PASS | Witness requirement, partial terminal children, material exclusion on failure/hold, measured cost retained |
| Artifacts/snapshots/spans/candidates, timestamps/URLs/extraction identity/origin digest | PASS | Reference-graph checks and full normalized roundtrip; second caller retrieved local fixture and checked its exact hash/byte length/field pointer |
| Imported run/step/attempt/index/seed lineage | PASS | Explicit all-or-nothing lineage; roundtrip retained without inventing identities |
| Malformed output and unsupported versions have explicit safe diagnostics | PASS | Nested nonfinite/coercion/ref errors, duplicate JSON keys, malformed UTF-8, root/nested version probes |
| Candidate disposition cannot silently become accepted | PASS | Literal unreviewed disposition, revalidation of model-copy/construct bypasses, digest tamper rejection |
| Core bundle imports without model keys, Neo4j or Celery | PASS | Final installed wheel in Pydantic-only venv, `env -i`, no PYTHONPATH; Neo4j/Celery/SQLAlchemy absent; heavy core namespace never imported |
| Callable public entrypoint and successful/failed local examples | PASS | `raw/example-final/`, installed-example/installed-consumer logs, executable `examples/acquisition_contract.py` |

The contract represents input types with a type tag and strict finite JSON, not a new domain schema registry. It validates declarations and provenance relationships, not producer honesty or source authorization. Unknown model/graph fields and unmeasured resources are not fabricated as zero-confidence/zero-cost values. `None` is explicit unavailable/not-applicable context.

## Exercised checks

Exact argv, cwd, explicit environment overrides, return codes and durations are in `raw/verification/*.json`; adjacent stdout/stderr are retained.

- `contract-formatted-final`: **46 passed**. Covers required boundaries and regression cases; no wiring/source-text assertions.
- `integrated-regression`: **47 passed**, covering structured execution, execution persistence, graph projection service, template execution task and template enricher. Canonical behavior unchanged.
- `api-schema-smoke`: migrated `ConnectorTestOutcome` consumed/serialized/reloaded the same canonical `RedactedDiagnostic` type. Full historical API endpoint failures were not rerun merely to reconfirm them; this packet does not claim a full API-suite pass.
- `wheel-build-final` / `wheel-install-final`: built the real core wheel and installed it with `--no-deps` in a fresh Pydantic-only environment. This proves the contract import boundary, not minimal installation of the whole integrated core distribution.
- `installed-example-final`: emitted a successful source-backed local-fixture bundle and an explicit caller-supplied invalid-input bundle, with different operation identities.
- `installed-consumer-final`: independent process parsed both, checked local artifact digest/bytes/JSON pointer, occurrence ownership and unreviewed disposition; no infrastructure packages or service/model keys.
- `independent-probes-final`: reviewer-authored program rerun against exact final source in the pure environment; output preserved.

Two existing deprecation warnings (passlib crypt, class-based Pydantic config) remain. Integrated tests emitted logger teardown SQL UUID/string errors after all assertions passed; no logger code was changed. These are outside the canonical-import/data-contract change and are not silently suppressed or claimed repaired.

## Adversarial review

Reviewer: **S01AdversarialReview**, separate read-only `explore` subagent (model `openai-codex/gpt-5.6-terra`). It inspected source/docs, authored executable probes, attacked public callers and independently retested the fix. This is a separate agent, not a separate human organization. The review was pinned by source hashes before the implementation commit; those hashes match the committed wheel members.

`raw/review/verdict.json`: PASS, no outstanding blockers. Introduced F1 was a nested canonical diagnostic `model_construct`/`model_copy` bypass: `build_bundle` could leak a secret-bearing Pydantic warning and throw an unnormalized serialization exception. Strict diagnostic revalidation now rejects it before dumping; public errors are `ContractError`, with zero warnings/secret echo. Two failing-before tests and final passing results are retained (`review-f1-red`, final suite). Direct Pydantic constructors still expose ordinary `ValidationError`; documentation explicitly excludes their raw errors from untrusted logging.

The first implementation draft was rejected during integration: duplicate occurrences/outcomes collapsed in set comparisons; serialization did not reject a changed origin digest; two relative imports were missed. All three integrity tests were observed failing before repair (`contract-integrity-red`). A later NaN-to-null serialization bypass was independently pinned failing-before (`nonfinite-red`) and fixed by validating Python values before JSON encoding. Draft shape-only/incidental tests were replaced with behavioral checks; no unobserved test-first claim is made.

## Blast, drift, Doctor and Hunt

Pre-edit scope: `raw/scope-plan.md`. Raw pre-index/root outputs: `raw/pre/`. Raw post outputs: `raw/post/`, with interpreted results in `raw/post-summary.json`. The latter's original `artifact_limit` describes the review agent's inability to write files; the parent subsequently recovered and archived its exact tool-result logs. Raw post logs retain stdout JSON inside capture wrappers. Blast wrapper `__ARGV__` lines contain an unquoted target token; they are preserved verbatim, not misrepresented as valid JSON arrays. Exact executable invocations are reconstructible from root/direction plus the captured flags/cwd.

- Before generation: `sha256:dbef0d67348a684550e90ffe4ef47072b74ff68c82a33c00ca5e4309caefb68c`.
- After generation: `sha256:ec8e06b94bcb974cfcd2d68b5abf3e27442dbfd689c93b3ebab1c3218f2131bf`.
- Isolated index refreshed successfully; tool-reported drift was zero after refresh and after the first packet-copy state. These are timestamped observations, not a claim that later audit-document additions were indexed.
- Canonical class upstream queries report `no-call-edges`; downstream edges are largely class members. **External-consumer graph completeness is UNKNOWN.** New `build_bundle` callers in the executable example/tests also evade upstream discovery. `parse_bundle` internal chains are discoverable, not whole-consumer proof.
- LSP references were attempted; pyright-langserver was unavailable (ENOENT). Absolute/relative import tracing and runtime consumer exercises provide bounded supplementary evidence, not complete semantic reference coverage. No leftover old production import was found.
- Doctor `--verify-refactor build_bundle` returned `dead_code`, “safe to delete,” score 100 despite exercised example/test callers. **FAIL for trustworthy deletion advice**; its zero-indexed-caller heuristic has no completeness guard. This is not a product dead-code finding.
- The previous Hunt invocation passed Python source directories to CLI-command selectors. It discovered/probed **0 commands** and exercised **0 JSON contracts** because it attempted `<Fl0sint cwd>/bin/pip3r.mjs`, which is absent, before validating those selectors. **INVALID TARGET / ZERO COVERAGE**, not a scoped Python audit.
- Main repository shared index: **17/17 baseline hashes identical**, zero missing/different. Only the isolated worktree index was refreshed.

The earlier decision incorrectly carried P0 discovery limitations into S01 acceptance. Source tracing, tests and contract review do not turn the failed or inapplicable impact checks into PASS. DEF-41 remains incomplete until DEF-105 establishes meaningful coverage and the corrected evidence is independently accepted.

## Scope and recovery

Base-to-implementation delta: 19 paths = 6 additions, 12 modifications, one R100 rename. Canonical model consumers migrated in core/API/tests; core wheel metadata includes the new namespace. New contract, behavioral tests, local example and documentation match the pre-edit plan. `tests/acquisition/__init__.py` is the only literal extra beyond the named test file and follows existing test-package convention. Subsequent additions are confined to this audit packet.

Recovery is documented in `docs/developers/acquisition-contract.md`: stop new producers before rollback once consumers adopt the contract; revert implementation as a unit; retain immutable bundles, original identities/digests, lineage and actual resources. Do not erase evidence or reset spent budgets or mint new operation IDs to hide uncertain completion. No runtime service was deployed, so no service-disable action is needed.

Workspace note: the first nested worktree setup failed and left a recursively copied plain directory at `.worktrees/def-41-contract` (observed 28 GiB, no `.git` link). It was reported as a tool anomaly and preserved rather than deleting potentially unrelated bytes. Implementation used the verified sibling Git worktree `fl0sint-def41-s01`. This residue is not in the commit or runtime artifact.

## Acceptance correction evidence

The correction is post-implementation investigation, not manufactured pre-edit proof. Only this audit packet changes. `raw/correction/` retains exact argv/cwd/env/return codes plus stdout/stderr, independent reviews and adjudication. The original pre-summary has an empty `targets` object; the correction derives an explicit summary from the preserved raw pre-results, without inventing missing baseline queries.

- Fresh isolated generation `sha256:51feb3012224314f60c99bc3c79990ff7c3c9e6d340fa98689717f15920e3d72`, drift zero before queries. Initial drift was nine audit files, no source-symbol staleness. Shared-index 17/17 hashes, 19 implementation hashes and five configuration hashes still match; source remains `b7828cc9`.
- Expanded roots cover `canonical_input_hash`, `persist_structured_result`, `reconstruct_structured_result` and `aggregate_status`. Real task/persistence/status/serialization edges are now retained. Tool-reported complete bounded traversals do not establish whole-program completeness; raw Cypher and source traces remain separate evidence.
- Hash upstream finds five CALLS then aborts. Its next frontier contains 18 rows. Both installed-schema validation and direct installed `GraphosLadybugClient.queryIncomingEdges(..., ["CALLS"], 0)` reject `METHOD_OVERRIDES`. GraphOS validates before filtering, then discards the exception; Blast emits only `query-interrupted`. This is a concrete schema/traversal defect, not a timeout or stale index.
- Independent `S01ImpactCoverageCorrection` confirms 12 baseline import occurrences across 11 files, including both lazy task imports, migrated with byte-identical models. Its original count of 13 and proposed requirement for a production bundle importer were challenged and retracted; the correction is retained. The standalone installed-wheel caller is valid S01 scope.
- Independent `S01AuditToolGapDiagnosis` traced the three tool failures to source. Neither reviewer accepts incomplete impact evidence as a clean gate. Original contract-only adversarial PASS remains limited to its stated behavioral scope.
- DEF-105 tracks GraphOS compatibility/error propagation, Doctor completeness-aware advice, and a correctly targeted, meaningful Python audit route. No Pip3r source changes, deployment, shared reindex or next-ticket implementation were performed in this correction.
