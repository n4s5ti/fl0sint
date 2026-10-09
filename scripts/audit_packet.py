#!/usr/bin/env python3
"""Development audit-packet validator; not part of any shipped runtime.

Checks a docs/audits/<ISSUE>/packet.json manifest for completeness, artifact
digests, revision freshness and internal consistency. A valid packet does not
prove behavioural correctness, and this tool never approves review or labels.
"""

import argparse
import fnmatch
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

SCHEMA = "fl0sint.audit-packet/v1"
REPORT_SCHEMA = "fl0sint.audit-packet-report/v1"
PACKET_FILE = "packet.json"
AUDITS_DIR = "docs/audits"
ISSUE_RE = re.compile(r"DEF-[0-9]+")
SHA1_RE = re.compile(r"[0-9a-f]{40}")
SHA256_RE = re.compile(r"[0-9a-f]{64}")

STATUSES = frozenset(
    {"PASS", "FAIL", "SKIP", "UNKNOWN", "DEGRADED", "BLOCKED", "NOT_APPLICABLE"}
)
# Results that are neither success nor failure must be disclosed as limitations.
DISCLOSE = frozenset({"UNKNOWN", "DEGRADED", "BLOCKED"})
CAPABILITIES = ("graph", "model", "browser", "network")
ANALYSIS_KINDS = frozenset({"blast", "drift", "hunt", "doctor", "index", "lexical"})
REQUIRED_ANALYSES = {"pre": ("blast",), "post": ("blast", "drift")}
FINDING_CLASSES = frozenset(
    {"fixed", "preexisting-debt", "out-of-scope", "accepted-nonblocking", "false-positive"}
)
REVIEW_STATUSES = frozenset({"pending", "accepted", "changes-requested"})
# Paths holding expected labels/fixtures; changes always need human review.
LABEL_PATTERNS = ("fixtures/*", "*/fixtures/*")
# Packets written before this schema existed (DEF-47 was in review when it was
# introduced). They are reported, never validated or passed; changed mode only
# lets a change add files to them, never modify or delete existing evidence.
LEGACY_PACKETS = frozenset(
    {
        "DEF-23", "DEF-38", "DEF-39", "DEF-40", "DEF-41", "DEF-42",
        "DEF-43", "DEF-44", "DEF-45", "DEF-46", "DEF-47", "DEF-105",
    }
)
# Kinds whose raw payload the validator understands; an unrecognizable payload of
# these kinds is UNKNOWN. "index" and "lexical" have no payload rules.
RECOGNIZED = {
    "blast": lambda p: "risk" in p,
    "drift": lambda p: p.get("mode") == "drift",
    "hunt": lambda p: isinstance(p.get("hunt"), dict),
    "doctor": lambda p: any(k in p for k in ("hunt", "results", "refactor")),
}


class GitError(RuntimeError):
    pass


def git_bytes(root, *args):
    proc = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, check=False
    )
    if proc.returncode != 0:
        raise GitError(f"git {' '.join(args)}: {proc.stderr.decode(errors='replace').strip()}")
    return proc.stdout


def git(root, *args):
    return git_bytes(root, *args).decode()


def git_ok(root, *args):
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, check=False
    ).returncode == 0


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def path_matches(path, patterns):
    """Patterns ending in '/' are directory prefixes; others are fnmatch globs."""
    for pattern in patterns:
        if pattern.endswith("/"):
            if path.startswith(pattern):
                return True
        elif fnmatch.fnmatchcase(path, pattern):
            return True
    return False


