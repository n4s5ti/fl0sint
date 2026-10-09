"""Website text extraction through the shared admitted HTTP implementation."""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from dataclasses import dataclass
from os import PathLike
from typing import Any, Dict, List, Sequence
from urllib.parse import urlsplit

from flowsint_execution.acquisition import (
    AcquisitionRequest,
    InputOccurrence,
    Resources,
)
from flowsint_execution.fetch import (
    FetchParameters,
    FetchStatus,
    TrustedFetchPolicy,
    admit_fetch,
    execute_fetch_with_source_proof,
)
from flowsint_execution.artifacts import ArtifactContext, ArtifactState, FilesystemArtifactStore, RetentionAuthority
from flowsint_execution.artifact_runtime import (
    PersistedSourceProof, encode_source_proof, load_artifact_runtime,
)
from flowsint_execution.models import (
    EvidenceEnvelope,
    InputOutcome,
    OutcomeStatus,
    RedactedDiagnostic,
    StructuredExecutionResult,
    canonical_input_hash,
)
from flowsint_execution.observed_extraction import (
    ObservedExtractionResult, serialize_observed_extraction_metadata,
)
from flowsint_types.phrase import Phrase
from flowsint_types.website import Website
_CAPABILITY_DIGEST = hashlib.sha256(b"flowsint.website-to-text.fetch.v1").hexdigest()
_CALLER_ID = "website-to-text"
_SCOPE = "local-web-fetch"
_MAX_OBSERVATION_RESULT_BYTES = 8 * 1024 * 1024



def _origin(url: str) -> str:
    parts = urlsplit(url)
    parsed_port = parts.port
    port = parsed_port if parsed_port is not None else (443 if parts.scheme == "https" else 80)
    if port == 0:
        raise ValueError("invalid URL port")
    host = parts.hostname or ""
    host = f"[{host.lower()}]" if ":" in host else host.lower()
    return f"{parts.scheme}://{host}:{port}"


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


@dataclass(frozen=True)
class WebsiteTextOccurrence:
    source: Website
    index: int
    input_ref: str
    status: OutcomeStatus
    outputs: tuple[Phrase, ...] = ()
    diagnostic: RedactedDiagnostic | None = None
    actual_resources: Resources | None = None
    fetch_status: FetchStatus | None = None
    artifact_reference: object | None = None
    span_references: tuple[object, ...] = ()
    source_proof: str | None = None
    observation_result: ObservedExtractionResult | None = None

    def to_input_outcome(self) -> InputOutcome:
        evidence = ()
        if self.source_proof is not None and self.artifact_reference is not None:
            evidence = (EvidenceEnvelope(
                input_ref=self.input_ref,
                destination_id="website_to_text",
                endpoint_id="shared_http_fetch",
                capability="enrich.read",
                policy_version="source-proof/1.0",
                artifact_sha256=self.artifact_reference.content_digest,
                artifact_reference=self.source_proof,
                source_rights="reviewed_runtime_policy",
                schema_version="source-proof/1.0",
                parser_version="html-normalizer/1",
                confidence=1.0,
                verification_state="retained",
                retrieved_at=self.artifact_reference.retrieved_at,
                ingested_at=self.artifact_reference.retrieved_at,
            ),)
        return InputOutcome(
            input_ref=self.input_ref,
            status=self.status,
            outputs=self.outputs,
            diagnostic=self.diagnostic,
            evidence=evidence,
            metadata=(() if self.observation_result is None else (
                serialize_observed_extraction_metadata(self.observation_result),
            )),
        )


