"""API contract tests for redacted connector-template test output."""

from flowsint_core.core.auth import create_access_token
from flowsint_core.core.connector_egress import (
    ConnectorEndpointDefinition,
    ConnectorRequestField,
    DestinationDefinition,
    DestinationRegistry,
    DestinationRegistryDocument,
)
from flowsint_core.core.execution import EvidenceEnvelope, InputOutcome, OutcomeStatus, StructuredExecutionResult
from flowsint_core.core.models import EnricherTemplate, Profile
from flowsint_core.templates.types import Template
from flowsint_types import Location


def _registry() -> DestinationRegistry:
    return DestinationRegistry(
        DestinationRegistryDocument(
            version=1,
            destinations=(
                DestinationDefinition(
                    destination_id="approved_directory",
                    base_url="https://approved.example",
                    endpoints=(
                        ConnectorEndpointDefinition(
                            endpoint_id="lookup",
                            capability="enrich.read",
                            method="GET",
                            path="/lookup/{address}",
                            input_type="Location",
                            output_type="Location",
                            request_fields=(
                                ConnectorRequestField(
                                    name="address",
                                    source_field="address",
                                    location="path",
                                    value_type="string",
                                ),
                            ),
                            response_mappings={
                                "address": "normalized",
                                "city": "city",
                                "country": "country",
                                "zip": "zip",
                            },
                        ),
                    ),
                ),
            ),
        )
    )


def _template() -> Template:
    return Template.model_validate(
        {
            "name": "approved-location-lookup",
            "description": "test",
            "category": "Location",
            "version": 1.0,
            "input": {"type": "Location", "key": "address"},
            "connector": {
                "destination_id": "approved_directory",
                "endpoint_id": "lookup",
                "capability": "enrich.read",
            },
            "output": {"type": "Location"},
            "evidence": {"source_rights": "test-only"},
        }
    )


def test_template_test_route_redacts_result_and_transport_material(
    client, db_session, monkeypatch
):
    from app.api.routes import enricher_templates as routes

    user = Profile(email="template-user@example.com", hashed_password="x")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    strict_template = _template()
    stored = EnricherTemplate(
        name=strict_template.name,
        description=strict_template.description,
        category=strict_template.category,
        version=strict_template.version,
        content=strict_template.model_dump(mode="json"),
        owner_id=user.id,
    )
    db_session.add(stored)
    db_session.commit()
    db_session.refresh(stored)
    monkeypatch.setattr(routes, "destination_registry", _registry())

    captured_inputs = []

    class FakeEnricher:
        def __init__(self, **_kwargs):
            self.endpoint = type(
                "Endpoint",
                (),
                {
                    "destination_id": "approved_directory",
                    "endpoint_id": "lookup",
                    "capability": "enrich.read",
                },
            )()

        async def execute_structured(self, values):
            captured_inputs.extend(values)
            return StructuredExecutionResult(
                enricher_name="connector:approved_directory:lookup",
                outcomes=(
                    InputOutcome(
                        input_ref="b" * 64,
                        status=OutcomeStatus.SUCCESS,
                        outputs=(
                            Location(
                                address="private mapped output",
                                city="Private City",
                                country="US",
                                zip="00000",
                            ),
                        ),
                        evidence=(
                            EvidenceEnvelope(
                                input_ref="b" * 64,
                                destination_id="approved_directory",
                                endpoint_id="lookup",
                                capability="enrich.read",
                                policy_version="1",
                                artifact_sha256="a" * 64,
                                artifact_reference="body:sha256:" + "a" * 64,
                                source_rights="test-only",
                                schema_version="v1",
                                parser_version="v1",
                                confidence=1.0,
                                verification_state="verified",
                            ),
                        ),
                    ),
                ),
            )

    monkeypatch.setattr(routes, "TemplateEnricher", FakeEnricher)
    token = create_access_token({"sub": user.email})
    response = client.post(
        f"/api/enrichers/templates/{stored.id}/test",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "input_value": {
                "address": "private input",
                "city": "Private City",
                "country": "US",
                "zip": "00000",
            }
        },
    )

    assert response.status_code == 200
    assert captured_inputs == [
        {
            "address": "private input",
            "city": "Private City",
            "country": "US",
            "zip": "00000",
        }
    ]
    payload = response.json()
    assert payload["outcomes"][0]["visible_outputs"] == 1
    assert "private input" not in response.text
    assert "private mapped output" not in response.text
    assert "https://approved.example" not in response.text
    assert "Authorization" not in response.text


def test_template_update_keeps_wrapper_metadata_and_content_synchronized(
    db_session, monkeypatch
):
    from app.api.routes import enricher_templates as routes

    user = Profile(email="template-update-user@example.com", hashed_password="x")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    strict_template = _template()
    stored = EnricherTemplate(
        name=strict_template.name,
        description=strict_template.description,
        category=strict_template.category,
        version=strict_template.version,
        content=strict_template.model_dump(mode="json"),
        owner_id=user.id,
    )
    db_session.add(stored)
    db_session.commit()
    db_session.refresh(stored)
    monkeypatch.setattr(routes, "destination_registry", _registry())
    wrapper_payload = routes.update_template(
        template_id=stored.id,
        update_data=routes.EnricherTemplateUpdate(
            description="Updated through wrapper metadata"
        ),
        db=db_session,
        current_user=user,
    )

    assert wrapper_payload["description"] == "Updated through wrapper metadata"
    assert (
        wrapper_payload["content"]["description"]
        == "Updated through wrapper metadata"
    )

    updated_content = wrapper_payload["content"]
    updated_content["name"] = "approved-address-preview-v2"
    content_payload = routes.update_template(
        template_id=stored.id,
        update_data=routes.EnricherTemplateUpdate(content=updated_content),
        db=db_session,
        current_user=user,
    )

    assert content_payload["name"] == "approved-address-preview-v2"
    assert content_payload["content"]["name"] == "approved-address-preview-v2"
