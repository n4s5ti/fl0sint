"""Focused tests for registry-backed connector egress."""

import asyncio
from pathlib import Path

from contextlib import asynccontextmanager
from unittest.mock import MagicMock

import httpx
import pytest
from pydantic import ValidationError

from flowsint_core.core.connector_egress import (
    ConnectorEndpointDefinition,
    ConnectorPolicyError,
    ConnectorRequestField,
    DestinationDefinition,
    DestinationRegistry,
    DestinationRegistryDocument,
    EgressAuthorizer,
)
from flowsint_core.core.execution import OutcomeStatus
from flowsint_core.core.template_enricher import TemplateEnricher
from flowsint_core.templates.loader.yaml_loader import YamlLoader
from flowsint_core.templates.types import (
    Template,
    TemplateConnector,
    TemplateEvidenceConfig,
    TemplateInput,
    TemplateOutput,
)
from flowsint_types import Location


def registry(
    *,
    request_fields: tuple[ConnectorRequestField, ...] | None = None,
    secret_headers: dict[str, str] | None = None,
    response_mappings: dict[str, str] | None = None,
    timeout_seconds: float = 1,
) -> DestinationRegistry:
    endpoint = ConnectorEndpointDefinition(
        endpoint_id="lookup",
        capability="enrich.read",
        method="GET",
        path="/lookup/{address}",
        input_type="Location",
        output_type="Location",
        request_fields=request_fields
        or (
            ConnectorRequestField(
                name="address",
                source_field="address",
                location="path",
                value_type="string",
            ),
        ),
        response_mappings=(
            response_mappings
            if response_mappings is not None
            else {
                "address": "normalized",
                "city": "city",
                "country": "country",
                "zip": "zip",
            }
        ),
        secret_headers=secret_headers or {},
        timeout_seconds=timeout_seconds,
        max_response_bytes=128,
    )
    return DestinationRegistry(
        DestinationRegistryDocument(
            version=1,
            destinations=(
                DestinationDefinition(
                    destination_id="approved_directory",
                    base_url="https://approved.example",
                    endpoints=(endpoint,),
                ),
            ),
        )
    )


def template(*, destination_id: str = "approved_directory") -> Template:
    return Template(
        name="approved-location-lookup",
        description="Registry-backed test connector",
        category="Location",
        version=1.0,
        input=TemplateInput(type="Location", key="address"),
        connector=TemplateConnector(
            destination_id=destination_id,
            endpoint_id="lookup",
            capability="enrich.read",
        ),
        output=TemplateOutput(type="Location"),
        evidence=TemplateEvidenceConfig(source_rights="test-only"),
    )


def location(address: str = "input address") -> Location:
    return Location(address=address, city="Test City", country="US", zip="00000")


class RecordingVault:
    def __init__(self, secret: str = "header-secret"):
        self.secret = secret
        self.calls = 0

    def get_secret(self, name: str) -> str:
        self.calls += 1
        return self.secret


@asynccontextmanager
async def mock_client(handler):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        follow_redirects=False,
        trust_env=False,
    ) as client:
        yield client


def patch_client(monkeypatch, handler) -> None:
    @asynccontextmanager
    async def context(_self):
        async with mock_client(handler) as client:
            yield client

    monkeypatch.setattr(TemplateEnricher, "_structured_execution_context", context)


