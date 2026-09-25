"""Tests for the SearchSystems Camoufox enricher."""

from pathlib import Path
from unittest.mock import MagicMock, PropertyMock, patch

import pytest
from flowsint_enrichers import ENRICHER_REGISTRY, load_all_enrichers
from flowsint_types.phrase import Phrase
from flowsint_types.social_account import SocialAccount
from flowsint_types.username import Username


@pytest.fixture(autouse=True)
def reset_registry():
    load_all_enrichers()
    yield


class TestRegistry:
    def test_enricher_is_registered(self):
        assert ENRICHER_REGISTRY.enricher_exists("phrase_to_searchsystems")

    def test_metadata(self):
        e = ENRICHER_REGISTRY._enrichers["phrase_to_searchsystems"]
        assert e.name() == "phrase_to_searchsystems"
        assert e.category() == "social"
        assert e.key() == "text"
        assert e.icon() is not None
        assert e.InputType is Phrase
        assert e.OutputType is SocialAccount


class TestGracefulDegradation:
    @pytest.mark.asyncio
    async def test_fallback_when_scraper_missing(self):
        import flowsint_enrichers.social.to_searchsystems as mod

        with patch.object(
            mod, "STEALTH_SCRAPER", Path("/nonexistent/scrape.js")
        ):
            e = ENRICHER_REGISTRY._enrichers["phrase_to_searchsystems"]
            enricher = e(sketch_id="test", scan_id="test")

            results = await enricher.scan([
                Phrase(text="criminal records Texas"),
            ])

            assert len(results) == 1
            assert results[0].platform == "SearchSystems"

    @pytest.mark.asyncio
    async def test_multiple_queries(self):
        import flowsint_enrichers.social.to_searchsystems as mod

        with patch.object(
            mod, "STEALTH_SCRAPER", Path("/nonexistent/scrape.js")
        ):
            e = ENRICHER_REGISTRY._enrichers["phrase_to_searchsystems"]
            enricher = e(sketch_id="test", scan_id="test")

            results = await enricher.scan([
                Phrase(text="query one"),
                Phrase(text="query two"),
            ])

            assert len(results) == 2


class TestPostprocess:
    def test_creates_nodes(self):
        e = ENRICHER_REGISTRY._enrichers["phrase_to_searchsystems"]
        graph_mock = MagicMock()
        enricher = e(sketch_id="test", scan_id="test", graph_service=graph_mock)

        account = SocialAccount(
            username=Username(value="test", platform="SearchSystems"),
            platform="SearchSystems",
        )
        results = enricher.postprocess([account], [account])
        assert len(results) == 1
        assert graph_mock.create_node_from_flowsint_type.called


class TestParamsAndDocs:
    def test_params(self):
        e = ENRICHER_REGISTRY._enrichers["phrase_to_searchsystems"]
        params = e.get_params_schema()
        assert len(params) == 2
        assert params[0]["name"] == "timeout"
        assert params[1]["name"] == "headless"

    def test_docs(self):
        e = ENRICHER_REGISTRY._enrichers["phrase_to_searchsystems"]
        docs = e.documentation()
        assert "SearchSystems" in docs
        assert "Camoufox" in docs
        assert len(docs) > 200
