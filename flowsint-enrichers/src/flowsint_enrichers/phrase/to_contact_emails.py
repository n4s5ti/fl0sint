import re
from typing import List, Set

from flowsint_core.core.enricher_base import Enricher
from flowsint_core.core.logger import Logger
from flowsint_enrichers.registry import flowsint_enricher
from flowsint_types.email import Email
from flowsint_types.phrase import Phrase


EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)


@flowsint_enricher
class PhraseToContactEmails(Enricher):
    """[FireEnrich] Extract email addresses from fetched contact/about page text."""

    InputType = Phrase
    OutputType = Email

    @classmethod
    def name(cls) -> str:
        return "phrase_to_contact_emails"

    @classmethod
    def category(cls) -> str:
        return "Phrase"

    @classmethod
    def key(cls) -> str:
        return "text"

    @classmethod
    def documentation(cls) -> str:
        return "Extract concrete email addresses from contact/about page text using regex only."

    async def scan(self, data: List[InputType]) -> List[OutputType]:
        results: List[OutputType] = []
        seen: Set[str] = set()
        for phrase in data:
            try:
                for match in EMAIL_RE.findall(str(phrase.text or "")):
                    email_value = match.strip().lower().rstrip('.,;:)')
                    if email_value in seen:
                        continue
                    try:
                        results.append(Email(email=email_value))
                        seen.add(email_value)
                    except Exception as inner:
                        Logger.error(self.sketch_id, {"message": f"[FireEnrich] Invalid parsed email {email_value}: {inner}"})
                        continue
            except Exception as e:
                Logger.error(self.sketch_id, {"message": f"[FireEnrich] Failed to parse emails from phrase: {e}"})
                continue
        return results

    def postprocess(self, results: List[OutputType], original_input: List[InputType]) -> List[OutputType]:
        if not self._graph_service:
            return results
        for phrase in original_input:
            self.create_node(phrase)
        for email in results:
            self.create_node(email)
            for phrase in original_input:
                self.create_relationship(phrase, email, "MENTIONS_EMAIL")
            self.log_graph_message(f"[FIRE_ENRICH] Parsed email {email.email} from page text")
        return results


InputType = PhraseToContactEmails.InputType
OutputType = PhraseToContactEmails.OutputType
