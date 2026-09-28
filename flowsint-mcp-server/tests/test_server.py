"""Tests for FlowSint MCP server tools — HTTP contract verification via mocks."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from flowsint_mcp_server.server import (
    _build_template_create_body,
    _find_rendered_template_file,
    _load_json_payload,
    _load_template_context,
    _load_template_payload_file,
    _load_yaml_payload,
    _parse_csv_nodes,
    _render_template,
    _resolve_node_ids,
    create_enricher_template,
    create_flow,
    get_flow,
    get_sketch_graph,
    health,
    launch_enricher,
    launch_enrichers,
    list_analyses,
    list_enrichers,
    list_flows,
    list_investigations,
    login,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _mock_http_response(
    json_data: dict | None = None,
    status_code: int = 200,
    text: str = "ok",
    content_type: str = "application/json",
) -> Mock:
    resp = Mock(spec=["status_code", "text", "headers", "json"])
    resp.status_code = status_code
    resp.text = text
    resp.headers = {"content-type": content_type}
    resp.json.return_value = json_data if json_data is not None else {"ok": True}
    return resp


_TOKEN = "test-api-token-123"


# ---------------------------------------------------------------------------
# health (uses httpx.get directly)
# ---------------------------------------------------------------------------

class TestHealth:
    """health() calls httpx.get on /health with no auth."""

    def test_issues_get_and_parses_response(self):
        mock_resp = _mock_http_response({"status": "healthy"})
        with patch("flowsint_mcp_server.server.httpx.get", return_value=mock_resp) as get:
            result = health()

        get.assert_called_once()
        url = get.call_args[0][0] if get.call_args[0] else get.call_args.kwargs["url"]
        assert url.endswith("/health"), f"URL should end with /health, got {url}"

    def test_timeout_10(self):
        mock_resp = _mock_http_response()
        with patch("flowsint_mcp_server.server.httpx.get", return_value=mock_resp) as get:
            health()

        assert get.call_args.kwargs.get("timeout") == 10

    def test_no_authorization_header(self):
        mock_resp = _mock_http_response()
        with patch("flowsint_mcp_server.server.httpx.get", return_value=mock_resp) as get:
            health()

        headers = get.call_args.kwargs.get("headers", {})
        assert "Authorization" not in headers, "health should not send auth"


# ---------------------------------------------------------------------------
# login (uses httpx.post directly)
# ---------------------------------------------------------------------------

class TestLogin:
    """login() sends form-encoded POST to /api/auth/token."""

    def test_issues_post_to_auth_token(self):
        mock_resp = _mock_http_response({"access_token": "jwt-xxx"})
        with patch("flowsint_mcp_server.server.httpx.post", return_value=mock_resp) as post:
            login("user@test.com", "sekret")

        post.assert_called_once()
        url = post.call_args[0][0]
        assert url.endswith("/api/auth/token"), f"expected /api/auth/token, got {url}"

    def test_sends_username_and_password_as_form_data(self):
        mock_resp = _mock_http_response({"access_token": "jwt-xxx"})
        with patch("flowsint_mcp_server.server.httpx.post", return_value=mock_resp) as post:
            login("alice@example.com", "s3cret!")

        data = post.call_args.kwargs.get("data", {})
        assert data.get("username") == "alice@example.com"
        assert data.get("password") == "s3cret!"

    def test_timeout_15(self):
        mock_resp = _mock_http_response({"access_token": "jwt-xxx"})
        with patch("flowsint_mcp_server.server.httpx.post", return_value=mock_resp) as post:
            login("x@y.com", "sekret")

        assert post.call_args.kwargs.get("timeout") == 15

    def test_returns_token_on_success(self):
        mock_resp = _mock_http_response({"access_token": "my-jwt"})
        with patch("flowsint_mcp_server.server.httpx.post", return_value=mock_resp) as post:
            result = login("a@b.com", "sekret")

        assert "my-jwt" in result

    def test_handles_http_error(self):
        mock_resp = _mock_http_response(status_code=401, text="unauthorized")
        with patch("flowsint_mcp_server.server.httpx.post", return_value=mock_resp) as post:
            result = login("a@b.com", "wrong")

        assert "Login failed" in result
        assert "401" in result


# ---------------------------------------------------------------------------
# _req-based tools (all use httpx.request internally)
# ---------------------------------------------------------------------------

class TestListEnrichers:
    """list_enrichers() -> GET /api/enrichers."""

    def test_get_enrichers_no_category(self):
        mock_resp = _mock_http_response()
        with patch("flowsint_mcp_server.server.httpx.request", return_value=mock_resp) as req:
            list_enrichers(api_token=_TOKEN)

        req.assert_called_once()
        assert req.call_args[0][0] == "GET"
        assert req.call_args[0][1].endswith("/api/enrichers")

    def test_passes_category_param(self):
        mock_resp = _mock_http_response()
        with patch("flowsint_mcp_server.server.httpx.request", return_value=mock_resp) as req:
            list_enrichers(category="dns", api_token=_TOKEN)

        params = req.call_args.kwargs.get("params", {})
        assert params == {"category": "dns"}

    def test_no_category_param_when_empty(self):
        mock_resp = _mock_http_response()
        with patch("flowsint_mcp_server.server.httpx.request", return_value=mock_resp) as req:
            list_enrichers(api_token=_TOKEN)

        assert req.call_args.kwargs.get("params") is None

    def test_sends_bearer_token(self):
        mock_resp = _mock_http_response()
        with patch("flowsint_mcp_server.server.httpx.request", return_value=mock_resp) as req:
            list_enrichers(api_token=_TOKEN)

        headers = req.call_args.kwargs.get("headers", {})
        assert headers.get("Authorization") == f"Bearer {_TOKEN}"


class TestCreateEnricherTemplate:
    """create_enricher_template() -> POST /api/enrichers/templates."""

    def test_posts_to_templates_endpoint(self):
        mock_resp = _mock_http_response()
        template_content = json.dumps({
            "name": "my-template",
            "category": "Domain",
            "version": 1.0,
            "input": {"type": "string"},
            "request": {"method": "GET", "url": "https://example.com"},
            "output": {"type": "json"},
            "response": {"expect": "json"},
        })
        with patch("flowsint_mcp_server.server.httpx.request", return_value=mock_resp) as req:
            create_enricher_template(
                name="demo-template",
                category="domain",
                content=template_content,
                description="Demo template",
                is_public=True,
                version=2.0,
                api_token=_TOKEN,
            )

        req.assert_called_once()
        assert req.call_args[0][0] == "POST"
        assert req.call_args[0][1].endswith("/api/enrichers/templates")

        body = req.call_args.kwargs.get("json", {})
        assert body["name"] == "demo-template"
        assert body["category"] == "domain"
        assert body["description"] == "Demo template"
        assert body["is_public"] is True
        assert body["version"] == 2.0
        assert body["content"]["name"] == "my-template"

    def test_invalid_json_content_is_reported(self):
        result = create_enricher_template(
            name="demo-template",
            category="domain",
            content="not-json",
            api_token=_TOKEN,
        )
        assert result.startswith("Invalid content JSON")

    def test_cookiecutter_template_rendering_is_sent_to_api(self, tmp_path):
        mock_resp = _mock_http_response()
        fixture_path = Path(__file__).resolve().parent / "fixtures" / "enricher-template-cookiecutter"
        rendered_output = tmp_path / "_render_output"
        rendered_output.mkdir(parents=True, exist_ok=True)
        (rendered_output / "template.json").write_text(
            json.dumps(
                {
                    "name": "rendered-template",
                    "category": "test",
                    "request": {"url": "https://example.com/{value}"},
                    "input": {"type": "string"},
                    "output": {"type": "json"},
                    "response": {"expect": "json"},
                }
            ),
            encoding="utf-8",
        )
        template_context = json.dumps(
            {
                "template_name": "rendered-template",
                "template_category": "test",
                "template_request_url": "https://example.com/{value}",
            }
        )
        with (
            patch("flowsint_mcp_server.server.httpx.request", return_value=mock_resp) as req,
            patch(
                "cookiecutter.main.cookiecutter",
                return_value=str(rendered_output),
            ),
        ):
            create_enricher_template(
                name="cli-template",
                category="dns",
                template_path=str(fixture_path),
                template_context=template_context,
                description="From cookiecutter mode",
                is_public=True,
                version=3.0,
                api_token=_TOKEN,
            )

        req.assert_called_once()
        body = req.call_args.kwargs.get("json", {})
        assert body["name"] == "cli-template"
        assert body["category"] == "dns"
        assert body["version"] == 3.0
        assert body["is_public"] is True
        assert body["content"]["name"] == "rendered-template"
        assert body["content"]["category"] == "test"
        assert body["content"]["request"]["url"] == "https://example.com/{value}"

    def test_template_context_invalid_json(self):
        fixture_path = Path(__file__).resolve().parent / "fixtures" / "enricher-template-cookiecutter"
        result = create_enricher_template(
            name="demo-template",
            category="domain",
            template_path=str(fixture_path),
            template_context="{not-json}",
            api_token=_TOKEN,
        )
        assert result.startswith("Invalid template_context JSON")

    def test_template_path_required_when_no_content(self):
        result = create_enricher_template(
            name="demo-template",
            category="domain",
            api_token=_TOKEN,
        )
        assert result == "content is required unless template_path is provided"

    def test_content_and_template_path_are_mutually_exclusive(self):
        fixture_path = Path(__file__).resolve().parent / "fixtures" / "enricher-template-cookiecutter"
        template_content = json.dumps({
            "name": "template",
            "category": "Domain",
            "version": 1.0,
            "input": {"type": "string"},
            "request": {"method": "GET", "url": "https://example.com"},
            "output": {"type": "json"},
            "response": {"expect": "json"},
        })
        result = create_enricher_template(
            name="demo-template",
            category="domain",
            template_path=str(fixture_path),
            template_context="{}",
            content=template_content,
            api_token=_TOKEN,
        )
        assert result.startswith(
            "Provide either `content` (legacy JSON mode) or "
            "`template_path` (cookiecutter mode)"
        )


    def test_passes_bearer_token(self):
        mock_resp = _mock_http_response()
        template_content = json.dumps({
            "name": "template",
            "category": "Domain",
            "version": 1.0,
            "input": {"type": "string"},
            "request": {"method": "GET", "url": "https://example.com"},
            "output": {"type": "json"},
            "response": {"expect": "json"},
        })
        with patch("flowsint_mcp_server.server.httpx.request", return_value=mock_resp) as req:
            create_enricher_template(
                name="demo-template",
                category="domain",
                content=template_content,
                api_token=_TOKEN,
            )

        headers = req.call_args.kwargs.get("headers", {})
        assert headers.get("Authorization") == f"Bearer {_TOKEN}"


class TestParseCsvNodes:
    """_parse_csv_nodes() reads CSV input from the filesystem."""

    def test_reads_one_id_per_row_and_trims_whitespace(self, tmp_path):
        csv_path = tmp_path / "nodes.csv"
        csv_path.write_text(" node-a,\nnode-b\n  node-c  ,\n", encoding="utf-8")

        node_ids = _parse_csv_nodes(str(csv_path))

        assert node_ids == ["node-a", "node-b", "node-c"]

    def test_skips_single_column_header_row(self, tmp_path):
        csv_path = tmp_path / "nodes_with_header.csv"
        csv_path.write_text("node_id\nx\n", encoding="utf-8")

        node_ids = _parse_csv_nodes(str(csv_path))

        assert node_ids == ["x"]

    def test_fails_if_csv_row_has_multiple_values(self, tmp_path):
        csv_path = tmp_path / "multi_col.csv"
        csv_path.write_text("node_id,node_extra\nnode-a,abc\n", encoding="utf-8")

        with pytest.raises(ValueError, match="has extra columns"):
            _parse_csv_nodes(str(csv_path))

    def test_returns_empty_list_for_empty_file(self, tmp_path):
        csv_path = tmp_path / "empty.csv"
        csv_path.write_text("", encoding="utf-8")

        assert _parse_csv_nodes(str(csv_path)) == []

    def test_returns_empty_list_when_argument_missing(self):
        assert _parse_csv_nodes("") == []

    def test_file_read_error_is_reported(self):
        with pytest.raises(ValueError, match="Failed to read node IDs"):
            _parse_csv_nodes("/does/not/exist/nodes.csv")


class TestResolveNodeIds:
    """_resolve_node_ids() enforces deterministic node-id precedence."""

    def test_prefers_explicit_node_ids_before_csv(self, tmp_path):
        csv_path = tmp_path / "from_csv.csv"
        csv_path.write_text("from-csv\n", encoding="utf-8")

        values, error = _resolve_node_ids("explicit-a, explicit-b", str(csv_path))

        assert error is None
        assert values == ["explicit-a", "explicit-b", "from-csv"]

    def test_uses_csv_when_explicit_node_ids_are_empty(self, tmp_path):
        csv_path = tmp_path / "from_csv_only.csv"
        csv_path.write_text("from-csv-1\nfrom-csv-2\n", encoding="utf-8")

        values, error = _resolve_node_ids("", str(csv_path))

        assert error is None
        assert values == ["from-csv-1", "from-csv-2"]

    def test_returns_error_on_bad_csv_path(self):
        values, error = _resolve_node_ids("", "/does/not/exist/nodes.csv")

        assert values == []
        assert "Failed to read node IDs from '/does/not/exist/nodes.csv'" in error


class TestListInvestigations:
    """list_investigations() -> GET /api/investigations."""

    def test_get_investigations(self):
        mock_resp = _mock_http_response()
        with patch("flowsint_mcp_server.server.httpx.request", return_value=mock_resp) as req:
            list_investigations(api_token=_TOKEN)

        req.assert_called_once()
        assert req.call_args[0][0] == "GET"
        assert req.call_args[0][1].endswith("/api/investigations")

    def test_sends_bearer_token(self):
        mock_resp = _mock_http_response()
        with patch("flowsint_mcp_server.server.httpx.request", return_value=mock_resp) as req:
            list_investigations(api_token=_TOKEN)

        headers = req.call_args.kwargs.get("headers", {})
        assert headers.get("Authorization") == f"Bearer {_TOKEN}"

    def test_no_token_omits_auth_header(self):
        mock_resp = _mock_http_response()
        with patch("flowsint_mcp_server.server.httpx.request", return_value=mock_resp) as req:
            list_investigations()

        headers = req.call_args.kwargs.get("headers", {})
        assert "Authorization" not in headers


class TestListFlows:
    """list_flows() -> GET /api/flows."""

    def test_get_flows_no_category(self):
        mock_resp = _mock_http_response()
        with patch("flowsint_mcp_server.server.httpx.request", return_value=mock_resp) as req:
            list_flows(api_token=_TOKEN)

        req.assert_called_once()
        assert req.call_args[0][0] == "GET"
        assert req.call_args[0][1].endswith("/api/flows")

    def test_passes_category_param(self):
        mock_resp = _mock_http_response()
        with patch("flowsint_mcp_server.server.httpx.request", return_value=mock_resp) as req:
            list_flows(category="osint", api_token=_TOKEN)

        params = req.call_args.kwargs.get("params", {})
        assert params == {"category": "osint"}

    def test_sends_bearer_token(self):
        mock_resp = _mock_http_response()
        with patch("flowsint_mcp_server.server.httpx.request", return_value=mock_resp) as req:
            list_flows(api_token=_TOKEN)

        headers = req.call_args.kwargs.get("headers", {})
        assert headers.get("Authorization") == f"Bearer {_TOKEN}"


class TestGetFlow:
    """get_flow() -> GET /api/flows/{flow_id}."""

    def test_get_flow_by_id(self):
        mock_resp = _mock_http_response()
        with patch("flowsint_mcp_server.server.httpx.request", return_value=mock_resp) as req:
            get_flow("flow-42", api_token=_TOKEN)

        req.assert_called_once()
        assert req.call_args[0][0] == "GET"
        assert req.call_args[0][1].endswith("/api/flows/flow-42")

    def test_sends_bearer_token(self):
        mock_resp = _mock_http_response()
        with patch("flowsint_mcp_server.server.httpx.request", return_value=mock_resp) as req:
            get_flow("f1", api_token=_TOKEN)

        headers = req.call_args.kwargs.get("headers", {})
        assert headers.get("Authorization") == f"Bearer {_TOKEN}"


class TestCreateFlow:
    """create_flow() -> POST /api/flows/create."""

    def test_issues_post_to_create(self):
        mock_resp = _mock_http_response()
        with patch("flowsint_mcp_server.server.httpx.request", return_value=mock_resp) as req:
            create_flow('{"name":"test"}', api_token=_TOKEN)

        req.assert_called_once()
        assert req.call_args[0][0] == "POST"
        assert req.call_args[0][1].endswith("/api/flows/create")

    def test_sends_parsed_json_body(self):
        mock_resp = _mock_http_response()
        with patch("flowsint_mcp_server.server.httpx.request", return_value=mock_resp) as req:
            create_flow('{"name":"audit","type":"dns"}', api_token=_TOKEN)

        body = req.call_args.kwargs.get("json", {})
        assert body == {"name": "audit", "type": "dns"}

    def test_sends_bearer_token(self):
        mock_resp = _mock_http_response()
        with patch("flowsint_mcp_server.server.httpx.request", return_value=mock_resp) as req:
            create_flow('{"name":"test"}', api_token=_TOKEN)

        headers = req.call_args.kwargs.get("headers", {})
        assert headers.get("Authorization") == f"Bearer {_TOKEN}"

class TestLaunchEnricher:
    """launch_enricher() -> POST /api/enrichers/{name}/launch."""

    def test_issues_post_to_launch(self):
        mock_resp = _mock_http_response()
        with patch("flowsint_mcp_server.server.httpx.request", return_value=mock_resp) as req:
            launch_enricher("geoip", "node1,node2", "sketch-1", api_token=_TOKEN)

        req.assert_called_once()
        assert req.call_args[0][0] == "POST"
        assert req.call_args[0][1].endswith("/api/enrichers/geoip/launch")

    def test_sends_node_ids_and_sketch_id_as_json(self):
        mock_resp = _mock_http_response()
        with patch("flowsint_mcp_server.server.httpx.request", return_value=mock_resp) as req:
            launch_enricher("whois", "n1, n2, n3", "sk-99", api_token=_TOKEN)

        body = req.call_args.kwargs.get("json", {})
        assert body["node_ids"] == ["n1", "n2", "n3"]
        assert body["sketch_id"] == "sk-99"

    def test_handles_single_node(self):
        mock_resp = _mock_http_response()
        with patch("flowsint_mcp_server.server.httpx.request", return_value=mock_resp) as req:
            launch_enricher("hashlookup", "single-node", "sk-1", api_token=_TOKEN)

        body = req.call_args.kwargs.get("json", {})
        assert body["node_ids"] == ["single-node"]

    def test_handles_empty_node_ids(self):
        mock_resp = _mock_http_response()
        with patch("flowsint_mcp_server.server.httpx.request", return_value=mock_resp) as req:
            launch_enricher("test", "", "sk-1", api_token=_TOKEN)

        body = req.call_args.kwargs.get("json", {})
        assert body["node_ids"] == []

    def test_supports_node_ids_csv_file(self, tmp_path):
        mock_resp = _mock_http_response()
        csv_path = tmp_path / "nodes.csv"
        csv_path.write_text("csv-node-a\ncsv-node-b\n", encoding="utf-8")

        with patch("flowsint_mcp_server.server.httpx.request", return_value=mock_resp) as req:
            launch_enricher(
                "whois",
                "",
                "sk-1",
                node_ids_csv=str(csv_path),
                api_token=_TOKEN,
            )

        body = req.call_args.kwargs.get("json", {})
        assert body["node_ids"] == ["csv-node-a", "csv-node-b"]

    def test_merges_explicit_node_ids_and_csv_node_ids(self, tmp_path):
        mock_resp = _mock_http_response()
        csv_path = tmp_path / "nodes.csv"
        csv_path.write_text("csv-node\n", encoding="utf-8")

        with patch("flowsint_mcp_server.server.httpx.request", return_value=mock_resp) as req:
            launch_enricher(
                "whois",
                "explicit-a",
                "sk-1",
                node_ids_csv=str(csv_path),
                api_token=_TOKEN,
            )

        body = req.call_args.kwargs.get("json", {})
        assert body["node_ids"] == ["explicit-a", "csv-node"]

    def test_reports_csv_read_error(self):
        result = launch_enricher(
            "whois",
            "n1",
            "sk-1",
            node_ids_csv="/does/not/exist/nodes.csv",
            api_token=_TOKEN,
        )
        parsed = json.loads(result)
        assert parsed["error"] == "Failed to read node IDs from '/does/not/exist/nodes.csv': [Errno 2] No such file or directory: '/does/not/exist/nodes.csv'"

    def test_csv_header_row_is_accepted(self, tmp_path: Path):
        node_ids_csv = tmp_path / "node_ids_with_header.csv"
        node_ids_csv.write_text("node_id\nnode-a\nnode-b\n", encoding="utf-8")

        mock_resp = _mock_http_response()
        with patch("flowsint_mcp_server.server.httpx.request", return_value=mock_resp) as req:
            launch_enricher(
                "domain_to_whois",
                "",
                "sk-1",
                node_ids_csv=str(node_ids_csv),
                api_token=_TOKEN,
            )

        body = req.call_args.kwargs.get("json", {})
        assert body["node_ids"] == ["node-a", "node-b"]

    def test_loads_quoted_csv_node_id(self, tmp_path: Path):
        node_ids_csv = tmp_path / "node_ids_quoted.csv"
        node_ids_csv.write_text('"node,with,comma"\n', encoding="utf-8")

        mock_resp = _mock_http_response()
        with patch("flowsint_mcp_server.server.httpx.request", return_value=mock_resp) as req:
            launch_enricher(
                "domain_to_whois",
                "",
                "sk-1",
                node_ids_csv=str(node_ids_csv),
                api_token=_TOKEN,
            )

        body = req.call_args.kwargs.get("json", {})
        assert body["node_ids"] == ["node,with,comma"]

    def test_rejects_csv_with_extra_columns(self, tmp_path: Path):
        node_ids_csv = tmp_path / "invalid_node_ids.csv"
        node_ids_csv.write_text("node-1,node-meta\nnode-2,x\n", encoding="utf-8")

        result = launch_enricher(
            "domain_to_whois",
            "",
            "sk-1",
            node_ids_csv=str(node_ids_csv),
            api_token=_TOKEN,
        )

        payload = json.loads(result)
        assert "extra columns" in payload["error"]

    def test_sends_bearer_token(self):
        mock_resp = _mock_http_response()
        with patch("flowsint_mcp_server.server.httpx.request", return_value=mock_resp) as req:
            launch_enricher("x", "n1", "s1", api_token=_TOKEN)

        headers = req.call_args.kwargs.get("headers", {})
        assert headers.get("Authorization") == f"Bearer {_TOKEN}"

    def test_sends_runtime_launch_params(self):
        mock_resp = _mock_http_response()
        with patch("flowsint_mcp_server.server.httpx.request", return_value=mock_resp) as req:
            launch_enricher(
                "domain_to_whois",
                "n1",
                "s1",
                enable_http2=True,
                enable_quic=True,
                enable_gpu=True,
                gpu_provider="cudf",
                max_concurrency=17,
                request_timeout=29,
                launch_params_json='{"headless": true, "max_concurrency": 4}',
                api_token=_TOKEN,
            )

        body = req.call_args.kwargs.get("json", {})
        assert body["params"]["enable_http2"] is True
        assert body["params"]["enable_quic"] is True
        assert body["params"]["enable_gpu"] is True
        assert body["params"]["gpu_provider"] == "cudf"
        assert body["params"]["max_concurrency"] == 17
        assert body["params"]["request_timeout"] == 29
        assert body["params"]["headless"] is True

    def test_invalid_launch_params_json_is_reported(self):
        result = launch_enricher(
            "domain_to_whois",
            "n1",
            "s1",
            launch_params_json="{not-json}",
            api_token=_TOKEN,
        )
        assert result == "Invalid launch params JSON: Expecting property name enclosed in double quotes: line 1 column 2 (char 1)"

class TestLaunchEnrichers:
    """launch_enrichers() -> POST one or many /api/enrichers/{name}/launch."""

    def test_launches_multiple_enrichers(self):
        mock_resp = _mock_http_response({"id": "job-1"})
        with patch("flowsint_mcp_server.server.httpx.request", return_value=mock_resp) as req:
            result = launch_enrichers(
                "geoip, whois , hashlookup",
                "node-1,node-2",
                "sketch-1",
                api_token=_TOKEN,
            )

        assert req.call_count == 3
        payload = json.loads(result)
        assert payload["results"][0]["enricher_name"] == "geoip"
        assert payload["results"][1]["enricher_name"] == "whois"
        assert payload["results"][2]["enricher_name"] == "hashlookup"
        assert payload["results"][0]["result"] == {"id": "job-1"}

    def test_empty_names_returns_error(self):
        result = launch_enrichers("  ", "node-1", "sketch-1", api_token=_TOKEN)
        assert json.loads(result)["error"] == "enricher_names is required"

    def test_uses_node_ids_after_normalization(self):
        mock_resp = _mock_http_response({"id": "job-1"})
        with patch("flowsint_mcp_server.server.httpx.request", return_value=mock_resp) as req:
            launch_enrichers("dns", "n1, n2,  n3", "sketch-1", api_token=_TOKEN)

        req.assert_called_once()
        body = req.call_args.kwargs.get("json", {})
        assert body["node_ids"] == ["n1", "n2", "n3"]

    def test_supports_node_ids_csv_file_for_multiple_enrichers(self, tmp_path):
        mock_resp = _mock_http_response({"id": "job-1"})
        csv_path = tmp_path / "nodes.csv"
        csv_path.write_text("csv-node-a\ncsv-node-b\n", encoding="utf-8")

        with patch("flowsint_mcp_server.server.httpx.request", return_value=mock_resp) as req:
            result = launch_enrichers(
                "dns, whois",
                "",
                "sketch-1",
                node_ids_csv=str(csv_path),
                api_token=_TOKEN,
            )

        payload = json.loads(result)
        assert req.call_count == 2
        assert payload["results"][0]["result"]["id"] == "job-1"
        assert payload["results"][1]["result"]["id"] == "job-1"

        payloads = [call.kwargs.get("json", {}) for call in req.call_args_list]
        assert payloads[0]["node_ids"] == ["csv-node-a", "csv-node-b"]
        assert payloads[1]["node_ids"] == ["csv-node-a", "csv-node-b"]

    def test_launch_enrichers_accepts_csv_node_ids(self, tmp_path: Path):
        node_ids_csv = tmp_path / "node_ids.csv"
        node_ids_csv.write_text("node-2\nnode-3\n", encoding="utf-8")

        mock_resp = _mock_http_response({"id": "job-1"})
        with patch("flowsint_mcp_server.server.httpx.request", return_value=mock_resp) as req:
            launch_enrichers("dns", "", "sketch-1", node_ids_csv=str(node_ids_csv), api_token=_TOKEN)

        req.assert_called_once()
        body = req.call_args.kwargs.get("json", {})
        assert body["node_ids"] == ["node-2", "node-3"]

    def test_launch_enrichers_combines_node_ids_and_csv_input(self, tmp_path: Path):
        node_ids_csv = tmp_path / "node_ids.csv"
        node_ids_csv.write_text("node-4\n", encoding="utf-8")

        mock_resp = _mock_http_response({"id": "job-1"})
        with patch("flowsint_mcp_server.server.httpx.request", return_value=mock_resp) as req:
            launch_enrichers("dns", "node-1,node-2", "sketch-1", node_ids_csv=str(node_ids_csv), api_token=_TOKEN)

        req.assert_called_once()
        body = req.call_args.kwargs.get("json", {})
        assert body["node_ids"] == ["node-1", "node-2", "node-4"]

    def test_csv_rows_merge_for_multiple_enrichers(self, tmp_path):
        csv_path = tmp_path / "tmp_nodes.csv"
        csv_path.write_text("n3\nn4\n")

        mock_resp = _mock_http_response({"id": "job-1"})
        with patch("flowsint_mcp_server.server.httpx.request", return_value=mock_resp) as req:
            launch_enrichers(
                "dns,whois",
                "n1, n2",
                "sketch-1",
                node_ids_csv=str(csv_path),
                api_token=_TOKEN,
            )

        payloads = [call.kwargs.get("json", {}) for call in req.call_args_list]
        assert payloads[0]["node_ids"] == ["n1", "n2", "n3", "n4"]
        assert payloads[1]["node_ids"] == ["n1", "n2", "n3", "n4"]

    def test_launch_enrichers_sends_shared_launch_params(self, tmp_path):
        csv_path = tmp_path / "nodes.csv"
        csv_path.write_text("csv-node-a\ncsv-node-b\n", encoding="utf-8")

        mock_resp = _mock_http_response({"id": "job-1"})
        with patch("flowsint_mcp_server.server.httpx.request", return_value=mock_resp) as req:
            launch_enrichers(
                "dns,whois",
                "",
                "sketch-1",
                node_ids_csv=str(csv_path),
                enable_http2=True,
                gpu_provider="auto",
                launch_params_json='{"headless": false}',
                api_token=_TOKEN,
            )

        reqs = [call.kwargs.get("json", {}) for call in req.call_args_list]
        assert len(reqs) == 2
        assert reqs[0]["params"]["enable_http2"] is True
        assert reqs[0]["params"]["gpu_provider"] == "auto"
        assert reqs[0]["params"]["headless"] is False
        assert reqs[1]["params"]["enable_http2"] is True
    

class TestGetSketchGraph:
    """get_sketch_graph() -> GET /api/sketches/{sketch_id}/graph."""

    def test_issues_get_with_sketch_id(self):
        mock_resp = _mock_http_response()
        with patch("flowsint_mcp_server.server.httpx.request", return_value=mock_resp) as req:
            get_sketch_graph("sketch-abc", api_token=_TOKEN)

        req.assert_called_once()
        assert req.call_args[0][0] == "GET"
        assert req.call_args[0][1].endswith("/api/sketches/sketch-abc/graph")

    def test_sends_bearer_token(self):
        mock_resp = _mock_http_response()
        with patch("flowsint_mcp_server.server.httpx.request", return_value=mock_resp) as req:
            get_sketch_graph("sk-1", api_token=_TOKEN)

        headers = req.call_args.kwargs.get("headers", {})
        assert headers.get("Authorization") == f"Bearer {_TOKEN}"

    def test_no_token_omits_auth_header(self):
        mock_resp = _mock_http_response()
        with patch("flowsint_mcp_server.server.httpx.request", return_value=mock_resp) as req:
            get_sketch_graph("sk-1")

        headers = req.call_args.kwargs.get("headers", {})
        assert "Authorization" not in headers


class TestListAnalyses:
    """list_analyses() -> GET /api/analyses."""

    def test_issues_get_to_analyses(self):
        mock_resp = _mock_http_response()
        with patch("flowsint_mcp_server.server.httpx.request", return_value=mock_resp) as req:
            list_analyses(api_token=_TOKEN)

        req.assert_called_once()
        assert req.call_args[0][0] == "GET"
        assert req.call_args[0][1].endswith("/api/analyses")

    def test_sends_bearer_token(self):
        mock_resp = _mock_http_response()
        with patch("flowsint_mcp_server.server.httpx.request", return_value=mock_resp) as req:
            list_analyses(api_token=_TOKEN)

        headers = req.call_args.kwargs.get("headers", {})
        assert headers.get("Authorization") == f"Bearer {_TOKEN}"

    def test_no_token_omits_auth_header(self):
        mock_resp = _mock_http_response()
        with patch("flowsint_mcp_server.server.httpx.request", return_value=mock_resp) as req:
            list_analyses()

        headers = req.call_args.kwargs.get("headers", {})
        assert "Authorization" not in headers




# ---------------------------------------------------------------------------
# _load_template_context (new helper for template context validation)
# ---------------------------------------------------------------------------

class TestLoadTemplateContext:
    """_load_template_context() parses and validates template_context JSON."""

    def test_empty_returns_empty_dict(self):
        assert _load_template_context("") == {}

    def test_valid_json_object(self):
        assert _load_template_context('{"key": "val", "n": 42}') == {"key": "val", "n": 42}

    def test_invalid_json_raises_value_error(self):
        with pytest.raises(ValueError, match="Invalid template_context JSON"):
            _load_template_context("not-json")

    def test_non_dict_json_raises_value_error(self):
        with pytest.raises(ValueError, match="template_context must be a JSON object"):
            _load_template_context('["a", "b"]')


# ---------------------------------------------------------------------------
# _load_json_payload (new helper for validating content JSON must be a dict)
# ---------------------------------------------------------------------------

class TestLoadJsonPayload:
    """_load_json_payload() loads JSON and enforces dict type."""

    def test_valid_dict(self):
        assert _load_json_payload('{"a": 1}') == {"a": 1}

    def test_array_raises_value_error(self):
        with pytest.raises(ValueError, match="Template content must be a JSON object"):
            _load_json_payload('[1, 2, 3]')

    def test_invalid_json_raises_json_decode_error(self):
        with pytest.raises(json.JSONDecodeError):
            _load_json_payload("{{{broken}}}")


# ---------------------------------------------------------------------------
# _find_rendered_template_file (cookiecutter output file discovery)
# ---------------------------------------------------------------------------

class TestFindRenderedTemplateFile:
    """_find_rendered_template_file() locates output JSON/YAML from cookiecutter."""

    def test_prefers_template_json(self, tmp_path):
        (tmp_path / "template.json").write_text("{}")
        (tmp_path / "data.yaml").write_text("k: v")
        result = _find_rendered_template_file(tmp_path)
        assert result.name == "template.json"

    def test_prefers_template_yaml_when_no_template_json(self, tmp_path):
        (tmp_path / "template.yaml").write_text("k: v")
        (tmp_path / "data.json").write_text("{}")
        result = _find_rendered_template_file(tmp_path)
        assert result.name == "template.yaml"

    def test_prefers_template_yml_when_no_template_json_or_yaml(self, tmp_path):
        (tmp_path / "template.yml").write_text("k: v")
        (tmp_path / "data.json").write_text("{}")
        result = _find_rendered_template_file(tmp_path)
        assert result.name == "template.yml"

    def test_uses_first_candidate_when_no_template_named_file(self, tmp_path):
        (tmp_path / "output.json").write_text("{}")
        (tmp_path / "info.yaml").write_text("k: v")
        result = _find_rendered_template_file(tmp_path)
        # sorted alphabetically, so info.yaml comes first
        assert result.name == "info.yaml"

    def test_skips_cookiecutter_json(self, tmp_path):
        (tmp_path / "cookiecutter.json").write_text("{}")
        (tmp_path / "template.json").write_text("{}")
        result = _find_rendered_template_file(tmp_path)
        assert result.name == "template.json"

    def test_raises_when_no_json_or_yaml_files(self, tmp_path):
        (tmp_path / "readme.txt").write_text("hello")
        with pytest.raises(FileNotFoundError, match="contains no JSON/YAML"):
            _find_rendered_template_file(tmp_path)

    def test_returns_template_json_over_template_yaml(self, tmp_path):
        (tmp_path / "template.json").write_text("{}")
        (tmp_path / "template.yaml").write_text("k: v")
        result = _find_rendered_template_file(tmp_path)
        assert result.name == "template.json"

    def test_handles_nested_directories(self, tmp_path):
        nested = tmp_path / "subdir"
        nested.mkdir()
        (nested / "template.json").write_text("{}")
        result = _find_rendered_template_file(tmp_path)
        assert result.name == "template.json"


# ---------------------------------------------------------------------------
# _load_template_payload_file (load from .json or .yaml/.yml)
# ---------------------------------------------------------------------------

class TestLoadTemplatePayloadFile:
    """_load_template_payload_file() dispatches to JSON or YAML loader."""

    def test_loads_json_file(self, tmp_path):
        p = tmp_path / "data.json"
        p.write_text('{"a": 1}')
        assert _load_template_payload_file(p) == {"a": 1}

    def test_loads_yaml_file(self, tmp_path):
        p = tmp_path / "data.yaml"
        p.write_text("a: 1\n")
        assert _load_template_payload_file(p) == {"a": 1}

    def test_loads_yml_file(self, tmp_path):
        p = tmp_path / "data.yml"
        p.write_text("key: value\n")
        assert _load_template_payload_file(p) == {"key": "value"}

    def test_json_non_dict_raises(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("[1, 2]")
        with pytest.raises(ValueError, match="Template content must be a JSON object"):
            _load_template_payload_file(p)

    def test_yaml_non_dict_raises(self, tmp_path):
        p = tmp_path / "bad.yaml"
        p.write_text("- one\n- two\n")
        with pytest.raises(ValueError, match="Template content must be a YAML object"):
            _load_template_payload_file(p)


# ---------------------------------------------------------------------------
# _load_yaml_payload (standalone YAML loader)
# ---------------------------------------------------------------------------

class TestLoadYamlPayload:
    """_load_yaml_payload() reads and validates YAML files."""

    def test_loads_valid_yaml(self, tmp_path):
        p = tmp_path / "data.yaml"
        p.write_text("name: test\nversion: 1.0\n")
        assert _load_yaml_payload(p) == {"name": "test", "version": 1.0}

    def test_non_dict_yaml_raises(self, tmp_path):
        p = tmp_path / "list.yaml"
        p.write_text("- one\n- two\n")
        with pytest.raises(ValueError, match="Template content must be a YAML object"):
            _load_yaml_payload(p)

    def test_empty_file_raises(self, tmp_path):
        p = tmp_path / "empty.yaml"
        p.write_text("")
        with pytest.raises(ValueError, match="Template content must be a YAML object"):
            _load_yaml_payload(p)


# ---------------------------------------------------------------------------
# _build_template_create_body (API body construction)
# ---------------------------------------------------------------------------

class TestBuildTemplateCreateBody:
    """_build_template_create_body() builds the API JSON body."""

    def test_builds_body_without_description(self):
        body = _build_template_create_body(
            name="test-tpl",
            category="Domain",
            description="",
            version=1.5,
            is_public=False,
            payload_content={"input": {"type": "string"}},
        )
        assert body["name"] == "test-tpl"
        assert body["category"] == "Domain"
        assert body["version"] == 1.5
        assert body["content"] == {"input": {"type": "string"}}
        assert body["is_public"] is False
        assert "description" not in body

    def test_builds_body_with_description(self):
        body = _build_template_create_body(
            name="test-tpl",
            category="Domain",
            description="My template",
            version=1.0,
            is_public=True,
            payload_content={"input": {"type": "string"}},
        )
        assert body["description"] == "My template"
        assert body["content"] == {"input": {"type": "string"}}

    def test_content_is_not_mutated(self):
        original = {"name": "inner-name", "input": {"type": "int"}}
        body = _build_template_create_body(
            name="outer-name",
            category="Domain",
            description="",
            version=1.0,
            is_public=False,
            payload_content=original,
        )
        # content dict should be passed through without mutation
        assert body["content"]["name"] == "inner-name"
        assert body["content"]["input"] == {"type": "int"}
        # original should not have been altered
        assert original["name"] == "inner-name"

    def test_content_top_level_name_differs_from_body_name(self):
        body = _build_template_create_body(
            name="api-name",
            category="Cat",
            description="",
            version=2.0,
            is_public=True,
            payload_content={"name": "tpl-name", "category": "Cat"},
        )
        assert body["name"] == "api-name"
        assert body["content"]["name"] == "tpl-name"


# ---------------------------------------------------------------------------
# _render_template (cookiecutter rendering pipeline — error paths only)
# ---------------------------------------------------------------------------

class TestRenderTemplate:
    """_render_template() renders cookiecutter templates from the filesystem."""

    def test_missing_path_raises_file_not_found(self):
        with pytest.raises(FileNotFoundError, match="template_path does not exist"):
            _render_template("/does/not/exist", "{}")

    def test_file_path_instead_of_directory_raises_value_error(self, tmp_path):
        f = tmp_path / "not_a_dir.json"
        f.write_text("{}")
        with pytest.raises(ValueError, match="template_path must be a cookiecutter template directory"):
            _render_template(str(f), "{}")

    def test_bad_context_json_raises_value_error(self, tmp_path):
        # Need a real directory so the path existence check passes first
        with pytest.raises(ValueError, match="Invalid template_context JSON"):
            _render_template(str(tmp_path), "not-json-braces")

    def test_context_non_dict_raises_value_error(self, tmp_path):
        with pytest.raises(ValueError, match="template_context must be a JSON object"):
            _render_template(str(tmp_path), "[]")

    def test_cookiecutter_not_installed_raises_runtime_error(self, tmp_path):
        # We mock the import inside _render_template to simulate missing cookiecutter
        original_import = None
        with patch.dict("sys.modules", {"cookiecutter": None, "cookiecutter.main": None}):
            with pytest.raises(RuntimeError, match="cookiecutter is required"):
                _render_template(str(tmp_path), "{}")

    def test_successful_render_via_mock(self, tmp_path):
        """Mock the cookiecutter call to verify the full pipeline."""
        # Set up a cookiecutter template directory with cookiecutter.json
        (tmp_path / "cookiecutter.json").write_text('{"name": "Demo"}', encoding="utf-8")
        output = tmp_path / "_render_output"
        output.mkdir()
        (output / "template.json").write_text('{"name": "Rendered", "category": "Domain"}', encoding="utf-8")

        # NOTE: `output` (tmp_path/_render_output) serves double duty here:
        # 1. The test creates it as the fixture directory containing template.json
        # 2. _render_template constructs the same path as output_dir and later
        #    removes it in its finally block. The mock intercepts the cookiecutter
        #    call, so the template.json is read before cleanup.
        assert output.exists()
        with patch("cookiecutter.main.cookiecutter", return_value=str(output)) as mock_cc:
            result = _render_template(str(tmp_path), '{"extra": "val"}')

        assert result == {"name": "Rendered", "category": "Domain"}
        mock_cc.assert_called_once_with(
            str(tmp_path),
            no_input=True,
            extra_context={"extra": "val"},
            output_dir=str(output),
        )


# ---------------------------------------------------------------------------
# create_enricher_template — template_path mode (new optional path)
# ---------------------------------------------------------------------------

class TestCreateEnricherTemplateTemplatePath:
    """create_enricher_template() — template_path / template_context optional mode."""

    def test_template_path_with_content_returns_error(self):
        result = create_enricher_template(
            name="demo",
            category="Domain",
            content='{"a": 1}',
            template_path="/some/path",
            api_token=_TOKEN,
        )
        assert "Provide either" in result and "not both" in result

    def test_missing_both_content_and_template_path(self):
        result = create_enricher_template(
            name="demo",
            category="Domain",
            api_token=_TOKEN,
        )
        assert "content is required unless" in result

    def test_bad_template_path_propagates_file_not_found(self):
        result = create_enricher_template(
            name="demo",
            category="Domain",
            template_path="/nonexistent/template",
            api_token=_TOKEN,
        )
        assert "template_path does not exist" in result

    def test_renders_via_mock_and_posts_to_api(self, tmp_path):
        """End-to-end: mock cookiecutter rendering then verify the API payload."""
        # Set up a fake template dir with cookiecutter.json
        (tmp_path / "cookiecutter.json").write_text('{"name": "Demo"}', encoding="utf-8")
        output = tmp_path / "_cc_output"
        output.mkdir()
        (output / "template.json").write_text(
            '{"name": "RenderedTpl", "category": "Domain", "version": 1.0}',
            encoding="utf-8",
        )

        mock_resp = _mock_http_response()
        with (
            patch("flowsint_mcp_server.server.httpx.request", return_value=mock_resp) as req,
            patch("cookiecutter.main.cookiecutter", return_value=str(output)),
        ):
            create_enricher_template(
                name="my-api-name",
                category="Domain",
                template_path=str(tmp_path),
                template_context='{"extra": "val"}',
                description="Generated from cookiecutter",
                version=2.0,
                is_public=True,
                api_token=_TOKEN,
            )

        req.assert_called_once()
        body = req.call_args.kwargs.get("json", {})
        assert body["name"] == "my-api-name"
        assert body["category"] == "Domain"
        assert body["description"] == "Generated from cookiecutter"
        assert body["version"] == 2.0
        assert body["is_public"] is True
        # The content should be the rendered template file
        assert body["content"]["name"] == "RenderedTpl"
        assert body["content"]["category"] == "Domain"

# ---------------------------------------------------------------------------
# Additional coverage: error-handling in _req-based tools
# ---------------------------------------------------------------------------

class TestReqErrorHandling:
    """_req() error-path coverage shared by all _req-based tools."""

    @pytest.mark.parametrize("tool_call", [
        lambda: list_enrichers(api_token=_TOKEN),
        lambda: list_investigations(api_token=_TOKEN),
        lambda: list_flows(api_token=_TOKEN),
        lambda: list_analyses(api_token=_TOKEN),
    ])
    def test_http_error_returns_error_dict(self, tool_call):
        mock_resp = _mock_http_response(
            status_code=500, text="internal error", content_type="text/plain"
        )
        with patch("flowsint_mcp_server.server.httpx.request", return_value=mock_resp):
            result = tool_call()

        parsed = json.loads(result)
        assert parsed.get("error") == "HTTP 500"
        assert "internal error" in parsed.get("detail", "")

    @pytest.mark.parametrize("tool_call,label", [
        (lambda: list_investigations(api_token="bad-token"), "401 unauth"),
        (lambda: get_flow("nonexistent", api_token=_TOKEN), "404 missing"),
    ])
    def test_error_status_surfaced(self, tool_call, label):
        """Verify 401/404 errors produce readable error output, not silent."""
        status = 401 if "401" in label else 404
        detail_text = "Unauthorized" if status == 401 else "Not found"
        mock_resp = _mock_http_response(
            status_code=status, text=detail_text, content_type="text/plain"
        )
        with patch("flowsint_mcp_server.server.httpx.request", return_value=mock_resp):
            result = tool_call()

        parsed = json.loads(result)
        assert parsed.get("error") == f"HTTP {status}", \
            f"{label}: expected error field 'HTTP {status}', got {parsed}"
        assert detail_text in parsed.get("detail", ""), \
            f"{label}: expected detail containing '{detail_text}', got {parsed}"

    def test_empty_response_returns_ok(self):
        mock_resp = _mock_http_response(
            text="", content_type="text/plain"
        )
        with patch("flowsint_mcp_server.server.httpx.request", return_value=mock_resp) as req:
            result = list_enrichers(api_token=_TOKEN)

        parsed = json.loads(result)
        assert parsed == {"ok": True}

    def test_non_json_response_returns_raw(self):
        mock_resp = _mock_http_response(
            text="plain text body",
            content_type="text/plain",
        )
        with patch("flowsint_mcp_server.server.httpx.request", return_value=mock_resp) as req:
            result = list_enrichers(api_token=_TOKEN)

        parsed = json.loads(result)
        assert parsed == {"raw": "plain text body"}


# ---------------------------------------------------------------------------
# Verify the `Accept` header is always sent by _req
# ---------------------------------------------------------------------------

class TestAcceptHeader:
    """All _req-based calls must send Accept: application/json."""

    @pytest.mark.parametrize("tool_call", [
        lambda: list_enrichers(api_token=_TOKEN),
        lambda: list_investigations(api_token=_TOKEN),
        lambda: list_flows(api_token=_TOKEN),
        lambda: get_flow("f1", api_token=_TOKEN),
        lambda: create_flow('{"a":1}', api_token=_TOKEN),
        lambda: launch_enricher("e", "n1", "s1", api_token=_TOKEN),
        lambda: get_sketch_graph("s1", api_token=_TOKEN),
        lambda: list_analyses(api_token=_TOKEN),
    ])
    def test_accept_header_present(self, tool_call):
        mock_resp = _mock_http_response()
        with patch("flowsint_mcp_server.server.httpx.request", return_value=mock_resp) as req:
            tool_call()

        headers = req.call_args.kwargs.get("headers", {})
        assert headers.get("Accept") == "application/json"