class Checker:
    def __init__(self, root, packet_dir):
        self.root = Path(root).resolve()
        self.packet_dir = Path(packet_dir).resolve()
        self.errors = []
        self.warnings = []

    def error(self, code, message):
        self.errors.append({"code": code, "message": message})

    def warn(self, code, message):
        self.warnings.append({"code": code, "message": message})

    def field(self, obj, key, kind, where, required=True):
        """Return obj[key] if it has the expected type, else record E_SCHEMA."""
        if not isinstance(obj, dict) or key not in obj:
            if required:
                self.error("E_SCHEMA", f"{where}: missing '{key}'")
            return None
        value = obj[key]
        valid = isinstance(value, kind) and not (kind is int and isinstance(value, bool))
        if kind is str and valid and not value.strip():
            valid = False
        if not valid:
            self.error("E_SCHEMA", f"{where}.{key}: expected non-empty {getattr(kind, '__name__', kind)}")
            return None
        return value

    def items(self, obj, key, where, required=True):
        """Return a list of dict entries with unique 'id' (when present)."""
        value = self.field(obj, key, list, where, required)
        if value is None:
            return []
        entries = []
        seen = set()
        for index, entry in enumerate(value):
            if not isinstance(entry, dict):
                self.error("E_SCHEMA", f"{where}.{key}[{index}]: expected object")
                continue
            ident = entry.get("id")
            if ident is not None:
                if ident in seen:
                    self.error("E_SCHEMA", f"{where}.{key}: duplicate id {ident!r}")
                seen.add(ident)
            entries.append(entry)
        return entries


def _load_json(data):
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def observed_status(kind, data):
    """Downgrade implied by a raw pip3r payload, or None when it looks complete.

    Rules follow docs/audits/DEF-39/audit-procedure.md section 10. Exit code 0
    is never evidence of success on its own.
    """
    if not data.strip():
        return "BLOCKED", "artifact is empty"
    payload = _load_json(data)
    if kind not in RECOGNIZED:
        return None
    if not isinstance(payload, dict) or not RECOGNIZED[kind](payload):
        return "UNKNOWN", f"artifact is not a recognizable pip3r {kind} payload"
    if kind == "blast" and "risk" in payload:
        reasons = []
        if payload.get("risk") == "UNKNOWN":
            reasons.append("risk UNKNOWN")
        if payload.get("completeness") != "complete":
            reasons.append(f"completeness {payload.get('completeness')!r}")
        if payload.get("indexStatus") != "ready":
            reasons.append(f"indexStatus {payload.get('indexStatus')!r}")
        if payload.get("reason"):
            reasons.append(f"reason {payload['reason']!r}")
        if reasons:
            return "UNKNOWN", ", ".join(reasons)
    if kind == "drift" and payload.get("mode") == "drift":
        total = ((payload.get("result") or {}).get("summary") or {}).get("total")
        if total != 0:
            return "UNKNOWN", f"drift total {total!r}"
        if payload.get("indexStatus") != "ready":
            return "UNKNOWN", f"indexStatus {payload.get('indexStatus')!r}"
    if kind in ("hunt", "doctor") and isinstance(payload.get("hunt"), dict):
        coverage = payload["hunt"].get("coverage") or {}
        if not coverage.get("commandsDiscovered"):
            return "DEGRADED", "hunt coverage.commandsDiscovered is 0"
    return None


def validate_packet(root, packet_dir):
    """Validate one packet against HEAD and the working tree of ``root``."""
    c = Checker(root, packet_dir)
    report = {
        "schema": REPORT_SCHEMA,
        "packet": None,
        "issue": None,
        "rev": None,
        "packet_valid": False,
        "errors": c.errors,
        "warnings": c.warnings,
        "review": None,
        "acceptance": "not decided by this validator",
        "requires_human_review": [],
        "non_pass_results": [],
        "baseline_debt": [],
        "coverage_limitations": [],
    }
    try:
        _validate(c, report)
    except GitError as exc:
        c.error("E_GIT", str(exc))
    report["packet_valid"] = not c.errors
    return report


