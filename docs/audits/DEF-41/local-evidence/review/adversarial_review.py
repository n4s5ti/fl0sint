import hashlib
import json
from datetime import datetime, timezone

from flowsint_execution import acquisition as a
from flowsint_execution.models import OutcomeStatus as CanonicalStatus
from flowsint_execution.models import RedactedDiagnostic, canonical_input_hash

NOW = datetime(2026, 9, 26, tzinfo=timezone.utc)


def diag(code="unknown"):
    return RedactedDiagnostic(code=code, safe_message="safe diagnostic", retryable=False)


def inp(occurrence_id, value="same"):
    return a.InputOccurrence(
        occurrence_id=occurrence_id,
        input_ref=canonical_input_hash(value),
        type_tag="text",
        value=value,
        lineage=a.ImportedLineage(
            run_id="run", step_id="step", attempt=1,
            input_index=0 if occurrence_id == "o1" else 1,
            seed_entity_id="seed",
        ),
    )


def request(inputs=(inp("o1"), inp("o2"))):
    return a.AcquisitionRequest(
        operation_id="op", caller_id="caller", scope="scope",
        capability_digest="a" * 64, inputs=inputs,
        allocation=a.Resources(requests=2, bytes=20),
        parent_operation_id="parent", need_ref="need",
        retention_policy_digest="b" * 64,
    )


def graph_bundle():
    req = request()
    artifact = a.ArtifactReference(
        artifact_id="artifact", snapshot_id="snapshot", content_digest="c" * 64,
        byte_length=10, locator="store:artifact", source_family="fixture",
        origin="fixture", retrieved_at=NOW,
        requested_url="https://example.test/requested",
        final_url="https://example.test/final",
    )
    span = a.SpanReference(span_id="span", artifact_id="artifact", byte_start=0, byte_end=3)
    evidence = a.EvidenceReference(
        evidence_id="evidence", occurrence_id="o1", span_id="span",
        extraction_method="parser", extraction_version="1", subject_attribution="source",
    )
    candidate = a.CandidateReference(
        candidate_id="candidate", occurrence_id="o1", evidence_id="evidence",
    )
    success = a.OccurrenceOutcome(
        occurrence_id="o1", status=a.OutcomeStatus.SUCCESS_WITH_OUTPUT,
        underlying_status=CanonicalStatus.SUCCESS, candidate_ids=("candidate",),
        artifact_ids=("artifact",),
        completion_witness=a.CompletionWitness(reference="done", completed_at=NOW),
    )
    empty = a.OccurrenceOutcome(
        occurrence_id="o2", status=a.OutcomeStatus.VALID_NO_RESULT,
        underlying_status=CanonicalStatus.SUCCESS,
        completion_witness=a.CompletionWitness(reference="done", completed_at=NOW),
    )
    return a.build_bundle(
        request=req, outcomes=(success, empty), bundle_id="bundle", created_at=NOW,
        actual_resources=a.Resources(requests=2, bytes=10), artifacts=(artifact,), spans=(span,),
        evidence_list=(evidence,), candidates=(candidate,),
    )


