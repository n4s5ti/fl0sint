"""DEF-48 mutation check: each mutant must make test_release_smoke.py fail."""
import glob, hashlib, json, os, shutil, subprocess, sys
W = "/home/n4s5ti/Documents/dev/fl0sint-def48-smoke"
TARGET = f"{W}/scripts/release_smoke.py"
PKG = glob.glob(f"{W}/*-enrichers")[0]
MUTANTS = [
 ("M1", "observation lineage not checked", 'if item.get("occurrence_id") != occurrence or item.get("input_ref") != proof.get("input_ref"):', "if False:"),
 ("M2", "proof context not checked", "            if context.get(field) != value:", "            if False:"),
 ("M4", "retained digest not checked", 'if sha256(body) != digest or len(body) != artifact.get("byte_length"):', "if False:"),
 ("M5", "error without diagnostic accepted", "if not (outcome.get(\"diagnostic\") or {}).get(\"code\"):", "if False:"),
 ("M6", "jsonl not compared", "if lines is not None and lines != expected_lines:", "if False:"),
 ("M7", "report render not compared", "if render is not None and report != render(bundle):", "if False:"),
 ("M8", "fixture digest not compared", 'if artifact["content_digest"] != final["sha256"]:', "if False:"),
 ("M9", "outcome status/code not compared", 'if outcome["status"] != status or (outcome["diagnostic"] or {}).get("code") != code:', "if False:"),
 ("M10", "forbidden imports allowed", 'elif event["event"] == "import" and event["module"] in FORBIDDEN_MODULES:', "elif False:"),
 ("M11", "silent audit accepted", 'if not any(event["event"] == "import" for event in events):', "if False:"),
 ("M12", "baseline debt dropped", '"baseline_debt": sorted(current & known),', '"baseline_debt": [],'),
 ("M13", "new failures pass", '"status": "FAIL" if current - known else "PASS",', '"status": "PASS",'),
 ("M14", "log lines parsed as failures", r'FAILURE_LINE = re.compile(r"^(?:FAILED|ERROR) ([^\s:]+\.py(?:::\S+)?)(?:\s|$)")', r'FAILURE_LINE = re.compile(r"^(?:FAILED|ERROR)\s+(\S+?\.py\S*)")'),
 ("M15", "span bounds/href not checked", "if urljoin(artifact.get(\"final_url\") or \"\", raw) != value:", "if False:"),
 ("M16", "non-link value not bound to span", 'value.removeprefix("mailto:").casefold() in raw.casefold()):', 'True):'),
 ("M17", "outcome text not bound to spans", 'if outcome.get("text") != ([emitted_text] if emitted_text else []):', "if False:"),
 ("M18", "source_url not bound to final_url", 'if item.get("source_url") != artifact.get("final_url"):', "if False:"),
 ("M19", "unrecorded link/candidate allowed", 'if item.get("observation_id") not in observation_ids:', "if False:"),
 ("M20", "failure may carry source reference", 'if outcome.get("source_reference") is not None:', "if False:"),
 ("M21", "digest shape not validated", "if not (isinstance(digest, str) and SHA256_HEX.fullmatch(digest)", "if False and (isinstance(digest, str) and SHA256_HEX.fullmatch(digest)"),
 ("M22", "malformed record spans not guarded", 'bad("E_SPAN", f"{where}: artifact record spans are malformed")\n            continue', 'pass'),
]
original = open(TARGET, "rb").read(); digest = hashlib.sha256(original).hexdigest()
backup = "/tmp/def48/release_smoke.py.orig"; shutil.copyfile(TARGET, backup)
env = {**os.environ, "AUTH_SECRET": "x", "REDIS_URL": "redis://127.0.0.1:1"}
results = []
for ident, desc, old, new in MUTANTS:
    text = original.decode(); assert text.count(old) == 1, (ident, text.count(old))
    open(TARGET, "w").write(text.replace(old, new))
    proc = subprocess.run([f"{W}/.venv/bin/python", "-m", "pytest", "tests/enrichers/test_release_smoke.py", "-q", "-p", "no:cacheprovider", "-x"], cwd=PKG, env=env, capture_output=True, text=True, timeout=600)
    shutil.copyfile(backup, TARGET)
    restored = subprocess.run(["cmp", backup, TARGET]).returncode == 0
    failing = [l for l in proc.stdout.splitlines() if l.startswith("FAILED")]
    results.append({"id": ident, "description": desc, "replaced": old, "with": new, "pytest_exit": proc.returncode, "killed": proc.returncode != 0, "failing_tests": failing, "restored_cmp_equal": restored})
    print(ident, "killed" if proc.returncode else "SURVIVED", failing[:1])
final = hashlib.sha256(open(TARGET, "rb").read()).hexdigest()
json.dump({"target": "scripts/release_smoke.py", "sha256_before": digest, "sha256_after": final, "command": ".venv/bin/python -m pytest tests/enrichers/test_release_smoke.py -q -p no:cacheprovider -x", "mutations": results}, open("/tmp/def48/mutation-check.json", "w"), indent=2)
sys.exit(0 if all(r["killed"] and r["restored_cmp_equal"] for r in results) and final == digest else 1)