def _validate(c, report):
    root, packet_dir = c.root, c.packet_dir
    try:
        rel_packet = packet_dir.relative_to(root).as_posix()
    except ValueError:
        c.error("E_PACKET_PATH", f"{packet_dir} is outside the repository {root}")
        return
    report["packet"] = rel_packet
    report["rev"] = git(root, "rev-parse", "HEAD").strip()
    manifest_path = packet_dir / PACKET_FILE
    if not manifest_path.is_file():
        c.error("E_PACKET_MISSING", f"{rel_packet}/{PACKET_FILE} does not exist")
        return
    manifest = _load_json(manifest_path.read_bytes())
    if not isinstance(manifest, dict):
        c.error("E_SCHEMA", f"{PACKET_FILE} is not a JSON object")
        return
    if manifest.get("schema") != SCHEMA:
        c.error("E_SCHEMA", f"schema must be {SCHEMA!r}")
        return

    issue = c.field(manifest, "issue", str, "packet")
    report["issue"] = issue
    if issue is not None and (not ISSUE_RE.fullmatch(issue) or issue != packet_dir.name):
        c.error("E_ISSUE", f"issue {issue!r} must be DEF-<n> and equal the directory name {packet_dir.name!r}")

    base, head = _check_revisions(c, manifest)
    artifacts = _check_artifacts(c, manifest)
    capabilities = _check_capabilities(c, manifest)
    versions = c.field(manifest, "tool_versions", dict, "packet")
    if versions is not None and (
        not versions or not all(isinstance(v, str) and v.strip() for v in versions.values())
    ):
        c.error("E_SCHEMA", "packet.tool_versions: expected non-empty mapping of name to version")
    if head:
        _check_configuration(c, manifest, head)

    results = {}
    _check_analyses(c, manifest, base, head, artifacts, capabilities, results)
    _check_commands(c, manifest, artifacts, capabilities, results)
    _check_disclosure(c, manifest, results, report)
    cases = _check_cases(c, manifest, results, capabilities)

    changed = _changed_paths(c, base, head) if base and head else []
    _check_scope(c, manifest, changed)
    _check_labels(c, manifest, changed, report)
    _check_mutations(c, manifest, head, results)
    _check_findings(c, manifest)
    report["baseline_debt"] = _check_debt(c, manifest, results)
    report["review"] = _check_review(c, manifest)
    _check_rollback(c, manifest)
    report["non_pass_results"] = [
        {"id": ident, "source": "case" if ident in cases else "result", "status": status}
        for ident, status in sorted({**results, **cases}.items())
        if status != "PASS"
    ]


def _resolve_commit(c, manifest, key):
    value = c.field(manifest, key, str, "packet")
    if value is None:
        return None
    if not SHA1_RE.fullmatch(value):
        c.error("E_SCHEMA", f"packet.{key}: expected full 40-hex commit id")
        return None
    if not git_ok(c.root, "cat-file", "-e", f"{value}^{{commit}}"):
        c.error("E_COMMIT", f"packet.{key} {value} is not a commit in this repository")
        return None
    return value


def _check_revisions(c, manifest):
    base = _resolve_commit(c, manifest, "base_commit")
    head = _resolve_commit(c, manifest, "head_commit")
    if base and head and not git_ok(c.root, "merge-base", "--is-ancestor", base, head):
        c.error("E_COMMIT", f"base_commit {base} is not an ancestor of head_commit {head}")
    if not head:
        return base, head
    tree = c.field(manifest, "source_tree", str, "packet")
    actual_tree = git(c.root, "rev-parse", f"{head}^{{tree}}").strip()
    if tree is not None and tree != actual_tree:
        c.error("E_SOURCE_DIGEST", f"source_tree {tree} != {actual_tree} for head_commit")
    # Evidence describes head_commit. It is stale if source changed between head_commit and
    # the revision that published the packet. Later commits on a stacked branch belong to
    # other packets and do not make this one stale.
    rel_packet = c.packet_dir.relative_to(c.root).as_posix()
    pending = git(c.root, "status", "--porcelain", "--untracked-files=all", "--", rel_packet).strip()
    published = None if pending else git(c.root, "log", "-1", "--format=%H", "HEAD", "--", rel_packet).strip()
    packet_rev = published or "HEAD"
    if not git_ok(c.root, "merge-base", "--is-ancestor", head, packet_rev):
        c.error("E_STALE_HEAD", f"head_commit {head} is not an ancestor of the packet revision {packet_rev}")
        return base, head
    newer = [
        p for p in git(c.root, "diff", "--name-only", head, packet_rev).splitlines()
        if not in_packet_tree(p)
    ]
    if newer:
        c.error("E_STALE_HEAD", f"source changed after head_commit before the packet was published "
                f"({packet_rev[:12]}): {', '.join(newer[:10])}")
    if packet_rev == "HEAD":
        dirty = [
            line[3:] for line in git(c.root, "status", "--porcelain", "--untracked-files=no").splitlines()
            if not in_packet_tree(line[3:])
        ]
        if dirty:
            c.error("E_STALE_HEAD", f"working tree has uncommitted tracked changes: {', '.join(dirty[:10])}")
    return base, head


