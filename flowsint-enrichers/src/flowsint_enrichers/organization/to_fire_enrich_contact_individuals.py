from typing import Dict, List, Optional

from flowsint_core.core.enricher_base import Enricher
from flowsint_core.core.logger import Logger
from flowsint_enrichers.registry import flowsint_enricher
from flowsint_types.email import Email
from flowsint_types.individual import Individual
from flowsint_types.organization import Organization


@flowsint_enricher
class OrganizationToFireEnrichContactIndividuals(Enricher):
    """[FireEnrich] Build canonical Individual contact records from organization-level B2C/B2B role pivots."""

    InputType = Organization
    OutputType = Individual

    @classmethod
    def name(cls) -> str:
        return "organization_to_fire_enrich_contact_individuals"

    @classmethod
    def category(cls) -> str:
        return "Organization"

    @classmethod
    def key(cls) -> str:
        return "name"

    @classmethod
    def get_params_schema(cls) -> List[Dict[str, object]]:
        return [
            {
                "name": "b2c_roles",
                "type": "string",
                "description": "Comma-separated B2C contact roles. Used first.",
                "required": False,
                "default": "support,help,customer success,bookings,appointments,sales",
            },
            {
                "name": "b2b_roles",
                "type": "string",
                "description": "Comma-separated secondary B2B contact roles.",
                "required": False,
                "default": "founder,ceo,partnerships,press,recruiting",
            },
            {
                "name": "local_part_strategy",
                "type": "select",
                "description": "How aggressively to generate likely email local-parts for each role.",
                "required": False,
                "default": "standard",
                "options": [
                    {"label": "Standard", "value": "standard"},
                    {"label": "Broad", "value": "broad"},
                ],
            },
        ]

    @classmethod
    def documentation(cls) -> str:
        return (
            "Generate canonical Individual records with employer, job_title, likely contact emails, and social profile URLs embedded in metadata. "
            "B2C roles are preferred; B2B roles remain secondary."
        )

    def _domain_for(self, organization: Organization) -> Optional[str]:
        website = getattr(organization, "website", None)
        if not website:
            return None
        host = str(website).replace("https://", "").replace("http://", "").split("/", 1)[0].strip().lower()
        return host or None

    def _slug_for(self, organization: Organization) -> Optional[str]:
        domain = self._domain_for(organization)
        if domain:
            parts = [p for p in domain.split('.') if p not in {"www", "com", "io", "ai", "co", "net", "org"}]
            if parts:
                return parts[0].replace('-', '')
        if organization.name:
            return ''.join(ch for ch in organization.name.lower() if ch.isalnum())
        return None

    def _social_profiles(self, organization: Organization) -> Optional[List[str]]:
        slug = self._slug_for(organization)
        if not slug:
            return None
        return [
            f"https://www.linkedin.com/company/{slug}",
            f"https://x.com/{slug}",
            f"https://www.facebook.com/{slug}",
            f"https://www.instagram.com/{slug}",
            f"https://github.com/{slug}",
        ]

    def _local_part(self, role: str) -> str:
        compact = ''.join(ch for ch in role.lower() if ch.isalnum())
        if compact == 'customersuccess':
            return 'success'
        return compact

    async def scan(self, data: List[InputType]) -> List[OutputType]:
        b2c_roles = [role.strip() for role in self.params.get("b2c_roles", "support,help,customer success,bookings,appointments,sales").split(",") if role.strip()]
        b2b_roles = [role.strip() for role in self.params.get("b2b_roles", "founder,ceo,partnerships,press,recruiting").split(",") if role.strip()]
        strategy = self.params.get("local_part_strategy", "standard")
        ordered_roles = b2c_roles + [role for role in b2b_roles if role not in b2c_roles]
        if strategy == "broad":
            ordered_roles += [role for role in ["info", "team"] if role not in ordered_roles]
        results: List[OutputType] = []
        for organization in data:
            domain = self._domain_for(organization)
            socials = self._social_profiles(organization)
            for role in ordered_roles:
                try:
                    emails = [Email(email=f"{self._local_part(role)}@{domain}")] if domain else None
                    source = "fire_enrich_b2c" if role in b2c_roles else "fire_enrich_b2b"
                    results.append(
                        Individual(
                            full_name=f"{organization.name} {role.title()}",
                            employer=organization.name,
                            job_title=role.title(),
                            email_addresses=emails,
                            social_media_profiles=socials,
                            phone_numbers=None,
                            source=source,
                        )
                    )
                except Exception as e:
                    Logger.error(self.sketch_id, {"message": f"[FireEnrich] Failed to build canonical Individual for {organization.name} / {role}: {e}"})
                    continue
        return results

    def postprocess(self, results: List[OutputType], original_input: List[InputType]) -> List[OutputType]:
        if not self._graph_service:
            return results
        by_org = {organization.name: organization for organization in original_input}
        for individual in results:
            self.create_node(individual)
            organization = by_org.get(individual.employer)
            if organization:
                self.create_node(organization)
                relation = "HAS_B2C_CONTACT_PERSON" if individual.source == "fire_enrich_b2c" else "HAS_B2B_CONTACT_PERSON"
                self.create_relationship(organization, individual, relation)
                if individual.email_addresses:
                    for email in individual.email_addresses:
                        self.create_node(email)
                        self.create_relationship(individual, email, "HAS_EMAIL")
                self.log_graph_message(f"[FIRE_ENRICH] {organization.name} -> {relation} {individual.nodeLabel}")
        return results


InputType = OrganizationToFireEnrichContactIndividuals.InputType
OutputType = OrganizationToFireEnrichContactIndividuals.OutputType
