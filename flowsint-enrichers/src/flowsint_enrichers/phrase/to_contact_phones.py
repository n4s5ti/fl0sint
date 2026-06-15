import re
from typing import List, Set

from flowsint_core.core.enricher_base import Enricher
from flowsint_core.core.logger import Logger
from flowsint_enrichers.registry import flowsint_enricher
from flowsint_types.phone import Phone
from flowsint_types.phrase import Phrase


PHONE_CANDIDATE_RE = re.compile(r"(?:\+?\d[\d\s().-]{6,}\d)")


@flowsint_enricher
class PhraseToContactPhones(Enricher):
    """[FireEnrich] Extract phone numbers from fetched contact/about page text."""

    InputType = Phrase
    OutputType = Phone

    @classmethod
    def name(cls) -> str:
        return "phrase_to_contact_phones"

    @classmethod
    def category(cls) -> str:
        return "Phrase"

    @classmethod
    def key(cls) -> str:
        return "text"

    @classmethod
    def documentation(cls) -> str:
        return "Extract phone number candidates from contact/about page text using regex plus Phone validation."

    async def scan(self, data: List[InputType]) -> List[OutputType]:
        results: List[OutputType] = []
        seen: Set[str] = set()
        for phrase in data:
            try:
                text = str(phrase.text or "")
                for match in PHONE_CANDIDATE_RE.findall(text):
                    candidate = " ".join(match.split()).strip().rstrip('.,;:)')
                    if candidate in seen:
                        continue
                    try:
                        phone = Phone(number=candidate)
                        results.append(phone)
                        seen.add(phone.number)
                    except Exception:
                        continue
            except Exception as e:
                Logger.error(self.sketch_id, {"message": f"[FireEnrich] Failed to parse phones from phrase: {e}"})
                continue
        return results

    def postprocess(self, results: List[OutputType], original_input: List[InputType]) -> List[OutputType]:
        if not self._graph_service:
            return results
        for phrase in original_input:
            self.create_node(phrase)
        for phone in results:
            self.create_node(phone)
            for phrase in original_input:
                self.create_relationship(phrase, phone, "MENTIONS_PHONE")
            self.log_graph_message(f"[FIRE_ENRICH] Parsed phone {phone.number} from page text")
        return results


InputType = PhraseToContactPhones.InputType
OutputType = PhraseToContactPhones.OutputType
