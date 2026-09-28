"""Deterministic, graph-free observations from retained UTF-8 HTML bytes."""

from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass, field
from enum import Enum
from html.parser import HTMLParser
from typing import Callable
from urllib.parse import unquote, urljoin, urlsplit

from .acquisition import ArtifactBindingError, ArtifactReference
from .models import RedactedDiagnostic


class ObservationKind(str, Enum):
    GENERAL_TEXT = "observed_general_text"
    LINK = "observed_link"
    FORM_CANDIDATE = "observed_form_candidate"
    GENERAL_INBOX = "observed_general_inbox"
    ROLE_INBOX = "observed_role_inbox"
    PERSON_ASSOCIATED = "observed_person_associated"
    UNKNOWN_CONTACT = "observed_unknown_contact"
    PHONE_CONTACT = "observed_phone_contact"
    # Retained public spellings used by the bridge. The extractor does not emit them.
    GENERAL_CONTACT = "observed_general_contact"
    ROLE_CONTACT = "observed_role_contact"
    EMAIL_MEDIA = "observed_email_media"
    PHONE_MEDIA = "observed_phone_media"
    GENERATED_HYPOTHESIS = "generated_hypothesis"


class ReviewState(str, Enum):
    UNREVIEWED = "unreviewed"


class ExecutionState(str, Enum):
    NOT_EXECUTABLE = "not_executable"
    UNKNOWN = "unknown"


