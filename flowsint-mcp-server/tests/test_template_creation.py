"""Tests for enricher template creation — helper functions and cookiecutter mode."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from flowsint_mcp_server.server import (
    _load_template_context,
    _load_json_payload,
    _find_rendered_template_file,
    _build_template_create_body,
    _render_template,
    create_enricher_template,
)


# ---------------------------------------------------------------------------
# _load_template_context
# ---------------------------------------------------------------------------

class TestLoadTemplateContext:
    """_load_template_context() parses JSON template context."""

    def test_empty_string_returns_empty_dict(self):
        assert _load_template_context("") == {}

    def test_valid_json_parses(self):
        result = _load_template_context('{"key": "value", "num": 42}')
        assert result == {"key": "value", "num": 42}

    def test_invalid_json_raises(self):
        with pytest.raises(ValueError, match="Invalid template_context JSON"):
            _load_template_context("not-json")

    def test_non_dict_json_raises(self):
        with pytest.raises(ValueError, match="template_context must be a JSON object"):
            _load_template_context('["list"]')


# ---------------------------------------------------------------------------
# _load_json_payload
# ---------------------------------------------------------------------------

class TestLoadJsonPayload:
    """_load_json_payload() parses and validates JSON content."""

    def test_valid_json_object(self):
        payload = _load_json_payload('{"name": "test", "type": "domain"}')
        assert payload == {"name": "test", "type": "domain"}

    def test_invalid_json_raises(self):
        with pytest.raises(json.JSONDecodeError):
            _load_json_payload("not-json")

    def test_non_dict_json_raises_value_error(self):
        with pytest.raises(ValueError, match="must be a JSON object"):
            _load_json_payload('["list"]')


# ---------------------------------------------------------------------------
# _find_rendered_template_file
# ---------------------------------------------------------------------------

class TestFindRenderedTemplateFile:
    """_find_rendered_template_file() locates generated JSON/YAML in output."""

    def test_picks_template_json_over_others(self, tmp_path):
        (tmp_path / "template.json").write_text("{}")
        (tmp_path / "other.yaml").write_text("a: 1")
        result = _find_rendered_template_file(tmp_path)
        assert result.name == "template.json"

    def test_picks_template_yaml_when_no_json(self, tmp_path):
        (tmp_path / "other.json").write_text("{}")
        (tmp_path / "template.yaml").write_text("a: 1")
        result = _find_rendered_template_file(tmp_path)
        assert result.name == "template.yaml"

    def test_falls_back_to_first_candidate(self, tmp_path):
        (tmp_path / "enricher.json").write_text("{}")
        result = _find_rendered_template_file(tmp_path)
        assert result.name == "enricher.json"

    def test_ignores_cookiecutter_json(self, tmp_path):
        (tmp_path / "cookiecutter.json").write_text("{}")
        (tmp_path / "my_template.json").write_text('{"name": "test"}')
        result = _find_rendered_template_file(tmp_path)
        assert result.name == "my_template.json"

    def test_raises_when_no_content_files(self, tmp_path):
        (tmp_path / "README.md").write_text("# hi")
        (tmp_path / "main.py").write_text("pass")
        with pytest.raises(FileNotFoundError, match="no JSON/YAML file"):
            _find_rendered_template_file(tmp_path)


# ---------------------------------------------------------------------------
# _build_template_create_body
# ---------------------------------------------------------------------------

class TestBuildTemplateCreateBody:
    """_build_template_create_body() assembles the POST body correctly."""

    def test_injects_name_category_version_into_content(self):
        content = {"input": {"type": "string"}, "output": {"type": "json"}}
        body = _build_template_create_body(
            name="my-enricher",
            category="Domain",
            description="My enricher",
            version=2.0,
            is_public=True,
            payload_content=content,
        )
        assert body["name"] == "my-enricher"
        assert body["category"] == "Domain"
        assert body["version"] == 2.0
        assert body["is_public"] is True
        assert body["description"] == "My enricher"
        assert body["content"]["input"] == {"type": "string"}

    def test_omits_description_when_empty(self):
        content = {"input": {"type": "string"}}
        body = _build_template_create_body(
            name="t",
            category="C",
            description="",
            version=1.0,
            is_public=False,
            payload_content=content,
        )
        assert "description" not in body.get("content", {})
        assert "description" not in body

    def test_does_not_mutate_original_content_dict(self):
        original = {"input": {"type": "string"}}
        _build_template_create_body(
            name="t",
            category="C",
            description="test",
            version=1.0,
            is_public=False,
            payload_content=original,
        )
        assert original == {"input": {"type": "string"}}


# ---------------------------------------------------------------------------
# _render_template
# ---------------------------------------------------------------------------

class TestRenderTemplate:
    """_render_template() error paths (cookiecutter not installed locally)."""

    def test_raises_file_not_found(self):
        with pytest.raises(FileNotFoundError, match="does not exist"):
            _render_template("/nonexistent/path", "{}")

    def test_raises_on_file_path(self, tmp_path):
        f = tmp_path / "template.json"
        f.write_text("{}")
        with pytest.raises(ValueError, match="must be a cookiecutter template directory"):
            _render_template(str(f), "{}")


# ---------------------------------------------------------------------------
# create_enricher_template with template_path
# ---------------------------------------------------------------------------

_TOKEN = "test-api-token-123"


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


class TestCreateEnricherTemplateTemplatePath:
    """create_enricher_template() with template_path (cookiecutter mode)."""

    def test_rejects_both_content_and_template_path(self):
        result = create_enricher_template(
            name="test",
            category="Domain",
            content='{"key": "val"}',
            template_path="/some/path",
        )
        assert "not both" in result

    def test_rejects_missing_content_and_no_template_path(self):
        result = create_enricher_template(
            name="test",
            category="Domain",
            content="",
        )
        assert "content is required" in result

    def test_returns_error_on_nonexistent_template_path(self):
        result = create_enricher_template(
            name="test",
            category="Domain",
            content="",
            template_path="/does/not/exist",
        )
        assert "does not exist" in result

    def test_returns_error_when_template_path_is_file(self, tmp_path):
        f = tmp_path / "template.json"
        f.write_text("{}")
        result = create_enricher_template(
            name="test",
            category="Domain",
            content="",
            template_path=str(f),
        )
        assert "must be a cookiecutter template directory" in result

    def test_sends_bearer_token_alongside_template_path(self, tmp_path):
        cookie_dir = tmp_path / "cookie_template"
        cookie_dir.mkdir()
        (cookie_dir / "cookiecutter.json").write_text('{"enricher_name": "MyEnricher"}')
        project_dir = cookie_dir / "{{cookiecutter.enricher_name}}"
        project_dir.mkdir()
        (project_dir / "template.json").write_text('{"input": {"type": "string"}}')

        mock_resp = _mock_http_response()
        with patch("flowsint_mcp_server.server.httpx.request", return_value=mock_resp) as req:
            result = create_enricher_template(
                name="test",
                category="Domain",
                content="",
                template_path=str(cookie_dir),
                api_token=_TOKEN,
            )
        req.assert_called_once()
        assert req.call_args[0][0] == "POST"
        assert req.call_args[0][1].endswith("/api/enrichers/templates")
        body = req.call_args.kwargs.get("json", {})
        assert body["content"]["input"]["type"] == "string"
        assert body["name"] == "test"

        headers = req.call_args.kwargs.get("headers", {})
        assert headers.get("Authorization") == f"Bearer {_TOKEN}"

    def test_content_only_legacy_mode_still_works(self):
        mock_resp = _mock_http_response()
        template_content = json.dumps({
            "input": {"type": "string"},
            "request": {"method": "GET", "url": "https://example.com"},
            "output": {"type": "json"},
            "response": {"expect": "json"},
        })
        with patch("flowsint_mcp_server.server.httpx.request", return_value=mock_resp) as req:
            create_enricher_template(
                name="legacy-test",
                category="Domain",
                content=template_content,
                description="Legacy",
                version=1.0,
                is_public=False,
                api_token=_TOKEN,
            )

        req.assert_called_once()
        assert req.call_args[0][0] == "POST"
        assert req.call_args[0][1].endswith("/api/enrichers/templates")

        body = req.call_args.kwargs.get("json", {})
        assert body["name"] == "legacy-test"
        assert body["category"] == "Domain"
        assert body["description"] == "Legacy"
        assert body["content"]["input"]["type"] == "string"
