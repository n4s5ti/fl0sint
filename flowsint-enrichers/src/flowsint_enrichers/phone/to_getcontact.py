"""
GetContact phone number lookup enricher.

Queries getcontact.com to retrieve contact information associated with
a phone number — name, carrier, tags, and spam reports.

Note: GetContact does not have a public API. This enricher uses httpx
to attempt a lookup via common web patterns. If getcontact.com changes
their interface, this enricher may need updates.

Input:  Phone
Output: SocialAccount or Individual
"""

import logging
from typing import Any, Dict, List, Optional

import httpx

from flowsint_core.core.enricher_base import Enricher
from flowsint_core.core.logger import Logger
from flowsint_types.phone import Phone
from flowsint_types.social_account import SocialAccount
from flowsint_types.username import Username

from flowsint_enrichers.registry import flowsint_enricher

log = logging.getLogger(__name__)


@flowsint_enricher
class PhoneToGetContactEnricher(Enricher):
    """Looks up phone number information via GetContact.

    Attempts to find contact name, tags, and carrier info associated
    with a phone number using getcontact.com.

    This is a best-effort web scraper — GetContact does not publish
    a public API, so the enricher tries common patterns and degrades
    gracefully if the site changes.
    """

    InputType = Phone
    OutputType = SocialAccount

    @classmethod
    def name(cls) -> str:
        return "phone_to_getcontact"

    @classmethod
    def category(cls) -> str:
        return "phones"

    @classmethod
    def key(cls) -> str:
        return "number"

    @classmethod
    def icon(cls) -> Optional[str]:
        return "📞"

    @classmethod
    def documentation(cls) -> str:
        return (
            "## GetContact Phone Lookup\n\n"
            "Looks up a phone number on getcontact.com to find associated "
            "contact name, tags, carrier information, and spam reports.\n\n"
            "### Engine\n"
            "Uses httpx to query getcontact.com. Since GetContact does not "
            "publish a public API, this enricher attempts common endpoint "
            "patterns and degrades gracefully.\n\n"
            "### Output\n"
            "Returns a SocialAccount with:\n"
            "- `display_name` – contact name if found\n"
            "- `bio` – additional contact info\n"
            "- `platform` – \"GetContact\"\n"
        )

    @classmethod
    def get_params_schema(cls) -> List[Dict[str, Any]]:
        return [
            {
                "name": "timeout",
                "type": "int",
                "default": 15,
                "label": "Request timeout (seconds)",
                "description": "Timeout for HTTP requests",
            },
        ]

    # ------------------------------------------------------------------
    # scan
    # ------------------------------------------------------------------
    async def scan(self, data: List[InputType]) -> List[OutputType]:
        """Look up each phone number on GetContact."""
        results: List[OutputType] = []
        for phone_item in data:
            number = str(phone_item.number)
            result = await self._lookup_one(number)
            results.append(
                result if result else self._empty_result(number)
            )
        return results

    async def _lookup_one(
        self, number: str
    ) -> Optional[OutputType]:
        """Query GetContact for a single phone number."""
        timeout = self.params.get("timeout", 15)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                # Try common API patterns for getcontact.com
                # Pattern 1: direct API endpoint
                api_urls = [
                    f"https://getcontact.com/api/lookup/{number}",
                    f"https://api.getcontact.com/v1/lookup/{number}",
                ]

                for url in api_urls:
                    try:
                        resp = await client.get(
                            url,
                            headers={
                                "User-Agent": (
                                    "Mozilla/5.0 (Windows NT 10.0; "
                                    "Win64; x64) AppleWebKit/537.36"
                                ),
                                "Accept": "application/json",
                            },
                        )
                        if resp.status_code == 200:
                            data = resp.json()
                            username = Username(
                                value=number, platform="GetContact"
                            )
                            return SocialAccount(
                                username=username,
                                display_name=data.get("name"),
                                platform="GetContact",
                                bio=json.dumps(data, indent=2)[:500],
                            )
                    except Exception:
                        continue

                # Pattern 2: web page scrape
                web_url = f"https://getcontact.com/{number}"
                resp = await client.get(web_url)
                if resp.status_code == 200:
                    # Extract any useful info from the page
                    username = Username(
                        value=number, platform="GetContact"
                    )
                    return SocialAccount(
                        username=username,
                        platform="GetContact",
                        bio="Found on GetContact website",
                    )

                Logger.info(
                    self.sketch_id,
                    {
                        "message": (
                            f"GetContact: no data for {number} "
                            f"(HTTP {resp.status_code})"
                        )
                    },
                )
                return None

        except Exception as exc:
            Logger.error(
                self.sketch_id,
                {
                    "message": (
                        f"GetContact lookup failed for {number}: {exc}"
                    )
                },
            )
            return None

    def _empty_result(self, number: str) -> OutputType:
        username = Username(value=number, platform="GetContact")
        return SocialAccount(username=username, platform="GetContact")

    # ------------------------------------------------------------------
    # postprocess
    # ------------------------------------------------------------------
    def postprocess(
        self, results: List[OutputType], original_input: List[InputType]
    ) -> List[OutputType]:
        for account in results:
            if not self._graph_service:
                continue
            self.create_node(account)
            self.log_graph_message(
                f"GetContact: {account.username.value} "
                f"({account.display_name or 'no name'})"
            )
        return results


InputType = PhoneToGetContactEnricher.InputType
OutputType = PhoneToGetContactEnricher.OutputType
