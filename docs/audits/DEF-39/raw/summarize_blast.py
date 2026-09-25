#!/usr/bin/env python3
"""Summarize a pip3r blast JSON file without dumping symbol content."""
import json, sys, os

def summarize(path):
    out = {"file": os.path.basename(path), "bytes": os.path.getsize(path)}
    raw = open(path).read()
    if not raw.strip():
        out["empty"] = True
        return out
    try:
        d = json.loads(raw)
    except Exception as e:
        out["parse_error"] = str(e)
        out["head"] = raw[:300]
        return out
    out["top_level_keys"] = list(d.keys()) if isinstance(d, dict) else type(d).__name__
    if not isinstance(d, dict):
        return out
    for k in ("contentType", "schema", "version", "schemaVersion", "$schema", "mode", "error",
              "symbol", "repo", "lbugPath", "rootPath", "direction", "risk", "completeness",
              "completenessReasons", "indexStatus", "warnings", "degraded", "fallback", "maxDepth", "depth"):
        if k in d:
            out[k] = d[k]
    if "target" in d and isinstance(d["target"], dict):
        out["target"] = {k: d["target"].get(k) for k in ("id", "name", "filePath", "nodeType", "startLine", "endLine")}
    if "summary" in d:
        out["summary"] = d["summary"]
    if "symbols" in d:
        out["symbols"] = [{k: s.get(k) for k in ("id", "nodeType")} for s in d["symbols"]]
    if "edges" in d:
        out["edges"] = d["edges"]
    if "files" in d:
        out["files"] = [{"filePath": f.get("filePath"), "count": f.get("count")} for f in d["files"]]
    if "affected_modules" in d:
        out["affected_modules"] = d["affected_modules"]
    if "affected_processes" in d:
        out["affected_processes"] = d["affected_processes"]
    if "byDepth" in d:
        out["byDepth"] = {k: {"label": v.get("label"), "symbols": [s.get("id") for s in v.get("symbols", [])]} for k, v in d["byDepth"].items()}
    return out

if __name__ == "__main__":
    res = [summarize(p) for p in sys.argv[1:]]
    print(json.dumps(res if len(res) > 1 else res[0], indent=1))