def _check_artifacts(c, manifest):
    """Map artifact id -> bytes for every artifact whose digest verifies."""
    declared = c.field(manifest, "artifacts", dict, "packet")
    verified = {}
    for ident, entry in (declared or {}).items():
        where = f"artifacts.{ident}"
        rel = c.field(entry, "path", str, where)
        digest = c.field(entry, "sha256", str, where)
        if rel is None or digest is None:
            continue
        target = (c.packet_dir / rel).resolve()
        if PurePosixPath(rel).is_absolute() or not target.is_relative_to(c.packet_dir):
            c.error("E_ARTIFACT_PATH", f"{where}: {rel!r} escapes the packet directory")
            continue
        if not target.is_file():
            c.error("E_ARTIFACT_MISSING", f"{where}: {rel} does not exist")
            continue
        data = target.read_bytes()
        if sha256(data) != digest:
            c.error("E_ARTIFACT_DIGEST", f"{where}: sha256 mismatch for {rel}")
            continue
        verified[ident] = data
    c.declared_artifacts = set(declared or {})
    return verified


def _artifact_ref(c, ident, where):
    if ident not in c.declared_artifacts:
        c.error("E_REF", f"{where}: unknown artifact {ident!r}")
        return False
    return True


def _check_capabilities(c, manifest):
    caps = c.field(manifest, "capabilities", dict, "packet") or {}
    result = {}
    for name in CAPABILITIES:
        if not isinstance(caps.get(name), bool):
            c.error("E_SCHEMA", f"capabilities.{name}: expected boolean enabled flag")
        else:
            result[name] = caps[name]
    return result


def _check_configuration(c, manifest, head):
    config = c.field(manifest, "configuration", dict, "packet")
    if config is None:
        return
    if not config:
        c.error("E_SCHEMA", "packet.configuration: expected at least one configuration digest")
    for path, digest in config.items():
        try:
            actual = sha256(git_bytes(c.root, "show", f"{head}:{path}"))
        except GitError:
            c.error("E_CONFIG_DIGEST", f"configuration path {path} is absent at head_commit")
            continue
        if digest != actual:
            c.error("E_CONFIG_DIGEST", f"configuration {path}: sha256 {digest} != {actual} at head_commit")


def _status(c, entry, where, capabilities):
    status = c.field(entry, "status", str, where)
    if status is None:
        return None
    if status not in STATUSES:
        c.error("E_SCHEMA", f"{where}.status: {status!r} is not one of {sorted(STATUSES)}")
        return None
    if status == "NOT_APPLICABLE":
        cap = entry.get("requires_capability")
        if cap not in CAPABILITIES or capabilities.get(cap) is not False:
            c.error(
                "E_NOT_APPLICABLE",
                f"{where}: NOT_APPLICABLE requires 'requires_capability' naming a capability declared disabled",
            )
    return status


def _check_result_entry(c, entry, where, artifacts, capabilities, kind=None):
    status = _status(c, entry, where, capabilities)
    c.field(entry, "command", str, where)
    exit_code = c.field(entry, "exit_code", int, where)
    art = c.field(entry, "artifact", str, where)
    if art is not None and _artifact_ref(c, art, where) and art in artifacts and status == "PASS":
        observed = observed_status(kind, artifacts[art])
        if observed:
            c.error(
                "E_SEMANTIC_MISMATCH",
                f"{where}: declared PASS but artifact shows {observed[0]} ({observed[1]})",
            )
    if status == "PASS" and exit_code is not None and exit_code != 0:
        c.error("E_EXIT_STATUS", f"{where}: declared PASS with exit code {exit_code}")
    return status


