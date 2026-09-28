"""Tests for TemplateGeneratorService."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from flowsint_core.core.services.exceptions import ValidationError
from flowsint_core.core.services.template_generator_service import (
    TemplateGeneratorService,
    _extract_yaml,
)


VALID_YAML = """\
name: ip-directory-lookup
category: Ip
version: 1.0
input:
  type: Ip
  key: address
connector:
  destination_id: approved_ip_directory
  endpoint_id: lookup
  capability: enrich.read
output:
  type: Ip
"""

VALID_YAML_WITH_FENCES = f"""\
Here is the template:

```yaml
{VALID_YAML}```

Let me know if you need changes.
"""


LEGACY_ARBITRARY_URL_YAML = """\
name: unsafe-legacy-lookup
category: Ip
version: 1.0
input:
  type: Ip
  key: address
request:
  method: GET
  url: https://unapproved.example/{{address}}
response:
  expect: json
  map:
    address: query
output:
  type: Ip
"""

INVALID_YAML = """\
name: bad-template
this is: [not: valid: yaml:
  broken:
"""

MISSING_FIELDS_YAML = """\
name: incomplete
category: Ip
version: 1.0
"""


def _make_service():
    """Create a TemplateGeneratorService with mocked dependencies."""
    db = MagicMock()
    vault_service = MagicMock()
    vault_service.get_secret.return_value = "fake-api-key"
    return TemplateGeneratorService(db=db, vault_service=vault_service)


class TestExtractYaml:
    def test_plain_yaml(self):
        assert _extract_yaml("name: foo\nversion: 1.0") == "name: foo\nversion: 1.0"

    def test_strips_yaml_fences(self):
        text = "```yaml\nname: foo\n```"
        assert _extract_yaml(text) == "name: foo"

    def test_strips_yml_fences(self):
        text = "```yml\nname: foo\n```"
        assert _extract_yaml(text) == "name: foo"

    def test_strips_plain_fences(self):
        text = "```\nname: foo\n```"
        assert _extract_yaml(text) == "name: foo"

    def test_strips_fences_with_surrounding_text(self):
        text = "Here is the template:\n\n```yaml\nname: foo\n```\n\nDone."
        assert _extract_yaml(text) == "name: foo"


class TestTemplateGeneratorService:
    @pytest.mark.asyncio
    async def test_generate_valid_yaml(self):
        service = _make_service()
        mock_provider = MagicMock()
        mock_provider.complete = AsyncMock(return_value=VALID_YAML)

        with patch.object(service, "_get_llm_provider", return_value=mock_provider):
            result = await service.generate("lookup IP geolocation", uuid4())

        assert "name: ip-directory-lookup" in result
        assert "category: Ip" in result
        # Verify provider.complete was called with system + user messages
        mock_provider.complete.assert_called_once()
        messages = mock_provider.complete.call_args[0][0]
        assert len(messages) == 2
        assert messages[0].role.value == "system"
        assert messages[1].role.value == "user"
        assert messages[1].content == "lookup IP geolocation"

    @pytest.mark.asyncio
    async def test_generate_strips_markdown_fences(self):
        service = _make_service()
        mock_provider = MagicMock()
        mock_provider.complete = AsyncMock(return_value=VALID_YAML_WITH_FENCES)

        with patch.object(service, "_get_llm_provider", return_value=mock_provider):
            result = await service.generate("lookup IP geolocation", uuid4())

        assert "```" not in result
        assert "name: ip-directory-lookup" in result

    @pytest.mark.asyncio
    async def test_generate_invalid_yaml_raises(self):
        service = _make_service()
        mock_provider = MagicMock()
        mock_provider.complete = AsyncMock(return_value=INVALID_YAML)

        with patch.object(service, "_get_llm_provider", return_value=mock_provider):
            with pytest.raises(ValidationError, match="invalid YAML"):
                await service.generate("do something", uuid4())

    @pytest.mark.asyncio
    async def test_generate_missing_fields_raises(self):
        service = _make_service()
        mock_provider = MagicMock()
        mock_provider.complete = AsyncMock(return_value=MISSING_FIELDS_YAML)

        with patch.object(service, "_get_llm_provider", return_value=mock_provider):
            with pytest.raises(ValidationError, match="failed validation"):
                await service.generate("do something", uuid4())

    @pytest.mark.asyncio
    async def test_generate_rejects_legacy_arbitrary_url_template(self):
        service = _make_service()
        mock_provider = MagicMock()
        mock_provider.complete = AsyncMock(return_value=LEGACY_ARBITRARY_URL_YAML)

        with patch.object(service, "_get_llm_provider", return_value=mock_provider):
            with pytest.raises(ValidationError, match="failed validation"):
                await service.generate("use an arbitrary URL", uuid4())

    @pytest.mark.asyncio
    async def test_generate_non_dict_yaml_raises(self):
        service = _make_service()
        mock_provider = MagicMock()
        mock_provider.complete = AsyncMock(return_value="- item1\n- item2\n")

        with patch.object(service, "_get_llm_provider", return_value=mock_provider):
            with pytest.raises(ValidationError, match="non-object"):
                await service.generate("do something", uuid4())

    @pytest.mark.asyncio
    async def test_system_prompt_contains_schema(self):
        service = _make_service()
        mock_provider = MagicMock()
        mock_provider.complete = AsyncMock(return_value=VALID_YAML)

        with patch.object(service, "_get_llm_provider", return_value=mock_provider):
            await service.generate("test prompt", uuid4())

        messages = mock_provider.complete.call_args[0][0]
        system_prompt = messages[0].content
        # Verify schema elements are present
        assert "name" in system_prompt
        assert "category" in system_prompt
        assert "input" in system_prompt
        assert "connector" in system_prompt
        assert "destination_id" in system_prompt
        assert "endpoint_id" in system_prompt
        assert "enrich.read" in system_prompt
        assert "output" in system_prompt
        assert "projection" in system_prompt
        # Verify examples are present
        assert "ip-directory-lookup" in system_prompt
        assert "projected-ip-lookup" in system_prompt
        assert "Never emit URLs" in system_prompt
