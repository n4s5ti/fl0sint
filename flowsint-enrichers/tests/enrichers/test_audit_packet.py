"""DEF-90 audit-packet validator behaviour (scripts/audit_packet.py).

Each test builds a throwaway git repository and a packet against it, so the
validator's revision, digest and scope checks run against real git state.
"""

import copy
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts/audit_packet.py"
_spec = importlib.util.spec_from_file_location("audit_packet", SCRIPT)
audit_packet = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(audit_packet)

BLAST_OK = {
    "risk": "LOW",
    "completeness": "complete",
    "completenessReasons": [],
    "indexStatus": "ready",
    "summary": {"totalEdges": 1},
}
DRIFT_OK = {"mode": "drift", "result": {"summary": {"total": 0}}, "indexStatus": "ready"}


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _commit(repo, message):
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


class Packet:
    def __init__(self, repo, base, head):
        self.repo = repo
        self.base = base
        self.head = head
        self.dir = repo / "docs/audits/DEF-900"
        self.files = {
            "pre-blast": ("raw/pre-blast.json", json.dumps(BLAST_OK)),
            "post-blast": ("raw/post-blast.json", json.dumps(BLAST_OK)),
            "post-drift": ("raw/post-drift.json", json.dumps(DRIFT_OK)),
            "tests": ("raw/tests.txt", "2 passed\n"),
            "recovery": ("recovery.md", "Revert the head commit.\n"),
        }
        self.manifest = {
            "schema": audit_packet.SCHEMA,
            "issue": "DEF-900",
            "base_commit": base,
            "head_commit": head,
            "source_tree": _git(repo, "rev-parse", f"{head}^{{tree}}"),
            "configuration": {"pyproject.toml": _sha((repo / "pyproject.toml").read_bytes())},
            "tool_versions": {"pip3r": "1.2.6", "python": "3.12"},
            "capabilities": {"graph": False, "model": False, "browser": False, "network": False},
            "index": {"mode": "isolated-worktree", "analyzed_commit": head, "drift": "post-drift"},
            "analyses": [
                self.analysis("pre-blast", "pre", "blast", base),
                self.analysis("post-blast", "post", "blast", head),
                self.analysis("post-drift", "post", "drift", head),
            ],
            "commands": [
                {"id": "tests", "command": "pytest -q", "exit_code": 0, "status": "PASS", "artifact": "tests"}
            ],
            "required_cases": ["C1"],
            "cases": [{"id": "C1", "title": "helper returns two", "status": "PASS", "evidence": ["tests"]}],
            "scope": {
                "predicted_paths": ["src/app.py"],
                "predicted_symbols": ["src/app.py:helper"],
                "actual_symbols": ["src/app.py:helper"],
                "explained_changes": [],
            },
            "mutations": [
                {
                    "id": "M1",
                    "path": "src/app.py",
                    "description": "helper returns 0",
                    "killed": True,
                    "killed_by": "tests",
                    "restored_sha256": _sha((repo / "src/app.py").read_bytes()),
                }
            ],
            "findings": [],
            "baseline_debt": [],
            "coverage_limitations": [],
            "label_changes": [],
            "review": {"status": "pending", "reviewer": None, "independent": True},
            "rollback": {"procedure": "git revert the head commit", "document": "recovery"},
        }

    @staticmethod
    def analysis(ident, phase, kind, commit):
        return {
            "id": ident,
            "phase": phase,
            "kind": kind,
            "target": "src/app.py:helper",
            "analyzed_commit": commit,
            "command": f"pip3r {kind} --json",
            "exit_code": 0,
            "status": "PASS",
            "artifact": ident,
        }

    def advance_head(self, message):
        """Commit pending source edits as the audited head and re-pin post evidence."""
        self.head = self.manifest["head_commit"] = _commit(self.repo, message)
        self.manifest["source_tree"] = _git(self.repo, "rev-parse", "HEAD^{tree}")
        self.manifest["index"]["analyzed_commit"] = self.head
        for analysis in self.manifest["analyses"]:
            if analysis["phase"] == "post":
                analysis["analyzed_commit"] = self.head

    def entry(self, section, ident):
        return next(e for e in self.manifest[section] if e["id"] == ident)

    def write(self):
        artifacts = {}
        for ident, (rel, text) in self.files.items():
            target = self.dir / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
            artifacts[ident] = {"path": rel, "sha256": _sha(target.read_bytes())}
        manifest = copy.deepcopy(self.manifest)
        manifest["artifacts"] = artifacts
        (self.dir / "packet.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return self

    def validate(self):
        return audit_packet.validate_packet(self.repo, self.dir)

    def codes(self):
        self.write()
        return {e["code"] for e in self.validate()["errors"]}


@pytest.fixture
def packet(tmp_path):
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "tests/fixtures").mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "def90@example.invalid")
    _git(repo, "config", "user.name", "DEF-90 test")
    _git(repo, "config", "commit.gpgsign", "false")
    (repo / "pyproject.toml").write_text("[project]\nname = 'demo'\n")
    (repo / "src/app.py").write_text("def run():\n    return 1\n")
    (repo / "tests/fixtures/labels.json").write_text('{"label": "a"}\n')
    base = _commit(repo, "base")
    (repo / "src/app.py").write_text("def run():\n    return 1\n\n\ndef helper():\n    return 2\n")
    head = _commit(repo, "head")
    return Packet(repo, base, head)


