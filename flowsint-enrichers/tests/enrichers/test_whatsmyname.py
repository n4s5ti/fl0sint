"""Tests for the WhatsMyName username-enumeration enricher."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from flowsint_enrichers import ENRICHER_REGISTRY, load_all_enrichers
from flowsint_types.social_account import SocialAccount
from flowsint_types.username import Username


@pytest.fixture(autouse=True)
def reset_registry():
    load_all_enrichers()
    yield


SAMPLE_SITES = [
    {
        "name": "Twitter",
        "uri_check": "https://twitter.com/{username}",
        "e_code": 200,
        "m_method": "GET",
        "m_string": "statuses?",
    },
    {
        "name": "GitHub",
        "uri_check": "https://github.com/{username}",
        "e_code": 200,
        "m_method": "GET",
        "m_string": '"repository"',
    },
]


# ===================================================================
# 1. Registry integration
# ===================================================================


class TestRegistry:
    def test_enricher_is_registered(self):
        assert ENRICHER_REGISTRY.enricher_exists("username_to_whatsmyname")

    def test_enricher_metadata(self):
        e = ENRICHER_REGISTRY._enrichers["username_to_whatsmyname"]
        assert e.name() == "username_to_whatsmyname"
        assert e.category() == "social"
        assert e.key() == "value"
        assert e.icon() == "\u2713"
        assert e.InputType is Username
        assert e.OutputType is SocialAccount

    def test_schemas(self):
        e = ENRICHER_REGISTRY._enrichers["username_to_whatsmyname"]
        assert e.input_schema()["type"] == "Username"
        assert e.output_schema()["type"] == "SocialAccount"


# ===================================================================
# 2. Scan
# ===================================================================


class TestScan:
    @pytest.mark.asyncio
    async def test_returns_empty_when_site_list_unavailable(self):
        import flowsint_enrichers.social.to_whatsmyname as mod

        with patch.object(
            mod.WhatsMyNameEnricher, "_fetch_site_list", return_value=[]
        ):
            e = ENRICHER_REGISTRY._enrichers["username_to_whatsmyname"]
            enricher = e(sketch_id="test", scan_id="test")
            results = await enricher.scan([Username(value="testuser")])
            assert len(results) == 0

    @pytest.mark.asyncio
    async def test_returns_matches(self):
        import flowsint_enrichers.social.to_whatsmyname as mod

        with patch.object(
            mod.WhatsMyNameEnricher, "_fetch_site_list", return_value=SAMPLE_SITES
        ), patch.object(
            mod.WhatsMyNameEnricher, "_check_site", new_callable=AsyncMock
        ) as mock_check:
            mock_check.side_effect = [
                SocialAccount(
                    username=Username(value="testuser"),
                    platform="Twitter",
                    profile_url="https://twitter.com/testuser",
                ),
                None,
            ]

            e = ENRICHER_REGISTRY._enrichers["username_to_whatsmyname"]
            enricher = e(sketch_id="test", scan_id="test")
            results = await enricher.scan([Username(value="testuser")])

            assert len(results) == 1
            assert results[0].platform == "Twitter"
            assert results[0].profile_url == "https://twitter.com/testuser"
            assert results[0].username.value == "testuser"


# ===================================================================
# 3. _check_site unit tests
# ===================================================================


def _make_mock_client(get_side_effect=None, head_side_effect=None):
    """Build an AsyncMock that behaves like ``httpx.AsyncClient``."""
    client = MagicMock(spec=httpx.AsyncClient)
    client.get = AsyncMock(side_effect=get_side_effect)
    client.head = AsyncMock(side_effect=head_side_effect)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    return client


class TestCheckSite:
    """Direct unit tests for ``_check_site``."""

    @pytest.mark.asyncio
    async def test_m_string_found_returns_account(self):
        import flowsint_enrichers.social.to_whatsmyname as mod

        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.text = "some content with statuses? in it"
        mock_resp.status_code = 200

        client = _make_mock_client(get_side_effect=[mock_resp])

        with patch("httpx.AsyncClient", return_value=client):
            e = ENRICHER_REGISTRY._enrichers["username_to_whatsmyname"]
            enricher = e(sketch_id="test", scan_id="test")

            result = await enricher._check_site(
                Username(value="testuser"), SAMPLE_SITES[0]
            )
            assert result is not None
            assert result.platform == "Twitter"
            assert result.profile_url == "https://twitter.com/testuser"

    @pytest.mark.asyncio
    async def test_m_string_not_found_returns_none(self):
        import flowsint_enrichers.social.to_whatsmyname as mod

        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.text = "some content without the detection string"
        mock_resp.status_code = 200

        client = _make_mock_client(get_side_effect=[mock_resp])

        with patch("httpx.AsyncClient", return_value=client):
            e = ENRICHER_REGISTRY._enrichers["username_to_whatsmyname"]
            enricher = e(sketch_id="test", scan_id="test")

            result = await enricher._check_site(
                Username(value="testuser"), SAMPLE_SITES[0]
            )
            assert result is None

    @pytest.mark.asyncio
    async def test_head_method(self):
        import flowsint_enrichers.social.to_whatsmyname as mod

        site_with_head = {
            "name": "HeadTest",
            "uri_check": "https://example.com/{username}",
            "e_code": 200,
            "m_method": "HEAD",
            "m_string": "some-header",
        }

        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.text = "some-header present"
        mock_resp.status_code = 200

        client = _make_mock_client(head_side_effect=[mock_resp])

        with patch("httpx.AsyncClient", return_value=client):
            e = ENRICHER_REGISTRY._enrichers["username_to_whatsmyname"]
            enricher = e(sketch_id="test", scan_id="test")

            result = await enricher._check_site(
                Username(value="testuser"), site_with_head
            )
            assert result is not None
            assert result.platform == "HeadTest"
            # Verify HEAD was used, not GET
            client.head.assert_awaited_once()
            client.get.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_timeout_returns_none(self):
        import flowsint_enrichers.social.to_whatsmyname as mod

        client = _make_mock_client(
            get_side_effect=httpx.TimeoutException("timed out")
        )

        with patch("httpx.AsyncClient", return_value=client):
            e = ENRICHER_REGISTRY._enrichers["username_to_whatsmyname"]
            enricher = e(sketch_id="test", scan_id="test")

            result = await enricher._check_site(
                Username(value="testuser"), SAMPLE_SITES[0]
            )
            assert result is None

    @pytest.mark.asyncio
    async def test_4xx_returns_none(self):
        import flowsint_enrichers.social.to_whatsmyname as mod

        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 404
        mock_request = MagicMock()

        client = _make_mock_client(
            get_side_effect=httpx.HTTPStatusError(
                "404 Not Found", request=mock_request, response=mock_resp
            )
        )

        with patch("httpx.AsyncClient", return_value=client):
            e = ENRICHER_REGISTRY._enrichers["username_to_whatsmyname"]
            enricher = e(sketch_id="test", scan_id="test")

            result = await enricher._check_site(
                Username(value="testuser"), SAMPLE_SITES[0]
            )
            assert result is None

    @pytest.mark.asyncio
    async def test_5xx_returns_none(self):
        import flowsint_enrichers.social.to_whatsmyname as mod

        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 500
        mock_request = MagicMock()

        client = _make_mock_client(
            get_side_effect=httpx.HTTPStatusError(
                "500 Server Error", request=mock_request, response=mock_resp
            )
        )

        with patch("httpx.AsyncClient", return_value=client):
            e = ENRICHER_REGISTRY._enrichers["username_to_whatsmyname"]
            enricher = e(sketch_id="test", scan_id="test")

            result = await enricher._check_site(
                Username(value="testuser"), SAMPLE_SITES[0]
            )
            assert result is None

    @pytest.mark.asyncio
    async def test_network_error_returns_none(self):
        import flowsint_enrichers.social.to_whatsmyname as mod

        client = _make_mock_client(
            get_side_effect=httpx.RequestError("Connection refused")
        )

        with patch("httpx.AsyncClient", return_value=client):
            e = ENRICHER_REGISTRY._enrichers["username_to_whatsmyname"]
            enricher = e(sketch_id="test", scan_id="test")

            result = await enricher._check_site(
                Username(value="testuser"), SAMPLE_SITES[0]
            )
            assert result is None

    @pytest.mark.asyncio
    async def test_no_m_string_fallback_to_status_code(self):
        """When no m_string is provided, match by expected status code."""
        import flowsint_enrichers.social.to_whatsmyname as mod

        site_no_string = {
            "name": "NoStringSite",
            "uri_check": "https://example.com/{username}",
            "e_code": 200,
            "m_method": "GET",
            "m_string": "",
        }

        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 200
        mock_resp.text = ""

        client = _make_mock_client(get_side_effect=[mock_resp])

        with patch("httpx.AsyncClient", return_value=client):
            e = ENRICHER_REGISTRY._enrichers["username_to_whatsmyname"]
            enricher = e(sketch_id="test", scan_id="test")

            result = await enricher._check_site(
                Username(value="testuser"), site_no_string
            )
            assert result is not None
            assert result.platform == "NoStringSite"

    @pytest.mark.asyncio
    async def test_no_m_string_wrong_status_returns_none(self):
        import flowsint_enrichers.social.to_whatsmyname as mod

        site_no_string = {
            "name": "NoStringSite",
            "uri_check": "https://example.com/{username}",
            "e_code": 200,
            "m_method": "GET",
            "m_string": "",
        }

        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 404
        mock_resp.text = ""

        client = _make_mock_client(get_side_effect=[mock_resp])

        with patch("httpx.AsyncClient", return_value=client):
            e = ENRICHER_REGISTRY._enrichers["username_to_whatsmyname"]
            enricher = e(sketch_id="test", scan_id="test")

            result = await enricher._check_site(
                Username(value="testuser"), site_no_string
            )
            assert result is None


# ===================================================================
# 4. Postprocess
# ===================================================================


class TestPostprocess:
    def test_postprocess_creates_nodes(self):
        e = ENRICHER_REGISTRY._enrichers["username_to_whatsmyname"]
        graph_mock = MagicMock()
        enricher = e(sketch_id="test", scan_id="test", graph_service=graph_mock)

        account = SocialAccount(
            username=Username(value="testuser", platform="twitter"),
            platform="Twitter",
            profile_url="https://twitter.com/testuser",
        )

        results = enricher.postprocess([account], [account])
        assert len(results) == 1
        assert graph_mock.create_node_from_flowsint_type.called

    def test_postprocess_no_graph_returns_results(self):
        e = ENRICHER_REGISTRY._enrichers["username_to_whatsmyname"]
        enricher = e(sketch_id="test", scan_id="test")
        # Explicitly disable graph
        enricher._graph_service = None

        account = SocialAccount(
            username=Username(value="testuser", platform="twitter"),
            platform="Twitter",
            profile_url="https://twitter.com/testuser",
        )

        results = enricher.postprocess([account], [account])
        assert len(results) == 1


# ===================================================================
# 5. Params & docs
# ===================================================================


class TestParamsAndDocs:
    def test_params_schema(self):
        e = ENRICHER_REGISTRY._enrichers["username_to_whatsmyname"]
        params = e.get_params_schema()
        assert params == []

    def test_documentation(self):
        e = ENRICHER_REGISTRY._enrichers["username_to_whatsmyname"]
        docs = e.documentation()
        assert "WhatsMyName" in docs
        assert "WebBreacher" in docs
        assert len(docs) > 200
