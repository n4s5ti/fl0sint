"""Tests for the GHunt Google Account enricher."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest
from flowsint_enrichers import ENRICHER_REGISTRY, load_all_enrichers
from flowsint_types.email import Email
from flowsint_types.social_account import SocialAccount
from flowsint_types.username import Username


@pytest.fixture(autouse=True)
def reset_registry():
    load_all_enrichers()
    yield


# ===================================================================
# 1. Registry integration
# ===================================================================

class TestRegistry:
    def test_enricher_is_registered(self):
        assert ENRICHER_REGISTRY.enricher_exists("email_to_google_account")

    def test_enricher_metadata(self):
        e = ENRICHER_REGISTRY._enrichers["email_to_google_account"]
        assert e.name() == "email_to_google_account"
        assert e.category() == "Email"
        assert e.key() == "email"
        assert e.icon() is not None
        assert e.InputType is Email
        assert e.OutputType is SocialAccount

    def test_input_schema(self):
        e = ENRICHER_REGISTRY._enrichers["email_to_google_account"]
        schema = e.input_schema()
        assert schema["type"] == "Email"

    def test_output_schema(self):
        e = ENRICHER_REGISTRY._enrichers["email_to_google_account"]
        schema = e.output_schema()
        assert schema["type"] == "SocialAccount"


# ===================================================================
# 2. Graceful degradation
# ===================================================================

class TestGracefulDegradation:
    @pytest.mark.asyncio
    async def test_returns_empty_when_ghunt_missing(self):
        """Without GHunt installed, scan returns empty SocialAccount objects."""
        import flowsint_enrichers.email.to_google as mod

        with patch.object(mod, "HAS_GHUNT", False):
            e = ENRICHER_REGISTRY._enrichers["email_to_google_account"]
            enricher = e(sketch_id="test", scan_id="test")

            email_item = Email(email="test@gmail.com")
            results = await enricher.scan([email_item])

            assert len(results) == 1
            assert results[0].associated_emails == ["test@gmail.com"]
            assert results[0].platform == "Google"

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_creds(self):
        """Without GHunt creds, scan returns empty results."""
        import flowsint_enrichers.email.to_google as mod

        with patch.object(mod, "HAS_GHUNT", True), \
             patch.object(Path, "exists", return_value=False):
            e = ENRICHER_REGISTRY._enrichers["email_to_google_account"]
            enricher = e(sketch_id="test", scan_id="test")

            email_item = Email(email="test@gmail.com")
            results = await enricher.scan([email_item])

            assert len(results) == 1
            assert results[0].platform == "Google"


# ===================================================================
# 3. Mocked scan
# ===================================================================

class TestMockedScan:
    @pytest.mark.asyncio
    async def test_scan_multiple_emails(self):
        """Multiple emails produce SocialAccount results for each."""
        import flowsint_enrichers.email.to_google as mod

        with patch.object(mod, "HAS_GHUNT", True), \
             patch.object(Path, "exists", return_value=True), \
             patch.object(mod.EmailToGoogleEnricher, "_lookup_one",
                          return_value=None):
            e = ENRICHER_REGISTRY._enrichers["email_to_google_account"]
            enricher = e(sketch_id="test", scan_id="test")

            items = [
                Email(email="a@gmail.com"),
                Email(email="b@gmail.com"),
            ]
            results = await enricher.scan(items)

            assert len(results) == 2
            assert results[0].associated_emails == ["a@gmail.com"]
            assert results[1].associated_emails == ["b@gmail.com"]


# ===================================================================
# 4. Postprocess
# ===================================================================

class TestPostprocess:
    @pytest.mark.asyncio
    async def test_postprocess_creates_nodes_and_relationships(self):
        """postprocess() creates SocialAccount nodes + relationships."""
        e = ENRICHER_REGISTRY._enrichers["email_to_google_account"]
        graph_mock = MagicMock()
        enricher = e(sketch_id="test", scan_id="test", graph_service=graph_mock)

        account = SocialAccount(
            username=Username(value="12345", platform="Google"),
            display_name="Test User",
            platform="Google",
            associated_emails=["test@gmail.com"],
        )
        email_item = Email(email="test@gmail.com")

        results = enricher.postprocess([account], [email_item])

        assert len(results) == 1
        assert graph_mock.create_node_from_flowsint_type.called
        assert graph_mock.create_relationship.called


# ===================================================================
# 5. Params & docs
# ===================================================================

class TestParamsAndDocs:
    def test_params_schema(self):
        e = ENRICHER_REGISTRY._enrichers["email_to_google_account"]
        params = e.get_params_schema()

        assert len(params) == 1
        assert params[0]["name"] == "ghunt_creds_path"
        assert params[0]["type"] == "string"

    def test_documentation(self):
        e = ENRICHER_REGISTRY._enrichers["email_to_google_account"]
        docs = e.documentation()

        assert "GHunt" in docs
        assert "Google" in docs
        assert "Gaia" in docs
        assert "profile" in docs.lower()
        assert "pip install" in docs.lower()
        assert "ghunt login" in docs.lower()
        assert len(docs) > 200
