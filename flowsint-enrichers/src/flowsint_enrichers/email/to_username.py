from typing import List

from flowsint_core.core.enricher_base import Enricher
from flowsint_types.email import Email
from flowsint_types.username import Username

from flowsint_enrichers.registry import flowsint_enricher


@flowsint_enricher
class EmailToUsernameEnricher(Enricher):
    """From email to username."""

    InputType = Email
    OutputType = Username

    @classmethod
    def name(cls) -> str:
        return "email_to_username"

    @classmethod
    def category(cls) -> str:
        return "Email"

    @classmethod
    def key(cls) -> str:
        return "email"

    async def scan(self, data: List[InputType]) -> List[OutputType]:
        results: List[OutputType] = []

        for email in data:
            splitted = email.email.split("@")
            username = splitted[0]
            results.append(Username(value=username))

        return results

    def postprocess(
        self, results: List[OutputType], original_input: List[InputType]
    ) -> List[OutputType]:
        for email_obj, username_obj in zip(original_input, results):
            if not self._graph_service:
                continue
            # Stamp metadata on output only — no intermediate email node
            username_obj.source_tool = self.name()
            username_obj.source_input = email_obj.email
            username_obj.source_sketch_id = self.sketch_id
            username_obj.evidence_level = "E2"
            # Create only the TARGET node — clean nodeLabel is the candidate value
            self.create_node(username_obj)
            # Create relationship to the input email (framework limitation — can't reach original Individual)
            self.create_relationship(email_obj, username_obj, "HAS_USERNAME")

            self.log_graph_message(
                f"Exctracted username for {email_obj.email} -> username: {username_obj.value}"
            )

        return results


# Make types available at module level for easy access
InputType = EmailToUsernameEnricher.InputType
OutputType = EmailToUsernameEnricher.OutputType
