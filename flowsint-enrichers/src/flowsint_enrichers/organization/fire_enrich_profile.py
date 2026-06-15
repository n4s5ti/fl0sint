from typing import List

from flowsint_core.core.enricher_base import Enricher
from flowsint_enrichers.registry import flowsint_enricher
from flowsint_types.organization import Organization
from flowsint_types.phrase import Phrase


@flowsint_enricher
class OrganizationToFireEnrichProfile(Enricher):
    """Emit a canonical profile context phrase for Fire Enrich company profile extraction."""

    InputType = Organization
    OutputType = Phrase

    @classmethod
    def name(cls) -> str:
        return "organization_to_fire_enrich_profile"

    @classmethod
    def category(cls) -> str:
        return "Organization"

    @classmethod
    def key(cls) -> str:
        return "name"

    @classmethod
    def documentation(cls) -> str:
        return (
            "Canonical Fire Enrich profile context node. Use it before profile templates or custom "
            "LLM enrichers that derive industry, headquarters, and founded year."
        )

    async def scan(self, data: List[InputType]) -> List[OutputType]:
        results: List[OutputType] = []
        for organization in data:
            bits = [
                f"organization={organization.name}",
                f"website={getattr(organization, 'website', '')}",
                "stage=fire_enrich_profile",
            ]
            results.append(Phrase(text=" | ".join(bit for bit in bits if bit and not bit.endswith("="))))
        return results

    def postprocess(self, results: List[OutputType], original_input: List[InputType]) -> List[OutputType]:
        for organization_obj, phrase_obj in zip(original_input, results):
            if not self._graph_service:
                continue
            self.create_node(organization_obj)
            self.create_node(phrase_obj)
            self.create_relationship(organization_obj, phrase_obj, "HAS_PROFILE_CONTEXT")
            self.log_graph_message(
                f"[FIRE_ENRICH] Profile context prepared for {organization_obj.nodeLabel}"
            )
        return results


InputType = OrganizationToFireEnrichProfile.InputType
OutputType = OrganizationToFireEnrichProfile.OutputType
