import re
from typing import List, Set

from flowsint_core.core.enricher_base import Enricher
from flowsint_core.core.logger import Logger
from flowsint_enrichers.registry import flowsint_enricher
from flowsint_types.email import Email
from flowsint_types.individual import Individual
from flowsint_types.phone import Phone
from flowsint_types.phrase import Phrase


EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)
PHONE_RE = re.compile(r"(?:\+?\d[\d\s().-]{6,}\d)")
SOCIAL_RE = re.compile(r"""https?://(?:www\.)?(linkedin\.com|x\.com|twitter\.com|facebook\.com|instagram\.com|github\.com)/[^\s"'<>]+""", re.IGNORECASE)


@flowsint_enricher
class PhraseToContactIndividuals(Enricher):
    """[FireEnrich] Convert page text into canonical Individual contact records when concrete contact data exists."""

    InputType = Phrase
    OutputType = Individual

    @classmethod
    def name(cls) -> str:
        return "phrase_to_contact_individuals"

    @classmethod
    def category(cls) -> str:
        return "Phrase"

    @classmethod
    def key(cls) -> str:
        return "text"

    @classmethod
    def documentation(cls) -> str:
        return (
            "Build canonical Individual contact records from fetched page text by grouping parsed emails, phones, and social profile URLs. "
            "Best effort only; no paid APIs."
        )

    async def scan(self, data: List[InputType]) -> List[OutputType]:
        results: List[OutputType] = []
        for phrase in data:
            try:
                text = str(phrase.text or "")
                emails: List[Email] = []
                seen_emails: Set[str] = set()
                for match in EMAIL_RE.findall(text):
                    value = match.strip().lower().rstrip('.,;:)')
                    if value in seen_emails:
                        continue
                    try:
                        emails.append(Email(email=value))
                        seen_emails.add(value)
                    except Exception:
                        continue
                phones: List[Phone] = []
                seen_phones: Set[str] = set()
                for match in PHONE_RE.findall(text):
                    candidate = ' '.join(match.split()).strip().rstrip('.,;:)')
                    try:
                        phone = Phone(number=candidate)
                    except Exception:
                        continue
                    if phone.number in seen_phones:
                        continue
                    seen_phones.add(phone.number)
                    phones.append(phone)
                socials: List[str] = []
                seen_socials: Set[str] = set()
                for match in SOCIAL_RE.finditer(text):
                    url = match.group(0).rstrip('.,;:)')
                    if url in seen_socials:
                        continue
                    seen_socials.add(url)
                    socials.append(url)
                if not emails and not phones and not socials:
                    continue
                primary_email = str(emails[0].email) if emails else 'contact'
                label = primary_email.split('@', 1)[0].replace('.', ' ').replace('_', ' ').replace('-', ' ').title()
                results.append(
                    Individual(
                        full_name=label or 'Contact',
                        email_addresses=emails or None,
                        phone_numbers=phones or None,
                        social_media_profiles=socials or None,
                        source='fire_enrich_page_contact',
                    )
                )
            except Exception as e:
                Logger.error(self.sketch_id, {"message": f"[FireEnrich] Failed to build canonical Individual from phrase: {e}"})
                continue
        return results

    def postprocess(self, results: List[OutputType], original_input: List[InputType]) -> List[OutputType]:
        if not self._graph_service:
            return results
        for phrase in original_input:
            self.create_node(phrase)
        for individual in results:
            self.create_node(individual)
            for phrase in original_input:
                self.create_relationship(phrase, individual, "MENTIONS_CONTACT_PERSON")
            if individual.email_addresses:
                for email in individual.email_addresses:
                    self.create_node(email)
                    self.create_relationship(individual, email, "HAS_EMAIL")
            if individual.phone_numbers:
                for phone in individual.phone_numbers:
                    self.create_node(phone)
                    self.create_relationship(individual, phone, "HAS_PHONE")
            self.log_graph_message(f"[FIRE_ENRICH] Built canonical Individual {individual.nodeLabel} from page text")
        return results


InputType = PhraseToContactIndividuals.InputType
OutputType = PhraseToContactIndividuals.OutputType
