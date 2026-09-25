"""
Scrapling-powered website enricher.

Uses Scrapling's StealthyFetcher to fetch web page content with
anti-bot bypass (Cloudflare Turnstile/Interstitial, etc.) and
adaptive element tracking.

Input:  Website (url)
Output: Website (enriched with content, title, description,
        status_code, headers)

Requires: pip install scrapling[playwright]
          playwright install chromium
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional

from flowsint_core.core.enricher_base import Enricher
from flowsint_core.core.logger import Logger
from flowsint_types.website import Website

from flowsint_enrichers.registry import flowsint_enricher

# ---------------------------------------------------------------------------
# Optional: Scrapling
# ---------------------------------------------------------------------------
try:
    from scrapling.fetchers import StealthyFetcher

    HAS_SCRAPLING = True
except ImportError:
    HAS_SCRAPLING = False

log = logging.getLogger(__name__)


@flowsint_enricher
class WebsiteToScraplingEnricher(Enricher):
    """Scrapes website content using Scrapling's StealthyFetcher.

    Fetches web pages with full browser automation and anti-bot bypass,
    including Cloudflare Turnstile/Interstitial challenge solving.
    Extracts visible text, page title, meta description, HTTP status,
    and response headers.

    Requires the ``scrapling`` package with Playwright and a Chromium
    browser installed.
    """

    InputType = Website
    OutputType = Website

    @classmethod
    def name(cls) -> str:
        return "website_to_scrapling"

    @classmethod
    def category(cls) -> str:
        return "Website"

    @classmethod
    def key(cls) -> str:
        return "url"

    @classmethod
    def icon(cls) -> Optional[str]:
        return "🕷️"

    @classmethod
    def documentation(cls) -> str:
        return (
            "## Scrapling Website Enricher\n\n"
            "Uses [Scrapling](https://github.com/D4Vinci/Scrapling) to fetch web pages "
            "with advanced anti-bot bypass capabilities:\n\n"
            "- **StealthyFetcher** – full Chromium browser automation with fingerprint "
            "spoofing\n"
            "- **Cloudflare bypass** – automatic solving of Turnstile and Interstitial "
            "challenges\n"
            "- **Adaptive parsing** – survives minor website structure changes\n"
            "- **JavaScript rendering** – loads dynamic content\n\n"
            "### Output\n"
            "Returns the same Website entity enriched with:\n"
            "- `content` – visible text extracted from the page\n"
            "- `title` – page `<title>` element content\n"
            "- `description` – meta description content\n"
            "- `status_code` – HTTP response status code\n"
            "- `headers` – HTTP response headers\n"
            "- `active` – whether the page loaded successfully (status < 400)\n\n"
            "### Parameters\n"
            "- `headless` – run browser in headless mode (default: true)\n"
            "- `solve_cloudflare` – attempt Cloudflare challenge solving (default: true)\n"
            "- `network_idle` – wait for network idle (default: true)\n"
            "- `timeout` – page load timeout in ms (default: 30000)\n"
            "- `max_concurrency` – concurrent browser instances (default: 3)\n"
        )

    @classmethod
    def get_params_schema(cls) -> List[Dict[str, Any]]:
        return [
            {
                "name": "headless",
                "type": "bool",
                "default": True,
                "label": "Headless mode",
                "description": "Run browser in headless (invisible) mode",
            },
            {
                "name": "solve_cloudflare",
                "type": "bool",
                "default": True,
                "label": "Solve Cloudflare challenges",
                "description": "Automatically solve Cloudflare Turnstile and "
                "Interstitial challenges",
            },
            {
                "name": "network_idle",
                "type": "bool",
                "default": True,
                "label": "Wait for network idle",
                "description": "Wait until no network activity for 500ms before "
                "capturing page",
            },
            {
                "name": "timeout",
                "type": "int",
                "default": 30000,
                "label": "Page load timeout (ms)",
                "description": "Maximum time in milliseconds to wait for page load",
            },
            {
                "name": "max_concurrency",
                "type": "int",
                "default": 3,
                "label": "Max concurrent browsers",
                "description": "Maximum number of simultaneous browser instances",
            },
        ]

    @classmethod
    def required_params(self) -> bool:
        return True

    # ------------------------------------------------------------------
    # scan — concurrent Scrapling fetches
    # ------------------------------------------------------------------
    async def scan(self, data: List[InputType]) -> List[OutputType]:
        """Fetch websites concurrently using Scrapling StealthyFetcher."""
        if not HAS_SCRAPLING:
            Logger.error(
                self.sketch_id,
                {
                    "message": (
                        "Scrapling is not installed. "
                        "Run: pip install scrapling[playwright] && "
                        "playwright install chromium"
                    ),
                },
            )
            return data  # Return originals unenriched

        concurrency = self.params.get("max_concurrency", 3)
        sem = asyncio.Semaphore(concurrency)

        async def _fetch_one(website: Website) -> Optional[Website]:
            """Fetch a single URL with Scrapling, mutate & return the Website."""
            async with sem:
                url = str(website.url)
                try:
                    page = await asyncio.to_thread(
                        StealthyFetcher.fetch,
                        url,
                        headless=self.params.get("headless", True),
                        solve_cloudflare=self.params.get("solve_cloudflare", True),
                        network_idle=self.params.get("network_idle", True),
                        timeout=self.params.get("timeout", 30000),
                        disable_resources=True,
                        load_dom=True,
                    )

                    # Enrich Website with response data
                    website.status_code = page.status
                    website.headers = dict(page.headers) if page.headers else None
                    website.active = page.status < 400

                    if page.status < 400:
                        # Extract visible text content
                        website.content = page.get_all_text(
                            separator=" ", strip=True, ignore_tags=("script", "style")
                        )

                        # Extract title
                        title_el = page.css("title::text")
                        if title_el:
                            website.title = title_el.get()

                        # Extract meta description
                        desc_el = page.css(
                            'meta[name="description"]::attr(content)'
                        )
                        if desc_el:
                            website.description = desc_el.get()

                    Logger.info(
                        self.sketch_id,
                        {
                            "message": (
                                f"Scrapling fetched {url} "
                                f"(status={page.status}, "
                                f"content_len={len(website.content or '')})"
                            )
                        },
                    )

                except Exception as exc:
                    Logger.error(
                        self.sketch_id,
                        {
                            "message": (
                                f"Scrapling failed for {url}: {exc}"
                            )
                        },
                    )
                    website.active = False
                    website.status_code = 0

                return website

        # Fan out with concurrency limit
        tasks = [_fetch_one(w) for w in data]
        results: List[OutputType] = []
        for coro in asyncio.as_completed(tasks):
            result = await coro
            if result is not None:
                results.append(result)

        return results

    # ------------------------------------------------------------------
    # postprocess — write to graph
    # ------------------------------------------------------------------
    def postprocess(
        self, results: List[OutputType], original_input: List[InputType]
    ) -> List[OutputType]:
        """Write enriched Website nodes and log results to the graph."""
        for website in results:
            if not self._graph_service:
                continue

            url = str(website.url)
            self.create_node(website)

            status = website.status_code or 0
            content_len = len(website.content) if website.content else 0
            self.log_graph_message(
                f"Scrapling enriched {url} "
                f"(status={status}, content={content_len} chars)"
            )

        return results


# Make types available at module level for easy access
InputType = WebsiteToScraplingEnricher.InputType
OutputType = WebsiteToScraplingEnricher.OutputType