def _check_analyses(c, manifest, base, head, artifacts, capabilities, results):
    analyses = c.items(manifest, "analyses", "packet")
    present = {"pre": set(), "post": set()}
    by_id = {}
    for index, entry in enumerate(analyses):
        where = f"analyses[{entry.get('id', index)}]"
        ident = c.field(entry, "id", str, where)
        phase = c.field(entry, "phase", str, where)
        kind = c.field(entry, "kind", str, where)
        if phase not in (None, "pre", "post"):
            c.error("E_SCHEMA", f"{where}.phase: expected 'pre' or 'post'")
            phase = None
        if kind is not None and kind not in ANALYSIS_KINDS:
            c.error("E_SCHEMA", f"{where}.kind: {kind!r} is not one of {sorted(ANALYSIS_KINDS)}")
            kind = None
        analyzed = c.field(entry, "analyzed_commit", str, where)
        expected = {"pre": base, "post": head}.get(phase)
        if analyzed is not None and expected and analyzed != expected:
            c.error(
                "E_STALE_ANALYSIS",
                f"{where}: {phase} analysis ran against {analyzed}, expected {phase == 'pre' and 'base' or 'head'}_commit {expected}",
            )
        status = _check_result_entry(c, entry, where, artifacts, capabilities, kind)
        if ident is not None and status is not None:
            results[ident] = status
            by_id[ident] = entry
        if phase and kind and status != "NOT_APPLICABLE":
            present[phase].add(kind)
    for phase, kinds in REQUIRED_ANALYSES.items():
        for kind in kinds:
            if kind not in present[phase]:
                code = "E_PRE_MISSING" if phase == "pre" else "E_POST_MISSING"
                c.error(code, f"no {phase}-edit {kind} report recorded")

    index = c.field(manifest, "index", dict, "packet")
    if index is not None:
        c.field(index, "mode", str, "index")
        analyzed = c.field(index, "analyzed_commit", str, "index")
        if analyzed is not None and head and analyzed != head:
            c.error("E_STALE_INDEX", f"index analyzed_commit {analyzed} != head_commit {head}")
        drift = c.field(index, "drift", str, "index")
        if drift is not None:
            entry = by_id.get(drift)
            if entry is None or entry.get("kind") != "drift" or entry.get("phase") != "post":
                c.error("E_REF", f"index.drift {drift!r} must name a post-edit drift analysis")
            elif results.get(drift) != "PASS":
                c.error("E_STALE_INDEX", f"index freshness drift {drift!r} is {results.get(drift)}, not PASS")


def _check_commands(c, manifest, artifacts, capabilities, results):
    for index, entry in enumerate(c.items(manifest, "commands", "packet")):
        where = f"commands[{entry.get('id', index)}]"
        ident = c.field(entry, "id", str, where)
        status = _check_result_entry(c, entry, where, artifacts, capabilities)
        if ident is not None and status is not None:
            if ident in results:
                c.error("E_SCHEMA", f"{where}: id collides with an analysis id")
            results[ident] = status


def _check_disclosure(c, manifest, results, report):
    limits = c.items(manifest, "coverage_limitations", "packet")
    disclosed = set()
    for index, entry in enumerate(limits):
        where = f"coverage_limitations[{index}]"
        ref = c.field(entry, "ref", str, where)
        c.field(entry, "description", str, where)
        if ref is not None:
            if ref not in results:
                c.error("E_REF", f"{where}: unknown analysis/command {ref!r}")
            disclosed.add(ref)
    for ident, status in results.items():
        if status in DISCLOSE and ident not in disclosed:
            c.error("E_UNDISCLOSED", f"{ident} is {status} but not listed in coverage_limitations")
    report["coverage_limitations"] = limits


def _check_cases(c, manifest, results, capabilities):
    required = c.field(manifest, "required_cases", list, "packet")
    cases = c.items(manifest, "cases", "packet")
    if required is not None and (not required or not all(isinstance(r, str) for r in required)):
        c.error("E_SCHEMA", "packet.required_cases: expected non-empty list of case ids")
        required = None
    counts = {}
    by_id = {}
    for index, entry in enumerate(cases):
        where = f"cases[{entry.get('id', index)}]"
        ident = c.field(entry, "id", str, where)
        c.field(entry, "title", str, where)
        status = _status(c, entry, where, capabilities)
        evidence = c.field(entry, "evidence", list, where)
        if status is None or ident is None:
            continue
        if ident in results:
            c.error("E_SCHEMA", f"{where}: case id collides with an analysis/command id")
        by_id[ident] = status
        counts[status] = counts.get(status, 0) + 1
        if status == "NOT_APPLICABLE":
            continue
        if not evidence:
            c.error("E_SCHEMA", f"{where}.evidence: expected non-empty list of analysis/command ids")
            continue
        for ref in evidence:
            if ref not in results:
                c.error("E_REF", f"{where}: unknown evidence {ref!r}")
            elif status == "PASS" and results[ref] != "PASS":
                c.error("E_CASE_EVIDENCE", f"{where}: PASS cites {ref} whose status is {results[ref]}")
    for ident in required or []:
        if ident not in by_id:
            c.error("E_CASE_MISSING", f"required case {ident} has no result")
        elif by_id[ident] not in ("PASS", "NOT_APPLICABLE"):
            c.error("E_CASE_NOT_PASS", f"required case {ident} is {by_id[ident]}")
    # Aggregates are never trusted; a declared total must match the cases.
    totals = manifest.get("case_totals")
    if totals is not None:
        if not isinstance(totals, dict):
            c.error("E_SCHEMA", "packet.case_totals: expected object")
        else:
            for status, value in totals.items():
                if counts.get(status, 0) != value:
                    c.error(
                        "E_AGGREGATE_MISMATCH",
                        f"case_totals.{status}={value!r} but cases contain {counts.get(status, 0)}",
                    )
            for status, value in counts.items():
                if status not in totals:
                    c.error("E_AGGREGATE_MISMATCH", f"case_totals omits {value} {status} case(s)")
    return by_id