class TestRegistryBackedTemplateEnricher:
    @pytest.mark.asyncio
    async def test_approved_enrichment_succeeds(self, monkeypatch):
        requests = []

        def handler(request):
            requests.append(request)
            return httpx.Response(
                200,
                json={
                    "normalized": "Approved address",
                    "city": "Test City",
                    "country": "US",
                    "zip": "00000",
                },
            )

        patch_client(monkeypatch, handler)
        enricher = TemplateEnricher(
            template=template(),
            registry=registry(),
            runtime_authorizer=EgressAuthorizer.enrichment(),
            sketch_id="safe-sketch",
        )
        enricher._graph_service = MagicMock()

        result = await enricher.execute_structured([location()])

        assert result.outcomes[0].status is OutcomeStatus.SUCCESS
        assert result.outcomes[0].outputs[0].address == "Approved address"
        assert requests[0].url.host == "approved.example"
        assert str(requests[0].url).endswith("/lookup/input%20address")


    @pytest.mark.asyncio
    async def test_connector_client_disables_ambient_proxy_and_ca_settings(
        self, monkeypatch
    ):
        initialized = {}

        class RecordingClient:
            def __init__(self, **kwargs):
                initialized.update(kwargs)

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

        monkeypatch.setenv("HTTPS_PROXY", "https://ambient-proxy.example")
        monkeypatch.setenv("SSL_CERT_FILE", "/tmp/ambient-ca.pem")
        monkeypatch.setattr(
            "flowsint_core.core.template_enricher.httpx.AsyncClient",
            RecordingClient,
        )
        enricher = TemplateEnricher(
            template=template(),
            registry=registry(),
            runtime_authorizer=EgressAuthorizer.enrichment(),
        )

        async with enricher._structured_execution_context():
            pass

        assert initialized == {"follow_redirects": False, "trust_env": False}
    def test_unknown_destination_is_denied_before_vault_or_http(self):
        vault = RecordingVault()
        network_called = False
        with pytest.raises(ConnectorPolicyError):
            TemplateEnricher(
                template=template(destination_id="unapproved_destination"),
                registry=registry(),
                runtime_authorizer=EgressAuthorizer.enrichment(),
                vault=vault,
            )
        assert vault.calls == 0
        assert network_called is False

    def test_empty_default_registry_fails_closed(self):
        with pytest.raises(ConnectorPolicyError):
            TemplateEnricher(
                template=template(),
                registry=DestinationRegistry.empty(),
                runtime_authorizer=EgressAuthorizer.enrichment(),
            )

    def test_unknown_input_field_is_denied_before_vault_or_http(self):
        vault = RecordingVault()
        with pytest.raises(ConnectorPolicyError):
            TemplateEnricher(
                template=template(),
                registry=registry(
                    request_fields=(
                        ConnectorRequestField(
                            name="address",
                            source_field="address",
                            location="path",
                            value_type="string",
                        ),
                        ConnectorRequestField(
                            name="forbidden",
                            source_field="forbidden",
                            location="query",
                            value_type="string",
                        ),
                    )
                ),
                runtime_authorizer=EgressAuthorizer.enrichment(),
                vault=vault,
            )
        assert vault.calls == 0


    def test_incomplete_output_mapping_is_denied_before_vault_or_http(self):
        vault = RecordingVault()

        with pytest.raises(ConnectorPolicyError):
            TemplateEnricher(
                template=template(),
                registry=registry(
                    response_mappings={
                        "address": "normalized",
                        "city": "city",
                        "country": "country",
                    }
                ),
                runtime_authorizer=EgressAuthorizer.enrichment(),
                vault=vault,
            )

        assert vault.calls == 0
    @pytest.mark.asyncio
    async def test_unknown_supplied_input_field_never_reaches_http(self, monkeypatch):
        requests = []

        def handler(request):
            requests.append(request)
            return httpx.Response(200, json={"normalized": "unused"})

        patch_client(monkeypatch, handler)
        enricher = TemplateEnricher(
            template=template(),
            registry=registry(),
            runtime_authorizer=EgressAuthorizer.enrichment(),
        )
        enricher._graph_service = MagicMock()

        result = await enricher.execute_structured(
            [{"address": "approved", "unapproved": "denied"}]
        )

        assert result.outcomes[0].status is OutcomeStatus.FAILURE
        assert result.outcomes[0].diagnostic.code == "connector_validation_failed"
        assert requests == []

    def test_non_enrichment_action_cannot_be_declared(self):
        with pytest.raises(ValidationError):
            Template.model_validate(
                {
                    **template().model_dump(mode="json"),
                    "connector": {
                        "destination_id": "approved_directory",
                        "endpoint_id": "lookup",
                        "capability": "outreach.send",
                    },
                }
            )

    def test_runtime_authority_must_independently_grant_enrichment(self):
        with pytest.raises(ConnectorPolicyError):
            TemplateEnricher(
                template=template(),
                registry=registry(),
                runtime_authorizer=EgressAuthorizer(),
            )

    @pytest.mark.asyncio
    async def test_redirect_is_not_followed(self, monkeypatch):
        requests = []

        def handler(request):
            requests.append(request)
            return httpx.Response(
                302,
                headers={"location": "https://unapproved.example/redirected"},
            )

        patch_client(monkeypatch, handler)
        enricher = TemplateEnricher(
            template=template(),
            registry=registry(),
            runtime_authorizer=EgressAuthorizer.enrichment(),
        )
        enricher._graph_service = MagicMock()

        result = await enricher.execute_structured([location()])

        assert len(requests) == 1
        assert result.outcomes[0].status is OutcomeStatus.FAILURE
        assert result.outcomes[0].diagnostic.code == "connector_response_invalid"
        assert "unapproved.example" not in result.model_dump_json()

    @pytest.mark.asyncio
    async def test_response_over_policy_limit_is_rejected(self, monkeypatch):
        def handler(_request):
            return httpx.Response(200, content=b"x" * 129)

        patch_client(monkeypatch, handler)
        enricher = TemplateEnricher(
            template=template(),
            registry=registry(),
            runtime_authorizer=EgressAuthorizer.enrichment(),
        )
        enricher._graph_service = MagicMock()

        result = await enricher.execute_structured([location()])

        assert result.outcomes[0].status is OutcomeStatus.FAILURE
        assert result.outcomes[0].diagnostic.code == "connector_response_invalid"


    @pytest.mark.asyncio
    async def test_total_response_deadline_covers_full_streamed_body(self, monkeypatch):
        class SlowDripStream(httpx.AsyncByteStream):
            async def __aiter__(self):
                payload = (
                    b'{"normalized":"untrusted-body-material","city":"Test City",'
                    b'"country":"US","zip":"00000"}'
                )
                for chunk in (payload[:40], payload[40:]):
                    await asyncio.sleep(0.06)
                    yield chunk

            async def aclose(self):
                return None

        def handler(_request):
            return httpx.Response(200, stream=SlowDripStream())

        patch_client(monkeypatch, handler)
        enricher = TemplateEnricher(
            template=template(),
            registry=registry(timeout_seconds=0.1),
            runtime_authorizer=EgressAuthorizer.enrichment(),
        )
        enricher._graph_service = MagicMock()

        result = await enricher.execute_structured([location()])

        outcome = result.outcomes[0]
        assert outcome.status is OutcomeStatus.FAILURE
        assert outcome.diagnostic is not None
        assert outcome.diagnostic.code == "connector_timeout"
        assert outcome.diagnostic.retryable is True
        assert "untrusted-body-material" not in result.model_dump_json()
    @pytest.mark.asyncio
    async def test_secret_and_raw_response_are_not_in_diagnostics_or_logs(
        self, monkeypatch
    ):
        logger = MagicMock()
        monkeypatch.setattr("flowsint_core.core.template_enricher.Logger", logger)

        def handler(request):
            assert request.headers["Authorization"] == "header-secret"
            return httpx.Response(
                200,
                json={
                    "normalized": "safe mapped output",
                    "city": "Test City",
                    "country": "US",
                    "zip": "00000",
                    "document": "raw document and personal material",
                },
            )

        patch_client(monkeypatch, handler)
        vault = RecordingVault()
        enricher = TemplateEnricher(
            template=template(),
            registry=registry(secret_headers={"Authorization": "connector-token"}),
            runtime_authorizer=EgressAuthorizer.enrichment(),
            vault=vault,
            sketch_id="safe-sketch",
        )
        enricher._graph_service = MagicMock()

        result = await enricher.execute_structured([location()])

        serialized = result.model_dump_json()
        logged = str(logger.info.call_args)
        assert vault.calls == 1
        assert "header-secret" not in serialized
        assert "raw document and personal material" not in serialized
        assert "input address" not in logged
        assert "header-secret" not in logged
        assert "raw document and personal material" not in logged


class TestConnectorTemplateFixture:
    def test_wholesaler_preview_fixture_uses_only_registry_selection(self):
        template_fixture = YamlLoader.get_template_from_file(
            str(Path(__file__).parent / "wholesaler-address-preview.yaml")
        )

        assert template_fixture.connector.destination_id == "census_geocoder"
        assert template_fixture.connector.endpoint_id == "location_oneline"
        assert template_fixture.connector.capability == "enrich.read"
        assert "request" not in template_fixture.model_dump(mode="json")
        assert "secrets" not in template_fixture.model_dump(mode="json")