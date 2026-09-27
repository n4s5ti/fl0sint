"""Consumer-visible integrity checks for the standalone wire contract."""

import hashlib
import json
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from flowsint_execution import acquisition as a
from flowsint_execution.models import OutcomeStatus as CanonicalStatus
from flowsint_execution.models import RedactedDiagnostic, canonical_input_hash

NOW = datetime(2026, 9, 26, tzinfo=timezone.utc)


def occurrence(name="ocr_1"):
    value = "https://example.test/page"
    return a.InputOccurrence(
        occurrence_id=name,
        input_ref=canonical_input_hash(value),
        type_tag="url",
        value=value,
        lineage=a.ImportedLineage(
            run_id="run",
            step_id="fetch",
            attempt=2,
            input_index=int(name.split("_")[-1]),
            seed_entity_id="seed",
        ),
    )


def request(inputs=None):
    return a.AcquisitionRequest(
        operation_id="op_1",
        capability_digest="a" * 64,
        caller_id="local",
        scope="fixture",
        inputs=(occurrence(),) if inputs is None else inputs,
        allocation=a.Resources(requests=3, bytes=1000),
        retention_policy_digest="b" * 64,
        parent_operation_id="parent",
        need_ref="need",
    )


def diagnostic(code="no_witness"):
    return RedactedDiagnostic(
        code=code, safe_message="Completion unknown", retryable=False
    )


def unknown(name="ocr_1"):
    return a.OccurrenceOutcome(
        occurrence_id=name,
        status=a.OutcomeStatus.UNKNOWN_OUTCOME,
        diagnostic=diagnostic(),
    )


def fields(counts=(0, 1, 2)):
    inputs = tuple(occurrence(f"ocr_{i}") for i in range(len(counts)))
    outputs, evidence, candidates = [], [], []
    for inp, count in zip(inputs, counts):
        ids = tuple(f"candidate_{inp.occurrence_id}_{j}" for j in range(count))
        outputs.append(
            a.OccurrenceOutcome(
                occurrence_id=inp.occurrence_id,
                status=(
                    a.OutcomeStatus.SUCCESS_WITH_OUTPUT
                    if count
                    else a.OutcomeStatus.VALID_NO_RESULT
                ),
                underlying_status=CanonicalStatus.SUCCESS,
                candidate_ids=ids,
                completion_witness=a.CompletionWitness(
                    reference="completed", completed_at=NOW
                ),
            )
        )
        for cid in ids:
            evidence.append(
                a.EvidenceReference(
                    evidence_id="e_" + cid,
                    occurrence_id=inp.occurrence_id,
                    span_id="span",
                    extraction_method="fixture",
                    extraction_version="1",
                    subject_attribution="source field",
                )
            )
            candidates.append(
                a.CandidateReference(
                    candidate_id=cid,
                    occurrence_id=inp.occurrence_id,
                    evidence_id="e_" + cid,
                )
            )
    artifacts = (
        (
            a.ArtifactReference(
                artifact_id="artifact",
                snapshot_id="snapshot",
                content_digest="c" * 64,
                byte_length=10,
                locator="fixture:source",
                source_family="fixture",
                origin="example.test",
                retrieved_at=NOW,
                event_at=NOW,
                requested_url="https://example.test/page",
                final_url="https://example.test/final",
            ),
        )
        if candidates
        else ()
    )
    return dict(
        request=request(inputs),
        outcomes=tuple(outputs),
        bundle_id="bundle_1",
        created_at=NOW,
        actual_resources=a.Resources(
            requests=len(counts), bytes=10 if candidates else 0
        ),
        artifacts=artifacts,
        spans=(
            (
                a.SpanReference(
                    span_id="span", artifact_id="artifact", byte_start=0, byte_end=5
                ),
            )
            if candidates
            else ()
        ),
        evidence_list=tuple(evidence),
        candidates=tuple(candidates),
    )


def resign(data):
    """Bypass only digest freshness, so malformed fixtures attack structure itself."""
    payload = {k: v for k, v in data.items() if k != "origin_digest"}
    data["origin_digest"] = hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return json.dumps(data)


def test_duplicate_input_occurrences_cannot_collapse_in_set_comparison():
    with pytest.raises(ValidationError, match="duplicate input occurrence"):
        request((occurrence(), occurrence()))


def test_duplicate_outcomes_cannot_collapse_in_set_comparison():
    with pytest.raises(a.ContractError):
        a.build_bundle(
            request=request(),
            outcomes=(unknown(), unknown()),
            bundle_id="bundle_1",
            created_at=NOW,
            actual_resources=a.Resources(),
        )