def in_packet_tree(path):
    """docs/audits/<ISSUE>/... files are evidence, gated per packet in changed mode."""
    parts = PurePosixPath(path).parts
    return parts[:2] == ("docs", "audits") and len(parts) >= 4


def _changed_paths(c, base, head):
    """Committed base..head changes plus untracked files, excluding audit packets."""
    committed = git(c.root, "diff", "--name-only", "--no-renames", base, head).splitlines()
    untracked = git(
        c.root, "ls-files", "--others", "--exclude-standard"
    ).splitlines()
    return [
        path
        for path in committed + [f"untracked:{p}" for p in untracked]
        if not in_packet_tree(path.removeprefix("untracked:"))
    ]


def _check_scope(c, manifest, changed):
    scope = c.field(manifest, "scope", dict, "packet")
    if scope is None:
        return
    predicted = c.field(scope, "predicted_paths", list, "scope") or []
    predicted_symbols = set(c.field(scope, "predicted_symbols", list, "scope") or [])
    actual_symbols = c.field(scope, "actual_symbols", list, "scope") or []
    explained_paths, explained_symbols = set(), set()
    for index, entry in enumerate(c.items(scope, "explained_changes", "scope")):
        where = f"scope.explained_changes[{index}]"
        c.field(entry, "reason", str, where)
        if isinstance(entry.get("path"), str):
            explained_paths.add(entry["path"])
        elif isinstance(entry.get("symbol"), str):
            explained_symbols.add(entry["symbol"])
        else:
            c.error("E_SCHEMA", f"{where}: expected 'path' or 'symbol'")
    c.predicted_paths = predicted
    for path in changed:
        bare = path.removeprefix("untracked:")
        if not path_matches(bare, predicted) and bare not in explained_paths:
            kind = "untracked file" if path.startswith("untracked:") else "changed file"
            c.error("E_SCOPE_PATH", f"{kind} {bare} is outside the predicted scope and unexplained")
    for symbol in actual_symbols:
        if symbol not in predicted_symbols and symbol not in explained_symbols:
            c.error("E_SCOPE_SYMBOL", f"changed symbol {symbol} was not predicted and is unexplained")


def _check_labels(c, manifest, changed, report):
    touched = sorted(
        {p.removeprefix("untracked:") for p in changed if path_matches(p.removeprefix("untracked:"), LABEL_PATTERNS)}
    )
    declared = {}
    for index, entry in enumerate(c.items(manifest, "label_changes", "packet")):
        where = f"label_changes[{index}]"
        path = c.field(entry, "path", str, where)
        c.field(entry, "reason", str, where)
        if {"approved", "accepted", "approved_by"} & set(entry):
            c.error("E_LABEL_SELF_APPROVAL", f"{where}: label approval cannot be recorded in the packet")
        if path is not None:
            declared[path] = entry
    for path in touched:
        if path not in declared:
            c.error("E_LABEL_UNDECLARED", f"fixture/label change {path} is not declared in label_changes")
    for path in declared:
        if path not in touched:
            c.warn("W_LABEL_UNCHANGED", f"label_changes lists {path}, which did not change")
    report["requires_human_review"] = touched