def test_complete_packet_is_valid_and_review_stays_separate(packet):
    packet.write()
    _commit(packet.repo, "packet")  # a packet-only commit after head is not staleness
    report = packet.validate()
    assert report["errors"] == []
    assert report["packet_valid"] is True
    assert report["review"] == {"status": "pending", "reviewer": None, "independent": True}
    assert report["acceptance"] == "not decided by this validator"


def test_missing_pre_edit_report_is_rejected(packet):
    packet.manifest["analyses"] = [a for a in packet.manifest["analyses"] if a["phase"] != "pre"]
    assert "E_PRE_MISSING" in packet.codes()


def test_missing_or_altered_pre_edit_artifact_is_rejected(packet):
    packet.write()
    (packet.dir / "raw/pre-blast.json").unlink()
    assert "E_ARTIFACT_MISSING" in {e["code"] for e in packet.validate()["errors"]}
    packet.write()
    (packet.dir / "raw/pre-blast.json").write_text(json.dumps({**BLAST_OK, "risk": "HIGH"}))
    assert "E_ARTIFACT_DIGEST" in {e["code"] for e in packet.validate()["errors"]}


def test_source_change_after_head_commit_is_stale(packet):
    packet.write()
    (packet.repo / "src/app.py").write_text("def run():\n    return 3\n")
    _commit(packet.repo, "later source change")
    assert "E_STALE_HEAD" in {e["code"] for e in packet.validate()["errors"]}


def test_later_commits_on_a_stacked_branch_do_not_stale_a_published_packet(packet):
    packet.write()
    _commit(packet.repo, "publish packet")
    (packet.repo / "src/app.py").write_text("def run():\n    return 5\n")
    _commit(packet.repo, "next issue changes source")
    report = packet.validate()
    assert report["errors"] == [], report["errors"]


def test_source_published_with_the_packet_after_head_is_stale(packet):
    packet.write()
    (packet.repo / "src/app.py").write_text("def run():\n    return 6\n")
    _commit(packet.repo, "packet plus unaudited source edit")
    assert "E_STALE_HEAD" in {e["code"] for e in packet.validate()["errors"]}


def test_uncommitted_source_edit_is_stale(packet):
    packet.write()
    (packet.repo / "src/app.py").write_text("def run():\n    return 4\n")
    assert "E_STALE_HEAD" in {e["code"] for e in packet.validate()["errors"]}


def test_stale_index_and_analysis_metadata_are_rejected(packet):
    packet.manifest["index"]["analyzed_commit"] = packet.base
    packet.entry("analyses", "post-blast")["analyzed_commit"] = packet.base
    codes = packet.codes()
    assert {"E_STALE_INDEX", "E_STALE_ANALYSIS"} <= codes


