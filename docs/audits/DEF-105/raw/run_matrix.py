#!/usr/bin/env python3
"""DEF-105 gate-recovery matrix runner: serial pip3r commands, raw capture, summary."""
import json, os, subprocess, sys, time, glob, hashlib
from pathlib import Path

WT = "/home/n4s5ti/Documents/dev/fl0sint-def41-s01"
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/def105/raw")
OUT.mkdir(parents=True, exist_ok=True)
PKG = os.path.basename(glob.glob(f"{WT}/*-core")[0])[: -len("-core")]
CORE = f"{PKG}-core/src/{PKG}_core"
EXE = f"{PKG}-core/src/{PKG}_execution"
API = f"{PKG}-api/app/api/schemas"
ENV = dict(os.environ, PIP3R_DISCLOSURE="off")

def blast(name, target, direction, depth, extra=()):
    return (name, ["pip3r", "blast", "--cwd", WT, "--no-auto-index", "--include-tests",
                   "--direction", direction, "--max-depth", str(depth), "--json", *extra, target])

ROOTS = {
    # models (moved canonical value models): constructors, status reader, hash/ID creation
    f"{EXE}/models.py:canonical_input_hash": (5, 5),
    f"{EXE}/models.py:EvidenceEnvelope": (3, 2),
    f"{EXE}/models.py:InputOutcome": (3, 2),
    f"{EXE}/models.py:StructuredExecutionResult": (3, 2),
    f"{EXE}/models.py:OutcomeStatus": (3, 2),
    f"{EXE}/models.py:RedactedDiagnostic": (3, 2),
    # acquisition public contract: builders, serializers, parsers, constructors, digest (ID creation), status
    f"{EXE}/acquisition.py:build_bundle": (3, 3),
    f"{EXE}/acquisition.py:parse_bundle": (3, 3),
    f"{EXE}/acquisition.py:parse_request": (3, 3),
    f"{EXE}/acquisition.py:serialize_bundle": (3, 3),
    f"{EXE}/acquisition.py:serialize_request": (3, 3),
    f"{EXE}/acquisition.py:AcquisitionBundle": (3, 2),
    f"{EXE}/acquisition.py:AcquisitionRequest": (3, 2),
    f"{EXE}/acquisition.py:InputOccurrence": (3, 2),
    f"{EXE}/acquisition.py:OccurrenceOutcome": (3, 2),
    f"{EXE}/acquisition.py:CompletionWitness": (3, 2),
    f"{EXE}/acquisition.py:_digest": (3, 3),
    f"{EXE}/acquisition.py:OutcomeStatus": (3, 2),
    # integrated sinks / status readers / lazy task import
    f"{CORE}/core/services/execution_service.py:persist_structured_result": (3, 3),
    f"{CORE}/core/services/execution_service.py:reconstruct_structured_result": (3, 3),
    f"{CORE}/core/services/execution_service.py:aggregate_status": (3, 3),
    f"{CORE}/tasks/enricher.py:run_connector_template": (2, 2),
    f"{CORE}/tasks/enricher.py:_connector_failure_diagnostic": (2, 2),
    f"{API}/enricher_template.py:ConnectorTestOutcome": (2, 2),
}

CMDS = [
    ("drift", ["pip3r", "graphos", "--cwd", WT, "--drift", "--json", "--no-auto-index"]),
    ("graphos-status", ["pip3r", "graphos", "--cwd", WT, "--status", "--json", "--no-auto-index"]),
]
for target, (up, down) in ROOTS.items():
    sym = target.split(":")[1]
    fil = Path(target.split(":")[0]).stem
    CMDS.append(blast(f"blast-{fil}-{sym}-upstream", target, "upstream", up))
    CMDS.append(blast(f"blast-{fil}-{sym}-downstream", target, "downstream", down))
# CALLS-only control on the previously poisoned frontier
CMDS.append(blast("blast-models-canonical_input_hash-upstream-callsonly",
                  f"{EXE}/models.py:canonical_input_hash", "upstream", 5, ("--relation-types", "CALLS")))

