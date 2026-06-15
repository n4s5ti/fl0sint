from typing import List

from flowsint_core.core.enricher_base import Enricher
from flowsint_enrichers.registry import flowsint_enricher
from flowsint_types.organization import Organization
from flowsint_types.phrase import Phrase


@flowsint_enricher
class OrganizationToFireEnrichLeadership(Enricher):
    """Emit a canonical leadership context phrase for Fire Enrich executive extraction."""

    InputType = Organization
    OutputType = Phrase

    @classmethod
    def name(cls) -> str:
        return "organization_to_fire_enrich_leadership"

    @classmethod
    def category(cls) -> str:
        return "Organization"

    @classmethod
    def key(cls) -> str:
        return "name"

    @classmethod
    def documentation(cls) -> str:
        return (
            "Canonical Fire Enrich leadership context node. Use it before CEO and executive-profile "
            "templates or custom enrichers."
        )

    async def scan(self, data: List[InputType]) -> List[OutputType]:
        return [Phrase(text=f"organization={organization.name} | stage=fire_enrich_leadership") for organization in data]

    def postprocess(self, results: List[OutputType], original_input: List[InputType]) -> List[OutputType]:
        for organization_obj, phrase_obj in zip(original_input, results):
            if not self._graph_service:
                continue
            self.create_node(organization_obj)
            self.create_node(phrase_obj)
            self.create_relationship(organization_obj, phrase_obj, "HAS_LEADERSHIP_CONTEXT")
            self.log_graph_message(
                f"[FIRE_ENRICH] Leadership context prepared for {organization_obj.nodeLabel}"
            )
        return results


InputType = OrganizationToFireEnrichLeadership.InputType
OutputType = OrganizationToFireEnrichLeadership.OutputType
