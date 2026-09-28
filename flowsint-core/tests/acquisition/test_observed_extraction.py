from __future__ import annotations

import hashlib
from datetime import datetime, timezone

import pytest

from flowsint_execution.acquisition import ArtifactReference
from flowsint_execution.observed_extraction import (
    ArtifactBindingError, ExtractionPolicy, GeneratedHypothesis, Observation,
    ObservationKind, ObservedExtractionResult, RawSpan, extract_observations,
    resolve_observation_span,
)


def artifact(body: bytes, final_url: str = "https://example.test/team/index.html") -> ArtifactReference:
    digest = hashlib.sha256(body).hexdigest()
    now = datetime(2025, 1, 1, tzinfo=timezone.utc)
    return ArtifactReference(
        artifact_id="artifact-test", snapshot_id="snapshot-test",
        content_digest=digest, byte_length=len(body), locator=f"sha256:{digest}",
        source_family="web", origin="fixture", retrieved_at=now,
        requested_url=final_url, final_url=final_url,
    )


def extract(body: bytes, **kwargs):
    url = kwargs.pop("final_url", "https://example.test/team/index.html")
    return extract_observations(
        body, artifact=artifact(body, url), occurrence_id="occurrence-test",
        input_ref="b" * 64, final_url=url, **kwargs,
    )


def contacts(result: ObservedExtractionResult):
    kinds = {ObservationKind.GENERAL_INBOX, ObservationKind.ROLE_INBOX,
             ObservationKind.PERSON_ASSOCIATED, ObservationKind.UNKNOWN_CONTACT,
             ObservationKind.PHONE_CONTACT}
    return [o for o in result.observations if o.kind in kinds]


def test_exact_duplicate_and_script_shadow_spans_are_occurrence_owned():
    body = (b'<script>const x="same@example.test"</script>'
            b'<p>same@example.test and same@example.test</p>')
    found = [o for o in contacts(extract(body)) if o.value == "same@example.test"]
    first = body.index(b"same@example.test", body.index(b"</script>"))
    starts = [first, body.index(b"same@example.test", first + 1)]
    assert [o.raw_span.start_byte for o in found] == starts
    assert len({o.observation_id for o in found}) == 2
    assert all(body[o.raw_span.start_byte:o.raw_span.end_byte] == b"same@example.test" for o in found)


def test_attribute_positions_are_local_and_entities_preserve_raw_source():
    body = (b'<!-- href="/wrong" --><a data-x="1 > 0" href="/a?x=1&amp;y=2">One &amp; Two</a>'
            b'<a href="/a?x=1&amp;y=2">Again</a>')
    links = [o for o in extract(body).observations if o.kind is ObservationKind.LINK]
    assert [o.value for o in links] == ["https://example.test/a?x=1&y=2"] * 2
    assert [body[o.raw_span.start_byte:o.raw_span.end_byte] for o in links] == [b"/a?x=1&amp;y=2"] * 2
    assert links[0].raw_span != links[1].raw_span
    assert links[0].anchor_text == "One & Two"


def test_malformed_unclosed_quote_does_not_fabricate_a_span():
    body = b'<a href="/broken><span>visible@example.test</span><p>after@example.test</p>'
    result = extract(body)
    assert not [o for o in result.observations if o.kind is ObservationKind.LINK]
    assert all(resolve_observation_span(body, artifact(body), o.raw_span)[0] for o in result.observations)


def test_explicit_cards_bind_separate_people_but_generic_inboxes_never_bind():
    body = b"""
    <section class='person'><h2>Ada Example</h2><p>Lead ada@example.test support@example.test</p></section>
    <section class='person'><h2>Bo Example</h2><p>Lead bo@example.test info@example.test</p></section>
    <h1>Chief Executive Cara Example</h1><footer>cara@example.test press@example.test</footer>
    """
    by_value = {o.value: o for o in contacts(extract(body))}
    assert (by_value["ada@example.test"].kind, by_value["ada@example.test"].person_name) == (ObservationKind.PERSON_ASSOCIATED, "Ada Example")
    assert (by_value["bo@example.test"].kind, by_value["bo@example.test"].person_name) == (ObservationKind.PERSON_ASSOCIATED, "Bo Example")
    assert by_value["support@example.test"].kind is ObservationKind.ROLE_INBOX
    assert by_value["support@example.test"].person_name is None
    assert by_value["info@example.test"].kind is ObservationKind.GENERAL_INBOX
    assert by_value["info@example.test"].person_name is None
    assert by_value["cara@example.test"].kind is ObservationKind.UNKNOWN_CONTACT
    assert by_value["cara@example.test"].person_name is None
    assert by_value["press@example.test"].kind is ObservationKind.ROLE_INBOX


def test_explicit_name_label_can_bind_a_person_without_page_wide_inference():
    body = (b'<article class="profile"><span class="person-name">Name: Dee Example</span>'
            b'<p>dee@example.test</p></article>')
    observation = next(o for o in contacts(extract(body)) if o.value == "dee@example.test")
    assert observation.kind is ObservationKind.PERSON_ASSOCIATED
    assert observation.person_name == "Dee Example"


