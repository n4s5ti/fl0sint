from typing import Dict, List

from flowsint_core.core.enricher_base import Enricher
from flowsint_core.core.logger import Logger
from flowsint_enrichers.registry import flowsint_enricher
from flowsint_types.individual import Individual
from flowsint_types.organization import Organization


@flowsint_enricher
class OrganizationToFireEnrichContactPeople(Enricher):
    """[FireEnrich] Create B2C-first and B2B-secondary people pivots for contact discovery around a company."""

    InputType = Organization
    OutputType = Individual

    @classmethod
    def name(cls) -> str:
        return "organization_to_fire_enrich_contact_people"

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
        ]

    @classmethod
    def documentation(cls) -> str:
        return (
            "Generate B2C-first human-contact pivots tied to an Organization. Primary roles target support, bookings, "
            "appointments, customer success, and sales. Secondary roles keep B2B outreach paths available."
        )

    async def scan(self, data: List[InputType]) -> List[OutputType]:
        b2c_roles = [role.strip() for role in self.params.get("b2c_roles", "support,help,customer success,bookings,appointments,sales").split(",") if role.strip()]
        b2b_roles = [role.strip() for role in self.params.get("b2b_roles", "founder,ceo,partnerships,press,recruiting").split(",") if role.strip()]
        ordered_roles = b2c_roles + [role for role in b2b_roles if role not in b2c_roles]
        results: List[OutputType] = []
        for organization in data:
            for role in ordered_roles:
                try:
                    results.append(
                        Individual(
                            full_name=f"{organization.name} {role.title()}",
                            job_title=role.title(),
                            employer=organization.name,
                            source="fire_enrich_b2c" if role in b2c_roles else "fire_enrich_b2b",
                        )
                    )
                except Exception as e:
                    Logger.error(
                        self.sketch_id,
                        {"message": f"[FireEnrich] Failed to create contact person pivot for {organization.name} / {role}: {e}"},
                    )
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
                self.log_graph_message(f"[FIRE_ENRICH] {organization.name} -> {relation} {individual.nodeLabel}")
        return results


InputType = OrganizationToFireEnrichContactPeople.InputType
OutputType = OrganizationToFireEnrichContactPeople.OutputType