# Cypher: import consumers of the execution package (aliases / lazy imports live at file level)
CY = [
    ("cypher-imports-execution", f"MATCH (a)-[r]->(b) WHERE b.filePath CONTAINS '{EXE}/' AND r.type IN ['IMPORTS','CALLS','EXTENDS','METHOD_OVERRIDES','HAS_METHOD','DEFINES'] RETURN a.id,a.filePath,r.type,b.id,b.filePath"),
    ("cypher-relation-types", f"MATCH ()-[r]->() RETURN r.type AS type, count(*) AS n"),
    ("cypher-execution-symbols", f"MATCH (n) WHERE n.filePath CONTAINS '{EXE}/' RETURN n.id, labels(n) AS labels, n.filePath"),
    ("cypher-sink-calls", f"MATCH (a)-[r]->(b) WHERE (a.filePath CONTAINS 'services/execution_service.py' OR b.filePath CONTAINS 'services/execution_service.py') AND r.type='CALLS' RETURN a.id,r.type,b.id"),
]
for name, q in CY:
    CMDS.append((name, ["pip3r", "graphos", "--cwd", WT, "--cypher", q, "--limit", "1000", "--json", "--no-auto-index"]))

# Doctor: negative controls (symbols with known executed callers) and hunt honesty
for sym in ("build_bundle", "canonical_input_hash", "serialize_bundle", "persist_structured_result"):
    CMDS.append((f"doctor-verify-{sym}", ["pip3r", "doctor", "--cwd", WT, "--verify-refactor", sym, "--json"]))
CMDS.append(("doctor-graph", ["pip3r", "doctor", "--cwd", WT, "--json"]))
CMDS.append(("doctor-check-test", ["pip3r", "doctor", "--cwd", WT, "--check", "test", "--json"]))
CMDS.append(("hunt-default-target", ["pip3r", "doctor", "--cwd", WT, "--hunt", "--json"]))
CMDS.append(("hunt-source-scope", ["pip3r", "doctor", "--cwd", WT, "--hunt", "--hunt-target", "/home/n4s5ti/Documents/dev/pip3r/bin/pip3r.mjs", "--scope", EXE, "--json"]))
CMDS.append(("hunt-selfprobe-blast", ["pip3r", "doctor", "--cwd", WT, "--hunt", "--hunt-target", "/home/n4s5ti/Documents/dev/pip3r/bin/pip3r.mjs", "--scope", "blast", "--json"]))
CMDS.append(("drift-after", ["pip3r", "graphos", "--cwd", WT, "--drift", "--json", "--no-auto-index"]))

summary = []
for name, argv in CMDS:
    t0 = time.time()
    p = subprocess.run(["timeout", "180", *argv], capture_output=True, text=True, env=ENV, cwd="/tmp/def105")
    wall = round(time.time() - t0, 3)
    (OUT / f"{name}.stdout").write_text(p.stdout)
    (OUT / f"{name}.stderr").write_text(p.stderr)
    (OUT / f"{name}.json").write_text(json.dumps({"argv": argv, "rc": p.returncode, "wallSeconds": wall,
        "stdout_sha256": hashlib.sha256(p.stdout.encode()).hexdigest(),
        "stderr_sha256": hashlib.sha256(p.stderr.encode()).hexdigest()}, indent=2))
    row = {"name": name, "rc": p.returncode, "wallSeconds": wall}
    try:
        d = json.loads(p.stdout)
        if name.startswith("blast-"):
            row.update(risk=d.get("risk"), completeness=d.get("completeness"), reasons=d.get("completenessReasons") or d.get("reasons"),
                       warnings=d.get("warnings"), symbols=len(d.get("symbols", []) or []), edges=len(d.get("edges", []) or []),
                       files=len(d.get("files", []) or []), indexStatus=d.get("indexStatus"),
                       edgeTypes=sorted({e.get("type") for e in d.get("edges", []) or []}))
        elif name.startswith("drift"):
            row.update(total=(d.get("result") or d).get("total", (d.get("result") or d).get("summary")))
        elif name.startswith("cypher-"):
            row.update(rows=len(((d.get("result") or {}).get("rows")) or []))
        elif name.startswith("doctor-verify"):
            row.update(keys=sorted(d.keys()))
    except Exception as e:  # noqa
        row["parse"] = f"{type(e).__name__}: {e}"[:200]
        row["stderr_head"] = p.stderr.strip().splitlines()[:2] if p.stderr.strip() else []
    summary.append(row)
    print(json.dumps(row), flush=True)
(OUT / "summary.json").write_text(json.dumps(summary, indent=2))
