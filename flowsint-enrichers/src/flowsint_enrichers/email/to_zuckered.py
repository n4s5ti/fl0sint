"""HaveIBeenZuckered enricher for email addresses.

Checks whether an email address appears in the Facebook data breach
by querying haveibeenzuckered.com API endpoints.
"""

from typing import Any, Dict, List

import httpx

from flowsint_core.core.enricher_base import Enricher
from flowsint_core.core.logger import Logger
from flowsint_enrichers.registry import flowsint_enricher
from flowsint_types.email import Email


@flowsint_enricher
class EmailToZuckeredEnricher(Enricher):
    """[HaveIBeenZuckered] Checks whether an email was exposed in the Facebook data breach.

    Queries haveibeenzuckered.com — a Vue.js SPA that checks if contact
    information (phone/email) was part of the Facebook data breach.
    The enricher tries multiple common API endpoint patterns with graceful
    fallback when the API is unavailable.
    """

    InputType = Email
    OutputType = Dict[str, Any]

    @classmethod
    def name(cls) -> str:
        return "email_to_zuckered"

    @classmethod
    def category(cls) -> str:
        return "Email"

    @classmethod
    def key(cls) -> str:
        return "email"

    @classmethod
    def icon(cls) -> str:
        return "Z"

    async def scan(self, data: List[InputType]) -> List[OutputType]:
        """Query HaveIBeenZuckered for each email address.

        Args:
            data: List of Email objects to check.

        Returns:
            List of dicts with breach check results, one per input email.
            Unenriched entries (API unreachable or errors) return an empty dict.
        """
        results: List[OutputType] = []

        for email_obj in data:
            email_value = email_obj.email
            result = await self._check_email(email_value)
            results.append(result)

        return results

    async def _check_email(self, email: str) -> Dict[str, Any]:
        """Try to check a single email against HaveIBeenZuckered.

        Tries several common SPA backend API patterns in order.

        Args:
            email: The email address to check.

        Returns:
            A dict with at minimum {"email": email, "found": False}
            if the API is unreachable or no match found.
        """
        base_url = "https://haveibeenzuckered.com"
        patterns = [
            ("POST", f"{base_url}/api/check", {"json": {"email": email}}),
            ("POST", f"{base_url}/api/check", {"data": {"email": email}}),
            ("GET", f"{base_url}/api/check", {"params": {"q": email}}),
            ("GET", f"{base_url}/api/check", {"params": {"email": email}}),
            ("POST", f"{base_url}/api/v1/check", {"json": {"email": email}}),
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
                            "email": email,
                            "found": True,
                            "source": "haveibeenzuckered.com",
                            "data": data,
                        }

                    if response.status_code in (404, 410):
                        return {
                            "email": email,
                            "found": False,
                            "source": "haveibeenzuckered.com",
                        }

                except Exception as exc:
                    Logger.debug(
                        self.sketch_id,
                        {
                            "message": (
                                f"(EmailToZuckered) Pattern {method} {url} "
                                f"failed for {email}: {exc}"
                            ),
                        },
                    )
                    continue

        Logger.error(
            self.sketch_id,
            {
                "message": (
                    f"(EmailToZuckered) All API patterns exhausted for {email}. "
                    "The haveibeenzuckered.com API may have changed."
                ),
            },
        )
        return {"email": email, "found": False, "error": "API unreachable"}

    def postprocess(
        self, results: List[OutputType], original_input: List[InputType]
    ) -> List[OutputType]:
        """Create graph nodes and relationships when a breach is found."""
        if not self._graph_service:
            return results

        for email_obj, result in zip(original_input, results):
            if result.get("found"):
                try:
                    self.create_node(email_obj)
                    self.log_graph_message(
                        f"(EmailToZuckered) {email_obj.email} found in Facebook data breach"
                    )
                except Exception as e:
                    Logger.error(
                        self.sketch_id,
                        {
                            "message": (
                                f"(EmailToZuckered) Failed to create graph nodes "
                                f"for {email_obj.email}: {e}"
                            ),
                        },
                    )

        return results


# Make types available at module level for easy access
InputType = EmailToZuckeredEnricher.InputType
OutputType = EmailToZuckeredEnricher.OutputType