def test_nonzero_drift_cannot_certify_index_freshness(packet):
    drift = copy.deepcopy(DRIFT_OK)
    drift["result"]["summary"]["total"] = 3
    packet.files["post-drift"] = ("raw/post-drift.json", json.dumps(drift))
    assert "E_SEMANTIC_MISMATCH" in packet.codes()
    packet.entry("analyses", "post-drift")["status"] = "UNKNOWN"
    packet.manifest["coverage_limitations"] = [{"ref": "post-drift", "description": "dirty index"}]
    assert "E_STALE_INDEX" in packet.codes()


def test_unknown_blast_with_exit_zero_is_not_accepted_as_pass(packet):
    unknown = {**BLAST_OK, "risk": "UNKNOWN", "completeness": "partial",
               "completenessReasons": ["query-interrupted"]}
    packet.files["post-blast"] = ("raw/post-blast.json", json.dumps(unknown))
    assert "E_SEMANTIC_MISMATCH" in packet.codes()

    packet.entry("analyses", "post-blast")["status"] = "UNKNOWN"
    assert "E_UNDISCLOSED" in packet.codes()

    packet.manifest["coverage_limitations"] = [{"ref": "post-blast", "description": "partial graph"}]
    packet.write()
    report = packet.validate()
    assert report["packet_valid"] is True
    assert {"id": "post-blast", "source": "result", "status": "UNKNOWN"} in report["non_pass_results"]

    packet.entry("cases", "C1")["evidence"] = ["post-blast"]
    assert "E_CASE_EVIDENCE" in packet.codes()


@pytest.mark.parametrize(
    "ident, payload",
    [
        ("post-blast", '{"risk": "UNKNOWN", "completeness": "par'),  # truncated by a timeout
        ("post-blast", '{"error": "index unavailable"}'),
        ("post-drift", '{"mode": "driftX", "result": {"summary": {"total": 37}}}'),
    ],
)
def test_unrecognizable_payload_declared_pass_is_rejected(packet, ident, payload):
    packet.files[ident] = (f"raw/{ident}.json", payload)
    assert "E_SEMANTIC_MISMATCH" in packet.codes()


def test_degraded_hunt_with_exit_zero_is_not_pass(packet):
    hunt = {"hunt": {"findings": [], "coverage": {"commandsDiscovered": 0}}}
    packet.files["hunt"] = ("raw/hunt.json", json.dumps(hunt))
    packet.manifest["analyses"].append(packet.analysis("hunt", "post", "hunt", packet.head))
    assert "E_SEMANTIC_MISMATCH" in packet.codes()


def test_pass_with_nonzero_exit_is_rejected(packet):
    packet.entry("commands", "tests")["exit_code"] = 1
    assert "E_EXIT_STATUS" in packet.codes()


def test_untracked_file_outside_scope_is_flagged(packet):
    (packet.repo / "notes.txt").write_text("stray\n")
    codes = packet.codes()
    assert "E_SCOPE_PATH" in codes
    packet.manifest["scope"]["explained_changes"] = [{"path": "notes.txt", "reason": "operator scratch"}]
    assert "E_SCOPE_PATH" not in packet.codes()


def test_committed_change_outside_scope_and_unpredicted_symbol_are_flagged(packet):
    packet.manifest["scope"]["predicted_paths"] = ["lib/"]
    packet.manifest["scope"]["actual_symbols"].append("src/app.py:run")
    codes = packet.codes()
    assert {"E_SCOPE_PATH", "E_SCOPE_SYMBOL", "E_MUTATION_ENVELOPE"} <= codes


@pytest.mark.parametrize("status", ["FAIL", "SKIP"])
def test_required_case_failure_is_not_hidden_by_green_totals(packet, status):
    packet.entry("cases", "C1")["status"] = status
    packet.manifest["case_totals"] = {"PASS": 1, "FAIL": 0, "SKIP": 0}
    codes = packet.codes()
    assert {"E_CASE_NOT_PASS", "E_AGGREGATE_MISMATCH"} <= codes


def test_missing_required_case_is_rejected(packet):
    packet.manifest["required_cases"].append("C2")
    assert "E_CASE_MISSING" in packet.codes()


