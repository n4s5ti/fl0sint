"""
WhatsMyName username-enumeration enricher.

Uses the WebBreacher/WhatsMyName project's curated list of social media and web
platforms to check whether a given username exists on each platform.  The site
database is fetched live from the project's GitHub-hosted JSON and queried via
HTTP(S) HEAD/GET requests, checking for platform-specific detection strings.

Source:  https://github.com/WebBreacher/WhatsMyName
JSON:    https://raw.githubusercontent.com/WebBreacher/WhatsMyName/main/web_accounts_list.json
License: https://github.com/WebBreacher/WhatsMyName?tab=CC0-1.0-1-ov-file (CC0 1.0)

Input:  Username
Output: SocialAccount (one per platform where the username exists)
"""

import logging
from typing import Any, Dict, List

import httpx

from flowsint_core.core.enricher_base import Enricher
from flowsint_core.core.logger import Logger
from flowsint_types.social_account import SocialAccount
from flowsint_types.username import Username

from flowsint_enrichers.registry import flowsint_enricher

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
WHATS_MY_NAME_URL = (
    "https://raw.githubusercontent.com/WebBreacher/WhatsMyName/"
    "main/web_accounts_list.json"
)
DEFAULT_TIMEOUT = 15.0  # seconds per request
FETCH_TIMEOUT = 30.0  # seconds for the site-list fetch


@flowsint_enricher
class WhatsMyNameEnricher(Enricher):
    """[WHATSMYNAME] Scans usernames across hundreds of platforms using
    the WebBreacher/WhatsMyName site list.

    Each platform is checked by making an HTTP(S) request to the platform's
    profile URL template and looking for a platform-specific detection string
    in the response body.
    """

    InputType = Username
    OutputType = SocialAccount

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    @classmethod
    def name(cls) -> str:
        return "username_to_whatsmyname"

    @classmethod
    def category(cls) -> str:
        return "social"

    @classmethod
    def key(cls) -> str:
        return "value"

    @classmethod
    def icon(cls) -> str:
        return "\u2713"

    # ------------------------------------------------------------------
    # Parameters
    # ------------------------------------------------------------------

    @classmethod
    def get_params_schema(cls) -> List[Dict[str, Any]]:
        return []

    # ------------------------------------------------------------------
    # Scan
    # ------------------------------------------------------------------

    async def scan(self, data: List[Username]) -> List[SocialAccount]:
        """Check each username against all platforms in the WhatsMyName site list.

        Fetches the site database once, then for each username sends HTTP requests
        to every platform and returns a ``SocialAccount`` for each positive match.
        """
        results: List[SocialAccount] = []

        # Fetch the site list once
        sites = await self._fetch_site_list()
        if not sites:
            Logger.error(
                self.sketch_id,
                {"message": "WhatsMyName: could not fetch site list, aborting scan."},
            )
            return results

        for username in data:
            for site in sites:
                try:
                    account = await self._check_site(username, site)
                    if account is not None:
                        results.append(account)
                except Exception as exc:
                    Logger.debug(
                        self.sketch_id,
                        {
                            "message": (
                                f"WhatsMyName: error checking {site.get('name', '?')} "
                                f"for {username.value}: {exc}"
                            )
                        },
                    )
                    continue

        return results

    async def _fetch_site_list(self) -> List[Dict[str, Any]]:
        """Download and parse the WebBreacher/WhatsMyName site database."""
        try:
            async with httpx.AsyncClient(timeout=FETCH_TIMEOUT) as client:
                resp = await client.get(WHATS_MY_NAME_URL)
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            Logger.error(
                self.sketch_id,
                {
                    "message": (
                        f"WhatsMyName: failed to fetch site list from "
                        f"{WHATS_MY_NAME_URL}: {exc}"
                    )
                },
            )
            return []

        # The JSON has a top-level "sites" key containing the array
        sites = data.get("sites", [])
        if not isinstance(sites, list):
            Logger.error(
                self.sketch_id,
                {"message": "WhatsMyName: site list JSON has unexpected structure."},
            )
            return []

        # Filter out entries that lack a check URI or detection string
        valid: List[Dict[str, Any]] = []
        for site in sites:
            uri_check = site.get("uri_check", "")
            m_string = site.get("m_string", "")
            if uri_check and m_string:
                valid.append(site)

        Logger.info(
            self.sketch_id,
            {
                "message": (
                    f"WhatsMyName: loaded {len(valid)}/{len(sites)} "
                    f"valid site checks."
                )
            },
        )
        return valid

    async def _check_site(
        self, username: Username, site: Dict[str, Any]
    ) -> SocialAccount | None:
        """Check a single platform for the given username.

        Returns a ``SocialAccount`` if the detection string is found in the
        response, or ``None`` when the profile does not exist (or the check
        could not be completed).
        """
        uri_check = site["uri_check"]
        m_method = site.get("m_method", "GET").upper()
        m_string = site["m_string"]

        url = uri_check.replace("{username}", username.value)

        try:
            async with httpx.AsyncClient(
                timeout=DEFAULT_TIMEOUT,
                follow_redirects=True,
            ) as client:
                if m_method == "HEAD":
                    resp = await client.head(url)
                else:
                    resp = await client.get(url)
        except httpx.TimeoutException:
            Logger.debug(
                self.sketch_id,
                {
                    "message": (
                        f"WhatsMyName: timeout checking {site['name']} "
                        f"for {username.value}"
                    )
                },
            )
            return None
        except httpx.HTTPStatusError as exc:
            # 4xx generally means "not found" — not an error
            if exc.response.status_code < 500:
                return None
            Logger.debug(
                self.sketch_id,
                {
                    "message": (
                        f"WhatsMyName: server error {exc.response.status_code} "
                        f"from {site['name']} for {username.value}"
                    )
                },
            )
            return None
        except httpx.RequestError as exc:
            Logger.debug(
                self.sketch_id,
                {
                    "message": (
                        f"WhatsMyName: request failed for {site['name']} "
                        f"on {username.value}: {exc}"
                    )
                },
            )
            return None

        # Check if the detection string appears in the response body
        # Some platforms detect presence via status code only (no m_string)
        e_code = site.get("e_code", 200)

        if m_string:
            body = resp.text
            if m_string in body:
                return SocialAccount(
                    username=username,
                    platform=site.get("name", site.get("name", "")),
                    profile_url=url,
                )
        else:
            # Fallback: match by expected status code
            if resp.status_code == e_code:
                return SocialAccount(
                    username=username,
                    platform=site.get("name", site.get("name", "")),
                    profile_url=url,
                )

        return None

    # ------------------------------------------------------------------
    # Postprocess (graph)
    # ------------------------------------------------------------------

    def postprocess(
        self, results: List[SocialAccount], original_input: List[Username]
    ) -> List[SocialAccount]:
        """Create Neo4j nodes and relationships for found accounts."""
        if not self._graph_service:
            return results

        for account in results:
            try:
                self.create_node(account.username)
                self.create_node(account)
                self.create_relationship(
                    account.username, account, "HAS_SOCIAL_ACCOUNT"
                )
                self.log_graph_message(
                    f"{account.username.value} -> account found on {account.platform}"
                )
            except Exception as exc:
                Logger.error(
                    self.sketch_id,
                    {
                        "message": (
                            f"Failed to create graph nodes for "
                            f"{account.username.value} on {account.platform}: {exc}"
                        )
                    },
                )
                continue

        return results


# Make types available at module level for easy access
InputType = WhatsMyNameEnricher.InputType
OutputType = WhatsMyNameEnricher.OutputType
