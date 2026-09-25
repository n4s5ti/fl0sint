"""Tests for the GetContact phone lookup enricher."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from flowsint_enrichers import ENRICHER_REGISTRY, load_all_enrichers
from flowsint_types.phone import Phone
from flowsint_types.social_account import SocialAccount
from flowsint_types.username import Username


@pytest.fixture(autouse=True)
def reset_registry():
    load_all_enrichers()
    yield


class TestRegistry:
    def test_enricher_is_registered(self):
        assert ENRICHER_REGISTRY.enricher_exists("phone_to_getcontact")

    def test_metadata(self):
        e = ENRICHER_REGISTRY._enrichers["phone_to_getcontact"]
        assert e.name() == "phone_to_getcontact"
        assert e.category() == "phones"
        assert e.key() == "number"
        assert e.icon() is not None
        assert e.InputType is Phone
        assert e.OutputType is SocialAccount


class TestGracefulDegradation:
    @pytest.mark.asyncio
    async def test_returns_empty_on_api_failure(self):
        import flowsint_enrichers.phone.to_getcontact as mod

        with patch.object(
            mod.PhoneToGetContactEnricher, "_lookup_one",
            return_value=None,
        ):
            e = ENRICHER_REGISTRY._enrichers["phone_to_getcontact"]
            enricher = e(sketch_id="test", scan_id="test")

            results = await enricher.scan([
                Phone(number="+14155552671"),
            ])

            assert len(results) == 1
            assert results[0].platform == "GetContact"


class TestPostprocess:
    def test_creates_nodes(self):
        e = ENRICHER_REGISTRY._enrichers["phone_to_getcontact"]
        graph_mock = MagicMock()
        enricher = e(sketch_id="test", scan_id="test", graph_service=graph_mock)

        account = SocialAccount(
            username=Username(value="+14155552671", platform="GetContact"),
            platform="GetContact",
        )
        results = enricher.postprocess([account], [account])
        assert len(results) == 1
        assert graph_mock.create_node_from_flowsint_type.called


class TestParamsAndDocs:
    def test_params(self):
        e = ENRICHER_REGISTRY._enrichers["phone_to_getcontact"]
        params = e.get_params_schema()
        assert len(params) == 1
        assert params[0]["name"] == "timeout"

    def test_docs(self):
        e = ENRICHER_REGISTRY._enrichers["phone_to_getcontact"]
        docs = e.documentation()
        assert "GetContact" in docs
        assert len(docs) > 100
