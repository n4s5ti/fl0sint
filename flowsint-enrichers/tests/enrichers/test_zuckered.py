"""Tests for HaveIBeenZuckered enrichers (email + phone).

Uses direct method patching to avoid real HTTP calls.
"""

from unittest.mock import MagicMock, patch

import pytest
from flowsint_enrichers import ENRICHER_REGISTRY, load_all_enrichers
from flowsint_types.email import Email
from flowsint_types.phone import Phone


@pytest.fixture(autouse=True)
def reset_registry():
    load_all_enrichers()
    yield


# ===================================================================
# Registry
# ===================================================================

class TestEmailRegistry:
    def test_is_registered(self):
        assert ENRICHER_REGISTRY.enricher_exists("email_to_zuckered")

    def test_metadata(self):
        e = ENRICHER_REGISTRY._enrichers["email_to_zuckered"]
        assert e.name() == "email_to_zuckered"
        assert e.category() == "Email"
        assert e.key() == "email"
        assert e.InputType is Email

class TestPhoneRegistry:
    def test_is_registered(self):
        assert ENRICHER_REGISTRY.enricher_exists("phone_to_zuckered")

    def test_metadata(self):
        e = ENRICHER_REGISTRY._enrichers["phone_to_zuckered"]
        assert e.name() == "phone_to_zuckered"
        assert e.category() == "phones"
        assert e.key() == "number"
        assert e.InputType is Phone


# ===================================================================
# Scan — patch _check_email / _check_phone directly
# ===================================================================

class TestEmailScan:
    @pytest.mark.asyncio
    async def test_api_unreachable(self):
        import flowsint_enrichers.email.to_zuckered as mod

        with patch.object(mod.EmailToZuckeredEnricher, "_check_email",
                          return_value={"email": "test@example.com", "found": False, "error": "API unreachable"}):
            e = ENRICHER_REGISTRY._enrichers["email_to_zuckered"]
            enricher = e(sketch_id="test", scan_id="test")
            results = await enricher.scan([Email(email="test@example.com")])

        assert len(results) == 1
        assert results[0]["found"] is False

    @pytest.mark.asyncio
    async def test_no_match(self):
        import flowsint_enrichers.email.to_zuckered as mod

        with patch.object(mod.EmailToZuckeredEnricher, "_check_email",
                          return_value={"email": "safe@example.com", "found": False}):
            e = ENRICHER_REGISTRY._enrichers["email_to_zuckered"]
            enricher = e(sketch_id="test", scan_id="test")
            results = await enricher.scan([Email(email="safe@example.com")])

        assert results[0]["found"] is False

    @pytest.mark.asyncio
    async def test_finds_breach(self):
        import flowsint_enrichers.email.to_zuckered as mod

        with patch.object(mod.EmailToZuckeredEnricher, "_check_email",
                          return_value={"email": "pwned@example.com", "found": True, "data": {"found": True}}):
            e = ENRICHER_REGISTRY._enrichers["email_to_zuckered"]
            enricher = e(sketch_id="test", scan_id="test")
            results = await enricher.scan([Email(email="pwned@example.com")])

        assert results[0]["found"] is True

    @pytest.mark.asyncio
    async def test_multiple(self):
        import flowsint_enrichers.email.to_zuckered as mod

        with patch.object(mod.EmailToZuckeredEnricher, "_check_email") as mock:
            mock.side_effect = [
                {"email": "a@b.com", "found": False},
                {"email": "c@d.com", "found": True},
            ]
            e = ENRICHER_REGISTRY._enrichers["email_to_zuckered"]
            enricher = e(sketch_id="test", scan_id="test")
            results = await enricher.scan([Email(email="a@b.com"), Email(email="c@d.com")])

        assert results[0]["found"] is False
        assert results[1]["found"] is True


class TestPhoneScan:
    @pytest.mark.asyncio
    async def test_api_unreachable(self):
        import flowsint_enrichers.phone.to_zuckered as mod

        with patch.object(mod.PhoneToZuckeredEnricher, "_check_phone",
                          return_value={"phone": "+14155550100", "found": False, "error": "API unreachable"}):
            e = ENRICHER_REGISTRY._enrichers["phone_to_zuckered"]
            enricher = e(sketch_id="test", scan_id="test")
            results = await enricher.scan([Phone(number="+14155550100")])

        assert results[0]["found"] is False

    @pytest.mark.asyncio
    async def test_finds_breach(self):
        import flowsint_enrichers.phone.to_zuckered as mod

        with patch.object(mod.PhoneToZuckeredEnricher, "_check_phone",
                          return_value={"phone": "+14155550100", "found": True}):
            e = ENRICHER_REGISTRY._enrichers["phone_to_zuckered"]
            enricher = e(sketch_id="test", scan_id="test")
            results = await enricher.scan([Phone(number="+14155550100")])

        assert results[0]["found"] is True


# ===================================================================
# Postprocess
# ===================================================================

class TestPostprocess:
    def test_email_creates_nodes(self):
        e = ENRICHER_REGISTRY._enrichers["email_to_zuckered"]
        g = MagicMock()
        enricher = e(sketch_id="test", scan_id="test", graph_service=g)
        out = enricher.postprocess(
            [{"email": "a@b.com", "found": True}],
            [Email(email="a@b.com")],
        )
        assert len(out) == 1

    def test_phone_creates_nodes(self):
        e = ENRICHER_REGISTRY._enrichers["phone_to_zuckered"]
        g = MagicMock()
        enricher = e(sketch_id="test", scan_id="test", graph_service=g)
        out = enricher.postprocess(
            [{"phone": "+14155550100", "found": True}],
            [Phone(number="+14155550100")],
        )
        assert len(out) == 1


# ===================================================================
# Imports
# ===================================================================

def test_email_imports():
    import flowsint_enrichers.email.to_zuckered as mod
    assert mod.InputType is Email

def test_phone_imports():
    import flowsint_enrichers.phone.to_zuckered as mod
    assert mod.InputType is Phone
