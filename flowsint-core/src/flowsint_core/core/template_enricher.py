"""Strict, registry-backed connector template execution."""

from __future__ import annotations

import asyncio
import hashlib
import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncIterator, List, Optional
from urllib.parse import quote

import httpx
from flowsint_types import FlowsintType, get_type
from flowsint_types.registry import load_all_types

from flowsint_core.core.connector_egress import (
    ConnectorCredentialUnavailable,
    ConnectorPolicyError,
    ConnectorRedirectDenied,
    ConnectorResponseTooLarge,
    ConnectorValidationError,
    DestinationRegistry,
    EgressAuthorizer,
)
from flowsint_core.core.enricher_base import Enricher
from flowsint_core.core.execution import (
    EvidenceEnvelope,
    InputOutcome,
    OutcomeStatus,
    RedactedDiagnostic,
    StructuredExecutionResult,
    canonical_input_hash,
)
from flowsint_core.core.logger import Logger
from flowsint_core.templates.types import Template


class TemplateEnricherError(Exception):
    """A safe connector response-processing failure."""


class TemplateEnricher(Enricher):
    """Execute a template only through a resolved deployment-owned endpoint."""

    InputType = FlowsintType
    OutputType = FlowsintType

    def __init__(
        self,
        template: Template,
        registry: DestinationRegistry,
        runtime_authorizer: EgressAuthorizer,
        sketch_id: Optional[str] = None,
        scan_id: Optional[str] = None,
        vault=None,
    ):
        self.template = template
        self.InputType = self._detect_type(template.input.type)
        self.OutputType = self._detect_type(template.output.type)
        self.endpoint = registry.resolve(
            template.connector, template.input.type, template.output.type
        )
        runtime_authorizer.require(self.endpoint.capability)
        super().__init__(
            sketch_id=sketch_id,
            scan_id=scan_id,
            vault=vault,
            params_schema=[],
            params={},
        )
        self._last_response_artifact_sha256: str | None = None
        self._last_http_status_class: int | None = None

    @staticmethod
    def _detect_type(type_name: str) -> type[FlowsintType]:
        load_all_types()
        detected_type = get_type(type_name, case_sensitive=True)
        if detected_type is None:
            raise ConnectorPolicyError("connector type denied")
        return detected_type

    def name(self) -> str:  # type: ignore[override]
        return f"connector:{self.endpoint.destination_id}:{self.endpoint.endpoint_id}"

    def category(self) -> str:  # type: ignore[override]
        return self.template.category

    def key(self) -> str:  # type: ignore[override]
        return self.template.input.key

    @classmethod
    def documentation(cls) -> str:
        return "Registry-backed enrichment connector."

    async def async_init(self) -> None:
        """Do not resolve credentials before per-input policy validation."""


    def _reject_unknown_input_fields(self, value: Any) -> None:
        if not isinstance(value, dict):
            return
        allowed_fields = set(self.InputType.model_fields)
        if set(value) - allowed_fields:
            raise ConnectorValidationError("connector input field invalid")

    def _validated_request_values(self, input_obj: Any) -> dict[str, Any]:
        values: dict[str, Any] = {}
        for field in self.endpoint.definition.request_fields:
            value = getattr(input_obj, field.source_field, None)
            if value is None:
                if field.required:
                    raise ConnectorValidationError("connector input field invalid")
                continue
            if field.value_type == "string":
                if not isinstance(value, str) or len(value) > field.max_length:
                    raise ConnectorValidationError("connector input field invalid")
            elif field.value_type == "integer":
                if isinstance(value, bool) or not isinstance(value, int):
                    raise ConnectorValidationError("connector input field invalid")
            elif field.value_type == "number":
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise ConnectorValidationError("connector input field invalid")
            elif field.value_type == "boolean" and not isinstance(value, bool):
                raise ConnectorValidationError("connector input field invalid")
            values[field.name] = value
        return values

    def _request_parts(
        self, input_obj: Any
    ) -> tuple[str, dict[str, str], dict[str, Any], dict[str, Any] | None]:
        values = self._validated_request_values(input_obj)
        definition = self.endpoint.definition
        path = definition.path
        query: dict[str, Any] = dict(definition.fixed_query)
        json_body: dict[str, Any] = {}
        for field in definition.request_fields:
            if field.name not in values:
                continue
            value = values[field.name]
            if field.location == "path":
                path = path.replace("{" + field.name + "}", quote(str(value), safe=""))
            elif field.location == "query":
                if field.name in query:
                    raise ConnectorPolicyError("connector query field denied")
                query[field.name] = value
            else:
                json_body[field.name] = value

        url = f"{self.endpoint.base_url}{path}"
        parsed = httpx.URL(url)
        base = httpx.URL(self.endpoint.base_url)
        if (
            parsed.scheme != "https"
            or parsed.scheme != base.scheme
            or parsed.host != base.host
            or parsed.port != base.port
        ):
            raise ConnectorPolicyError("connector origin denied")
        return (
            url,
            dict(definition.fixed_headers),
            query,
            json_body if definition.method == "POST" else None,
        )

    def _resolved_headers(self, fixed_headers: dict[str, str]) -> dict[str, str]:
        headers = dict(fixed_headers)
        for header_name, secret_name in self.endpoint.definition.secret_headers.items():
            value = self.vault.get_secret(secret_name) if self.vault is not None else None
            if not value:
                raise ConnectorCredentialUnavailable("connector credential unavailable")
            headers[header_name] = value
        return headers

    async def _read_response(self, response: httpx.Response) -> bytes:
        limit = self.endpoint.definition.max_response_bytes
        content_length = response.headers.get("content-length")
        if content_length is not None:
            try:
                if int(content_length) > limit:
                    raise ConnectorResponseTooLarge("connector response too large")
            except ValueError:
                raise ConnectorResponseTooLarge("connector response too large") from None
        content = bytearray()
        async for chunk in response.aiter_bytes(chunk_size=min(65_536, limit)):
            if len(chunk) > limit - len(content):
                raise ConnectorResponseTooLarge("connector response too large")
            content.extend(chunk)
        return bytes(content)

    @staticmethod
    def _extract_path(value: Any, path: str) -> Any:
        current = value
        for part in path.split("."):
            if isinstance(current, list):
                try:
                    current = current[int(part)]
                except (ValueError, IndexError) as error:
                    raise TemplateEnricherError("connector response mapping failed") from error
            elif isinstance(current, dict) and part in current:
                current = current[part]
            else:
                raise TemplateEnricherError("connector response mapping failed")
        return current

    def _map_response(self, response_bytes: bytes) -> List[Any]:
        try:
            response_data = json.loads(response_bytes)
        except (TypeError, ValueError) as error:
            raise TemplateEnricherError("connector response was invalid") from error
        items = response_data
        if self.endpoint.definition.response_array_path is not None:
            items = self._extract_path(
                response_data, self.endpoint.definition.response_array_path
            )
            if not isinstance(items, list):
                raise TemplateEnricherError("connector response array was invalid")
        if not isinstance(items, list):
            items = [items]
        results = []
        for item in items:
            mapped = {
                output_field: self._extract_path(item, source_path)
                for output_field, source_path in self.endpoint.definition.response_mappings.items()
            }
            try:
                results.append(self.OutputType(**mapped))
            except Exception as error:
                raise TemplateEnricherError("connector output was invalid") from error
        return results

    async def _process_single_input(
        self, client: httpx.AsyncClient, input_obj: Any
    ) -> List[Any]:
        self._last_response_artifact_sha256 = None
        self._last_http_status_class = None
        url, fixed_headers, query, json_body = self._request_parts(input_obj)
        headers = self._resolved_headers(fixed_headers)
        try:
            async with asyncio.timeout(self.endpoint.definition.timeout_seconds):
                async with client.stream(
                    self.endpoint.definition.method,
                    url,
                    headers=headers,
                    params=query,
                    json=json_body,
                    timeout=self.endpoint.definition.timeout_seconds,
                ) as response:
                    if 300 <= response.status_code < 400:
                        raise ConnectorRedirectDenied("connector redirect denied")
                    self._last_http_status_class = response.status_code // 100
                    response.raise_for_status()
                    response_bytes = await self._read_response(response)
        except TimeoutError as error:
            raise httpx.ReadTimeout("connector deadline exceeded") from error
        self._last_response_artifact_sha256 = hashlib.sha256(response_bytes).hexdigest()
        return self._map_response(response_bytes)

    @asynccontextmanager
    async def _structured_execution_context(self) -> AsyncIterator[httpx.AsyncClient]:
        async with httpx.AsyncClient(follow_redirects=False, trust_env=False) as client:
            yield client

    def _build_structured_evidence(
        self, input_obj: Any, input_ref: str
    ) -> tuple[EvidenceEnvelope, ...]:
        evidence = self.template.evidence
        artifact_sha256 = self._last_response_artifact_sha256
        return (
            EvidenceEnvelope(
                input_ref=input_ref,
                destination_id=self.endpoint.destination_id,
                endpoint_id=self.endpoint.endpoint_id,
                capability=self.endpoint.capability,
                policy_version=self.endpoint.policy_version,
                artifact_sha256=artifact_sha256,
                artifact_reference=(
                    f"body:sha256:{artifact_sha256}"
                    if artifact_sha256 is not None
                    else None
                ),
                source_rights=evidence.source_rights,
                schema_version=evidence.schema_version,
                parser_version=evidence.parser_version,
                confidence=evidence.confidence,
                verification_state=evidence.verification_state,
                retrieved_at=datetime.now(timezone.utc),
                ingested_at=datetime.now(timezone.utc),
            ),
        )

    def _classify_structured_exception(self, error: Exception) -> RedactedDiagnostic:
        if isinstance(error, ConnectorPolicyError):
            return RedactedDiagnostic(
                code="connector_policy_denied",
                safe_message="Connector policy denied the request.",
                retryable=False,
            )
        if isinstance(error, (ConnectorValidationError,)):
            return RedactedDiagnostic(
                code="connector_validation_failed",
                safe_message="Connector input validation failed.",
                retryable=False,
            )
        if isinstance(error, ConnectorCredentialUnavailable):
            return RedactedDiagnostic(
                code="connector_credential_unavailable",
                safe_message="Connector credentials are unavailable.",
                retryable=False,
            )
        if isinstance(error, httpx.TimeoutException):
            return RedactedDiagnostic(
                code="connector_timeout",
                safe_message="Connector request timed out.",
                retryable=True,
            )
        if isinstance(error, httpx.HTTPStatusError):
            return RedactedDiagnostic(
                code="connector_http_error",
                safe_message="Connector request failed.",
                retryable=error.response.status_code >= 500
                or error.response.status_code == 429,
            )
        if isinstance(
            error,
            (ConnectorRedirectDenied, ConnectorResponseTooLarge, TemplateEnricherError),
        ):
            return RedactedDiagnostic(
                code="connector_response_invalid",
                safe_message="Connector response was rejected.",
                retryable=False,
            )
        return RedactedDiagnostic(
            code="connector_response_invalid",
            safe_message="Connector response was rejected.",
            retryable=False,
        )

    def _classify_structured_success(
        self,
        outputs: tuple[Any, ...],
        evidence: tuple[EvidenceEnvelope, ...],
    ) -> tuple[OutcomeStatus, RedactedDiagnostic | None]:
        if outputs and self.template.evidence.source_rights == "unspecified":
            return (
                OutcomeStatus.HOLD,
                RedactedDiagnostic(
                    code="source_rights_unspecified",
                    safe_message="Source rights must be specified before outputs can be retained.",
                    retryable=False,
                ),
            )
        return OutcomeStatus.SUCCESS, None

    async def execute_structured(self, values: List[Any]) -> StructuredExecutionResult:
        """Execute inputs independently without logging, retaining, or returning raw egress data."""
        outcomes: list[InputOutcome] = []
        try:
            async with self._structured_execution_context() as client:
                for original_input in values:
                    input_ref = canonical_input_hash(original_input)
                    self._last_response_artifact_sha256 = None
                    self._last_http_status_class = None
                    try:
                        self._reject_unknown_input_fields(original_input)
                        input_obj = self._validate_single_input(original_input)
                        processed = await self._process_single_input(client, input_obj)
                        evidence = self._build_structured_evidence(input_obj, input_ref)
                        status, diagnostic = self._classify_structured_success(
                            tuple(processed), evidence
                        )
                        outcomes.append(
                            InputOutcome(
                                input_ref=input_ref,
                                status=status,
                                outputs=tuple(processed)
                                if status is OutcomeStatus.SUCCESS
                                else (),
                                diagnostic=diagnostic,
                                evidence=evidence,
                            )
                        )
                    except Exception as error:
                        outcomes.append(
                            InputOutcome(
                                input_ref=input_ref,
                                status=OutcomeStatus.FAILURE,
                                diagnostic=self._classify_structured_exception(error),
                                evidence=self._build_structured_evidence(
                                    original_input, input_ref
                                ),
                            )
                        )
        finally:
            try:
                self._graph_service.flush()
            except Exception:
                pass
        Logger.info(
            self.sketch_id,
            {
                "event": "connector_completed",
                "destination_id": self.endpoint.destination_id,
                "endpoint_id": self.endpoint.endpoint_id,
                "capability": self.endpoint.capability,
                "outcome_counts": {
                    status.value: sum(
                        outcome.status is status for outcome in outcomes
                    )
                    for status in OutcomeStatus
                },
                "http_status_class": self._last_http_status_class,
            },
        )
        return StructuredExecutionResult(enricher_name=self.name(), outcomes=tuple(outcomes))

    async def scan(self, values: List[Any]) -> List[Any]:
        result = await self.execute_structured(values)
        return [
            output
            for outcome in result.outcomes
            if outcome.status is OutcomeStatus.SUCCESS
            for output in outcome.outputs
        ]

    async def execute(self, values: List[Any]) -> List[Any]:
        return await self.scan(values)

    def postprocess(
        self, results: List[Any], input_data: Optional[List[Any]] = None
    ) -> List[Any]:
        return results


InputType = TemplateEnricher.InputType
OutputType = TemplateEnricher.OutputType