def test_case_id_cannot_shadow_a_result_id(packet):
    packet.entry("cases", "C1")["id"] = "tests"
    packet.manifest["required_cases"] = ["tests"]
    assert "E_SCHEMA" in packet.codes()


def test_disabled_capability_permits_only_its_own_checks_to_be_not_applicable(packet):
    packet.manifest["required_cases"].append("G1")
    packet.manifest["cases"].append(
        {"id": "G1", "title": "graph projection", "status": "NOT_APPLICABLE",
         "requires_capability": "graph", "evidence": []}
    )
    report = packet.write().validate()
    assert report["packet_valid"] is True
    # A waived required case stays visible to the release-gate reviewer.
    assert {"id": "G1", "source": "case", "status": "NOT_APPLICABLE"} in report["non_pass_results"]

    packet.manifest["capabilities"]["graph"] = True
    assert "E_NOT_APPLICABLE" in packet.codes()

    packet.manifest["capabilities"]["graph"] = False
    packet.entry("cases", "G1").pop("requires_capability")
    assert "E_NOT_APPLICABLE" in packet.codes()


def test_core_analysis_cannot_be_skipped_as_not_applicable(packet):
    pre = packet.entry("analyses", "pre-blast")
    pre["status"] = "NOT_APPLICABLE"
    pre["requires_capability"] = "graph"
    assert "E_PRE_MISSING" in packet.codes()


def test_fixture_label_change_is_surfaced_and_cannot_self_approve(packet):
    (packet.repo / "tests/fixtures/labels.json").write_text('{"label": "b"}\n')
    (packet.repo / "fixtures").mkdir()
    (packet.repo / "fixtures/root.json").write_text('{"label": "c"}\n')
    packet.advance_head("relabel")
    packet.manifest["scope"]["predicted_paths"] += ["tests/fixtures/", "fixtures/"]
    assert "E_LABEL_UNDECLARED" in packet.codes()

    packet.manifest["label_changes"] = [
        {"path": "tests/fixtures/labels.json", "reason": "fix label"},
        {"path": "fixtures/root.json", "reason": "new root fixture"},
    ]
    packet.write()
    report = packet.validate()
    assert report["packet_valid"] is True
    assert report["requires_human_review"] == ["fixtures/root.json", "tests/fixtures/labels.json"]

    packet.manifest["label_changes"][0]["approved"] = True
    assert "E_LABEL_SELF_APPROVAL" in packet.codes()


def test_introduced_blocking_finding_must_be_fixed(packet):
    packet.manifest["findings"] = [
        {"id": "R1", "summary": "race", "origin": "introduced", "blocking": True,
         "classification": "out-of-scope", "link": "DEF-1"}
    ]
    assert "E_FINDING_BLOCKING" in packet.codes()
    packet.manifest["findings"][0].pop("classification")
    assert "E_FINDING_UNCLASSIFIED" in packet.codes()


def test_surviving_or_unrestored_mutation_is_rejected(packet):
    mutation = packet.entry("mutations", "M1")
    mutation["killed"] = False
    mutation["restored_sha256"] = "0" * 64
    assert {"E_MUTATION_SURVIVED", "E_MUTATION_RESTORE"} <= packet.codes()


def test_accepted_review_requires_named_reviewer_and_evidence(packet):
    packet.manifest["review"] = {"status": "accepted", "reviewer": None, "independent": True}
    assert "E_REVIEW" in packet.codes()


def test_configuration_digest_is_recomputed_at_head(packet):
    packet.manifest["configuration"]["pyproject.toml"] = "0" * 64
    assert "E_CONFIG_DIGEST" in packet.codes()


def _cli(repo, *args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--repo", str(repo), *args],
        capture_output=True, text=True, check=False,
    )


