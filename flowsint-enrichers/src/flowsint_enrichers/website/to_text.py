"""Website text extraction through the shared admitted HTTP implementation."""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Sequence
from urllib.parse import urlsplit

from flowsint_core.core.enricher_base import Enricher
from flowsint_core.core.forensics import legacy_execution_boundary
from flowsint_core.core.logger import Logger
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
from flowsint_execution.artifacts import ArtifactState, FilesystemArtifactStore, RetentionAuthority
from flowsint_execution.models import (
    InputOutcome,
    OutcomeStatus,
    RedactedDiagnostic,
    StructuredExecutionResult,
    canonical_input_hash,
)
from flowsint_types.phrase import Phrase
from flowsint_types.website import Website
from flowsint_enrichers.registry import flowsint_enricher

_CAPABILITY_DIGEST = hashlib.sha256(b"flowsint.website-to-text.fetch.v1").hexdigest()
_CALLER_ID = "website-to-text"
_SCOPE = "local-web-fetch"


class WebsiteFetchError(RuntimeError):
    """Legacy-list execution could not represent one or more typed failures."""


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

    def to_input_outcome(self) -> InputOutcome:
        return InputOutcome(
            input_ref=self.input_ref,
            status=self.status,
            outputs=self.outputs,
            diagnostic=self.diagnostic,
        )


