"""Version 1.0 acquisition interchange; no runtime services or acceptance authority.

Models are frozen, but JSON input values may contain mutable containers. Use the
public parse/serialize functions at trust boundaries: serialization revalidates
both structure and content digest rather than trusting a model instance.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    JsonValue,
    ValidationError,
    field_validator,
    model_validator,
)

from .models import OutcomeStatus as CanonicalOutcomeStatus
from .models import RedactedDiagnostic, canonical_input_hash

FORMAT_VERSION = "1.0"
Identifier = Annotated[str, Field(min_length=1, max_length=256, pattern=r"^\S+$")]
Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Count = Annotated[int, Field(ge=0)]
Amount = Annotated[float, Field(ge=0, allow_inf_nan=False)]
Timestamp = Annotated[
    AwareDatetime, AfterValidator(lambda v: v.astimezone(timezone.utc))
]


def _json_value(value: Any) -> Any:
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float and math.isfinite(value):
        return value
    if type(value) is list:
        for item in value:
            _json_value(item)
        return value
    if type(value) is dict and all(type(k) is str for k in value):
        for item in value.values():
            _json_value(item)
        return value
    raise ValueError("expected finite JSON value with string object keys")


FiniteJSON = Annotated[JsonValue, BeforeValidator(_json_value)]


class ContractModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        allow_inf_nan=False,
        revalidate_instances="always",
    )


class ContractError(ValueError):
    """Safe wire error; paths contain schema names/indices, never input values."""

    def __init__(self, code: str, field_paths: tuple[str, ...] = ()):
        self.code = code
        self.field_paths = field_paths
        super().__init__(code + (": " + ", ".join(field_paths) if field_paths else ""))


def _unique(values, label):
    values = tuple(values)
    if len(values) != len(set(values)):
        raise ValueError("duplicate " + label)
    return set(values)


class Resources(ContractModel):
    """Independent allocation/measurement dimensions; None means unavailable/N/A."""

    requests: Count | None = None
    pages: Count | None = None
    bytes: Count | None = None
    elapsed_seconds: Amount | None = None
    model_calls: Count | None = None
    input_tokens: Count | None = None
    output_tokens: Count | None = None
    browser_actions: Count | None = None
    concurrency: Count | None = None
    fanout: Count | None = None
    money_usd: Amount | None = None


class ImportedLineage(ContractModel):
    run_id: Identifier
    step_id: Identifier
    attempt: Annotated[int, Field(ge=1)]
    input_index: Count
    seed_entity_id: Identifier


class InputOccurrence(ContractModel):
    occurrence_id: Identifier
    input_ref: Digest
    type_tag: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_.-]{0,63}$")]
    value: FiniteJSON
    lineage: ImportedLineage | None = None

    @model_validator(mode="after")
    def check_input_hash(self):
        if self.input_ref != canonical_input_hash(self.value):
            raise ValueError("input_ref does not match canonical input value")
        return self


class AcquisitionRequest(ContractModel):
    format_version: Literal["1.0"] = FORMAT_VERSION
    operation_id: Identifier
    caller_id: Identifier
    scope: Annotated[str, Field(min_length=1, max_length=1024)]
    capability_digest: Digest
    inputs: tuple[InputOccurrence, ...]
    allocation: Resources
    parent_operation_id: Identifier | None = None
    need_ref: Identifier | None = None
    endpoint_policy_digest: Digest | None = None
    projection_profile_digest: Digest | None = None
    retention_policy_digest: Digest | None = None

    @model_validator(mode="after")
    def check_occurrences(self):
        _unique((i.occurrence_id for i in self.inputs), "input occurrence")
        return self


def _http_url(value: str | None):
    if value is not None:
        parts = urlsplit(value)
        if (
            parts.scheme not in ("http", "https")
            or not parts.hostname
            or parts.username is not None
            or parts.password is not None
            or any(c.isspace() for c in value)
        ):
            raise ValueError("expected HTTP(S) URL without credentials")
    return value


URL = Annotated[str, Field(min_length=1, max_length=8192), AfterValidator(_http_url)]


class ArtifactReference(ContractModel):
    artifact_id: Identifier
    snapshot_id: Identifier
    content_digest: Digest
    byte_length: Count
    locator: Annotated[str, Field(min_length=1, max_length=8192)]
    source_family: Identifier
    origin: Annotated[str, Field(min_length=1, max_length=8192)]
    retrieved_at: Timestamp
    event_at: Timestamp | None = None
    requested_url: URL | None = None
    final_url: URL | None = None

    @model_validator(mode="after")
    def check_url_pair(self):
        if (self.requested_url is None) != (self.final_url is None):
            raise ValueError("requested_url and final_url must be supplied together")
        return self


class SpanReference(ContractModel):
    span_id: Identifier
    artifact_id: Identifier
    byte_start: Count | None = None
    byte_end: Count | None = None
    field_pointer: (
        Annotated[str, Field(max_length=2048, pattern=r"^(?:/(?:[^~]|~[01])*)*$")]
        | None
    ) = None
    normalized_start: Count | None = None
    normalized_end: Count | None = None
    raw_offset_unit: Literal["byte"] | None = None
    normalized_offset_unit: Literal["unicode_code_point"] | None = None
    source_encoding: Literal["utf-8"] | None = None

    @model_validator(mode="after")
    def check_span(self):
        if self.field_pointer is not None:
            if self.byte_start is not None or self.byte_end is not None:
                raise ValueError("field pointer and byte range are mutually exclusive")
        elif (
            self.byte_start is None
            or self.byte_end is None
            or self.byte_start >= self.byte_end
        ):
            raise ValueError(
                "span requires a nonempty half-open byte range or JSON pointer"
            )
        normalized = (self.normalized_start, self.normalized_end)
        if normalized != (None, None):
            if (
                self.field_pointer is not None
                or self.normalized_start is None
                or self.normalized_end is None
                or self.normalized_start >= self.normalized_end
                or self.raw_offset_unit != "byte"
                or self.normalized_offset_unit != "unicode_code_point"
                or self.source_encoding != "utf-8"
            ):
                raise ValueError("normalized mapping requires exact offset declarations")
        elif any((self.raw_offset_unit, self.normalized_offset_unit, self.source_encoding)):
            raise ValueError("offset declarations require normalized range")
        return self


class EvidenceReference(ContractModel):
    evidence_id: Identifier
    occurrence_id: Identifier
    span_id: Identifier
    extraction_method: Identifier
    extraction_version: Identifier
    subject_attribution: Annotated[str, Field(min_length=1, max_length=2048)]


class CandidateReference(ContractModel):
    candidate_id: Identifier
    occurrence_id: Identifier
    evidence_id: Identifier
    disposition: Literal["unreviewed"] = "unreviewed"


class OutcomeStatus(str, Enum):
    SUCCESS_WITH_OUTPUT = "success_with_output"
    VALID_NO_RESULT = "valid_no_result"
    PARTIAL = "partial"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    TOOL_ERROR = "tool_error"
    INVALID_INPUT = "invalid_input"
    POLICY_DENIED = "policy_denied"
    UNKNOWN_OUTCOME = "unknown_outcome"
    RETENTION_HOLD = "retention_hold"


_SUCCESS = {OutcomeStatus.SUCCESS_WITH_OUTPUT, OutcomeStatus.VALID_NO_RESULT}
_FAILURE = {
    OutcomeStatus.RATE_LIMITED,
    OutcomeStatus.TIMEOUT,
    OutcomeStatus.TOOL_ERROR,
    OutcomeStatus.INVALID_INPUT,
    OutcomeStatus.POLICY_DENIED,
}


class CompletionWitness(ContractModel):
    """Producer's explicit completion declaration; not independent verification."""

    reference: Identifier
    completed_at: Timestamp


