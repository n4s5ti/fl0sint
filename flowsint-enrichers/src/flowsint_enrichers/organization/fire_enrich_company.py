from typing import List

from flowsint_core.core.enricher_base import Enricher
from flowsint_enrichers.registry import flowsint_enricher
from flowsint_types.organization import Organization
from flowsint_types.website import Website


@flowsint_enricher
class WebsiteToFireEnrichCompany(Enricher):
    """Normalize a company website into a canonical Fire Enrich company organization node."""

    InputType = Website
    OutputType = Organization

    @classmethod
    def name(cls) -> str:
        return "website_to_fire_enrich_company"

    @classmethod
    def category(cls) -> str:
        return "Website"

    @classmethod
    def key(cls) -> str:
        return "website"

    @classmethod
    def documentation(cls) -> str:
        return (
            "Canonical Fire Enrich discovery node. Use after domain/website extraction to create "
            "an Organization anchor that downstream Fire Enrich templates can enrich."
        )

    async def scan(self, data: List[InputType]) -> List[OutputType]:
        results: List[OutputType] = []
        for website in data:
            website_value = str(website.url).strip().rstrip("/")
            host = website_value.replace("https://", "").replace("http://", "").split("/", 1)[0]
            name_guess = host.split(":", 1)[0].split(".")[0].replace("-", " ").replace("_", " ").strip()
            company_name = " ".join(part.capitalize() for part in name_guess.split()) if name_guess else host
            results.append(
                Organization(
                    name=company_name or host,
                    nom_raison_sociale=company_name or host,
                    website=website_value,
                    source="fire_enrich_discovery",
                )
            )
        return results

    def postprocess(self, results: List[OutputType], original_input: List[InputType]) -> List[OutputType]:
        for website_obj, organization_obj in zip(original_input, results):
            if not self._graph_service:
                continue
            self.create_node(website_obj)
            self.create_node(organization_obj)
            self.create_relationship(website_obj, organization_obj, "IDENTIFIES_ORGANIZATION")
            self.log_graph_message(
                f"[FIRE_ENRICH] Canonical company anchor {organization_obj.nodeLabel} from {website_obj.url}"
            )
        return results


InputType = WebsiteToFireEnrichCompany.InputType
OutputType = WebsiteToFireEnrichCompany.OutputType
