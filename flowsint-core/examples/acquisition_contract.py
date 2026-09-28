"""Emit successful/failed contract examples; no network or runtime services.

Usage: python examples/acquisition_contract.py OUTPUT_DIRECTORY
The successful example retains and reads a controlled local fixture. The failed
example is an explicit caller-supplied INVALID_INPUT response, not a fetch.
"""

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from flowsint_execution.acquisition import (
    AcquisitionRequest,
    ArtifactReference,
    CandidateReference,
    CompletionWitness,
    EvidenceReference,
    InputOccurrence,
    OccurrenceOutcome,
    OutcomeStatus,
    Resources,
    SpanReference,
    build_bundle,
    parse_bundle,
    serialize_bundle,
    serialize_request,
)
from flowsint_execution.models import (
    OutcomeStatus as CanonicalStatus,
    RedactedDiagnostic,
    canonical_input_hash,
)


def main(directory: Path):
    directory.mkdir(parents=True, exist_ok=False)
    source = directory / "source.json"
    source.write_text('{"name":"Example Organization"}\n', encoding="utf-8")
    body = source.read_bytes()
    now = datetime.now(timezone.utc)
    operation_id = str(uuid4())
    value = "source.json"
    request = AcquisitionRequest(
        operation_id=operation_id,
        caller_id="local-example",
        scope="controlled-local-fixture",
        capability_digest=hashlib.sha256(b"contract-example:1").hexdigest(),
        inputs=(
            InputOccurrence(
                occurrence_id="input-1",
                input_ref=canonical_input_hash(value),
                type_tag="record_locator",
                value=value,
            ),
        ),
        allocation=Resources(bytes=len(body), requests=0),
    )
    success = build_bundle(
        request=request,
        bundle_id=str(uuid4()),
        created_at=now,
        actual_resources=Resources(bytes=len(body), requests=0),
        outcomes=(
            OccurrenceOutcome(
                occurrence_id="input-1",
                status=OutcomeStatus.SUCCESS_WITH_OUTPUT,
                underlying_status=CanonicalStatus.SUCCESS,
                candidate_ids=("candidate-1",),
                completion_witness=CompletionWitness(
                    reference="local-fixture-read", completed_at=now
                ),
            ),
        ),
        artifacts=(
            ArtifactReference(
                artifact_id="artifact-1",
                snapshot_id="snapshot-1",
                content_digest=hashlib.sha256(body).hexdigest(),
                byte_length=len(body),
                locator="source.json",
                source_family="local_fixture",
                origin="controlled-example",
                retrieved_at=now,
            ),
        ),
        spans=(
            SpanReference(
                span_id="span-1", artifact_id="artifact-1", field_pointer="/name"
            ),
        ),
        evidence_list=(
            EvidenceReference(
                evidence_id="evidence-1",
                occurrence_id="input-1",
                span_id="span-1",
                extraction_method="json_pointer",
                extraction_version="1",
                subject_attribution="fixture record /name",
            ),
        ),
        candidates=(
            CandidateReference(
                candidate_id="candidate-1",
                occurrence_id="input-1",
                evidence_id="evidence-1",
            ),
        ),
    )
    failed_request = request.model_copy(update={"operation_id": str(uuid4())})
    failed = build_bundle(
        request=failed_request,
        bundle_id=str(uuid4()),
        created_at=now,
        actual_resources=Resources(requests=0, bytes=0),
        outcomes=(
            OccurrenceOutcome(
                occurrence_id="input-1",
                status=OutcomeStatus.INVALID_INPUT,
                underlying_status=CanonicalStatus.FAILURE,
                diagnostic=RedactedDiagnostic(
                    code="invalid_input",
                    safe_message="Caller rejected fixture input",
                    retryable=False,
                ),
            ),
        ),
    )
    (directory / "request.json").write_text(
        serialize_request(request), encoding="utf-8"
    )
    for name, bundle in (("success", success), ("failed", failed)):
        path = directory / (name + ".json")
        path.write_text(serialize_bundle(bundle), encoding="utf-8")
        restored = parse_bundle(path.read_bytes())
        print(
            json.dumps(
                {
                    "file": path.name,
                    "status": restored.outcomes[0].status.value,
                    "candidates": [c.disposition for c in restored.candidates],
                    "origin_digest": restored.origin_digest,
                }
            )
        )


if __name__ == "__main__":
    main(Path(sys.argv[1]))
