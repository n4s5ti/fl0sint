#!/usr/bin/env python3
"""Second local caller: consumes a standalone bundle offline (stdlib only, no services)."""
import base64, hashlib, json, sys
out = sys.argv[1]
bundle = json.load(open(f"{out}/bundle.json"))
assert bundle["schema_version"] == "standalone-scraper/1.0"
rows = []
for o in bundle["outcomes"]:
    row = {"key": o["key"], "status": o["status"],
           "links": [l["value"] for l in o["links"]],
           "candidates": [(c["kind"], c["value"], c["disposition"]) for c in o["candidates"]]}
    if o["source_reference"]:
        art = o["source_reference"]["artifact"]; dg = art["content_digest"]
        body = open(f"{out}/source-artifacts/objects/{dg[:2]}/{dg}.body", "rb").read()
        assert hashlib.sha256(body).hexdigest() == dg, "digest mismatch"
        tok = o["source_reference"]["source_proof"].split(".")[1]
        proof = json.loads(base64.urlsafe_b64decode(tok + "=" * (-len(tok) % 4)))
        assert proof["context"]["operation_id"] == bundle["operation_id"]
        spans = o["source_reference"]["spans"]
        if spans:
            s = spans[0]
            row["first_span_bytes"] = body[s["byte_start"]:s["byte_end"]].decode("utf-8", "replace")[:60]
    rows.append(row)
print(json.dumps({"consumed": len(rows), "operation": bundle["operation_id"], "rows": rows[:4]}, indent=1)[:1500])
