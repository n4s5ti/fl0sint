"""DEF-90 mutation check: each mutant must make the focused validator tests fail."""
import glob
import hashlib
import json
import os
import shutil
import subprocess
import sys

W = "/home/n4s5ti/Documents/dev/fl0sint-def90-packet"
TARGET = f"{W}/scripts/audit_packet.py"
PKG = glob.glob(f"{W}/*-enrichers")[0]
MUTANTS = [
    ("M1", "blast payload semantics ignored", 'if kind == "blast" and "risk" in payload:', 'if kind == "blastX" and "risk" in payload:'),
    ("M2", "post-head source commits not stale", "    if newer:\n", "    if False:\n"),
    ("M3", "untracked files excluded from scope", 'committed + [f"untracked:{p}" for p in untracked]', "committed"),
    ("M4", "SKIP accepted for required cases", 'not in ("PASS", "NOT_APPLICABLE"):', 'not in ("PASS", "NOT_APPLICABLE", "SKIP"):'),
    ("M5", "NOT_APPLICABLE ignores capability flag", "capabilities.get(cap) is not False", "False"),
    ("M6", "analysis revision not checked", "if analyzed is not None and expected and analyzed != expected:", "if False:"),
    ("M7", "label self-approval allowed", 'if {"approved", "accepted", "approved_by"} & set(entry):', "if False:"),
    ("M8", "pre-edit blast not required", 'REQUIRED_ANALYSES = {"pre": ("blast",),', 'REQUIRED_ANALYSES = {"pre": (),'),
    ("M9", "case_totals not compared", "if counts.get(status, 0) != value:", "if False:"),
    ("M10", "PASS evidence status not checked", 'elif status == "PASS" and results[ref] != "PASS":', "elif False:"),
    ("M11", "unrecognized payload fails open", "if not isinstance(payload, dict) or not RECOGNIZED[kind](payload):", "if not isinstance(payload, dict) or False:"),
    ("M12", "legacy evidence rewrite allowed", '        if status != "A":\n', "        if False:\n"),
    ("M13", "waived cases hidden from report", "for ident, status in sorted({**results, **cases}.items())", "for ident, status in sorted(results.items())"),
    ("M14", "root fixtures not label-gated", 'LABEL_PATTERNS = ("fixtures/*", "*/fixtures/*")', 'LABEL_PATTERNS = ("*/fixtures/*",)'),
    ("M15", "legacy freeze dropped when a manifest is added", '            report["errors"].extend(errors)\n', ""),
    ("M16", "case/result id collision allowed", "        if ident in results:\n            c.error(\"E_SCHEMA\", f\"{where}: case id collides", "        if False:\n            c.error(\"E_SCHEMA\", f\"{where}: case id collides"),
    ("M17", "staleness measured to HEAD, not the packet revision", 'packet_rev = published or "HEAD"', 'packet_rev = "HEAD"'),
    ("M18", "rename detection hides source moved into packet", '"diff", "--name-only", "--no-renames", head, packet_rev', '"diff", "--name-only", head, packet_rev'),
]
original = open(TARGET, "rb").read()
digest = hashlib.sha256(original).hexdigest()
backup = "/tmp/def90/audit_packet.py.orig"
shutil.copyfile(TARGET, backup)
env = {**os.environ, "UV_PROJECT_ENVIRONMENT": "/home/n4s5ti/Documents/dev/fl0sint/.venv"}
results = []
for ident, description, old, new in MUTANTS:
    text = original.decode()
    assert text.count(old) == 1, (ident, text.count(old))
    open(TARGET, "w").write(text.replace(old, new))
    proc = subprocess.run(
        ["uv", "run", "--no-sync", "pytest", "tests/enrichers/test_audit_packet.py", "-q", "-p", "no:cacheprovider", "-x"],
        cwd=PKG, env=env, capture_output=True, text=True, timeout=600,
    )
    shutil.copyfile(backup, TARGET)
    restored = subprocess.run(["cmp", backup, TARGET]).returncode == 0
    failing = [l for l in proc.stdout.splitlines() if l.startswith("FAILED")]
    results.append({
        "id": ident, "description": description, "replaced": old, "with": new,
        "pytest_exit": proc.returncode, "killed": proc.returncode != 0,
        "failing_tests": failing, "restored_cmp_equal": restored,
    })
    print(ident, "killed" if proc.returncode else "SURVIVED", failing[:1], "restored" if restored else "NOT RESTORED")
final = hashlib.sha256(open(TARGET, "rb").read()).hexdigest()
json.dump({"target": "scripts/audit_packet.py", "sha256_before": digest, "sha256_after": final,
           "command": "uv run --no-sync pytest tests/enrichers/test_audit_packet.py -q -p no:cacheprovider -x",
           "mutations": results}, open("/tmp/def90/mutation-check.json", "w"), indent=2)
sys.exit(0 if all(r["killed"] and r["restored_cmp_equal"] for r in results) and final == digest else 1)
