"""Graph-backed website-text enricher using the shared admitted extractor."""

from __future__ import annotations

import asyncio
from os import PathLike
from typing import Any, Dict, List

from flowsint_core.core.enricher_base import Enricher
from flowsint_core.core.forensics import legacy_execution_boundary
from flowsint_core.core.logger import Logger
from flowsint_execution.artifacts import FilesystemArtifactStore, RetentionAuthority
from flowsint_execution.models import (
    InputOutcome,
    OutcomeStatus,
    StructuredExecutionResult,
    canonical_input_hash,
)
from flowsint_enrichers.registry import flowsint_enricher
from flowsint_types.phrase import Phrase
from flowsint_types.website import Website

from .text_extraction import WebsiteTextExtractor, WebsiteTextOccurrence


class WebsiteFetchError(RuntimeError):
    """Legacy-list execution could not represent one or more typed failures."""


@flowsint_enricher
class WebsiteToText(Enricher):
    """Extract readable text using shared admitted acquisition and graph orchestration."""

    InputType = Website
    OutputType = Phrase

    def __init__(
        self,
        *args,
        artifact_store: FilesystemArtifactStore | None = None,
        retention_authority: RetentionAuthority | None = None,
        runtime_config: str | PathLike[str] | None = None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self._extractor = WebsiteTextExtractor(
            params=self.params,
            artifact_store=artifact_store,
            retention_authority=retention_authority,
            runtime_config=runtime_config,
            outputs_from_text=self._outputs_from_text,
        )

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

    @staticmethod
    def _outputs_from_text(text):
        return WebsiteTextExtractor._default_outputs_from_text(text)

    @staticmethod
    def _diagnostic(code, message, retryable=False):
        return WebsiteTextExtractor._diagnostic(code, message, retryable)

    @staticmethod
    def _legacy_outputs(occurrences):
        return WebsiteTextExtractor._legacy_outputs(occurrences)

    def _sync_extractor(self):
        self._extractor.params = self.params
        self._extractor._outputs_from_text = self._outputs_from_text
        return self._extractor

    def _build_operation(self, specs, *, operation_id: str | None = None):
        return self._sync_extractor()._build_operation(specs, operation_id=operation_id)

    async def _scan_occurrence_specs(self, specs, *, operation_id: str | None = None):
        return await self._sync_extractor()._scan_occurrence_specs(specs, operation_id=operation_id)

    async def _scan_occurrences(self, data, *, start_index=0, operation_id: str | None = None):
        return await self._sync_extractor()._scan_occurrences(
            data, start_index=start_index, operation_id=operation_id
        )

    async def scan(self, data: List[Website], *, operation_id: str | None = None):
        return list(await self._scan_occurrences(data, operation_id=operation_id))

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