def test_mailto_tel_form_and_safe_links_have_supporting_attribute_spans():
    body = (b'<section class="person"><h2>Ada Example</h2>'
            b'<a href="mailto:ada@example.test?subject=Hi">Email</a>'
            b'<a href="tel:+1-202-555-0101">Call</a></section>'
            b'<form action="/contact?department=research" method="post"></form>'
            b'<a href="javascript:alert(1)">bad</a>'
            b'<a href="https://user:secret@outside.test/x">credential</a>'
            b'<a href="../directory?page=2">page</a>')
    result = extract(body)
    email = next(o for o in result.observations if o.kind is ObservationKind.PERSON_ASSOCIATED)
    phone = next(o for o in result.observations if o.kind is ObservationKind.PHONE_CONTACT)
    form = next(o for o in result.observations if o.kind is ObservationKind.FORM_CANDIDATE)
    assert body[email.raw_span.start_byte:email.raw_span.end_byte].startswith(b"mailto:")
    assert body[phone.raw_span.start_byte:phone.raw_span.end_byte].startswith(b"tel:")
    assert body[form.raw_span.start_byte:form.raw_span.end_byte] == b"/contact?department=research"
    assert form.value == "https://example.test/contact?department=research"
    assert [o.value for o in result.observations if o.kind is ObservationKind.LINK] == ["https://example.test/directory?page=2"]


def test_digest_length_final_url_and_utf8_are_checked():
    body = b"<p>ok@example.test</p>"
    ref = artifact(body)
    with pytest.raises(ArtifactBindingError, match="digest|length"):
        extract_observations(body + b"x", artifact=ref, occurrence_id="o", input_ref="i", final_url=ref.final_url)
    with pytest.raises(ArtifactBindingError, match="final URL"):
        extract_observations(body, artifact=ref, occurrence_id="o", input_ref="i", final_url="https://other.test/")
    unbound_ref = artifact(body).model_copy(update={"requested_url": None, "final_url": None})
    with pytest.raises(ValueError, match="without credentials"):
        extract_observations(body, artifact=unbound_ref, occurrence_id="o", input_ref="i", final_url="https://user:secret@example.test/")
    bad = b"<p>\xff</p>"
    result = extract_observations(bad, artifact=artifact(bad), occurrence_id="o", input_ref="i", final_url="https://example.test/team/index.html")
    assert result.observations == ()
    assert [(d.code, d.safe_message) for d in result.diagnostics] == [("decode_error", "Retained source is not valid UTF-8.")]


def test_resolve_span_authenticates_artifact_before_returning_bytes():
    body = "Café".encode()
    assert resolve_observation_span(body, artifact(body), RawSpan(0, len(body))) == (body, "Café")
    with pytest.raises(ArtifactBindingError):
        resolve_observation_span(body + b"!", artifact(body), RawSpan(0, len(body)))


@pytest.mark.parametrize("field,value", [("max_body_bytes", 0), ("max_observations", True),
    ("max_value_bytes", -1), ("max_context_bytes", 0), ("max_diagnostics", False)])
def test_policy_limits_are_strict_positive_integers(field, value):
    with pytest.raises((TypeError, ValueError)):
        ExtractionPolicy(**{field: value})


def test_all_result_limits_are_real_and_report_skips():
    body = b"<p>a@example.test b@example.test c@example.test</p>"
    result = extract(body, policy=ExtractionPolicy(max_body_bytes=1024, max_observations=2,
        max_value_bytes=64, max_context_bytes=20, max_diagnostics=1))
    assert len(result.observations) <= 2
    assert sum(o.context_span.end_byte - o.context_span.start_byte for o in result.observations if o.context_span) <= 20
    assert result.total_observations_found > len(result.observations)
    assert result.observations_skipped == result.total_observations_found - len(result.observations)
    assert result.was_truncated
    with pytest.raises(ValueError, match="body limit"):
        extract(b"x" * 10, policy=ExtractionPolicy(max_body_bytes=9))


def test_models_reject_string_enums_and_generated_promotion():
    body = b"x"
    ref = artifact(body)
    base = dict(observation_id="obs-x", occurrence_id="occ-x", kind=ObservationKind.GENERAL_TEXT,
        value="x", artifact_id=ref.artifact_id, snapshot_id=ref.snapshot_id,
        content_digest=ref.content_digest, input_ref="b" * 64, source_url=ref.final_url,
        raw_span=RawSpan(0, 1))
    with pytest.raises(TypeError):
        Observation(**{**base, "kind": "observed_general_text"})
    with pytest.raises(TypeError):
        Observation(**{**base, "review_state": "accepted"})
    with pytest.raises(ValueError):
        Observation(**{**base, "kind": ObservationKind.GENERATED_HYPOTHESIS})
    hypothesis = GeneratedHypothesis.create(value="ada@example.test", basis="explicit alias transformation", source_observation_ids=("obs-x",))
    with pytest.raises(TypeError):
        ObservedExtractionResult(readable_text="x", observations=(hypothesis,), diagnostics=(),
            was_truncated=False, observations_skipped=0, total_observations_found=1)


def test_live_fixture_exercises_expected_acceptance_surface():
    with open("/tmp/def45-live-fixture.html", "rb") as fixture:
        body = fixture.read()
    result = extract(body)
    values = {o.value: o for o in result.observations}
    assert "hidden@directory.test" not in values
    assert any(o.value == "ada@directory.test" and o.person_name == "Ada Example" for o in result.observations)
    assert any(o.value == "bo@directory.test" and o.person_name == "Bo Example" for o in result.observations)
    assert values["info@directory.test"].kind is ObservationKind.GENERAL_INBOX
    assert values["press@directory.test"].kind is ObservationKind.ROLE_INBOX
    assert "Café & source proof." in result.readable_text
    assert "https://example.test/directory?page=1" in values
    assert "https://example.test/contact?department=research" in values


def test_cancellation_is_cooperative_and_bounded():
    calls = 0
    def cancelled():
        nonlocal calls
        calls += 1
        return calls >= 2
    result = extract(b"<p>a@example.test</p>", cancelled=cancelled)
    assert result.observations == ()
    assert result.diagnostics[0].code == "extraction_cancelled"
