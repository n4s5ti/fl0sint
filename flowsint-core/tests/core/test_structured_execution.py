"""Focused contracts for opt-in structured enricher execution."""

from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest
from flowsint_types.domain import Domain
from pydantic import ValidationError

from flowsint_core.core.enricher_base import Enricher
from flowsint_core.core.execution import (
    EvidenceEnvelope,
    OutcomeStatus,
    RedactedDiagnostic,
    canonical_input_hash,
)


class StructuredExecutionEnricher(Enricher):
    InputType = Domain
    OutputType = Domain

    @classmethod
    def name(cls) -> str:
        return "structured-execution-test"

    @classmethod
    def category(cls) -> str:
        return "Test"

    @classmethod
    def key(cls) -> str:
        return "domain"

    async def scan(self, values: list[Domain]) -> list[dict[str, str]]:
        domain = values[0].domain
        if domain == "failure.example":
            raise RuntimeError(
                "input-secret failure at https://sensitive.example/raw-body-secret"
            )
        if domain == "empty.example":
            return []
        if domain == "many.example":
            return [{"domain": "first.example"}, {"domain": "second.example"}]
        return [{"domain": domain}]


@pytest.mark.asyncio
async def test_structured_execution_keeps_input_cardinality_and_output_groups(
    monkeypatch,
):
    logger = MagicMock()
    monkeypatch.setattr("flowsint_core.core.enricher_base.Logger", logger)
    enricher = StructuredExecutionEnricher(sketch_id="test")
    enricher._graph_service = MagicMock()

    result = await enricher.execute_structured(
        [
            Domain(domain="success.example"),
            Domain(domain="failure.example"),
            Domain(domain="many.example"),
        ]
    )

    assert [outcome.status for outcome in result.outcomes] == [
        OutcomeStatus.SUCCESS,
        OutcomeStatus.FAILURE,
        OutcomeStatus.SUCCESS,
    ]
    assert result.outcomes[0].input_ref == canonical_input_hash(
        Domain(domain="success.example")
    )
    assert [len(outcome.outputs) for outcome in result.outcomes] == [1, 0, 2]
    assert result.outcomes[2].outputs == (
        {"domain": "first.example"},
        {"domain": "second.example"},
    )
    assert result.outcomes[1].diagnostic is not None
    assert result.outcomes[1].diagnostic.code == "unexpected_error"
    redacted = result.outcomes[1].diagnostic.model_dump_json()
    assert "input-secret" not in redacted
    assert "sensitive.example" not in redacted
    assert "raw-body-secret" not in redacted
    enricher._graph_service.flush.assert_called_once_with()


@pytest.mark.asyncio
async def test_structured_execution_distinguishes_empty_success_from_failure(
    monkeypatch,
):
    monkeypatch.setattr("flowsint_core.core.enricher_base.Logger", MagicMock())
    enricher = StructuredExecutionEnricher(sketch_id="test")
    enricher._graph_service = MagicMock()

    result = await enricher.execute_structured(
        [Domain(domain="empty.example"), Domain(domain="failure.example")]
    )

    assert result.outcomes[0].status is OutcomeStatus.SUCCESS
    assert result.outcomes[0].outputs == ()
    assert result.outcomes[0].diagnostic is None
    assert result.outcomes[1].status is OutcomeStatus.FAILURE
    assert result.outcomes[1].outputs == ()


def test_canonical_input_hash_is_stable_for_equivalent_input_shapes():
    first: dict[str, Any] = {"domain": "stable.example", "metadata": {"a": 1, "b": 2}}
    second = {"metadata": {"b": 2, "a": 1}, "domain": "stable.example"}

    assert canonical_input_hash(first) == canonical_input_hash(second)


def test_evidence_envelope_enforces_retainable_metadata():
    input_ref = canonical_input_hash({"domain": "evidence.example"})
    envelope = EvidenceEnvelope(
        input_ref=input_ref,
        request_url_pattern="https://api.example/{{domain}}",
        artifact_sha256="a" * 64,
        artifact_reference=f"body:sha256:{'a' * 64}",
        source_rights="test-only",
        schema_version="1",
        parser_version="1",
        confidence=0.5,
        verification_state="verified",
        observed_at=datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=2))),
    )

    assert envelope.observed_at.tzinfo is timezone.utc
    assert set(
        RedactedDiagnostic(
            code="timeout", safe_message="The operation timed out.", retryable=True
        ).model_dump()
    ) == {"code", "safe_message", "retryable"}
    with pytest.raises(ValidationError):
        EvidenceEnvelope(
            input_ref=input_ref,
            request_url_pattern="https://api.example/{{domain}}",
            artifact_sha256="not-a-hash",
            source_rights="test-only",
            schema_version="1",
            parser_version="1",
            confidence=1.1,
            verification_state="verified",
            observed_at=datetime(2026, 1, 1),
        )
