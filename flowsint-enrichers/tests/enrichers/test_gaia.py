"""Tests for the GHunt Gaia ID Person Lookup enricher."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from flowsint_enrichers import ENRICHER_REGISTRY, load_all_enrichers
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
        assert ENRICHER_REGISTRY.enricher_exists("gaia_id_to_person")

    def test_enricher_metadata(self):
        e = ENRICHER_REGISTRY._enrichers["gaia_id_to_person"]
        assert e.name() == "gaia_id_to_person"
        assert e.category() == "social"
        assert e.key() == "username"
        assert e.icon() is not None
        assert e.InputType is Username
        assert e.OutputType is SocialAccount

    def test_schemas(self):
        e = ENRICHER_REGISTRY._enrichers["gaia_id_to_person"]
        assert e.input_schema()["type"] == "Username"
        assert e.output_schema()["type"] == "SocialAccount"


# ===================================================================
# 2. Graceful degradation
# ===================================================================

class TestGracefulDegradation:
    @pytest.mark.asyncio
    async def test_returns_empty_when_ghunt_missing(self):
        import flowsint_enrichers.social.to_gaia as mod

        with patch.object(mod, "HAS_GHUNT", False):
            e = ENRICHER_REGISTRY._enrichers["gaia_id_to_person"]
            enricher = e(sketch_id="test", scan_id="test")

            results = await enricher.scan([
                Username(value="101299910324306641283", platform="Google"),
            ])

            assert len(results) == 1
            assert results[0].username.value == "101299910324306641283"
            assert results[0].platform == "Google"

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_creds(self):
        import flowsint_enrichers.social.to_gaia as mod

        with patch.object(mod, "HAS_GHUNT", True), \
             patch.object(Path, "exists", return_value=False):
            e = ENRICHER_REGISTRY._enrichers["gaia_id_to_person"]
            enricher = e(sketch_id="test", scan_id="test")

            results = await enricher.scan([
                Username(value="12345", platform="Google"),
            ])

            assert len(results) == 1
            assert results[0].platform == "Google"

    @pytest.mark.asyncio
    async def test_multiple_ids(self):
        import flowsint_enrichers.social.to_gaia as mod

        with patch.object(mod, "HAS_GHUNT", True), \
             patch.object(Path, "exists", return_value=True), \
             patch.object(mod.GaiaToPersonEnricher, "_lookup_one",
                          return_value=None):
            e = ENRICHER_REGISTRY._enrichers["gaia_id_to_person"]
            enricher = e(sketch_id="test", scan_id="test")

            results = await enricher.scan([
                Username(value="111", platform="Google"),
                Username(value="222", platform="Google"),
            ])

            assert len(results) == 2
            assert results[0].username.value == "111"
            assert results[1].username.value == "222"


# ===================================================================
# 3. Postprocess
# ===================================================================

class TestPostprocess:
    def test_postprocess_creates_nodes(self):
        e = ENRICHER_REGISTRY._enrichers["gaia_id_to_person"]
        graph_mock = MagicMock()
        enricher = e(sketch_id="test", scan_id="test", graph_service=graph_mock)

        account = SocialAccount(
            username=Username(value="12345", platform="Google"),
            display_name="Test User",
            platform="Google",
        )

        results = enricher.postprocess([account], [account])
        assert len(results) == 1
        assert graph_mock.create_node_from_flowsint_type.called


# ===================================================================
# 4. Params & docs
# ===================================================================

class TestParamsAndDocs:
    def test_params_schema(self):
        e = ENRICHER_REGISTRY._enrichers["gaia_id_to_person"]
        params = e.get_params_schema()
        assert len(params) == 1
        assert params[0]["name"] == "ghunt_creds_path"

    def test_documentation(self):
        e = ENRICHER_REGISTRY._enrichers["gaia_id_to_person"]
        docs = e.documentation()
        assert "GHunt" in docs
        assert "Gaia" in docs
        assert "pip install" in docs
        assert len(docs) > 200
