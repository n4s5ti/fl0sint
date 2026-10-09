from typing import Any
from urllib.parse import urlparse

from reconspread import Crawler

from flowsint_core.core.enricher_base import Enricher
from flowsint_core.core.graph import CaptureGraphRepository
from flowsint_core.core.logger import Logger
from flowsint_enrichers.registry import flowsint_enricher
from flowsint_types.domain import Domain
from flowsint_types.website import Website


@flowsint_enricher
class WebsiteToLinks(Enricher):
    """Crawl a website and return unreviewed, capture-only link candidates."""

    InputType = Website
    OutputType = Website

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        if kwargs.get("graph_service") is None:
            kwargs["capture_only"] = True
        super().__init__(*args, **kwargs)
        repository = self.graph_service.repository
        if not isinstance(repository, CaptureGraphRepository):
            raise ValueError(
                "WebsiteToLinks requires a capture-only GraphService; "
                "live graph repositories are not an acquisition fallback."
            )
        self._capture_repository = repository
        self._execution_id = 0

    @classmethod
    def name(cls) -> str:
        return "website_to_links"

    @classmethod
    def category(cls) -> str:
        return "Website"

    @classmethod
    def key(cls) -> str:
        return "url"

    def extract_domain(self, url: str) -> str:
        """Extract the network location from a discovered URL."""
        try:
            return urlparse(url).hostname or ""
        except Exception:
            return ""

    def _candidate_operations(self, lineage: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            operation.to_dict()
            for operation in self._capture_repository.get_captured_candidates()
            if operation.source_input == lineage
        ]

    async def scan(self, data: list[Website]) -> list[dict[str, Any]]:
        """Run the real crawler while retaining only local candidate operations."""
        self._capture_repository.clear_captures()
        self._execution_id += 1
        execution_id = self._execution_id
        results = []

        for input_index, website in enumerate(data):
            source_url = str(website.url)
            lineage = {
                "execution_id": execution_id,
                "input_index": input_index,
                "source_url": source_url,
            }
            with self._capture_repository.source_context(lineage):
                try:
                    Logger.info(
                        self.sketch_id,
                        {"message": f"Starting reconspread crawl of {source_url}"},
                    )
                    main_domain = self.extract_domain(source_url)

                    self.create_node(website)
                    if main_domain:
                        domain_obj = Domain(domain=main_domain)
                        self.create_node(domain_obj)
                        self.create_relationship(
                            website, domain_obj, "BELONGS_TO_DOMAIN"
                        )
                        self.log_graph_message(
                            f"Website {source_url} belongs to domain {main_domain}"
                        )

                    internal_urls = []
                    external_urls = []
                    external_domains = set()

                    def url_handler(url: str, is_external: bool = False) -> None:
                        """Capture direct crawler discoveries under the current input."""
                        if is_external:
                            external_urls.append(url)
                            domain = self.extract_domain(url)
                            if domain:
                                external_domains.add(domain)
                                url_obj = Website(url=url)
                                self.create_node(url_obj)
                                self.create_relationship(website, url_obj, "LINKS_TO")
                                self.log_graph_message(
                                    f"Website {source_url} links to external website {url}"
                                )

                                if domain != main_domain:
                                    domain_obj_ext = Domain(domain=domain)
                                    self.create_node(domain_obj_ext)
                                    self.create_relationship(
                                        url_obj, domain_obj_ext, "BELONGS_TO_DOMAIN"
                                    )
                                    domain_obj_main = Domain(domain=main_domain)
                                    self.create_relationship(
                                        domain_obj_main, domain_obj_ext, "LINKS_TO"
                                    )
                                    self.log_graph_message(
                                        f"External website {url} belongs to domain {domain}"
                                    )
                                    self.log_graph_message(
                                        f"Website {source_url} links to external domain {domain}"
                                    )
                            Logger.info(
                                self.sketch_id,
                                {
                                    "message": f"[EXTERNAL] Found: {url} -> Domain: {domain}"
                                },
                            )
                        else:
                            internal_urls.append(url)
                            if url != source_url:
                                internal_website = Website(url=url)
                                self.create_node(internal_website)
                                self.create_relationship(
                                    website, internal_website, "LINKS_TO"
                                )
                                self.log_graph_message(
                                    f"Website {source_url} links to internal website {url}"
                                )
                                if main_domain:
                                    domain_obj_int = Domain(domain=main_domain)
                                    self.create_relationship(
                                        internal_website,
                                        domain_obj_int,
                                        "BELONGS_TO_DOMAIN",
                                    )
                            Logger.info(
                                self.sketch_id, {"message": f"[INTERNAL] Found: {url}"}
                            )

                    crawler = Crawler(
                        url=source_url,
                        recursive=True,
                        same_domain_only=False,
                        verbose=False,
                        _on_result_callback=url_handler,
                    )
                    crawler.fetch()
                    crawler.extract_urls()
                    crawl_results = crawler.get_results()

                    for url in crawl_results.internal:
                        if url not in internal_urls:
                            internal_urls.append(url)

                    for url in crawl_results.external:
                        if url not in external_urls:
                            external_urls.append(url)
                            domain = self.extract_domain(url)
                            if domain:
                                external_domains.add(domain)

                    result = {
                        "website": source_url,
                        "execution_id": execution_id,
                        "input_index": input_index,
                        "main_domain": main_domain,
                        "internal_urls": internal_urls,
                        "external_urls": external_urls,
                        "external_domains": list(external_domains),
                    }
                    Logger.info(
                        self.sketch_id,
                        {
                            "message": (
                                f"Spread crawl completed for {source_url}: "
                                f"Main domain: {main_domain}, "
                                f"{len(internal_urls)} internal URLs, "
                                f"{len(external_urls)} external URLs, "
                                f"{len(external_domains)} external domains found."
                            )
                        },
                    )
                except Exception as error:
                    Logger.error(
                        self.sketch_id,
                        {"message": f"Error crawling {source_url}: {error}"},
                    )
                    main_domain = self.extract_domain(source_url)
                    self.create_node(website)
                    if main_domain:
                        domain_obj = Domain(domain=main_domain)
                        self.create_node(domain_obj)
                        self.create_relationship(
                            website, domain_obj, "BELONGS_TO_DOMAIN"
                        )
                        self.log_graph_message(
                            f"Website {source_url} belongs to domain {main_domain}"
                        )
                    result = {
                        "website": source_url,
                        "execution_id": execution_id,
                        "input_index": input_index,
                        "main_domain": main_domain,
                        "internal_urls": [],
                        "external_urls": [],
                        "external_domains": [],
                    }

            result["disposition"] = "unreviewed"
            result["candidate_operations"] = self._candidate_operations(lineage)
            results.append(result)

        return results

    def postprocess(
        self, results: list[dict[str, Any]], original_input: list[Website] | None = None
    ) -> list[dict[str, Any]]:
        """Keep captured candidates unreviewed; publication is intentionally absent."""
        return results


InputType = WebsiteToLinks.InputType
OutputType = WebsiteToLinks.OutputType
