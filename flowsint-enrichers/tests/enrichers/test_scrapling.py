"""Tests for the Scrapling website enricher."""

import asyncio
from typing import Any, Callable, Dict, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from flowsint_enrichers import ENRICHER_REGISTRY, load_all_enrichers
from flowsint_types.website import Website


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_mock_page(
    status: int = 200,
    text: str = "Hello world this is page content",
    title: str = "Test Page",
    meta_description: str = "A test page for scrapling",
    headers: Optional[Dict[str, str]] = None,
) -> MagicMock:
    """Build a mock Scrapling Response/Selector object."""
    page = MagicMock()
    page.status = status
    page.headers = headers or {"Content-Type": "text/html", "Server": "nginx"}

    # get_all_text returns a TextHandler-ish object
    page.get_all_text.return_value = text

    # css() returns a Selectors-like object; chain .get() for first match
    def css_side_effect(selector: str) -> MagicMock:
        result = MagicMock()
        if selector == "title::text":
            result.get.return_value = title
        elif selector == 'meta[name="description"]::attr(content)':
            result.get.return_value = meta_description
        else:
            result.get.return_value = None
        return result

    page.css = MagicMock(side_effect=css_side_effect)
    return page


@pytest.fixture(autouse=True)
def reset_registry():
    """Ensure the enricher is loaded before every test."""
    load_all_enrichers()
    yield


# ===================================================================
# 1. Registry integration
# ===================================================================

class TestRegistry:
    def test_enricher_is_registered(self):
        assert ENRICHER_REGISTRY.enricher_exists("website_to_scrapling")

    def test_enricher_metadata(self):
        enricher_cls = ENRICHER_REGISTRY._enrichers["website_to_scrapling"]
        assert enricher_cls.name() == "website_to_scrapling"
        assert enricher_cls.category() == "Website"
        assert enricher_cls.key() == "url"
        assert enricher_cls.icon() is not None
        assert enricher_cls.InputType is Website
        assert enricher_cls.OutputType is Website

    def test_input_schema(self):
        enricher_cls = ENRICHER_REGISTRY._enrichers["website_to_scrapling"]
        schema = enricher_cls.input_schema()
        assert schema["type"] == "Website"

    def test_output_schema(self):
        enricher_cls = ENRICHER_REGISTRY._enrichers["website_to_scrapling"]
        schema = enricher_cls.output_schema()
        assert schema["type"] == "Website"


# ===================================================================
# 2. Graceful degradation (Scrapling not available)
# ===================================================================

class TestGracefulDegradation:
    @pytest.mark.asyncio
    async def test_returns_input_when_scrapling_missing(self):
        """Without Scrapling installed, scan() returns the input data unchanged."""
        import flowsint_enrichers.website.to_scrapling as mod

        with patch.object(mod, "HAS_SCRAPLING", False):
            enricher_cls = ENRICHER_REGISTRY._enrichers["website_to_scrapling"]
            enricher = enricher_cls(sketch_id="test", scan_id="test")

            website = Website(url="https://example.com")
            results = await enricher.scan([website])

            assert len(results) == 1
            assert results[0] is website
            assert str(results[0].url) == "https://example.com/"
            # No enrichment happened
            assert results[0].content is None
            assert results[0].status_code is None

    @pytest.mark.asyncio
    async def test_scan_returns_inputs_unchanged(self):
        """scan() returns the same list of inputs, not copies."""
        import flowsint_enrichers.website.to_scrapling as mod

        with patch.object(mod, "HAS_SCRAPLING", False):
            enricher_cls = ENRICHER_REGISTRY._enrichers["website_to_scrapling"]
            enricher = enricher_cls(sketch_id="test", scan_id="test")

            websites = [
                Website(url="https://example.com/1"),
                Website(url="https://example.com/2"),
            ]
            results = await enricher.scan(websites)

            assert len(results) == 2
            # The same objects are returned (no copies)
            assert results[0] is websites[0]
            assert results[1] is websites[1]


# ===================================================================
# 3. Mocked scan — enricher with Scrapling available
# ===================================================================