class _Result(ContractModel):
    status: OutcomeStatus
    underlying_status: CanonicalOutcomeStatus | None = None
    candidate_ids: tuple[Identifier, ...] = ()
    artifact_ids: tuple[Identifier, ...] = ()
    diagnostic: RedactedDiagnostic | None = None
    completion_witness: CompletionWitness | None = None
    actual_resources: Resources | None = None

    @field_validator("diagnostic", mode="before")
    @classmethod
    def check_diagnostic_scalar(cls, diagnostic):
        if diagnostic is None:
            return None
        if isinstance(diagnostic, RedactedDiagnostic):
            diagnostic = {
                name: getattr(diagnostic, name, None)
                for name in RedactedDiagnostic.model_fields
            }
        return RedactedDiagnostic.model_validate(diagnostic, strict=True)

    @model_validator(mode="after")
    def check_status(self):
        _unique(self.candidate_ids, "candidate reference")
        _unique(self.artifact_ids, "artifact reference")
        if self.status in _SUCCESS:
            if self.diagnostic is not None or self.completion_witness is None:
                raise ValueError(
                    "successful acquisition requires completion witness and no diagnostic"
                )
            if bool(self.candidate_ids) != (
                self.status == OutcomeStatus.SUCCESS_WITH_OUTPUT
            ):
                raise ValueError("success status disagrees with candidate cardinality")
            expected = CanonicalOutcomeStatus.SUCCESS
        elif self.status == OutcomeStatus.PARTIAL:
            if self.diagnostic is None or self.completion_witness is not None:
                raise ValueError(
                    "partial requires diagnostic and per-child completion witnesses"
                )
            expected = None
        else:
            if (
                self.diagnostic is None
                or self.candidate_ids
                or self.artifact_ids
                or self.completion_witness is not None
            ):
                raise ValueError(
                    "failure/unknown/hold requires diagnostic and cannot carry material or completion"
                )
            expected = (
                CanonicalOutcomeStatus.HOLD
                if self.status == OutcomeStatus.RETENTION_HOLD
                else CanonicalOutcomeStatus.FAILURE if self.status in _FAILURE else None
            )
        if expected is not None and self.underlying_status not in (None, expected):
            raise ValueError("granular status contradicts underlying status")
        return self