def test_serializer_rejects_mutated_digest_instead_of_emitting_corrupt_bundle():
    bundle = a.build_bundle(
        request=request(),
        outcomes=(unknown(),),
        bundle_id="bundle_1",
        created_at=NOW,
        actual_resources=a.Resources(),
    )
    with pytest.raises(a.ContractError):
        a.serialize_bundle(bundle.model_copy(update={"origin_digest": "b" * 64}))


@pytest.mark.parametrize("counts", [(), (0,), (1,), (2,), (0, 1, 2)])
def test_zero_one_many_and_duplicate_content_roundtrip(counts):
    bundle = a.build_bundle(**fields(counts))
    restored = a.parse_bundle(a.serialize_bundle(bundle))
    assert [len(o.candidate_ids) for o in restored.outcomes] == list(counts)
    assert restored == bundle
    assert len({i.input_ref for i in restored.request.inputs}) == (1 if counts else 0)
    assert [i.occurrence_id for i in restored.request.inputs] == [
        o.occurrence_id for o in restored.outcomes
    ]
    assert all(c.disposition == "unreviewed" for c in restored.candidates)
    assert restored.actual_resources.model_calls is None
    assert [i.lineage.input_index for i in restored.request.inputs] == list(
        range(len(counts))
    )


def test_completion_order_does_not_change_ownership():
    data = fields()
    data["outcomes"] = tuple(reversed(data["outcomes"]))
    restored = a.parse_bundle(a.serialize_bundle(a.build_bundle(**data)))
    assert {o.occurrence_id: len(o.candidate_ids) for o in restored.outcomes} == {
        "ocr_0": 0,
        "ocr_1": 1,
        "ocr_2": 2,
    }


@pytest.mark.parametrize(
    "mutation",
    [
        lambda d: d["candidates"][0].update(disposition="accepted"),
        lambda d: d["candidates"][0].update(occurrence_id="ocr_0"),
        lambda d: d["spans"][0].update(byte_end=11),
        lambda d: d["spans"][0].update(byte_start=5, byte_end=5),
        lambda d: d["evidence_list"][0].update(span_id="missing"),
        lambda d: d["outcomes"].pop(),
        lambda d: d["outcomes"].append(d["outcomes"][0]),
        lambda d: d["artifacts"].append(dict(d["artifacts"][0], artifact_id="orphan")),
        lambda d: d["request"]["inputs"][0].update(input_ref="d" * 64),
        lambda d: d["request"]["inputs"][0]["lineage"].pop("seed_entity_id"),
        lambda d: d["actual_resources"].update(requests=True),
        lambda d: d.update(created_at="2026-09-26T00:00:00"),
    ],
)
def test_malformed_reference_graph_and_scalars_rejected(mutation):
    data = json.loads(a.serialize_bundle(a.build_bundle(**fields())))
    mutation(data)
    with pytest.raises(a.ContractError):
        a.parse_bundle(resign(data))


@pytest.mark.parametrize(
    "status",
    [
        a.OutcomeStatus.RATE_LIMITED,
        a.OutcomeStatus.TIMEOUT,
        a.OutcomeStatus.TOOL_ERROR,
        a.OutcomeStatus.INVALID_INPUT,
        a.OutcomeStatus.POLICY_DENIED,
        a.OutcomeStatus.UNKNOWN_OUTCOME,
        a.OutcomeStatus.RETENTION_HOLD,
    ],
)
def test_non_success_preserves_reason_underlying_status_and_spend(status):
    underlying = (
        CanonicalStatus.HOLD
        if status == a.OutcomeStatus.RETENTION_HOLD
        else CanonicalStatus.FAILURE
    )
    result = a.OccurrenceOutcome(
        occurrence_id="ocr_1",
        status=status,
        underlying_status=underlying,
        diagnostic=diagnostic("source_rights_unspecified"),
    )
    bundle = a.build_bundle(
        request=request(),
        outcomes=(result,),
        bundle_id="failed",
        created_at=NOW,
        actual_resources=a.Resources(bytes=52, elapsed_seconds=0.5),
    )
    restored = a.parse_bundle(a.serialize_bundle(bundle))
    assert restored.outcomes[0].diagnostic.code == "source_rights_unspecified"
    assert restored.outcomes[0].underlying_status == underlying
    assert restored.actual_resources.bytes == 52
    assert restored.candidates == ()