def _strict_positive(name: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be a positive integer")
    if value <= 0:
        raise ValueError(f"{name} must be a positive integer")


@dataclass(frozen=True)
class RawSpan:
    start_byte: int
    end_byte: int

    def __post_init__(self) -> None:
        if isinstance(self.start_byte, bool) or isinstance(self.end_byte, bool):
            raise TypeError("span offsets must be integers")
        if not isinstance(self.start_byte, int) or not isinstance(self.end_byte, int):
            raise TypeError("span offsets must be integers")
        if not 0 <= self.start_byte < self.end_byte:
            raise ValueError(f"Invalid span: [{self.start_byte}, {self.end_byte})")


@dataclass(frozen=True)
class ExtractionPolicy:
    max_body_bytes: int = 8 * 1024 * 1024
    max_observations: int = 1000
    max_value_bytes: int = 8192
    max_context_bytes: int = 1024 * 1024
    max_diagnostics: int = 16
    allow_relative_hrefs: bool = True
    allow_external_links: bool = True

    def __post_init__(self) -> None:
        for name in ("max_body_bytes", "max_observations", "max_value_bytes",
                     "max_context_bytes", "max_diagnostics"):
            _strict_positive(name, getattr(self, name))
        if type(self.allow_relative_hrefs) is not bool or type(self.allow_external_links) is not bool:
            raise TypeError("policy switches must be bool")


@dataclass(frozen=True)
class Observation:
    observation_id: str
    occurrence_id: str
    kind: ObservationKind
    value: str
    artifact_id: str
    snapshot_id: str
    content_digest: str
    input_ref: str
    source_url: str
    raw_span: RawSpan
    context_span: RawSpan | None = None
    anchor_text: str | None = None
    person_name: str | None = None
    role_name: str | None = None
    company_name: str | None = None
    review_state: ReviewState = ReviewState.UNREVIEWED
    execution_state: ExecutionState = ExecutionState.NOT_EXECUTABLE

    def __post_init__(self) -> None:
        if type(self.kind) is not ObservationKind:
            raise TypeError("kind must be ObservationKind")
        if self.kind is ObservationKind.GENERATED_HYPOTHESIS:
            raise ValueError("generated hypotheses are not observations")
        if type(self.review_state) is not ReviewState or self.review_state is not ReviewState.UNREVIEWED:
            raise TypeError("review_state must be ReviewState.UNREVIEWED")
        if type(self.execution_state) is not ExecutionState:
            raise TypeError("execution_state must be ExecutionState")
        for name in ("observation_id", "occurrence_id", "value", "artifact_id",
                     "snapshot_id", "content_digest", "input_ref", "source_url"):
            item = getattr(self, name)
            if not isinstance(item, str) or not item.strip():
                raise ValueError(f"{name} must be a nonempty string")
        if not re.fullmatch(r"[a-f0-9]{64}", self.content_digest):
            raise ValueError("content_digest must be lowercase SHA-256")
        if not re.fullmatch(r"[a-f0-9]{64}", self.input_ref):
            raise ValueError("input_ref must be lowercase SHA-256")
        if any(character.isspace() for character in self.occurrence_id):
            raise ValueError("occurrence_id must not contain whitespace")
        if _safe_http_url(self.source_url, None, allow_relative=False) != self.source_url:
            raise ValueError("source_url must be an HTTP(S) URL without credentials")
        if not isinstance(self.raw_span, RawSpan):
            raise TypeError("raw_span must be RawSpan")
        if self.context_span is not None and not isinstance(self.context_span, RawSpan):
            raise TypeError("context_span must be RawSpan")
        if self.person_name is not None and self.context_span is None:
            raise ValueError("person_name requires bounded context")
        if self.person_name is not None and self.kind is not ObservationKind.PERSON_ASSOCIATED:
            raise ValueError("person_name is only valid for person-associated observations")


@dataclass(frozen=True)
class GeneratedHypothesis:
    hypothesis_id: str
    value: str
    basis: str
    source_observation_ids: tuple[str, ...]

    @classmethod
    def create(cls, *, value: str, basis: str,
               source_observation_ids: tuple[str, ...]) -> "GeneratedHypothesis":
        if not value.strip() or not basis.strip() or not source_observation_ids:
            raise ValueError("hypothesis requires value, basis, and source observations")
        key = "\0".join((value, basis, *source_observation_ids)).encode()
        return cls("hypothesis-" + hashlib.sha256(key).hexdigest()[:24], value, basis,
                   source_observation_ids)


@dataclass(frozen=True)
class ObservedExtractionResult:
    readable_text: str
    observations: tuple[Observation, ...]
    diagnostics: tuple[RedactedDiagnostic, ...]
    was_truncated: bool
    observations_skipped: int
    total_observations_found: int

    def __post_init__(self) -> None:
        if not isinstance(self.observations, tuple) or any(type(o) is not Observation for o in self.observations):
            raise TypeError("observations must contain only Observation values")
        if not isinstance(self.diagnostics, tuple) or any(type(d) is not RedactedDiagnostic for d in self.diagnostics):
            raise TypeError("diagnostics must contain only RedactedDiagnostic values")
        if type(self.was_truncated) is not bool:
            raise TypeError("was_truncated must be bool")
        for name in ("observations_skipped", "total_observations_found"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a nonnegative integer")
        if self.total_observations_found != len(self.observations) + self.observations_skipped:
            raise ValueError("observation counts are inconsistent")
        ids = [o.observation_id for o in self.observations]
        if len(ids) != len(set(ids)):
            raise ValueError("observation IDs are not unique")

    def validate(self) -> None:
        """Compatibility hook; construction already performs complete validation."""


def _verify_artifact(body: bytes, artifact: ArtifactReference) -> None:
    if not isinstance(body, bytes):
        raise TypeError("body must be bytes")
    if len(body) != artifact.byte_length:
        raise ArtifactBindingError("artifact byte length mismatch")
    if hashlib.sha256(body).hexdigest() != artifact.content_digest:
        raise ArtifactBindingError("artifact digest mismatch")


def resolve_observation_span(body: bytes, artifact: ArtifactReference, span: RawSpan,
                             observation_kind: ObservationKind | None = None) -> tuple[bytes, str]:
    _verify_artifact(body, artifact)
    if not isinstance(span, RawSpan):
        raise TypeError("span must be RawSpan")
    if observation_kind is not None and type(observation_kind) is not ObservationKind:
        raise TypeError("observation_kind must be ObservationKind")
    if span.end_byte > len(body):
        raise ValueError("observation span is outside retained body")
    raw = body[span.start_byte:span.end_byte]
    try:
        return raw, raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("observation span is not valid UTF-8") from exc


@dataclass
class _Frame:
    tag: str
    start_char: int
    start_tag_end_char: int
    explicit_person: bool = False
    captures_person_name: bool = False
    end_char: int | None = None
    person_name_parts: list[str] = field(default_factory=list)
    anchor_parts: list[str] = field(default_factory=list)
    href: tuple[str, int, int] | None = None


@dataclass
class _Candidate:
    kind: ObservationKind
    value: str
    start_char: int
    end_char: int
    context: _Frame | None = None
    anchor_text: str | None = None


_EMAIL = re.compile(r"(?<![\w.+-])[A-Za-z0-9][A-Za-z0-9._%+-]*@(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}(?![\w-])")
_PHONE = re.compile(r"(?<!\d)(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}(?!\d)")
_ATTR = re.compile(r"(?is)(?<![\w:-])([a-z_:][\w:.-]*)\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s>]+))")
_BLOCKS = frozenset({"address", "article", "aside", "div", "footer", "header", "li", "main", "p", "section", "td"})
_SKIP = frozenset({"script", "style", "template", "noscript", "svg"})
_GENERAL = frozenset({"admin", "contact", "hello", "hi", "info", "inquiries", "office", "reception"})
_ROLES = frozenset({"billing", "careers", "help", "jobs", "legal", "media", "press", "sales", "security", "support"})


class _SourceParser(HTMLParser):
    def __init__(self, source: str, line_starts: list[int]):
        super().__init__(convert_charrefs=True)
        self.source = source
        self.line_starts = line_starts
        self.stack: list[_Frame] = []
        self.candidates: list[_Candidate] = []
        self.readable_parts: list[str] = []

    def _pos(self) -> int:
        line, col = self.getpos()
        return self.line_starts[line - 1] + col

    def _person(self) -> _Frame | None:
        return next((f for f in reversed(self.stack) if f.explicit_person), None)

    def _context(self) -> _Frame | None:
        return self._person() or next((f for f in reversed(self.stack) if f.tag in _BLOCKS), None)

    @staticmethod
    def _attrs(token: str, token_start: int) -> dict[str, tuple[str, int, int]]:
        found: dict[str, tuple[str, int, int]] = {}
        for match in _ATTR.finditer(token):
            group = next(i for i in (2, 3, 4) if match.group(i) is not None)
            name = match.group(1).lower()
            found[name] = (html.unescape(match.group(group)),
                           token_start + match.start(group), token_start + match.end(group))
        return found

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._start(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._start(tag)
        self._end(tag, self._pos() + len(self.get_starttag_text() or ""))

    def _start(self, tag: str) -> None:
        tag = tag.lower()
        start = self._pos()
        token = self.get_starttag_text() or ""
        raw_attrs = self._attrs(token, start)
        class_value = raw_attrs.get("class", ("", 0, 0))[0].lower().split()
        explicit = tag in {"article", "div", "section", "li"} and bool(
            {"person", "person-card", "profile", "team-member", "staff-member"}.intersection(class_value)
            or "data-person" in raw_attrs
        )
        itemprop = raw_attrs.get("itemprop", ("", 0, 0))[0].lower()
        captures_name = tag == "h2" or itemprop == "name" or bool(
            {"name", "person-name", "profile-name"}.intersection(class_value)
        )
        frame = _Frame(tag, start, start + len(token), explicit_person=explicit,
                       captures_person_name=captures_name)
        self.stack.append(frame)
        if tag == "a" and "href" in raw_attrs:
            frame.href = raw_attrs["href"]
        if tag == "form" and "action" in raw_attrs:
            value, value_start, value_end = raw_attrs["action"]
            safe = _safe_http_url(value, None, allow_relative=True)
            if safe is not None:
                self.candidates.append(_Candidate(ObservationKind.FORM_CANDIDATE, value,
                                                  value_start, value_end, self._context()))

    def handle_endtag(self, tag: str) -> None:
        self._end(tag.lower(), self._pos() + len(f"</{tag}>") )

    def _end(self, tag: str, end: int) -> None:
        index = next((i for i in range(len(self.stack) - 1, -1, -1) if self.stack[i].tag == tag), None)
        if index is None:
            return
        closing = self.stack[index:]
        del self.stack[index:]
        for frame in closing:
            frame.end_char = end
            if frame.tag == "a" and frame.href:
                href, start, stop = frame.href
                anchor = " ".join("".join(frame.anchor_parts).split()) or None
                self._href_candidate(href, start, stop, frame, anchor)

    def _href_candidate(self, href: str, start: int, stop: int, frame: _Frame,
                        anchor: str | None) -> None:
        lowered = href.strip().lower()
        context = self._context()
        if lowered.startswith("mailto:"):
            address = unquote(href[7:].split("?", 1)[0]).strip()
            if _EMAIL.fullmatch(address):
                self.candidates.append(_Candidate(_email_kind(address, context), address,
                                                  start, stop, context, anchor))
        elif lowered.startswith("tel:"):
            number = unquote(href[4:].split("?", 1)[0]).strip()
            if number:
                self.candidates.append(_Candidate(ObservationKind.PHONE_CONTACT, number,
                                                  start, stop, context, anchor))
        else:
            safe = _safe_http_url(href, None, allow_relative=True)
            if safe is not None:
                self.candidates.append(_Candidate(ObservationKind.LINK, href, start, stop,
                                                  context, anchor))

    def handle_data(self, data: str) -> None:
        if any(frame.tag in _SKIP for frame in self.stack):
            return
        start = self._pos()
        raw_end = self.source.find("<", start)
        if raw_end < 0:
            raw_end = len(self.source)
        raw = self.source[start:raw_end]
        decoded, mapping = _decode_with_mapping(raw, start)
        visible = " ".join(decoded.split())
        if visible:
            self.readable_parts.append(visible)
        for frame in self.stack:
            if frame.tag == "a":
                frame.anchor_parts.append(decoded)
        name_label_open = any(frame.captures_person_name for frame in self.stack)
        person = self._person()
        if person is not None and name_label_open and visible:
            person.person_name_parts.append(re.sub(r"^name\s*:\s*", "", visible,
                                                   flags=re.IGNORECASE))
        context = self._context()
        for match in _EMAIL.finditer(decoded):
            char_start, char_end = _mapped_range(mapping, match.start(), match.end())
            self.candidates.append(_Candidate(_email_kind(match.group(), context), match.group(),
                                              char_start, char_end, context))
        for match in _PHONE.finditer(decoded):
            char_start, char_end = _mapped_range(mapping, match.start(), match.end())
            self.candidates.append(_Candidate(ObservationKind.PHONE_CONTACT, match.group(),
                                              char_start, char_end, context))

    def close_open_frames(self) -> None:
        for frame in self.stack:
            frame.end_char = len(self.source)
        self.stack.clear()


def _decode_with_mapping(raw: str, absolute_start: int) -> tuple[str, list[tuple[int, int]]]:
    output: list[str] = []
    mapping: list[tuple[int, int]] = []
    index = 0
    entity = re.compile(r"&(?:#[xX][0-9a-fA-F]+|#\d+|[A-Za-z][A-Za-z0-9]+);")
    while index < len(raw):
        match = entity.match(raw, index)
        if match:
            decoded = html.unescape(match.group())
            output.extend(decoded)
            mapping.extend([(absolute_start + index, absolute_start + match.end())] * len(decoded))
            index = match.end()
        else:
            output.append(raw[index])
            mapping.append((absolute_start + index, absolute_start + index + 1))
            index += 1
    return "".join(output), mapping


def _mapped_range(mapping: list[tuple[int, int]], start: int, end: int) -> tuple[int, int]:
    return mapping[start][0], mapping[end - 1][1]


def _email_kind(address: str, context: _Frame | None) -> ObservationKind:
    local = address.rsplit("@", 1)[0].lower().split("+", 1)[0]
    if local in _GENERAL:
        return ObservationKind.GENERAL_INBOX
    if local in _ROLES:
        return ObservationKind.ROLE_INBOX
    if context is not None and context.explicit_person:
        return ObservationKind.PERSON_ASSOCIATED
    return ObservationKind.UNKNOWN_CONTACT


def _safe_http_url(value: str, base: str | None, *, allow_relative: bool) -> str | None:
    if not value or any(character.isspace() for character in value):
        return None
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if parsed.scheme:
        if parsed.scheme.lower() not in {"http", "https"}:
            return None
        if not parsed.hostname or parsed.username is not None or parsed.password is not None:
            return None
        return value
    if not allow_relative or value.startswith("//"):
        return None
    return urljoin(base, value) if base else value


def _byte_map(source: str) -> list[int]:
    offsets = [0]
    total = 0
    for character in source:
        total += len(character.encode("utf-8"))
        offsets.append(total)
    return offsets


def _observation_id(artifact: ArtifactReference, occurrence_id: str,
                    kind: ObservationKind, span: RawSpan) -> str:
    key = "\0".join((artifact.snapshot_id, artifact.content_digest, occurrence_id,
                     kind.value, str(span.start_byte), str(span.end_byte))).encode()
    return "observation-" + hashlib.sha256(key).hexdigest()[:32]


def _diagnostic(code: str, message: str) -> RedactedDiagnostic:
    return RedactedDiagnostic(code=code, safe_message=message, retryable=False)


def extract_observations(body: bytes, *, artifact: ArtifactReference, occurrence_id: str,
                         input_ref: str, final_url: str,
                         policy: ExtractionPolicy | None = None,
                         cancelled: Callable[[], bool] | None = None) -> ObservedExtractionResult:
    selected = policy or ExtractionPolicy()
    _verify_artifact(body, artifact)
    if _safe_http_url(final_url, None, allow_relative=False) != final_url:
        raise ValueError("final_url must be an HTTP(S) URL without credentials")
    if artifact.final_url is not None and final_url != artifact.final_url:
        raise ArtifactBindingError("source final URL does not match artifact final URL")
    if len(body) > selected.max_body_bytes:
        raise ValueError("retained body exceeds extraction body limit")
    if cancelled is not None and cancelled():
        return ObservedExtractionResult("", (), (_diagnostic("extraction_cancelled", "Extraction was cancelled."),), False, 0, 0)
    try:
        source = body.decode("utf-8")
    except UnicodeDecodeError:
        return ObservedExtractionResult("", (), (_diagnostic("decode_error", "Retained source is not valid UTF-8."),), False, 0, 0)
    line_starts = [0]
    for match in re.finditer("\n", source):
        line_starts.append(match.end())
    parser = _SourceParser(source, line_starts)
    parser.feed(source)
    parser.close()
    parser.close_open_frames()
    if cancelled is not None and cancelled():
        return ObservedExtractionResult(" ".join(parser.readable_parts), (),
            (_diagnostic("extraction_cancelled", "Extraction was cancelled."),), False, 0, 0)
    byte_offsets = _byte_map(source)
    observations: list[Observation] = []
    context_used = 0
    total = len(parser.candidates)
    for candidate in parser.candidates:
        value = candidate.value
        if candidate.kind in {ObservationKind.LINK, ObservationKind.FORM_CANDIDATE}:
            resolved = _safe_http_url(value, final_url, allow_relative=selected.allow_relative_hrefs)
            if resolved is None:
                continue
            if not selected.allow_external_links and urlsplit(resolved).hostname != urlsplit(final_url).hostname:
                continue
            value = resolved
        if len(value.encode("utf-8")) > selected.max_value_bytes:
            continue
        raw_span = RawSpan(byte_offsets[candidate.start_char], byte_offsets[candidate.end_char])
        context_span = None
        person_name = None
        if candidate.context is not None:
            context_end = candidate.context.end_char or len(source)
            proposed = RawSpan(byte_offsets[candidate.context.start_char], byte_offsets[context_end])
            cost = proposed.end_byte - proposed.start_byte
            if context_used + cost > selected.max_context_bytes:
                continue
            context_used += cost
            context_span = proposed
            if candidate.kind is ObservationKind.PERSON_ASSOCIATED:
                name = " ".join(candidate.context.person_name_parts).strip()
                if not name:
                    # An explicit container without its bounded name cannot assert identity.
                    candidate.kind = ObservationKind.UNKNOWN_CONTACT
                else:
                    person_name = name
        if len(observations) >= selected.max_observations:
            continue
        observations.append(Observation(
            observation_id=_observation_id(artifact, occurrence_id, candidate.kind, raw_span),
            occurrence_id=occurrence_id, kind=candidate.kind, value=value,
            artifact_id=artifact.artifact_id, snapshot_id=artifact.snapshot_id,
            content_digest=artifact.content_digest, input_ref=input_ref,
            source_url=final_url, raw_span=raw_span, context_span=context_span,
            anchor_text=candidate.anchor_text, person_name=person_name,
        ))
    skipped = total - len(observations)
    return ObservedExtractionResult(
        readable_text=" ".join(parser.readable_parts), observations=tuple(observations),
        diagnostics=(), was_truncated=skipped > 0, observations_skipped=skipped,
        total_observations_found=total,
    )


__all__ = ["ArtifactBindingError", "ExecutionState", "ExtractionPolicy",
           "GeneratedHypothesis", "Observation", "ObservationKind",
           "ObservedExtractionResult", "RawSpan", "ReviewState",
           "extract_observations", "resolve_observation_span"]