def _check_mutations(c, manifest, head, results):
    for index, entry in enumerate(c.items(manifest, "mutations", "packet")):
        where = f"mutations[{entry.get('id', index)}]"
        c.field(entry, "id", str, where)
        c.field(entry, "description", str, where)
        path = c.field(entry, "path", str, where)
        killed = c.field(entry, "killed", bool, where)
        killer = c.field(entry, "killed_by", str, where)
        restored = c.field(entry, "restored_sha256", str, where)
        if killer is not None and killer not in results:
            c.error("E_REF", f"{where}: killed_by {killer!r} is not a recorded command/analysis")
        if killed is False:
            c.error("E_MUTATION_SURVIVED", f"{where}: mutation was not killed")
        if path is None:
            continue
        if not path_matches(path, getattr(c, "predicted_paths", [])):
            c.error("E_MUTATION_ENVELOPE", f"{where}: {path} is outside the predicted scope")
        if head and restored is not None:
            try:
                actual = sha256(git_bytes(c.root, "show", f"{head}:{path}"))
            except GitError:
                c.error("E_MUTATION_RESTORE", f"{where}: {path} is absent at head_commit")
                continue
            if restored != actual:
                c.error("E_MUTATION_RESTORE", f"{where}: restored sha256 does not match {path} at head_commit")


def _check_findings(c, manifest):
    for index, entry in enumerate(c.items(manifest, "findings", "packet")):
        where = f"findings[{entry.get('id', index)}]"
        c.field(entry, "id", str, where)
        c.field(entry, "summary", str, where)
        origin = c.field(entry, "origin", str, where)
        blocking = c.field(entry, "blocking", bool, where)
        classification = entry.get("classification")
        if origin not in (None, "introduced", "preexisting"):
            c.error("E_SCHEMA", f"{where}.origin: expected 'introduced' or 'preexisting'")
        if classification not in FINDING_CLASSES:
            c.error("E_FINDING_UNCLASSIFIED", f"{where}: classification must be one of {sorted(FINDING_CLASSES)}")
            continue
        if origin == "introduced" and blocking and classification != "fixed":
            c.error("E_FINDING_BLOCKING", f"{where}: introduced blocking finding is {classification}, not fixed")
        if classification != "fixed" and not (
            isinstance(entry.get("link"), str) and entry["link"].strip()
        ):
            c.error("E_FINDING_UNCLASSIFIED", f"{where}: unfixed finding needs a 'link' to its tracking record")


def _check_debt(c, manifest, results):
    debt = c.items(manifest, "baseline_debt", "packet")
    for index, entry in enumerate(debt):
        where = f"baseline_debt[{index}]"
        c.field(entry, "id", str, where)
        c.field(entry, "description", str, where)
        ref = c.field(entry, "evidence", str, where)
        if ref is not None and ref not in results and ref not in c.declared_artifacts:
            c.error("E_REF", f"{where}: unknown evidence {ref!r}")
    return debt


def _check_review(c, manifest):
    review = c.field(manifest, "review", dict, "packet")
    if review is None:
        return None
    status = c.field(review, "status", str, "review")
    independent = c.field(review, "independent", bool, "review")
    reviewer = review.get("reviewer")
    if status is not None and status not in REVIEW_STATUSES:
        c.error("E_SCHEMA", f"review.status: expected one of {sorted(REVIEW_STATUSES)}")
    if reviewer is not None and not (isinstance(reviewer, str) and reviewer.strip()):
        c.error("E_SCHEMA", "review.reviewer: expected non-empty string or null")
    if status in ("accepted", "changes-requested"):
        if not reviewer:
            c.error("E_REVIEW", f"review.status {status} requires a named reviewer")
        evidence = review.get("evidence")
        if not isinstance(evidence, str) or not _artifact_ref(c, evidence, "review.evidence"):
            c.error("E_REVIEW", f"review.status {status} requires an evidence artifact")
    if independent is False:
        c.field(review, "limitation", str, "review")
    return {"status": status, "reviewer": reviewer, "independent": independent}


def _check_rollback(c, manifest):
    rollback = c.field(manifest, "rollback", dict, "packet")
    if rollback is None:
        return
    c.field(rollback, "procedure", str, "rollback")
    document = c.field(rollback, "document", str, "rollback")
    if document is not None:
        _artifact_ref(c, document, "rollback.document")