class WebsiteTextExtractor:
    """Shared admitted website-text acquisition without graph orchestration."""

    def __init__(
        self,
        *,
        params: Dict[str, Any] | None = None,
        artifact_store: FilesystemArtifactStore | None = None,
        retention_authority: RetentionAuthority | None = None,
        runtime_config: str | PathLike[str] | None = None,
        outputs_from_text=None,
    ):
        self.params = params if params is not None else {}
        self._artifact_store = artifact_store
        self._retention_authority = retention_authority
        self._runtime_config = runtime_config
        self._outputs_from_text = outputs_from_text or self._default_outputs_from_text

    @staticmethod
    def _default_outputs_from_text(text):
        return (Phrase(text=text),) if text else ()

    @staticmethod
    def _diagnostic(code, message, retryable=False):
        return RedactedDiagnostic(code=code, safe_message=message, retryable=retryable)

    def _cancelled_occurrence(self, index, source, input_ref, resources=None):
        return WebsiteTextOccurrence(
            source,
            index,
            input_ref,
            OutcomeStatus.HOLD,
            diagnostic=self._diagnostic(
                "cancelled", "The operation was cancelled before completion."
            ),
            actual_resources=resources,
            fetch_status=FetchStatus.CANCELLED,
        )

    def _failed_occurrence(
        self,
        index,
        source,
        input_ref,
        diagnostic=None,
        resources=None,
        fetch_status=None,
    ):
        return WebsiteTextOccurrence(
            source,
            index,
            input_ref,
            OutcomeStatus.FAILURE,
            diagnostic=diagnostic
            or self._diagnostic("unexpected_error", "Unexpected processing failure."),
            actual_resources=resources,
            fetch_status=fetch_status,
        )

    def _build_operation(self, specs, *, operation_id: str | None = None):
        def positive_int(name, default):
            value = self.params.get(name, default)
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
            return value

        max_response_bytes = positive_int("max_response_bytes", 1048576)
        max_concurrency = positive_int("max_concurrency", 10)
        request_timeout = positive_int("request_timeout", 10)
        urls = tuple(str(source.url) for _index, source, _ref in specs)
        origins = tuple(sorted(set(_origin(url) for url in urls)))
        endpoint_digest = _digest(
            {"allowed_origins": origins, "redirects": "same-origin-only"}
        )
        policy = TrustedFetchPolicy(
            caller_id=_CALLER_ID,
            scope=_SCOPE,
            capability_digest=_CAPABILITY_DIGEST,
            endpoint_policy_digest=endpoint_digest,
            allowed_origins=origins,
        )
        parameters = FetchParameters(
            max_redirects=self.params.get("max_redirects", 3),
            max_retries=self.params.get("max_retries", 0),
            max_bytes_per_input=max_response_bytes,
        )
        count = len(specs)
        attempts = 1 + parameters.max_redirects + parameters.max_retries
        request = AcquisitionRequest(
            operation_id=operation_id or f"website-to-text-{uuid.uuid4().hex}",
            caller_id=_CALLER_ID,
            scope=_SCOPE,
            capability_digest=_CAPABILITY_DIGEST,
            endpoint_policy_digest=endpoint_digest,
            inputs=tuple(
                InputOccurrence(
                    occurrence_id=f"input-{specs[position][0]}",
                    input_ref=canonical_input_hash(url),
                    type_tag="http_url",
                    value=url,
                )
                for position, url in enumerate(urls)
            ),
            allocation=Resources(
                requests=count * attempts,
                bytes=count * max_response_bytes,
                elapsed_seconds=request_timeout,
                concurrency=min(max_concurrency, count),
            ),
        )
        return admit_fetch(request, policy, parameters)

    async def _scan_occurrence_specs(
        self,
        specs: Sequence[tuple[int, Website, str]],
        *,
        operation_id: str | None = None,
    ):
        specs = tuple(specs)
        if not specs:
            return ()
        if self.params.get("enable_quic", False):
            diagnostic = self._diagnostic(
                "unsafe_transport_disabled",
                "Legacy QUIC transport is disabled because it cannot satisfy the fetch policy.",
            )
            return tuple(
                self._failed_occurrence(
                    *spec,
                    diagnostic,
                    Resources(requests=0, bytes=0, elapsed_seconds=0.0),
                    FetchStatus.POLICY_DENIED,
                )
                for spec in specs
            )
        try:
            operation = self._build_operation(specs, operation_id=operation_id)
            runtime = None
            if self._artifact_store is None or self._retention_authority is None:
                runtime = load_artifact_runtime(
                    caller_id=_CALLER_ID, scope=_SCOPE, source_family="http",
                    config_path=self._runtime_config,
                )
            store = self._artifact_store or (runtime.store if runtime else None)
            authority = self._retention_authority or (runtime.authority if runtime else None)
            decision = (
                authority.issue(operation.operation_id)
                if authority is not None
                else None
            )
            fetched = await execute_fetch_with_source_proof(
                operation,
                artifact_store=store,
                retention_decision=decision,
            )
        except asyncio.CancelledError:
            return tuple(self._cancelled_occurrence(*spec) for spec in specs)
        except Exception:
            return tuple(self._failed_occurrence(*spec) for spec in specs)
        expected = {
            item.occurrence_id: item.input_ref for item in operation.inputs
        }
        returned_ids = [result.occurrence_id for result in fetched.outcomes]
        result_is_bound = (
            fetched.operation_id == operation.operation_id
            and len(fetched.outcomes) == len(operation.inputs)
            and len(set(returned_ids)) == len(returned_ids)
            and set(returned_ids) == set(expected)
            and all(
                result.input_ref == expected.get(result.occurrence_id)
                for result in fetched.outcomes
            )
        )
        if not result_is_bound:
            return tuple(
                self._failed_occurrence(
                    *spec,
                    self._diagnostic(
                        "invalid_fetch_result",
                        "The fetch result did not match the admitted operation.",
                    ),
                )
                for spec in specs
            )
        outcome_by_id = {
            result.occurrence_id: result for result in fetched.outcomes
        }
        occurrences = []
        for admitted, (index, source, input_ref) in zip(operation.inputs, specs):
            result = outcome_by_id.get(admitted.occurrence_id)
            if result.fetch_status is FetchStatus.SUCCESS:
                if result.capture.state is ArtifactState.AVAILABLE and result.normalized is not None:
                    artifact = result.capture.artifact
                    context = ArtifactContext(
                        operation_id=operation.operation_id,
                        occurrence_id=admitted.occurrence_id,
                        caller_id=_CALLER_ID,
                        scope=_SCOPE,
                        source_family="http",
                        origin=artifact.origin,
                        requested_url=artifact.requested_url,
                        final_url=artifact.final_url,
                        retrieved_at=artifact.retrieved_at,
                        event_at=artifact.event_at,
                    )
                    try:
                        if result.observations is None:
                            raise ValueError("observation_result_too_large")
                        observation_metadata = serialize_observed_extraction_metadata(result.observations)
                        if len(observation_metadata.model_dump_json(warnings="error")) > _MAX_OBSERVATION_RESULT_BYTES:
                            raise ValueError("observation_result_too_large")
                        source_proof = encode_source_proof(PersistedSourceProof(
                            format_version="source-proof/1.0", input_ref=result.input_ref,
                            context=context, decision=decision, artifact=artifact,
                            spans=result.spans,
                        ))
                    except ValueError:
                        occurrences.append(self._failed_occurrence(
                            index, source, input_ref,
                            self._diagnostic("bounded_result_rejected", "Source proof or observation metadata exceeds the evidence boundary."),
                            result.actual_resources, FetchStatus.TOOL_ERROR,
                        ))
                        continue
                    occurrences.append(
                        WebsiteTextOccurrence(
                            source,
                            index,
                            input_ref,
                            OutcomeStatus.SUCCESS,
                            self._outputs_from_text(result.normalized.text),
                            actual_resources=result.actual_resources,
                            fetch_status=result.fetch_status,
                            artifact_reference=artifact,
                            span_references=result.spans,
                            source_proof=source_proof,
                            observation_result=result.observations,
                        )
                    )
                elif result.capture.state is ArtifactState.HOLD:
                    occurrences.append(
                        WebsiteTextOccurrence(
                            source, index, input_ref, OutcomeStatus.HOLD,
                            diagnostic=self._diagnostic(result.capture.reason, "Reviewed source retention is unavailable."),
                            actual_resources=result.actual_resources,
                            fetch_status=result.fetch_status,
                        )
                    )
                else:
                    occurrences.append(self._failed_occurrence(index, source, input_ref, self._diagnostic(result.capture.reason, "Source proof requires review and revalidation."), result.actual_resources, FetchStatus.TOOL_ERROR))
            elif result.fetch_status is FetchStatus.CANCELLED:
                occurrences.append(
                    self._cancelled_occurrence(
                        index, source, input_ref, result.actual_resources
                    )
                )
            else:
                occurrences.append(
                    self._failed_occurrence(
                        index,
                        source,
                        input_ref,
                        result.diagnostic,
                        result.actual_resources,
                        result.fetch_status,
                    )
                )
        return tuple(occurrences)

    async def _scan_occurrences(
        self,
        data: Sequence[Website],
        *,
        start_index=0,
        operation_id: str | None = None,
    ):
        return await self._scan_occurrence_specs(
            tuple(
                (start_index + offset, source, canonical_input_hash(source))
                for offset, source in enumerate(data)
            ),
            operation_id=operation_id,
        )

    @staticmethod
    def _legacy_outputs(occurrences):
        return [
            output
            for occurrence in occurrences
            if occurrence.status is OutcomeStatus.SUCCESS
            for output in occurrence.outputs
        ]

    async def scan(
        self, data: List[Website], *, operation_id: str | None = None
    ):
        return list(await self._scan_occurrences(data, operation_id=operation_id))
