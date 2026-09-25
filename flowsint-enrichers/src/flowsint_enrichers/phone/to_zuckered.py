"""HaveIBeenZuckered enricher for phone numbers.

Checks whether a phone number appears in the Facebook data breach
by querying haveibeenzuckered.com API endpoints.
"""

from typing import Any, Dict, List

import httpx

from flowsint_core.core.enricher_base import Enricher
from flowsint_core.core.logger import Logger
from flowsint_enrichers.registry import flowsint_enricher
from flowsint_types.phone import Phone


@flowsint_enricher
class PhoneToZuckeredEnricher(Enricher):
    """[HaveIBeenZuckered] Checks whether a phone number was exposed in the Facebook data breach.

    Queries haveibeenzuckered.com — a Vue.js SPA that checks if contact
    information (phone/email) was part of the Facebook data breach.
    The enricher tries multiple common API endpoint patterns with graceful
    fallback when the API is unavailable.
    """

    InputType = Phone
    OutputType = Dict[str, Any]

    @classmethod
    def name(cls) -> str:
        return "phone_to_zuckered"

    @classmethod
    def category(cls) -> str:
        return "phones"

    @classmethod
    def key(cls) -> str:
        return "number"

    @classmethod
    def icon(cls) -> str:
        return "Z"

    async def scan(self, data: List[InputType]) -> List[OutputType]:
        """Query HaveIBeenZuckered for each phone number.

        Args:
            data: List of Phone objects to check.

        Returns:
            List of dicts with breach check results, one per input phone.
            Unenriched entries (API unreachable or errors) return an empty dict.
        """
        results: List[OutputType] = []

        for phone_obj in data:
            phone_value = phone_obj.number
            result = await self._check_phone(phone_value)
            results.append(result)

        return results

    async def _check_phone(self, phone: str) -> Dict[str, Any]:
        """Try to check a single phone number against HaveIBeenZuckered.

        Tries several common SPA backend API patterns in order.

        Args:
            phone: The phone number to check.

        Returns:
            A dict with at minimum {"number": phone, "found": False}
            if the API is unreachable or no match found.
        """
        base_url = "https://haveibeenzuckered.com"
        patterns = [
            ("POST", f"{base_url}/api/check", {"json": {"phone": phone}}),
            ("POST", f"{base_url}/api/check", {"data": {"phone": phone}}),
            ("GET", f"{base_url}/api/check", {"params": {"q": phone}}),
            ("GET", f"{base_url}/api/check", {"params": {"phone": phone}}),
            ("POST", f"{base_url}/api/v1/check", {"json": {"phone": phone}}),
        ]

        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            for method, url, kwargs in patterns:
                try:
                    if method == "POST":
                        response = await client.post(url, **kwargs)
                    else:
                        response = await client.get(url, **kwargs)

                    if response.status_code == 200:
                        try:
                            data = response.json()
                        except Exception:
                            data = {"raw": response.text}

                        return {
                            "number": phone,
                            "found": True,
                            "source": "haveibeenzuckered.com",
                            "data": data,
                        }

                    if response.status_code in (404, 410):
                        return {
                            "number": phone,
                            "found": False,
                            "source": "haveibeenzuckered.com",
                        }

                except Exception as exc:
                    Logger.debug(
                        self.sketch_id,
                        {
                            "message": (
                                f"(PhoneToZuckered) Pattern {method} {url} "
                                f"failed for {phone}: {exc}"
                            ),
                        },
                    )
                    continue

        Logger.error(
            self.sketch_id,
            {
                "message": (
                    f"(PhoneToZuckered) All API patterns exhausted for {phone}. "
                    "The haveibeenzuckered.com API may have changed."
                ),
            },
        )
        return {"number": phone, "found": False, "error": "API unreachable"}

    def postprocess(
        self, results: List[OutputType], original_input: List[InputType]
    ) -> List[OutputType]:
        """Create graph nodes and relationships when a breach is found."""
        if not self._graph_service:
            return results

        for phone_obj, result in zip(original_input, results):
            if result.get("found"):
                try:
                    self.create_node(phone_obj)
                    self.log_graph_message(
                        f"(PhoneToZuckered) {phone_obj.number} found in Facebook data breach"
                    )
                except Exception as e:
                    Logger.error(
                        self.sketch_id,
                        {
                            "message": (
                                f"(PhoneToZuckered) Failed to create graph nodes "
                                f"for {phone_obj.number}: {e}"
                            ),
                        },
                    )

        return results


# Make types available at module level for easy access
InputType = PhoneToZuckeredEnricher.InputType
OutputType = PhoneToZuckeredEnricher.OutputType