def validate_changed(root, base):
    """Validate every audit packet touched between merge-base(base, HEAD) and HEAD."""
    root = Path(root).resolve()
    merge_base = git(root, "merge-base", base, "HEAD").strip()
    entries = [
        line.split("\t", 1)
        for line in git(root, "diff", "--name-status", "--no-renames", merge_base, "HEAD").splitlines()
    ]
    names, stray, rewritten = set(), set(), {}
    for status, path in entries:
        parts = PurePosixPath(path).parts
        if parts[:2] != ("docs", "audits") or len(parts) < 3:
            continue
        if len(parts) == 3:
            stray.add(path)
            continue
        names.add(parts[2])
        if status != "A":
            rewritten.setdefault(parts[2], []).append(f"{status} {path}")
    packets = []
    for name in sorted(names):
        packet_dir = root / AUDITS_DIR / name
        errors = [
            {"code": "E_LEGACY_EVIDENCE_CHANGED", "message": f"frozen legacy evidence changed: {change}"}
            for change in rewritten.get(name, [])
        ] if name in LEGACY_PACKETS else []
        if (packet_dir / PACKET_FILE).is_file() or name not in LEGACY_PACKETS:
            report = validate_packet(root, packet_dir)
            # Adding a manifest to a legacy directory does not unfreeze its old evidence.
            report["errors"].extend(errors)
            report["packet_valid"] = report["packet_valid"] and not errors
            packets.append(report)
            continue
        packets.append({
            "packet": f"{AUDITS_DIR}/{name}",
            "legacy": True,
            "packet_valid": False if errors else None,
            "errors": errors,
        })
    return {
        "schema": REPORT_SCHEMA,
        "mode": "changed",
        "base": merge_base,
        "rev": git(root, "rev-parse", "HEAD").strip(),
        "valid": all(p["packet_valid"] is not False for p in packets),
        "packets": packets,
        "unvalidated_audit_files": sorted(stray),
    }


def _print_packet(report, out):
    if report.get("legacy"):
        print(f"LEGACY {report['packet']}: pre-schema packet, not validated (no PASS claimed)", file=out)
        for err in report["errors"]:
            print(f"  error {err['code']}: {err['message']}", file=out)
        return
    verdict = "VALID" if report["packet_valid"] else "INVALID"
    print(f"{verdict} {report['packet']} at {report['rev']}", file=out)
    for err in report["errors"]:
        print(f"  error {err['code']}: {err['message']}", file=out)
    for warn in report["warnings"]:
        print(f"  warning {warn['code']}: {warn['message']}", file=out)
    for item in report["non_pass_results"]:
        print(f"  limitation {item['source']} {item['id']}: {item['status']}", file=out)
    for path in report["requires_human_review"]:
        print(f"  human review required: {path}", file=out)
    review = report.get("review") or {}
    print(
        f"  review: {review.get('status')} (reviewer={review.get('reviewer')}, "
        f"independent={review.get('independent')}); acceptance is not decided here",
        file=out,
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=".", help="repository root (default: .)")
    parser.add_argument("--report", help="also write the JSON report to this path")
    parser.add_argument("--json", action="store_true", help="print the JSON report")
    sub = parser.add_subparsers(dest="command", required=True)
    one = sub.add_parser("validate", help="validate one packet directory")
    one.add_argument("packet_dir")
    changed = sub.add_parser("changed", help="validate packets touched since BASE")
    changed.add_argument("--base", required=True, help="base ref or commit")
    args = parser.parse_args(argv)

    root = Path(args.repo).resolve()
    try:
        if args.command == "validate":
            report = validate_packet(root, Path(args.packet_dir).resolve())
            ok = report["packet_valid"]
        else:
            report = validate_changed(root, args.base)
            ok = report["valid"]
    except GitError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.report:
        Path(args.report).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if args.json:
        print(json.dumps(report, indent=2))
    elif args.command == "validate":
        _print_packet(report, sys.stdout)
    else:
        print(f"audit packets changed since {report['base']}: {len(report['packets'])}")
        for packet in report["packets"]:
            _print_packet(packet, sys.stdout)
        for path in report["unvalidated_audit_files"]:
            print(f"UNVALIDATED {path}: not inside an issue packet directory")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