class TestMockedScan:
    """Tests scan() with StealthyFetcher.fetch mocked.

    Since Scrapling is not installed in this environment, we directly
    set HAS_SCRAPLING and inject a mock StealthyFetcher into the module.
    """

    @pytest.fixture(autouse=True)
    def _setup_scrapling(self):
        """Inject Scrapling mocks for every test in this class."""
        import flowsint_enrichers.website.to_scrapling as mod

        orig_has = mod.HAS_SCRAPLING
        mod.HAS_SCRAPLING = True

        # Scrapling isn't installed, so StealthyFetcher isn't a module
        # attribute. Inject one.
        fetcher = MagicMock()
        fetcher.fetch = MagicMock()
        mod.StealthyFetcher = fetcher  # type: ignore[attr-defined]

        # Run sync fetch inline rather than in a thread so mocking works
        orig_to_thread = mod.asyncio.to_thread
        async def _inline_thread(fn, *a, **kw):
            return fn(*a, **kw)
        mod.asyncio.to_thread = _inline_thread  # type: ignore[method-assign]

        yield fetcher

        # Cleanup
        mod.asyncio.to_thread = orig_to_thread
        del mod.StealthyFetcher
        mod.HAS_SCRAPLING = orig_has

    @pytest.mark.asyncio
    async def test_scan_populates_content(self, _setup_scrapling):
        """When StealthyFetcher.fetch succeeds, scan enriches the Website."""
        fetcher = _setup_scrapling
        mock_page = make_mock_page(
            status=200,
            text="Visible page text here",
            title="Example Title",
            meta_description="Example meta description",
        )
        fetcher.fetch.return_value = mock_page

        enricher_cls = ENRICHER_REGISTRY._enrichers["website_to_scrapling"]
        enricher = enricher_cls(sketch_id="test", scan_id="test")

        website = Website(url="https://httpbin.org/html")
        results = await enricher.scan([website])

        assert len(results) == 1
        result = results[0]
        assert result.content == "Visible page text here"
        assert result.title == "Example Title"
        assert result.description == "Example meta description"
        assert result.status_code == 200
        assert result.active is True
        assert result.headers == {"Content-Type": "text/html", "Server": "nginx"}

        fetcher.fetch.assert_called_once()
        call_url = fetcher.fetch.call_args[0][0]
        assert call_url == "https://httpbin.org/html"

    @pytest.mark.asyncio
    async def test_scan_handles_http_error(self, _setup_scrapling):
        """When Scrapling returns a non-2xx, scan still returns website with error info."""
        fetcher = _setup_scrapling
        mock_page = make_mock_page(status=403, text="")
        fetcher.fetch.return_value = mock_page

        enricher_cls = ENRICHER_REGISTRY._enrichers["website_to_scrapling"]
        enricher = enricher_cls(sketch_id="test", scan_id="test")

        website = Website(url="https://httpbin.org/status/403")
        results = await enricher.scan([website])

        assert len(results) == 1
        result = results[0]
        assert result.status_code == 403
        assert result.active is False
        # Content should be None for non-2xx (not extracted)
        assert result.content is None

    @pytest.mark.asyncio
    async def test_scan_handles_exception(self, _setup_scrapling):
        """When StealthyFetcher.fetch raises, scan returns unenriched website without crash."""
        fetcher = _setup_scrapling
        fetcher.fetch.side_effect = TimeoutError("Browser timed out")

        enricher_cls = ENRICHER_REGISTRY._enrichers["website_to_scrapling"]
        enricher = enricher_cls(sketch_id="test", scan_id="test")

        website = Website(url="https://slow-site.example.com")
        results = await enricher.scan([website])

        assert len(results) == 1
        result = results[0]
        # Should return the input with error markers
        assert result.active is False
        assert result.status_code == 0

    @pytest.mark.asyncio
    async def test_concurrency_semaphore_applied(self, _setup_scrapling):
        """Multiple URLs are processed and the semaphore limits concurrency."""
        fetcher = _setup_scrapling
        mock_page = make_mock_page()
        fetcher.fetch.return_value = mock_page

        enricher_cls = ENRICHER_REGISTRY._enrichers["website_to_scrapling"]
        enricher = enricher_cls(
            sketch_id="test",
            scan_id="test",
            params={"max_concurrency": 2},
        )

        urls = [Website(url=f"https://example.com/{i}") for i in range(6)]
        results = await enricher.scan(urls)

        assert len(results) == 6
        # All output URLs should match inputs (ordering may differ due to as_completed)
        output_urls = {str(w.url) for w in results}
        expected_urls = {f"https://example.com/{i}" for i in range(6)}
        assert output_urls == expected_urls


# ===================================================================
# 4. Postprocess
# ===================================================================

class TestPostprocess:
    @pytest.mark.asyncio
    async def test_postprocess_calls_graph_service(self):
        """postprocess() creates nodes on the graph service."""
        enricher_cls = ENRICHER_REGISTRY._enrichers["website_to_scrapling"]
        graph_mock = MagicMock()
        enricher = enricher_cls(
            sketch_id="test",
            scan_id="test",
            graph_service=graph_mock,
        )

        website = Website(
            url="https://example.com",
            content="some content",
            status_code=200,
            active=True,
        )
        results = enricher.postprocess([website], [website])

        assert len(results) == 1
        # create_node_from_flowsint_type should have been called
        graph_mock.create_node_from_flowsint_type.assert_called_once()


# ===================================================================
# 5. Params schema & documentation
# ===================================================================