def test_changed_scope_cli_validates_packets_and_freezes_legacy_evidence(packet):
    legacy = packet.repo / "docs/audits/DEF-46"
    legacy.mkdir(parents=True)
    (legacy / "notes.md").write_text("pre-schema packet\n")
    since = _commit(packet.repo, "existing legacy evidence")
    (legacy / "addendum.md").write_text("appended later\n")
    packet.write()
    _commit(packet.repo, "packet plus legacy addendum")
    ok = _cli(packet.repo, "changed", "--base", since)
    assert ok.returncode == 0, ok.stdout + ok.stderr
    assert "VALID docs/audits/DEF-900" in ok.stdout
    assert "LEGACY docs/audits/DEF-46" in ok.stdout

    (legacy / "notes.md").write_text("rewritten evidence\n")
    unmanifested = packet.repo / "docs/audits/DEF-901"
    unmanifested.mkdir()
    (unmanifested / "notes.md").write_text("no manifest\n")
    _commit(packet.repo, "rewrite legacy and add unmanifested packet")
    bad = _cli(packet.repo, "--json", "changed", "--base", since)
    assert bad.returncode == 1
    report = {p["packet"]: p for p in json.loads(bad.stdout)["packets"]}
    assert [e["code"] for e in report["docs/audits/DEF-901"]["errors"]] == ["E_PACKET_MISSING"]
    assert [e["code"] for e in report["docs/audits/DEF-46"]["errors"]] == ["E_LEGACY_EVIDENCE_CHANGED"]
    # Sibling packet evidence is gated per packet, not counted as DEF-900 source drift.
    assert report["docs/audits/DEF-900"]["packet_valid"] is True

    # Adding a manifest to the legacy directory does not unfreeze the rewrite.
    shutil.copyfile(packet.dir / "packet.json", legacy / "packet.json")
    _commit(packet.repo, "upgrade legacy directory")
    upgraded = _cli(packet.repo, "--json", "changed", "--base", since)
    assert upgraded.returncode == 1
    legacy_report = next(
        p for p in json.loads(upgraded.stdout)["packets"] if p["packet"] == "docs/audits/DEF-46"
    )
    assert "E_LEGACY_EVIDENCE_CHANGED" in {e["code"] for e in legacy_report["errors"]}


RUNTIME_PROBE = r"""
import asyncio, importlib, json, sys, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

events = []
def hook(event, args):
    if event in ("import", "open", "exec", "subprocess.Popen") and "audit_packet" in repr(args):
        events.append(event)
sys.addaudithook(hook)

core, enrichers, types, mode = sys.argv[1:5]
if mode == "control":
    sys.path.insert(0, sys.argv[5])
    importlib.import_module("audit_packet")

class Silent:
    info = error = completed = staticmethod(lambda *a, **k: None)

links = importlib.import_module(f"{enrichers}.website.to_links")
base_module = importlib.import_module(f"{core}.core.enricher_base")
links.Logger = base_module.Logger = Silent
Website = importlib.import_module(f"{types}.website").Website

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b'<html><body><a href="/leaf">leaf</a></body></html>'
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *args):
        pass

server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
scraper = links.WebsiteToLinks(sketch_id="def90-runtime")
url = f"http://test.localhost:{server.server_port}/seed"
results = asyncio.run(scraper.execute([Website(url=url)]))
server.shutdown()
print(json.dumps({
    "candidates": len(results[0]["candidate_operations"]),
    "events": events,
    "modules": sorted(m for m in sys.modules if "audit_packet" in m),
}))
"""


def _package_module(suffix):
    return next(REPO_ROOT.glob(f"*-{suffix}/src/*_{suffix}")).name


@pytest.mark.parametrize("mode", ["runtime", "control"])
def test_scraper_runtime_does_not_import_or_invoke_validator(mode):
    env = {**os.environ, "NEO4J_URI_BOLT": "", "NEO4J_USERNAME": "", "NEO4J_PASSWORD": ""}
    env.setdefault("AUTH_SECRET", "def90-test-only")
    proc = subprocess.run(
        [sys.executable, "-c", RUNTIME_PROBE, _package_module("core"),
         _package_module("enrichers"), _package_module("types"), mode, str(SCRIPT.parent)],
        capture_output=True, text=True, env=env, timeout=120, check=False,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    observed = json.loads(proc.stdout.strip().splitlines()[-1])
    assert observed["candidates"] > 0
    if mode == "runtime":
        assert observed["events"] == [] and observed["modules"] == []
    else:  # the probe must be able to see a validator import when one happens
        assert observed["events"] and observed["modules"] == ["audit_packet"]
