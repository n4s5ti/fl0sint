from typing import Any, Dict, List, Optional

from flowsint_core.core.enricher_base import Enricher
from flowsint_core.core.logger import Logger
from flowsint_enrichers.registry import flowsint_enricher
from flowsint_types.organization import Organization
from flowsint_types.social_account import SocialAccount
from flowsint_types.username import Username


@flowsint_enricher
class OrganizationToFireEnrichCompanySocials(Enricher):
    """[FireEnrich] Derive likely company social profile pivots from an organization anchor."""

    InputType = Organization
    OutputType = SocialAccount

    @classmethod
    def name(cls) -> str:
        return "organization_to_fire_enrich_company_socials"

    @classmethod
    def category(cls) -> str:
        return "Organization"

    @classmethod
    def key(cls) -> str:
        return "name"

    @classmethod
    def documentation(cls) -> str:
        return (
            "Generate likely company social handles on LinkedIn, X, Facebook, Instagram, and GitHub from the company website."
        )

    def _slug(self, organization: Organization) -> Optional[str]:
        website = getattr(organization, "website", None)
        if website:
            host = str(website).replace("https://", "").replace("http://", "").split("/", 1)[0].strip().lower()
            parts = [p for p in host.split(".") if p not in {"www", "com", "io", "ai", "co", "net", "org"}]
            if parts:
                return parts[0].replace("-", "")
        if organization.name:
            return "".join(ch for ch in organization.name.lower() if ch.isalnum())
        return None

    async def scan(self, data: List[InputType]) -> List[OutputType]:
        results: List[OutputType] = []
        templates = {
            "linkedin": "https://www.linkedin.com/company/{slug}",
            "x": "https://x.com/{slug}",
            "facebook": "https://www.facebook.com/{slug}",
            "instagram": "https://www.instagram.com/{slug}",
            "github": "https://github.com/{slug}",
        }
        for organization in data:
            try:
                slug = self._slug(organization)
                if not slug:
                    Logger.error(self.sketch_id, {"message": f"[FireEnrich] No slug available for organization {organization.name}"})
                    continue
                username = Username(value=slug)
                for platform, url_template in templates.items():
                    results.append(
                        SocialAccount(
                            username=username,
                            platform=platform,
                            profile_url=url_template.format(slug=slug),
                            display_name=organization.name,
                        )
                    )
            except Exception as e:
                Logger.error(
                    self.sketch_id,
                    {"message": f"[FireEnrich] Failed to derive company socials for {organization.name}: {e}"},
                )
                continue
        return results

    def postprocess(self, results: List[OutputType], original_input: List[InputType]) -> List[OutputType]:
        if not self._graph_service:
            return results
        by_slug = {}
        for organization in original_input:
            slug = self._slug(organization)
            if slug:
                by_slug[slug] = organization
        for social in results:
            self.create_node(social)
            self.create_node(social.username)
            self.create_relationship(social.username, social, "HAS_SOCIAL_ACCOUNT")
            organization = by_slug.get(social.username.value)
            if organization:
                self.create_node(organization)
                self.create_relationship(organization, social, "HAS_COMPANY_SOCIAL")
                self.log_graph_message(f"[FIRE_ENRICH] {organization.name} -> company social {social.profile_url}")
        return results


InputType = OrganizationToFireEnrichCompanySocials.InputType
OutputType = OrganizationToFireEnrichCompanySocials.OutputType