def resign(data):
    payload = {key: value for key, value in data.items() if key != "origin_digest"}
    data["origin_digest"] = hashlib.sha256(
        json.dumps(payload, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return json.dumps(data, allow_nan=True)


def observe(name, call):
    try:
        result = call()
    except Exception as exc:
        print(f"{name}: RAISED {type(exc).__name__} code={getattr(exc, 'code', None)!r} secret_echo={'secret' in str(exc).lower()}")
    else:
        print(f"{name}: OK {result}")


bundle = graph_bundle()
wire = a.serialize_bundle(bundle)
restored = a.parse_bundle(wire)
print("roundtrip:", restored == bundle, restored.origin_digest == bundle.origin_digest,
      [x.occurrence_id for x in restored.request.inputs],
      [x.input_ref for x in restored.request.inputs],
      restored.candidates[0].disposition, restored.artifacts[0].final_url,
      restored.request.parent_operation_id, restored.request.need_ref,
      restored.request.inputs[0].lineage.seed_entity_id)
print("origin_digest:", bundle.origin_digest)
print("public_wire_prefix:", wire[:120])

observe("missing_allocation", lambda: a.AcquisitionRequest(
    operation_id="op", caller_id="caller", scope="scope", capability_digest="a" * 64, inputs=()
))
observe("duplicate_occurrence", lambda: request((inp("o1"), inp("o1"))))

for name, mutate in {
    "cross_owner_candidate": lambda d: d["candidates"][0].update(occurrence_id="o2"),
    "duplicate_candidate_id": lambda d: d["candidates"].append(dict(d["candidates"][0])),
    "candidate_accepted": lambda d: d["candidates"][0].update(disposition="accepted"),
    "orphan_artifact": lambda d: d["artifacts"].append(dict(d["artifacts"][0], artifact_id="orphan")),
    "one_sided_url": lambda d: d["artifacts"][0].pop("final_url"),
    "wrong_input_hash": lambda d: d["request"]["inputs"][0].update(value="changed"),
}.items():
    data = json.loads(wire)
    mutate(data)
    observe(name, lambda data=data: a.parse_bundle(resign(data)))

for name, raw in {
    "bad_utf8": b"\xff",
    "duplicate_json_key": b'{"format_version":"1.0","format_version":"1.0"}',
    "unsupported_version": json.dumps(dict(json.loads(a.serialize_request(request())), format_version="1.1")),
    "secret_extra": json.dumps(dict(json.loads(a.serialize_request(request())), secret="do-not-echo-secret")),
}.items():
    observe(name, lambda raw=raw: a.parse_request(raw))

observe("nested_unsupported_version", lambda: a.parse_bundle(json.dumps(dict(
    json.loads(wire), request=dict(json.loads(wire)["request"], format_version="1.1")
))))
observe("raw_nonfinite_json", lambda: a.parse_request(b'{"format_version":"1.0","operation_id":"op","caller_id":"caller","scope":"scope","capability_digest":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","inputs":[{"occurrence_id":"o","input_ref":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","type_tag":"json","value":NaN}],"allocation":{}}'))
accepted = bundle.candidates[0].model_copy(update={"disposition": "accepted"})
observe("copy_bypass_disposition", lambda: a.serialize_bundle(bundle.model_copy(update={"candidates": (accepted,)})))
mutated_input = request((inp("o1", {"items": [1]}),))
mutated_input.inputs[0].value["items"].append(2)
observe("nested_mutation_hash", lambda: a.serialize_request(mutated_input))
constructed_candidate = a.CandidateReference.model_construct(
    candidate_id="candidate", occurrence_id="o1", evidence_id="evidence", disposition="accepted"
)
observe("construct_bypass_disposition", lambda: a.serialize_bundle(bundle.model_copy(update={"candidates": (constructed_candidate,)})))
constructed_request = a.AcquisitionRequest.model_construct(
    format_version="1.0", operation_id="op", caller_id="caller", scope="scope", capability_digest="a" * 64,
    inputs=(), allocation={"requests": True}
)
observe("construct_bypass_request", lambda: a.serialize_request(constructed_request))

hold = a.OccurrenceOutcome(
    occurrence_id="o1", status=a.OutcomeStatus.RETENTION_HOLD,
    underlying_status=CanonicalStatus.HOLD, diagnostic=diag("retention_hold"), actual_resources=a.Resources(bytes=5),
)
hold_req = request((inp("o1"),))
hold_bundle = a.build_bundle(request=hold_req, outcomes=(hold,), bundle_id="hold", created_at=NOW,
                             actual_resources=a.Resources(bytes=5))
hold_restored = a.parse_bundle(a.serialize_bundle(hold_bundle))
print("retention_hold:", hold_restored.outcomes[0].status.value,
      hold_restored.outcomes[0].underlying_status.value,
      hold_restored.outcomes[0].candidate_ids, hold_restored.outcomes[0].artifact_ids,
      hold_restored.outcomes[0].actual_resources.bytes)

good = a.Suboutcome(
    child_id="good", status=a.OutcomeStatus.SUCCESS_WITH_OUTPUT, underlying_status=CanonicalStatus.SUCCESS,
    candidate_ids=("candidate",), artifact_ids=("artifact",),
    completion_witness=a.CompletionWitness(reference="done", completed_at=NOW),
)
held = a.Suboutcome(child_id="held", status=a.OutcomeStatus.RETENTION_HOLD,
                    underlying_status=CanonicalStatus.HOLD, diagnostic=diag("retention_hold"))
partial = a.OccurrenceOutcome(occurrence_id="o1", status=a.OutcomeStatus.PARTIAL,
                              candidate_ids=("candidate",), artifact_ids=("artifact",), diagnostic=diag("partial"),
                              suboutcomes=(good, held))
partial_empty = a.OccurrenceOutcome(occurrence_id="o2", status=a.OutcomeStatus.VALID_NO_RESULT,
                                    completion_witness=a.CompletionWitness(reference="done", completed_at=NOW))
partial_bundle = a.build_bundle(request=request(), outcomes=(partial, partial_empty), bundle_id="partial", created_at=NOW,
                                 actual_resources=a.Resources(bytes=10), artifacts=bundle.artifacts, spans=bundle.spans,
                                 evidence_list=bundle.evidence_list, candidates=bundle.candidates)
parsed_partial = a.parse_bundle(a.serialize_bundle(partial_bundle))
print("partial:", [(x.child_id, x.status.value, x.underlying_status.value) for x in parsed_partial.outcomes[0].suboutcomes],
      parsed_partial.outcomes[0].candidate_ids, parsed_partial.outcomes[0].artifact_ids)
