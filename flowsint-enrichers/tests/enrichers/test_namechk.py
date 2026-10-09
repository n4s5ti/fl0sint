"""Tests for the Namechk username availability checker enricher."""
from unittest.mock import AsyncMock, MagicMock, patch
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
        assert ENRICHER_REGISTRY.enricher_exists("username_to_namechk")
    def test_enricher_metadata(self):
        e = ENRICHER_REGISTRY._enrichers["username_to_namechk"]
        assert e.name() == "username_to_namechk"
        assert e.category() == "social"
        assert e.key() == "value"
        assert e.icon() is not None
        assert e.InputType is Username
        assert e.OutputType is SocialAccount
    def test_schemas(self):
        e = ENRICHER_REGISTRY._enrichers["username_to_namechk"]
        assert e.input_schema()["type"] == "Username"
        assert e.output_schema()["type"] == "SocialAccount"
# ===================================================================
# 2. Graceful degradation
# ===================================================================
class TestGracefulDegradation:
    @pytest.mark.asyncio
    async def test_returns_empty_when_naminter_missing(self):
        import flowsint_enrichers.social.to_namechk as mod
        with patch.object(mod, "HAS_NAMECHK", False):
            e = ENRICHER_REGISTRY._enrichers["username_to_namechk"]
            enricher = e(sketch_id="test", scan_id="test")
            results = await enricher.scan([
                Username(value="testuser", platform="github"),
            ])
            assert len(results) == 1
            assert results[0].username.value == "testuser"
            assert results[0].platform == "github"
    @pytest.mark.asyncio
    async def test_multiple_usernames_fallback(self):
        import flowsint_enrichers.social.to_namechk as mod
        with patch.object(mod, "HAS_NAMECHK", False):
            e = ENRICHER_REGISTRY._enrichers["username_to_namechk"]
            enricher = e(sketch_id="test", scan_id="test")
            results = await enricher.scan([
                Username(value="user1", platform="twitter"),
                Username(value="user2", platform="github"),
            ])
            assert len(results) == 2
            assert results[0].username.value == "user1"
            assert results[1].username.value == "user2"
# ===================================================================
# 3. Mocked scan
# ===================================================================
def _naminter_available(mod):
    """Behave as if the optional naminter package is installed, whether or not it is.

    naminter is not a locked dependency, so CI never has it. The scan path is exercised
    against a mocked Hunter.hunt, and the real Naminter session must never be constructed.
    """
    return (
        patch.object(mod, "HAS_NAMECHK", True),
        patch.object(mod.Hunter, "__init__", MagicMock(return_value=None)),
        patch.object(mod.Hunter, "close", AsyncMock(return_value=None)),
    )


class TestScan:
    @pytest.mark.asyncio
    async def test_mocked_scan_returns_found_accounts(self):
        import flowsint_enrichers.social.to_namechk as mod
        fake_results = [
            {"site": "github", "url": "https://github.com/testuser", "exists": True},
            {"site": "twitter", "url": "https://twitter.com/testuser", "exists": True},
            {"site": "nonexistent", "url": "https://example.com/nonexistent", "exists": False},
        ]
        available, init, close = _naminter_available(mod)
        with available, init, close, patch.object(mod.Hunter, "hunt", AsyncMock(return_value=fake_results)):
            e = ENRICHER_REGISTRY._enrichers["username_to_namechk"]
            enricher = e(sketch_id="test", scan_id="test")
            results = await enricher.scan([
                Username(value="testuser", platform="github"),
            ])
            assert len(results) == 2
            assert results[0].platform == "github"
            assert results[0].profile_url == "https://github.com/testuser"
            assert results[1].platform == "twitter"
    @pytest.mark.asyncio
    async def test_mocked_scan_no_matches(self):
        import flowsint_enrichers.social.to_namechk as mod
        fake_results = [
            {"site": "github", "url": "", "exists": False},
        ]
        available, init, close = _naminter_available(mod)
        with available, init, close, patch.object(mod.Hunter, "hunt", AsyncMock(return_value=fake_results)):
            e = ENRICHER_REGISTRY._enrichers["username_to_namechk"]
            enricher = e(sketch_id="test", scan_id="test")
            results = await enricher.scan([
                Username(value="rando", platform="github"),
            ])
            assert len(results) == 0
    @pytest.mark.asyncio
    async def test_hunt_exception_returns_empty(self):
        import flowsint_enrichers.social.to_namechk as mod
        available, init, close = _naminter_available(mod)
        with available, init, close, patch.object(mod.Hunter, "hunt", AsyncMock(side_effect=RuntimeError("boom"))):
            e = ENRICHER_REGISTRY._enrichers["username_to_namechk"]
            enricher = e(sketch_id="test", scan_id="test")
            results = await enricher.scan([
                Username(value="failcase", platform="github"),
            ])
            assert len(results) == 0
# ===================================================================
# 4. Postprocess
# ===================================================================
class TestPostprocess:
    def test_postprocess_creates_nodes(self):
        e = ENRICHER_REGISTRY._enrichers["username_to_namechk"]
        graph_mock = MagicMock()
        enricher = e(sketch_id="test", scan_id="test", graph_service=graph_mock)
        account = SocialAccount(
            username=Username(value="testuser", platform="github"),
            platform="github",
            profile_url="https://github.com/testuser",
        )
        results = enricher.postprocess([account], [account])
        assert len(results) == 1
        assert graph_mock.create_node_from_flowsint_type.called
    def test_postprocess_no_graph_service(self):
        e = ENRICHER_REGISTRY._enrichers["username_to_namechk"]
        enricher = e(sketch_id="test", scan_id="test")
        account = SocialAccount(
            username=Username(value="testuser", platform="github"),
            platform="github",
        )
        results = enricher.postprocess([account], [account])
        assert len(results) == 1
# ===================================================================
# 5. Params & docs
# ===================================================================
class TestParamsAndDocs:
    def test_params_schema(self):
        e = ENRICHER_REGISTRY._enrichers["username_to_namechk"]
        params = e.get_params_schema()
        assert len(params) == 1
        assert params[0]["name"] == "max_timeout"
    def test_documentation(self):
        e = ENRICHER_REGISTRY._enrichers["username_to_namechk"]
        docs = e.documentation()
        assert "Namechk" in docs
        assert "naminter" in docs
        assert "pip install" in docs
        assert len(docs) > 200
    def test_import_works(self):
        from flowsint_enrichers.social.to_namechk import (
            InputType, NamechkEnricher, OutputType,
        )
        assert NamechkEnricher.name() == "username_to_namechk"
        assert issubclass(NamechkEnricher, object)
        assert InputType is Username
        assert OutputType is SocialAccount
