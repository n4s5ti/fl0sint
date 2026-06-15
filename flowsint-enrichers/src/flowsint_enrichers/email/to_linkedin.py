"""
Email to LinkedIn profile enricher.

Searches for a LinkedIn profile associated with an email address.

Search methods (configurable):
  1. serper (default, recommended) — Serper.dev Google API, 2500 free/month
  2. httpx    — Direct HTTP search (most engines block automated queries)
  3. fallback — Graceful empty result when no method succeeds

Setup for serper:
  Set SERPER_API_KEY env var or add to enricher params:
    {"serper_api_key": "your-key-here"}
  Get a free key at https://serper.dev
"""

import logging
import os
import re
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus

from flowsint_core.core.enricher_base import Enricher
from flowsint_core.core.logger import Logger
from flowsint_enrichers.registry import flowsint_enricher
from flowsint_types.email import Email
from flowsint_types.social_account import SocialAccount
from flowsint_types.username import Username

log = logging.getLogger(__name__)


@flowsint_enricher
class EmailToLinkedinEnricher(Enricher):
    """Searches for a LinkedIn profile associated with an email address."""

    InputType = Email
    OutputType = SocialAccount

    _http_client: Optional["httpx.AsyncClient"] = None

    @classmethod
    def name(cls) -> str:
        return "email_to_linkedin"

    @classmethod
    def category(cls) -> str:
        return "Email"

    @classmethod
    def key(cls) -> str:
        return "email"

    @classmethod
    def get_params_schema(cls) -> List[Dict[str, Any]]:
        return [
            {
                "name": "search_method",
                "type": "str",
                "default": "serper",
                "label": "Search method",
                "description": "How to search for LinkedIn profiles: serper (API, recommended) or httpx (direct HTTP)",
                "options": ["serper", "httpx"],
            },
            {
                "name": "serper_api_key",
                "type": "str",
                "default": "",
                "label": "Serper.dev API key",
                "description": "API key for serper.dev (free at https://serper.dev). Falls back to SERPER_API_KEY env var.",
            },
            {
                "name": "timeout",
                "type": "int",
                "default": 10,
                "label": "Search timeout (seconds)",
            },
        ]

    # ------------------------------------------------------------------
    # scan
    # ------------------------------------------------------------------
    async def scan(self, data: List[InputType]) -> List[OutputType]:
        """Search for LinkedIn profile for each email."""
        method = self.params.get("search_method", "serper")
        timeout = self.params.get("timeout", 10)
        serper_key = (
            self.params.get("serper_api_key")
            or os.environ.get("SERPER_API_KEY")
            or ""
        )

        results: List[OutputType] = []

        for email in data:
            try:
                profile_url = await self._search_linkedin(
                    email.email,
                    method=method,
                    timeout=timeout,
                    serper_key=serper_key,
                )
                if profile_url is None:
                    continue

                username_str = self._extract_username(profile_url)

                social = SocialAccount(
                    username=Username(name=username_str or "unknown"),
                    platform="linkedin",
                    profile_url=profile_url,
                    display_name=None,
                    associated_emails=[email.email],
                )
                results.append(social)

                Logger.info(
                    self.sketch_id,
                    {
                        "message": (
                            f"Found LinkedIn profile for {email.email}: "
                            f"{profile_url}"
                        )
                    },
                )

            except Exception as e:
                Logger.warning(
                    self.sketch_id,
                    {
                        "message": (
                            f"Failed to search LinkedIn for "
                            f"{email.email}: {e}"
                        )
                    },
                )
                continue

        return results

    # ------------------------------------------------------------------
    # Search dispatch
    # ------------------------------------------------------------------
    async def _search_linkedin(
        self,
        email: str,
        method: str = "serper",
        timeout: int = 10,
        serper_key: str = "",
    ) -> Optional[str]:
        """Dispatch to the selected search method."""
        if method == "serper" and serper_key:
            return await self._search_via_serper(email, serper_key, timeout)
        # httpx fallback (or explicit)
        return await self._search_via_httpx(email, timeout)

    # ------------------------------------------------------------------
    # Serper.dev API (recommended)
    # ------------------------------------------------------------------
    async def _search_via_serper(
        self, email: str, api_key: str, timeout: int = 10
    ) -> Optional[str]:
        """Search via Serper.dev Google API."""
        dork = f'site:linkedin.com/in/ "{email}"'
        try:
            import httpx

            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(
                    "https://google.serper.dev/search",
                    json={"q": dork},
                    headers={
                        "X-API-KEY": api_key,
                        "Content-Type": "application/json",
                    },
                )
                if resp.status_code != 200:
                    log.debug("Serper returned %d: %s", resp.status_code, resp.text[:200])
                    return None

                data = resp.json()
                # Check organic results
                organic = data.get("organic", [])
                if not organic:
                    return None

                for result in organic:
                    link = result.get("link", "")
                    if "linkedin.com/in/" in link:
                        return link.split("?")[0]  # strip tracking params
        except ImportError:
            log.debug("httpx not available for serper search")
        except Exception as exc:
            log.debug("Serper search failed: %s", exc)
        return None

    # ------------------------------------------------------------------
    # Direct HTTP search (httpx) — most engines block automated queries
    # ------------------------------------------------------------------
    async def _search_via_httpx(
        self, email: str, timeout: int = 10
    ) -> Optional[str]:
        """Direct HTTP search via Bing. Often blocked by captcha."""
        dork = f'site:linkedin.com/in/ "{email}"'
        url = (
            "https://www.bing.com/search?q="
            f"{quote_plus(dork)}&setlang=en-US"
        )

        html = await self._fetch(url, timeout=timeout)
        if not html:
            return None

        return self._parse_linkedin_url(html)

    # ------------------------------------------------------------------
    # HTTP fetch
    # ------------------------------------------------------------------
    async def _fetch(self, url: str, timeout: int = 10) -> Optional[str]:
        """Fetch URL: httpx async, fallback to requests."""
        user_agent = (
            "Mozilla/5.0 (X11; Linux x86_64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/134.0.0.0 Safari/537.36"
        )
        try:
            import httpx

            if self._http_client is None:
                self._http_client = httpx.AsyncClient(
                    http2=True,
                    timeout=timeout,
                    follow_redirects=True,
                    headers={"User-Agent": user_agent},
                )
            resp = await self._http_client.get(url)
            if resp.status_code == 200:
                return resp.text
        except Exception as exc:
            log.debug("httpx fetch failed: %s", exc)

        # Sync fallback
        try:
            import requests

            resp = requests.get(
                url,
                timeout=timeout,
                headers={"User-Agent": user_agent},
            )
            if resp.status_code == 200:
                return resp.text
        except Exception as exc:
            log.debug("requests fetch failed: %s", exc)

        return None

    # ------------------------------------------------------------------
    # Parse LinkedIn URL from HTML
    # ------------------------------------------------------------------
    @staticmethod
    def _parse_linkedin_url(html: str) -> Optional[str]:
        """Extract first linkedin.com/in/ URL from HTML."""
        pattern = r'https?://(?:[a-z]+\.)?linkedin\.com/in/[a-zA-Z0-9_-]+'
        matches = re.findall(pattern, html)
        if matches:
            return matches[0].split("?")[0]
        return None

    @staticmethod
    def _extract_username(profile_url: str) -> Optional[str]:
        """Extract username from LinkedIn URL."""
        match = re.search(r'/in/([a-zA-Z0-9_-]+)', profile_url)
        return match.group(1) if match else None

    # ------------------------------------------------------------------
    # postprocess
    # ------------------------------------------------------------------
    def postprocess(
        self, results: List[OutputType], original_input: List[InputType]
    ) -> List[OutputType]:
        """Postprocess LinkedIn results into graph."""
        for email_obj, social in zip(original_input, results):
            if self._graph_service:
                self.create_node(email_obj)
                self.create_node(social)
                self.create_relationship(email_obj, social, "HAS_SOCIAL_ACCOUNT")
                self.log_graph_message(
                    f"Found LinkedIn profile {social.profile_url} "
                    f"for email {email_obj.email}."
                )
        return results


InputType = EmailToLinkedinEnricher.InputType
OutputType = EmailToLinkedinEnricher.OutputType