def test_partial_preserves_child_failures_and_exact_success_outputs():
    data = fields((1,))
    success = data["outcomes"][0]
    good = a.Suboutcome(
        child_id="fetch", **success.model_dump(exclude={"occurrence_id", "suboutcomes"})
    )
    bad = a.Suboutcome(
        child_id="second_fetch",
        status=a.OutcomeStatus.TIMEOUT,
        underlying_status=CanonicalStatus.FAILURE,
        diagnostic=diagnostic("timeout"),
    )
    data["outcomes"] = (
        a.OccurrenceOutcome(
            occurrence_id="ocr_0",
            status=a.OutcomeStatus.PARTIAL,
            candidate_ids=success.candidate_ids,
            diagnostic=diagnostic("partial"),
            suboutcomes=(good, bad),
        ),
    )
    restored = a.parse_bundle(a.serialize_bundle(a.build_bundle(**data)))
    assert [s.status for s in restored.outcomes[0].suboutcomes] == [
        a.OutcomeStatus.SUCCESS_WITH_OUTPUT,
        a.OutcomeStatus.TIMEOUT,
    ]
    assert restored.outcomes[0].suboutcomes[1].diagnostic.code == "timeout"
    with pytest.raises(ValidationError):
        a.OccurrenceOutcome(
            occurrence_id="ocr_0",
            status=a.OutcomeStatus.PARTIAL,
            diagnostic=diagnostic("partial"),
            suboutcomes=(bad,),
        )


def test_no_result_cannot_suppress_missing_completion_witness():
    with pytest.raises(ValidationError):
        a.OccurrenceOutcome(
            occurrence_id="ocr_1", status=a.OutcomeStatus.VALID_NO_RESULT
        )


@pytest.mark.parametrize("version", [None, "2.0", "1.1", 1])
def test_unsupported_versions_have_explicit_safe_diagnostic(version):
    data = json.loads(a.serialize_request(request()))
    if version is None:
        data.pop("format_version")
    else:
        data["format_version"] = version
    with pytest.raises(a.ContractError) as exc:
        a.parse_request(json.dumps(data))
    assert exc.value.code == "unsupported_format_version"


def test_nested_mutation_and_copy_cannot_publish_accepted_disposition():
    bundle = a.build_bundle(**fields((1,)))
    candidate = bundle.candidates[0].model_copy(update={"disposition": "accepted"})
    with pytest.raises(a.ContractError):
        a.serialize_bundle(bundle.model_copy(update={"candidates": (candidate,)}))
    value = {"items": [1]}
    inp = a.InputOccurrence(
        occurrence_id="dict",
        input_ref=canonical_input_hash(value),
        type_tag="json",
        value=value,
    )
    req = request((inp,))
    req.inputs[0].value["items"].append(2)
    with pytest.raises(a.ContractError):
        a.serialize_request(req)


@pytest.mark.parametrize(
    "raw", [b"\xff", "{", "[]", '{"format_version":"1.0","format_version":"1.0"}']
)
def test_invalid_json_and_duplicate_keys_are_explicit(raw):
    with pytest.raises(a.ContractError):
        a.parse_request(raw)


def test_validation_diagnostics_do_not_echo_secrets_or_extra_keys():
    data = json.loads(a.serialize_request(request()))
    data["sensitive-input-secret"] = "sensitive-value-secret"
    with pytest.raises(a.ContractError) as exc:
        a.parse_request(json.dumps(data))
    assert "secret" not in str(exc.value)
    assert "secret" not in repr(exc.value.field_paths)


@pytest.mark.parametrize(
    "value", [{"nested": [float("nan")]}, {"nested": [float("inf")]}, {1: "bad key"}]
)
def test_input_rejects_nonfinite_or_non_json_nested_values(value):
    with pytest.raises(ValidationError):
        a.InputOccurrence(
            occurrence_id="input", input_ref="a" * 64, type_tag="json", value=value
        )


def test_serializer_rejects_nonfinite_before_json_can_turn_it_into_null():
    inp = a.InputOccurrence(
        occurrence_id="null",
        input_ref=canonical_input_hash(None),
        type_tag="json",
        value=None,
    )
    req = request((inp,))
    corrupt = inp.model_copy(update={"value": float("nan")})
    with pytest.raises(a.ContractError):
        a.serialize_request(req.model_copy(update={"inputs": (corrupt,)}))


@pytest.mark.parametrize(
    "update", [{"safe_message": b"input-secret"}, {"retryable": "false"}]
)
def test_constructed_diagnostic_is_rejected_without_warning_or_secret_echo(update):
    import warnings

    corrupt = diagnostic().model_copy(update=update)
    outcome = unknown().model_copy(update={"diagnostic": corrupt})
    with warnings.catch_warnings(record=True) as caught:
        with pytest.raises(a.ContractError) as exc:
            a.build_bundle(
                request=request(),
                outcomes=(outcome,),
                bundle_id="bad",
                created_at=NOW,
                actual_resources=a.Resources(),
            )
    assert caught == []
    assert "input-secret" not in str(exc.value)
    assert exc.value.code == "malformed_contract"
