from typing import List

from flowsint_core.core.enricher_base import Enricher
from flowsint_enrichers.registry import flowsint_enricher
from flowsint_types.organization import Organization
from flowsint_types.phrase import Phrase


@flowsint_enricher
class OrganizationToFireEnrichFunding(Enricher):
    """Emit a canonical funding context phrase for Fire Enrich funding extraction."""

    InputType = Organization
    OutputType = Phrase

    @classmethod
    def name(cls) -> str:
        return "organization_to_fire_enrich_funding"

    @classmethod
    def category(cls) -> str:
        return "Organization"

    @classmethod
    def key(cls) -> str:
        return "name"

    @classmethod
    def documentation(cls) -> str:
        return (
            "Canonical Fire Enrich funding context node. Use it before funding-stage, investors, "
            "and valuation templates or custom enrichers."
        )

    async def scan(self, data: List[InputType]) -> List[OutputType]:
        return [Phrase(text=f"organization={organization.name} | stage=fire_enrich_funding") for organization in data]

    def postprocess(self, results: List[OutputType], original_input: List[InputType]) -> List[OutputType]:
        for organization_obj, phrase_obj in zip(original_input, results):
            if not self._graph_service:
                continue
            self.create_node(organization_obj)
            self.create_node(phrase_obj)
            self.create_relationship(organization_obj, phrase_obj, "HAS_FUNDING_CONTEXT")
            self.log_graph_message(
                f"[FIRE_ENRICH] Funding context prepared for {organization_obj.nodeLabel}"
            )
        return results


InputType = OrganizationToFireEnrichFunding.InputType
OutputType = OrganizationToFireEnrichFunding.OutputType