class Suboutcome(_Result):
    child_id: Identifier

    @model_validator(mode="after")
    def reject_nested_partial(self):
        if self.status == OutcomeStatus.PARTIAL:
            raise ValueError("suboutcomes must be terminal leaves")
        return self


class OccurrenceOutcome(_Result):
    occurrence_id: Identifier
    suboutcomes: tuple[Suboutcome, ...] = ()

    @model_validator(mode="after")
    def check_partial(self):
        if self.status != OutcomeStatus.PARTIAL:
            if self.suboutcomes:
                raise ValueError("only partial outcomes contain suboutcomes")
            return self
        _unique((s.child_id for s in self.suboutcomes), "child outcome")
        states = [s.status in _SUCCESS for s in self.suboutcomes]
        if not any(states) or all(states):
            raise ValueError("partial requires successful and unsuccessful children")
        children = tuple(c for s in self.suboutcomes for c in s.candidate_ids)
        if _unique(children, "child candidate") != set(self.candidate_ids):
            raise ValueError(
                "partial candidates must equal successful child candidates"
            )
        if set(a for s in self.suboutcomes for a in s.artifact_ids) != set(
            self.artifact_ids
        ):
            raise ValueError("partial artifacts must equal successful child artifacts")
        return self


class _BundlePayload(ContractModel):
    format_version: Literal["1.0"] = FORMAT_VERSION
    bundle_id: Identifier
    request: AcquisitionRequest
    created_at: Timestamp
    outcomes: tuple[OccurrenceOutcome, ...]
    actual_resources: Resources
    artifacts: tuple[ArtifactReference, ...] = ()
    spans: tuple[SpanReference, ...] = ()
    evidence_list: tuple[EvidenceReference, ...] = ()
    candidates: tuple[CandidateReference, ...] = ()

    @model_validator(mode="after")
    def check_reference_graph(self):
        requested = {i.occurrence_id for i in self.request.inputs}
        if (
            _unique((o.occurrence_id for o in self.outcomes), "outcome occurrence")
            != requested
        ):
            raise ValueError("outcomes must cover every input occurrence exactly once")
        indexes = []
        for records, key in (
            (self.artifacts, "artifact_id"),
            (self.spans, "span_id"),
            (self.evidence_list, "evidence_id"),
            (self.candidates, "candidate_id"),
        ):
            _unique((getattr(r, key) for r in records), key)
            indexes.append({getattr(r, key): r for r in records})
        artifacts, spans, evidence, candidates = indexes
        for span in self.spans:
            if span.artifact_id not in artifacts:
                raise ValueError("span references missing artifact")
            if (
                span.byte_end is not None
                and span.byte_end > artifacts[span.artifact_id].byte_length
            ):
                raise ValueError("span exceeds artifact byte length")
        for item in self.evidence_list:
            if item.occurrence_id not in requested or item.span_id not in spans:
                raise ValueError("evidence references missing occurrence or span")
        for candidate in self.candidates:
            item = evidence.get(candidate.evidence_id)
            if item is None or candidate.occurrence_id != item.occurrence_id:
                raise ValueError("candidate evidence attribution mismatch")
        used_candidates = []
        used_artifacts = set()
        for outcome in self.outcomes:
            for cid in outcome.candidate_ids:
                if (
                    cid not in candidates
                    or candidates[cid].occurrence_id != outcome.occurrence_id
                ):
                    raise ValueError("outcome candidate attribution mismatch")
                used_candidates.append(cid)
            used_artifacts.update(outcome.artifact_ids)
        if _unique(used_candidates, "output candidate") != set(candidates):
            raise ValueError("orphan or repeated candidate")
        if {c.evidence_id for c in self.candidates} != set(evidence):
            raise ValueError("orphan evidence")
        if {e.span_id for e in self.evidence_list} != set(spans):
            raise ValueError("orphan span")
        used_artifacts.update(s.artifact_id for s in self.spans)
        if used_artifacts != set(artifacts):
            raise ValueError("orphan or missing artifact")
        return self


