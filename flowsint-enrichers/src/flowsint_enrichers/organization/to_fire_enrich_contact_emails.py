from typing import Any, Dict, List, Optional

from flowsint_core.core.enricher_base import Enricher
from flowsint_core.core.logger import Logger
from flowsint_enrichers.registry import flowsint_enricher
from flowsint_types.email import Email
from flowsint_types.organization import Organization


@flowsint_enricher
class OrganizationToFireEnrichContactEmails(Enricher):
    """[FireEnrich] Derive B2C-first contact emails from an organization anchor while keeping B2B aliases available."""

    InputType = Organization
    OutputType = Email

    @classmethod
    def name(cls) -> str:
        return "organization_to_fire_enrich_contact_emails"

    @classmethod
    def category(cls) -> str:
        return "Organization"

    @classmethod
    def key(cls) -> str:
        return "name"

    @classmethod
    def get_params_schema(cls) -> List[Dict[str, Any]]:
        return [
            {
                "name": "local_part_strategy",
                "type": "select",
                "description": "How aggressively to generate company contact local-parts.",
                "required": False,
                "default": "standard",
                "options": [
                    {"label": "Standard", "value": "standard"},
                    {"label": "Broad", "value": "broad"},
                ],
            }
        ]

    @classmethod
    def documentation(cls) -> str:
        return (
            "Generate B2C-first inboxes such as support@, hello@, contact@, help@, bookings@, and appointments@. "
            "Also keeps B2B aliases like sales@, partnerships@, and press@ available as secondary pivots."
        )

    def _domain_for(self, organization: Organization) -> Optional[str]:
        website = getattr(organization, "website", None)
        if not website:
            return None
        host = str(website).replace("https://", "").replace("http://", "").split("/", 1)[0].strip().lower()
        return host or None

    async def scan(self, data: List[InputType]) -> List[OutputType]:
        strategy = self.params.get("local_part_strategy", "standard")
        b2c_parts = ["support", "help", "hello", "contact", "bookings", "appointments"]
        b2b_parts = ["sales", "info", "team"]
        if strategy == "broad":
            b2b_parts += ["press", "media", "partnerships", "founders"]
        local_parts = b2c_parts + [part for part in b2b_parts if part not in b2c_parts]
        results: List[OutputType] = []
        for organization in data:
            try:
                domain = self._domain_for(organization)
                if not domain:
                    Logger.error(
                        self.sketch_id,
                        {"message": f"[FireEnrich] No website/domain on organization {organization.name}; cannot derive contact emails."},
                    )
                    continue
                for local_part in local_parts:
                    try:
                        results.append(Email(email=f"{local_part}@{domain}"))
                    except Exception as inner:
                        Logger.error(
                            self.sketch_id,
                            {"message": f"[FireEnrich] Invalid derived email {local_part}@{domain}: {inner}"},
                        )
                        continue
            except Exception as e:
                Logger.error(
                    self.sketch_id,
                    {"message": f"[FireEnrich] Failed to derive contact emails for {organization.name}: {e}"},
                )
                continue
        return results

    def postprocess(self, results: List[OutputType], original_input: List[InputType]) -> List[OutputType]:
        if not self._graph_service:
            return results
        org_by_domain = {}
        for organization in original_input:
            domain = self._domain_for(organization)
            if domain:
                org_by_domain[domain] = organization
        b2c_aliases = {"support", "help", "hello", "contact", "bookings", "appointments"}
        for email in results:
            self.create_node(email)
            local_part, domain = str(email.email).split("@", 1)
            organization = org_by_domain.get(domain.lower())
            if organization:
                self.create_node(organization)
                relation = "HAS_B2C_CONTACT_EMAIL" if local_part in b2c_aliases else "HAS_B2B_CONTACT_EMAIL"
                self.create_relationship(organization, email, relation)
                self.log_graph_message(f"[FIRE_ENRICH] {organization.name} -> {relation} {email.email}")
        return results


InputType = OrganizationToFireEnrichContactEmails.InputType
OutputType = OrganizationToFireEnrichContactEmails.OutputType
