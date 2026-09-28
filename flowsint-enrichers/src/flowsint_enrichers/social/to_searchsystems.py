"""
SearchSystems public records directory enricher.

Uses Camoufox stealth headless browser to search the SearchSystems.net
public records directory — the internet's largest directory of free
public record databases (70,000+ databases organized by type and location).

This is a directory CATALOG, not a search engine. The enricher navigates
the directory to find relevant database links for a given search query
(name, location, record type).

Input:  Phrase (search query — e.g., "criminal records Texas" or a name)
Output: SocialAccount or Individual with links to relevant databases

Requires: Camoufox (installed via: pip install camoufox)
          Node.js 18+ for the stealth-scraper bridge
"""

import json
import logging
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from flowsint_core.core.enricher_base import Enricher
from flowsint_core.core.logger import Logger
from flowsint_types.phrase import Phrase
from flowsint_types.social_account import SocialAccount
from flowsint_types.username import Username

from flowsint_enrichers.registry import flowsint_enricher

log = logging.getLogger(__name__)

# Path to Camoufox stealth-scraper (from 0racle project)
STEALTH_SCRAPER = Path(os.path.expanduser(
    "~/Documents/dev/0racle/stealth-scraper/scrape.js"
))


@flowsint_enricher
class SearchSystemsEnricher(Enricher):
    """Searches the SearchSystems.net public records directory.

    Uses Camoufox stealth browser to navigate SearchSystems.net and find
    public record databases relevant to a search query (name, location,
    record type like "criminal" or "property").

    The directory indexes 70,000+ databases across all 50 US states and
    3,200+ counties — court records, criminal records, property records,
    vital records, business registrations, and more.
    """

    InputType = Phrase
    OutputType = SocialAccount

    @classmethod
    def name(cls) -> str:
        return "phrase_to_searchsystems"

    @classmethod
    def category(cls) -> str:
        return "social"

    @classmethod
    def key(cls) -> str:
        return "text"

    @classmethod
    def icon(cls) -> Optional[str]:
        return "📂"

    @classmethod
    def documentation(cls) -> str:
        return (
            "## SearchSystems Public Records Directory\n\n"
            "Searches [SearchSystems.net](https://publicrecords.searchsystems.net/), "
            "the internet's largest directory of free public record databases "
            "(70,000+ databases).\n\n"
            "### Input\n"
            "A search phrase — name, location, or record type "
            '(e.g., "criminal records Texas", "John Smith California").\n\n'
            "### Engine\n"
            "Uses **Camoufox** anti-detect Firefox for stealth browsing "
            "(bypasses bot detection). Requires Camoufox to be installed.\n\n"
            "### Output\n"
            "Returns a SocialAccount with:\n"
            "- `username.value` – the search query\n"
            "- `display_name` – search results summary\n"
            "- `bio` – links to found database directories\n"
            "- `platform` – \"SearchSystems\"\n"
        )

    @classmethod
    def get_params_schema(cls) -> List[Dict[str, Any]]:
        return [
            {
                "name": "timeout",
                "type": "int",
                "default": 30000,
                "label": "Navigation timeout (ms)",
                "description": "Timeout for page navigation in milliseconds",
            },
            {
                "name": "headless",
                "type": "bool",
                "default": True,
                "label": "Headless mode",
                "description": "Run browser in headless mode",
            },
        ]

    # ------------------------------------------------------------------
    # scan — Camoufox stealth search
    # ------------------------------------------------------------------
    async def scan(self, data: List[InputType]) -> List[OutputType]:
        """Search SearchSystems.net for each query."""
        if not STEALTH_SCRAPER.exists():
            Logger.error(
                self.sketch_id,
                {
                    "message": (
                        f"Camoufox stealth-scraper not found at "
                        f"{STEALTH_SCRAPER}. "
                        f"Clone from: github.com/n4s5ti/0racle"
                    ),
                },
            )
            return self._make_fallback(data)

        results: List[OutputType] = []
        for phrase_item in data:
            query = phrase_item.text
            result = await self._scrape_one(query)
            results.append(
                result if result else self._empty_result(query)
            )

        return results

    async def _scrape_one(
        self, query: str
    ) -> Optional[OutputType]:
        """Run Camoufox to search SearchSystems for a given query."""
        try:
            search_url = (
                f"https://publicrecords.searchsystems.net/"
                f"?s={query.replace(' ', '+')}"
            )

            result = await self._run_camoufox(search_url)
            if not result:
                return None

            # Parse the result
            text = result.get("text", "")
            links = self._extract_links(text)

            username = Username(value=query, platform="SearchSystems")

            account = SocialAccount(
                username=username,
                display_name=f"SearchSystems: {query}",
                platform="SearchSystems",
                bio=(
                    f"Found {len(links)} database links. "
                    f"Page content: {text[:200]}..."
                ),
                profile_url=search_url,
            )

            Logger.info(
                self.sketch_id,
                {
                    "message": (
                        f"SearchSystems: {query} -> "
                        f"{len(links)} links found"
                    )
                },
            )

            return account

        except Exception as exc:
            Logger.error(
                self.sketch_id,
                {
                    "message": (
                        f"SearchSystems failed for '{query}': {exc}"
                    )
                },
            )
            return None

    async def _run_camoufox(
        self, url: str
    ) -> Optional[Dict[str, Any]]:
        """Execute the Camoufox stealth-scraper for a URL."""
        try:
            proc = subprocess.run(
                [
                    "node", str(STEALTH_SCRAPER),
                    url,
                    "--text",
                    "--json",
                    "--timeout",
                    str(self.params.get("timeout", 30000)),
                ],
                capture_output=True,
                text=True,
                timeout=int(self.params.get("timeout", 30000)) // 1000 + 10,
            )

            if proc.returncode != 0:
                Logger.error(
                    self.sketch_id,
                    {
                        "message": (
                            f"Camoufox exited with code {proc.returncode}: "
                            f"{proc.stderr[:500]}"
                        )
                    },
                )
                return None

            # Parse JSON output (last line of stdout)
            for line in reversed(proc.stdout.strip().split("\n")):
                if line.startswith("{"):
                    return json.loads(line)

            return None

        except subprocess.TimeoutExpired:
            Logger.error(
                self.sketch_id,
                {
                    "message": f"Camoufox timed out for {url}"
                },
            )
            return None
        except Exception as exc:
            Logger.error(
                self.sketch_id,
                {
                    "message": (
                        f"Camoufox error for {url}: {exc}"
                    )
                },
            )
            return None

    def _extract_links(self, text: str) -> List[str]:
        """Extract URLs from page text content."""
        import re
        urls = re.findall(r'https?://[^\s]+', text)
        return [u.rstrip(".,)") for u in urls[:20]]

    def _make_fallback(self, data: List[InputType]) -> List[OutputType]:
        return [self._empty_result(item.text) for item in data]

    def _empty_result(self, query: str) -> OutputType:
        username = Username(value=query, platform="SearchSystems")
        return SocialAccount(username=username, platform="SearchSystems")

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
                f"SearchSystems: {account.username.value} "
                f"({len(account.bio or ''):,} chars)"
            )
        return results


InputType = SearchSystemsEnricher.InputType
OutputType = SearchSystemsEnricher.OutputType
