from __future__ import annotations

import dataclasses
import hashlib
from datetime import datetime, timezone
from pathlib import Path

import pytest

from flowsint_execution.acquisition import ArtifactReference
from flowsint_execution.observed_extraction import (
    ArtifactBindingError, ExtractionPolicy, GeneratedHypothesis, Observation,
    ObservationKind, ObservedExtractionResult, RawSpan, extract_observations,
    parse_observed_extraction_metadata, resolve_observation_span,
    serialize_observed_extraction_metadata,
)
from flowsint_execution.url_policy import disclose_url


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
    assert all(item.person_name is None for item in result.observations)


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


def test_resolver_requires_canonical_observation_ownership():
    body = b"alpha@example.test beta@example.test"
    result = extract(body)
    observation = next(item for item in result.observations if item.value == "beta@example.test")
    resolved = resolve_observation_span(
        body, artifact(body), observation,
        expected_occurrence_id="occurrence-test", expected_input_ref="b" * 64,
    )
    assert resolved.raw == b"beta@example.test"
    assert resolved.text == "beta@example.test"
    altered = dataclasses.replace(observation, value="alpha@example.test")
    with pytest.raises(ArtifactBindingError, match="canonical"):
        resolve_observation_span(body, artifact(body), altered,
            expected_occurrence_id="occurrence-test", expected_input_ref="b" * 64)
    with pytest.raises(ArtifactBindingError):
        resolve_observation_span(body + b"!", artifact(body), observation,
            expected_occurrence_id="occurrence-test", expected_input_ref="b" * 64)


def test_person_attribution_is_temporal_and_segment_bounded():
    body = (b'<section class="person"><p>first@example.test</p><h2>Ada One</h2>'
            b'<p>ada@example.test</p><h2>Bo Two</h2><p>bo@example.test</p></section>')
    found = {item.value: item for item in contacts(extract(body))}
    assert found["first@example.test"].kind is ObservationKind.UNKNOWN_CONTACT
    assert found["first@example.test"].person_name is None
    assert found["ada@example.test"].person_name == "Ada One"
    assert found["bo@example.test"].person_name == "Bo Two"
    ada_context = body[found["ada@example.test"].context_span.start_byte:
                       found["ada@example.test"].context_span.end_byte]
    assert b"Ada One" in ada_context and b"Bo Two" not in ada_context


def test_malformed_person_nesting_and_generic_cards_are_conservative():
    body = (b'<section class="person"><h2>Ada One</h2><div><h2>Bo Two</section>'
            b'<p>unknown@example.test</p><div class="inbox"><h2>Inbox Team</h2>'
            b'<p>named@example.test</p></div>')
    found = {item.value: item for item in contacts(extract(body))}
    assert found["unknown@example.test"].person_name is None
    assert found["named@example.test"].person_name is None
    assert found["named@example.test"].kind is ObservationKind.UNKNOWN_CONTACT


@pytest.mark.parametrize("args", [
    ("", "value", "basis", ("obs",)), ("id", "", "basis", ("obs",)),
    ("id", "value", "", ("obs",)), ("id", "value", "basis", ()),
])
def test_hypothesis_constructor_enforces_factory_invariants(args):
    with pytest.raises(ValueError):
        GeneratedHypothesis(*args)


@pytest.mark.parametrize("name", [
    "client_secret", "CLIENT-SECRET", "auth_token", "passwd", "pwd",
    "session_id", "x-signature", "oauth.code",
])
def test_url_disclosure_removes_credential_like_queries(name):
    disclosed = disclose_url(f"https://example.test/x?{name}=fixture-secret&view=full")
    assert "fixture-secret" not in disclosed
    assert disclosed == "https://example.test/x"


def test_url_disclosure_preserves_benign_meaningful_queries():
    assert disclose_url("https://example.test/x?page=2&view=full&edition=2026&department=research") == (
        "https://example.test/x?page=2&view=full&edition=2026&department=research"
    )


def test_candidate_urls_disclose_no_secret_but_keep_exact_supporting_span():
    body = (b'<a href="/next?client_secret=fixture-secret">next</a>'
            b'<form action="/submit?auth_token=fixture-secret"></form>')
    result = extract(body)
    candidates = [item for item in result.observations if item.kind in {
        ObservationKind.LINK, ObservationKind.FORM_CANDIDATE}]
    assert [item.value for item in candidates] == [
        "https://example.test/next", "https://example.test/submit"]
    assert all("fixture-secret" not in item.value for item in candidates)
    assert all(b"fixture-secret" in body[item.raw_span.start_byte:item.raw_span.end_byte]
               for item in candidates)


def test_observation_metadata_round_trips_actual_models_and_rejects_mutation():
    result = extract(b'<section class="person"><h2>Ada One</h2><p>ada@example.test</p></section>')
    metadata = serialize_observed_extraction_metadata(result)
    restored = parse_observed_extraction_metadata(metadata)
    assert restored == result
    assert type(restored.observations[0]) is Observation
    unknown = metadata.model_copy(update={"format_version": "observed-extraction/2.0"})
    with pytest.raises(ValueError, match="version"):
        parse_observed_extraction_metadata(unknown)
    payload = dict(metadata.payload)
    payload["unexpected"] = True
    with pytest.raises(ValueError):
        parse_observed_extraction_metadata(metadata.model_copy(update={"payload": payload}))


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


LIVE_FIXTURE = Path(__file__).parent / "fixtures" / "def45-live-fixture.html"
# Same bytes as docs/audits/DEF-45/raw/scripts/def45-live-fixture.html (DEF-45 checksums.json).
LIVE_FIXTURE_SHA256 = "ac756ce1480ade18ed745adb467b169861b616aee501de68312cb1319c76843e"


def test_live_fixture_exercises_expected_acceptance_surface():
    body = LIVE_FIXTURE.read_bytes()
    assert hashlib.sha256(body).hexdigest() == LIVE_FIXTURE_SHA256
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