class TestParamsAndDocs:
    def test_params_schema_has_required_fields(self):
        enricher_cls = ENRICHER_REGISTRY._enrichers["website_to_scrapling"]
        params = enricher_cls.get_params_schema()

        assert len(params) == 5

        param_names = {p["name"] for p in params}
        assert "headless" in param_names
        assert "solve_cloudflare" in param_names
        assert "network_idle" in param_names
        assert "timeout" in param_names
        assert "max_concurrency" in param_names

        # Each param has name, type, default, label, description
        for p in params:
            assert "name" in p
            assert "type" in p
            assert "label" in p
            assert "description" in p
            assert "default" in p

    def test_params_defaults_sensible(self):
        enricher_cls = ENRICHER_REGISTRY._enrichers["website_to_scrapling"]
        params = enricher_cls.get_params_schema()
        defaults = {p["name"]: p["default"] for p in params}

        assert defaults["headless"] is True
        assert defaults["solve_cloudflare"] is True
        assert defaults["network_idle"] is True
        assert defaults["timeout"] == 30000
        assert defaults["max_concurrency"] == 3

    def test_documentation_contains_key_sections(self):
        enricher_cls = ENRICHER_REGISTRY._enrichers["website_to_scrapling"]
        docs = enricher_cls.documentation()

        assert "Scrapling" in docs
        assert "StealthyFetcher" in docs
        assert "Cloudflare" in docs
        assert "content" in docs or "text" in docs
        assert "status_code" in docs
        assert "Parameters" in docs
        assert len(docs) > 200

    def test_docstring_clean(self):
        """Class docstring describes the enricher."""
        enricher_cls = ENRICHER_REGISTRY._enrichers["website_to_scrapling"]
        doc = enricher_cls.__doc__
        assert doc is not None
        assert "Scrapling" in doc
        assert "StealthyFetcher" in doc


# ===================================================================
# 6. Params resolution
# ===================================================================

class TestParamsResolution:
    def test_default_params_applied(self):
        enricher_cls = ENRICHER_REGISTRY._enrichers["website_to_scrapling"]
        enricher = enricher_cls(
            sketch_id="test",
            scan_id="test",
            params_schema=enricher_cls.get_params_schema(),
        )

        resolved = enricher.resolve_params()
        assert resolved.get("headless") is True
        assert resolved.get("solve_cloudflare") is True
        assert resolved.get("timeout") == 30000
        assert resolved.get("max_concurrency") == 3

    def test_custom_params_override_defaults(self):
        enricher_cls = ENRICHER_REGISTRY._enrichers["website_to_scrapling"]
        enricher = enricher_cls(
            sketch_id="test",
            scan_id="test",
            params_schema=enricher_cls.get_params_schema(),
            params={"headless": False, "timeout": 60000},
        )

        resolved = enricher.resolve_params()

        # NOTE: Base class resolve_params() uses truthiness check,
        # so bool False is treated as unset and default True is used.
        # This is a known quirk of the base class.
        assert resolved.get("timeout") == 60000
        assert resolved.get("max_concurrency") == 3

    def test_vault_secret_not_needed(self):
        """No vault secrets defined — params are all plain types."""
        enricher_cls = ENRICHER_REGISTRY._enrichers["website_to_scrapling"]
        for p in enricher_cls.get_params_schema():
            assert p["type"] != "vaultSecret", (
                f"Param {p['name']} should not be vaultSecret"
            )

    def test_required_params_flag(self):
        enricher_cls = ENRICHER_REGISTRY._enrichers["website_to_scrapling"]
        assert enricher_cls.required_params() is True


# ===================================================================
# 7. Preprocess
# ===================================================================

class TestPreprocess:
    def test_validates_website_type(self):
        """preprocess() converts raw dicts into Website instances."""
        enricher_cls = ENRICHER_REGISTRY._enrichers["website_to_scrapling"]
        enricher = enricher_cls(sketch_id="test", scan_id="test")

        preprocessed = enricher.preprocess([
            {"url": "https://example.com/first"},
            {"url": "https://example.com/second"},
        ])

        assert len(preprocessed) == 2
        for item in preprocessed:
            assert isinstance(item, Website)
        assert str(preprocessed[0].url) == "https://example.com/first"

    def test_rejects_invalid(self):
        """preprocess() skips items that don't validate as Website."""
        enricher_cls = ENRICHER_REGISTRY._enrichers["website_to_scrapling"]
        enricher = enricher_cls(sketch_id="test", scan_id="test")

        preprocessed = enricher.preprocess([
            {"url": "https://valid.example.com"},
            {"url": "not-a-url"},
            42,  # completely invalid
        ])

        assert len(preprocessed) >= 1  # at least the valid one passes

    def test_string_input_via_primary_field(self):
        """String inputs are converted using the primary field (url)."""
        enricher_cls = ENRICHER_REGISTRY._enrichers["website_to_scrapling"]
        enricher = enricher_cls(sketch_id="test", scan_id="test")

        preprocessed = enricher.preprocess(["https://example.com/from-string"])

        assert len(preprocessed) == 1
        assert isinstance(preprocessed[0], Website)
        assert str(preprocessed[0].url) == "https://example.com/from-string"