@flowsint_enricher
class WebsiteToText(Enricher):
    """Extract readable text using locally scoped admitted async HTTP."""

    InputType = Website
    OutputType = Phrase

    def __init__(self, *args, artifact_store: FilesystemArtifactStore | None = None, retention_authority: RetentionAuthority | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self._artifact_store = artifact_store
        self._retention_authority = retention_authority

    @classmethod
    def name(cls):
        return "website_to_text"

    @classmethod
    def category(cls):
        return "Website"

    @classmethod
    def key(cls):
        return "website"

    @classmethod
    def get_params_schema(cls) -> List[Dict[str, Any]]:
        return [
            {
                "name": "enable_quic",
                "type": "bool",
                "default": False,
                "label": "Enable QUIC/HTTP3 probe",
                "description": "Legacy option retained only to reject unsafe QUIC requests explicitly",
            },
            {
                "name": "max_concurrency",
                "type": "int",
                "default": 10,
                "label": "Max concurrent connections",
                "description": "Maximum simultaneous admitted HTTP operations",
            },
            {
                "name": "request_timeout",
                "type": "int",
                "default": 10,
                "label": "Total timeout (seconds)",
                "description": "Deadline covering dispatch, redirects, retries and streaming",
            },
            {
                "name": "max_response_bytes",
                "type": "int",
                "default": 1048576,
                "label": "Maximum bytes per input",
                "description": "Finite response allocation applied while streaming",
            },
            {
                "name": "max_redirects",
                "type": "int",
                "default": 3,
                "label": "Maximum same-origin redirects",
                "description": "Destinations are checked before dispatch",
            },
            {
                "name": "max_retries",
                "type": "int",
                "default": 0,
                "label": "Maximum retries",
                "description": "Retries share the original allocation",
            },
        ]

    def _outputs_from_text(self, text):
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

    def _build_operation(self, specs):
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
            operation_id=f"website-to-text-{uuid.uuid4().hex}",
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

    async def _scan_occurrence_specs(self, specs: Sequence[tuple[int, Website, str]]):
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
            operation = self._build_operation(specs)
            decision = (
                self._retention_authority.issue(operation.operation_id)
                if self._retention_authority is not None
                else None
            )
            fetched = await execute_fetch_with_source_proof(
                operation,
                artifact_store=self._artifact_store,
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
                    occurrences.append(
                        WebsiteTextOccurrence(
                            source,
                            index,
                            input_ref,
                            OutcomeStatus.SUCCESS,
                            self._outputs_from_text(result.normalized.text),
                            actual_resources=result.actual_resources,
                            fetch_status=result.fetch_status,
                            artifact_reference=result.capture.artifact,
                            span_references=result.spans,
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

    async def _scan_occurrences(self, data: Sequence[Website], *, start_index=0):
        return await self._scan_occurrence_specs(
            tuple(
                (start_index + offset, source, canonical_input_hash(source))
                for offset, source in enumerate(data)
            )
        )

    @staticmethod
    def _legacy_outputs(occurrences):
        return [
            output
            for occurrence in occurrences
            if occurrence.status is OutcomeStatus.SUCCESS
            for output in occurrence.outputs
        ]

    async def scan(self, data: List[InputType]):
        return list(await self._scan_occurrences(data))

    def postprocess(self, occurrences):
        retained = tuple(occurrences)
        if any(not isinstance(item, WebsiteTextOccurrence) for item in retained):
            raise TypeError(
                "WebsiteToText.postprocess requires occurrence envelopes from scan()."
            )
        self._postprocess_occurrences(retained)
        return self._legacy_outputs(retained)

    def _postprocess_occurrences(self, occurrences):
        if not self._graph_service:
            return
        for occurrence in occurrences:
            if occurrence.status is not OutcomeStatus.SUCCESS:
                continue
            self.create_node(occurrence.source)
            for output in occurrence.outputs:
                if output.text:
                    self.create_node(output)
                    self.create_relationship(
                        occurrence.source, output, "HAS_INNER_TEXT"
                    )
                    self.log_graph_message(
                        f"Extracted text from {occurrence.source.url} ({len(output.text)} chars)."
                    )

    @legacy_execution_boundary("legacy_enricher_execute")
    async def execute(self, values: List[Any]):
        if self.name() != "enricher_orchestrator":
            Logger.info(self.sketch_id, {"message": f"Enricher {self.name()} started."})
        try:
            await self.async_init()
            occurrences = await self.scan(self.preprocess(values))
            processed = self.postprocess(occurrences)
            self._graph_service.flush()
            failures = tuple(
                occurrence
                for occurrence in occurrences
                if occurrence.status is not OutcomeStatus.SUCCESS
            )
            if failures:
                codes = ",".join(
                    sorted(
                        {
                            occurrence.diagnostic.code
                            for occurrence in failures
                            if occurrence.diagnostic is not None
                        }
                    )
                )
                raise WebsiteFetchError(
                    f"Website fetch failed for {len(failures)} occurrence(s): {codes}"
                )
            if self.name() != "enricher_orchestrator":
                Logger.completed(
                    self.sketch_id, {"message": f"Enricher {self.name()} finished."}
                )
            return processed
        except asyncio.CancelledError:
            raise
        except Exception:
            if self.name() != "enricher_orchestrator":
                Logger.error(
                    self.sketch_id, {"message": f"Enricher {self.name()} errored."}
                )
            raise

    async def execute_structured(self, values: List[Any]):
        outcomes: list[InputOutcome | None] = [None] * len(values)
        if self.name() != "enricher_orchestrator":
            Logger.info(self.sketch_id, {"message": f"Enricher {self.name()} started."})
        try:
            await self.async_init()
        except asyncio.CancelledError:
            outcomes = [
                InputOutcome(
                    input_ref=canonical_input_hash(v),
                    status=OutcomeStatus.HOLD,
                    diagnostic=self._diagnostic(
                        "cancelled", "The operation was cancelled before completion."
                    ),
                )
                for v in values
            ]
        except Exception as error:
            diagnostic = self._classify_structured_exception(error)
            outcomes = [
                InputOutcome(
                    input_ref=canonical_input_hash(v),
                    status=OutcomeStatus.FAILURE,
                    diagnostic=diagnostic,
                )
                for v in values
            ]
        else:
            adapter, field = self._input_validation_context()
            specs = []
            for index, value in enumerate(values):
                try:
                    source = self._validate_single_input(
                        value, adapter=adapter, primary_field=field
                    )
                except Exception as error:
                    outcomes[index] = InputOutcome(
                        input_ref=canonical_input_hash(value),
                        status=OutcomeStatus.FAILURE,
                        diagnostic=self._classify_structured_exception(error),
                    )
                else:
                    specs.append((index, source, canonical_input_hash(value)))
            try:
                occurrences = await self._scan_occurrence_specs(specs)
                self.postprocess(occurrences)
            except asyncio.CancelledError:
                for index, value in enumerate(values):
                    if outcomes[index] is None:
                        outcomes[index] = InputOutcome(
                            input_ref=canonical_input_hash(value),
                            status=OutcomeStatus.HOLD,
                            diagnostic=self._diagnostic(
                                "cancelled",
                                "The operation was cancelled before completion.",
                            ),
                        )
            except Exception as error:
                diagnostic = self._classify_structured_exception(error)
                for index, value in enumerate(values):
                    if outcomes[index] is None:
                        outcomes[index] = InputOutcome(
                            input_ref=canonical_input_hash(value),
                            status=OutcomeStatus.FAILURE,
                            diagnostic=diagnostic,
                        )
            else:
                for occurrence in occurrences:
                    outcomes[occurrence.index] = occurrence.to_input_outcome()
        try:
            self._graph_service.flush()
        except Exception:
            Logger.error(
                self.sketch_id,
                {"message": f"Enricher {self.name()} graph flush failed."},
            )
        final = tuple(
            outcome
            or InputOutcome(
                input_ref=canonical_input_hash(values[index]),
                status=OutcomeStatus.FAILURE,
                diagnostic=self._diagnostic(
                    "unexpected_error", "Processing ended without a terminal outcome."
                ),
            )
            for index, outcome in enumerate(outcomes)
        )
        if self.name() != "enricher_orchestrator":
            Logger.completed(
                self.sketch_id, {"message": f"Enricher {self.name()} finished."}
            )
        return StructuredExecutionResult(enricher_name=self.name(), outcomes=final)


InputType = WebsiteToText.InputType
OutputType = WebsiteToText.OutputType
