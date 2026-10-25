"""Second local caller: consume example bundles through the installed wheel."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys

from flowsint_execution import acquisition as a

root = Path(sys.argv[1])
request = a.parse_request((root / "request.json").read_bytes())
success = a.parse_bundle((root / "success.json").read_bytes())
failed = a.parse_bundle((root / "failed.json").read_bytes())
assert request == success.request
assert a.parse_bundle(a.serialize_bundle(success)) == success
assert success.outcomes[0].status == a.OutcomeStatus.SUCCESS_WITH_OUTPUT
assert failed.outcomes[0].status == a.OutcomeStatus.INVALID_INPUT
assert failed.outcomes[0].diagnostic.code == "invalid_input"
assert failed.request.operation_id != success.request.operation_id
candidate = success.candidates[0]
assert candidate.disposition == "unreviewed"
evidence = next(e for e in success.evidence_list if e.evidence_id == candidate.evidence_id)
span = next(s for s in success.spans if s.span_id == evidence.span_id)
artifact = next(x for x in success.artifacts if x.artifact_id == span.artifact_id)
body = (root / artifact.locator).read_bytes()
assert hashlib.sha256(body).hexdigest() == artifact.content_digest
assert len(body) == artifact.byte_length
assert span.field_pointer == "/name" and json.loads(body)["name"] == "Example Organization"
assert candidate.occurrence_id == evidence.occurrence_id == success.outcomes[0].occurrence_id
absent = {m: importlib.util.find_spec(m) is None for m in ("neo4j", "celery", "sqlalchemy")}
assert all(absent.values())
assert "flowsint_core" not in sys.modules
assert "site-packages" in a.__file__
assert not any(k in os.environ for k in ("AUTH_SECRET", "REDIS_URL", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"))
print(json.dumps({"result": "PASS", "module": a.__file__, "absent": absent,
                  "source_sha256": artifact.content_digest, "source_value": json.loads(body)["name"],
                  "candidate_disposition": candidate.disposition, "failure": failed.outcomes[0].status.value,
                  "input_occurrence": candidate.occurrence_id}, indent=2))