def _canonical(data) -> str:
    return json.dumps(
        data, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":")
    )


def _digest(payload: _BundlePayload) -> str:
    data = payload.model_dump(mode="json", exclude={"origin_digest"}, warnings="error")
    return hashlib.sha256(_canonical(data).encode("utf-8")).hexdigest()


class AcquisitionBundle(_BundlePayload):
    origin_digest: Digest

    @model_validator(mode="after")
    def check_origin_digest(self):
        if self.origin_digest != _digest(self):
            raise ValueError("origin digest mismatch")
        return self


def _error(exc: ValidationError) -> ContractError:
    # Extra keys and JSON object keys are untrusted. Report only known schema names.
    names = {
        n
        for value in globals().values()
        if isinstance(value, type) and issubclass(value, BaseModel)
        for n in value.model_fields
    }
    paths = tuple(
        ".".join(
            str(p) if isinstance(p, int) or p in names else "<field>" for p in e["loc"]
        )
        or "$"
        for e in exc.errors(
            include_input=False, include_context=False, include_url=False
        )
    )
    return ContractError("malformed_contract", paths)


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ContractError("duplicate_json_key")
        result[key] = value
    return result


def _load(raw: str | bytes):
    try:
        obj = json.loads(
            raw,
            object_pairs_hook=_object,
            parse_constant=lambda _: (_ for _ in ()).throw(
                ContractError("invalid_json")
            ),
        )
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        if isinstance(exc, ContractError):
            raise
        raise ContractError("invalid_json") from None
    if not isinstance(obj, dict):
        raise ContractError("malformed_contract", ("$",))
    if obj.get("format_version") != FORMAT_VERSION:
        raise ContractError("unsupported_format_version", ("format_version",))
    return obj


def _parse(raw, model):
    obj = _load(raw)
    if model is AcquisitionBundle and isinstance(obj.get("request"), dict):
        if obj["request"].get("format_version") != FORMAT_VERSION:
            raise ContractError(
                "unsupported_format_version", ("request.format_version",)
            )
    try:
        return model.model_validate_json(_canonical(obj))
    except ValidationError as exc:
        raise _error(exc) from None
    except (ValueError, TypeError, RecursionError):
        raise ContractError("malformed_contract") from None


def parse_request(raw: str | bytes) -> AcquisitionRequest:
    return _parse(raw, AcquisitionRequest)


def parse_bundle(raw: str | bytes) -> AcquisitionBundle:
    return _parse(raw, AcquisitionBundle)


def _serialize(model, parser):
    try:
        expected = AcquisitionBundle if parser is parse_bundle else AcquisitionRequest
        validated = expected.model_validate(
            model.model_dump(mode="python", warnings="error")
        )
        wire = validated.model_dump_json(warnings="error")
    except (ValueError, TypeError, RecursionError):
        raise ContractError("malformed_contract") from None
    validated = parser(wire)
    return _canonical(validated.model_dump(mode="json", warnings="error"))


def serialize_request(request: AcquisitionRequest) -> str:
    return _serialize(request, parse_request)


def serialize_bundle(bundle: AcquisitionBundle) -> str:
    return _serialize(bundle, parse_bundle)


def build_bundle(
    *,
    request: AcquisitionRequest,
    outcomes: tuple[OccurrenceOutcome, ...],
    bundle_id: str,
    created_at: datetime,
    actual_resources: Resources,
    artifacts: tuple[ArtifactReference, ...] = (),
    spans: tuple[SpanReference, ...] = (),
    evidence_list: tuple[EvidenceReference, ...] = (),
    candidates: tuple[CandidateReference, ...] = (),
) -> AcquisitionBundle:
    """Validate payload and compute its origin digest; never invent provenance."""
    try:
        payload = _BundlePayload(
            request=request,
            outcomes=outcomes,
            bundle_id=bundle_id,
            created_at=created_at,
            actual_resources=actual_resources,
            artifacts=artifacts,
            spans=spans,
            evidence_list=evidence_list,
            candidates=candidates,
        )
        bundle = AcquisitionBundle(
            **payload.model_dump(warnings="error"), origin_digest=_digest(payload)
        )
        return bundle
    except ValidationError as exc:
        raise _error(exc) from None
    except (ValueError, TypeError, RecursionError):
        raise ContractError("malformed_contract") from None
