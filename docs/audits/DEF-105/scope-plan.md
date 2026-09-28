# DEF-105 scope — gate recovery for DEF-41 impact evidence

Purpose: rerun the DEF-41 root/caller/sink impact matrix against a fresh isolated
GraphOS index using the repaired Pip3r runtime (DEF-106 Blast traversal, DEF-107
Doctor deletion advice, DEF-108 Hunt targeting; pip3r commit `82745f2`), so that the
DEF-41 gate can be judged on real coverage instead of `query-interrupted` / `dead_code`
/ zero-probe output. Provenance is pinned in `baseline.json`.

## What this issue is not

- No Fl0sint product change. Analysis worktree `fl0sint-def41-s01` stays at
  `1198f29e` (implementation `b7828cc9`), working tree clean before and after.
- No Pip3r change. The repairs were made and committed under DEF-106/107/108; this
  packet only consumes the installed rebuilt runtime and records its hashes.
- No shared-checkout reindex. Only the isolated worktree index was rebuilt
  (`.gitnexus` moved aside to `/tmp/def105/gitnexus-prev`, then `graphos --index`).
- No fabricated pre-edit evidence: the DEF-41 pre-edit packet is historical and is
  not rewritten; this is a post-repair rerun.

## Declared matrix (24 roots, upstream + downstream each)

| Consumer form the issue asks to cover | Roots |
| --- | --- |
| ID / hash creation | `models.py:canonical_input_hash` (depth 5, plus CALLS-only control), `acquisition.py:_digest` |
| Model constructors | `EvidenceEnvelope`, `InputOutcome`, `StructuredExecutionResult`, `RedactedDiagnostic`, `AcquisitionBundle`, `AcquisitionRequest`, `InputOccurrence`, `OccurrenceOutcome`, `CompletionWitness` |
| Status readers | `models.py:OutcomeStatus`, `acquisition.py:OutcomeStatus`, `execution_service.py:aggregate_status`, `execution_service.py:reconstruct_structured_result` |
| Serializers / parsers | `serialize_request`, `serialize_bundle`, `parse_request`, `parse_bundle`, `build_bundle`, API `ConnectorTestOutcome` |
| Persistence sink | `execution_service.py:persist_structured_result` |
| Lazy task imports / registry consumer | `tasks/enricher.py:run_connector_template`, `tasks/enricher.py:_connector_failure_diagnostic` |
| Aliases | covered by the source import inventory (`OutcomeStatus as CanonicalStatus`, `import acquisition as a`) and the graph IMPORTS comparison |

Supplementary graph queries: relation-type census of the index, IMPORTS edges into
the `_execution` package compared against the exact `git grep` import inventory,
CALLS edges touching `execution_service.py`, all symbols under `_execution/`.

Doctor: `--verify-refactor` on four symbols with known executed callers
(`build_bundle`, `serialize_bundle`, `canonical_input_hash`,
`persist_structured_result`) as negative controls for DEF-107; `--test` to show the
npm-script route honestly reports `script missing` for this Python repository.

Hunt: default target (must fail honestly), source-directory `--scope` misuse (must be
rejected), and Pip3r self-probe from the foreign cwd (recorded, disclosed as not a
Python audit).

## Python audit route actually exercised

Pip3r has no Python test runner; Doctor checks are npm scripts and Hunt probes a CLI.
The supported Python-code route for DEF-41 is therefore: GraphOS index of the Python
sources (gitnexus 1.6.10-rc.45) + Blast/Cypher over it, plus the repository's own
executable Python consumers run in the isolated worktree with the project
interpreter: the 93 focused core tests (acquisition contract + every integrated
importer test), the API egress test, the local example caller, and the DEF-41
second caller against a freshly built wheel in a Pydantic-only environment.

Rule applied throughout: `completeness: partial`, `no-call-edges`, `script missing`,
Hunt errors and any graph coverage gap stay UNKNOWN/N-A; they are never promoted to
PASS. Graph UNKNOWN roots are instead paired with the exact source consumer inventory
and the executed consumer evidence, labelled as bounded behavioral evidence.
