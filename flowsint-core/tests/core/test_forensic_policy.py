"""Behavior contracts for forensic data-class research authorization."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Callable, cast
from uuid import UUID, uuid4

import pytest

from flowsint_core.core.forensics import (
    FORENSIC_DATA_CLASS_POLICY_VERSION,
    ForensicDataClass,
    ForensicPolicyDecision,
    ForensicPolicyDenied,
    PolicyAccessRequest,
    PolicyState,
    ResearchGrant,
    ResearchOperation,
    ReviewerAuthority,
    authorize_api_view,
    authorize_artifact_read,
    authorize_collection,
    authorize_connector_read,
    authorize_projection_read,
    authorize_query,
    evaluate_forensic_policy,
    data_class_ancestors,
    require_action_plane_authority,
    require_collection_grant,
)

NOW = datetime(2026, 8, 8, tzinfo=timezone.utc)
SOURCE = "connector:test-destination:test-endpoint"
RIGHTS = frozenset({"permitted"})


_UNSET_UUID = object()


def _request(
    operation: ResearchOperation = ResearchOperation.COLLECTION,
    *,
    data_class: ForensicDataClass = ForensicDataClass.PUBLIC,
    requested_at: datetime = NOW,
    source: str = SOURCE,
    source_rights: frozenset[str] = RIGHTS,
    evidence_envelope_id: UUID | None | object = _UNSET_UUID,
) -> PolicyAccessRequest:
    return PolicyAccessRequest(
        case_reference="case-1964",
        taxonomy_version=FORENSIC_DATA_CLASS_POLICY_VERSION,
        data_class=data_class,
        operation=operation,
        evidence_envelope_id=uuid4()
        if evidence_envelope_id is _UNSET_UUID
        else cast(UUID | None, evidence_envelope_id),
        source=source,
        source_rights=source_rights,
        subject_id="subject:test-operator",
        requested_at=requested_at,
    )


def _decision(
    request: PolicyAccessRequest,
    *,
    data_class: ForensicDataClass | None = None,
    state: PolicyState = PolicyState.ALLOW,
    authorities: frozenset[ReviewerAuthority] = frozenset(
        {ReviewerAuthority.CASE_OWNER}
    ),
    effective_at: datetime = NOW - timedelta(hours=1),
    expires_at: datetime = NOW + timedelta(days=1),
    supersedes: UUID | None = None,
    override_reference: str | None = None,
) -> ForensicPolicyDecision:
    return ForensicPolicyDecision(
        case_reference=cast(str, request.case_reference),
        taxonomy_version=FORENSIC_DATA_CLASS_POLICY_VERSION,
        data_class=data_class or cast(ForensicDataClass, request.data_class),
        operations=frozenset({cast(ResearchOperation, request.operation)}),
        source_scope=frozenset({cast(str, request.source)}),
        source_rights=cast(frozenset[str], request.source_rights),
        effective_at=effective_at,
        expires_at=expires_at,
        reviewer_authorities=authorities,
        rationale="reviewed for case authorization",
        state=state,
        supersedes=supersedes,
        override_reference=override_reference,
    )


def test_ac_107_explicit_decisions_authorize_collection_reveal_and_projection() -> None:
    collection = _request(ResearchOperation.COLLECTION)
    artifact = _request(ResearchOperation.ARTIFACT_READ)
    projection = _request(ResearchOperation.PROJECTION_READ)

    collection_grant = authorize_collection([_decision(collection)], collection)
    artifact_grant = authorize_artifact_read([_decision(artifact)], artifact)
    projection_grant = authorize_projection_read([_decision(projection)], projection)

    assert collection_grant.operation is ResearchOperation.COLLECTION
    assert artifact_grant.operation is ResearchOperation.ARTIFACT_READ
    assert projection_grant.operation is ResearchOperation.PROJECTION_READ


@pytest.mark.parametrize(
    ("operation", "authorizer"),
    [
        (ResearchOperation.COLLECTION, authorize_collection),
        (ResearchOperation.CONNECTOR_READ, authorize_connector_read),
        (ResearchOperation.ARTIFACT_READ, authorize_artifact_read),
        (ResearchOperation.PROJECTION_READ, authorize_projection_read),
        (ResearchOperation.API_VIEW, authorize_api_view),
        (ResearchOperation.QUERY, authorize_query),
    ],
)
def test_every_research_boundary_uses_the_shared_evaluator(
    operation: ResearchOperation,
    authorizer: Callable[
        [list[ForensicPolicyDecision], PolicyAccessRequest], ResearchGrant
    ],
) -> None:
    request = _request(operation)

    grant = authorizer([_decision(request)], request)

    assert grant.operation is operation
    with pytest.raises(ForensicPolicyDenied) as error:
        authorizer([], request)
    assert error.value.code == "forensic_policy_missing_hold"


def test_ac_108_missing_expired_and_conflicting_policy_is_auditable_hold() -> None:
    request = _request()
    missing = evaluate_forensic_policy([], request)
    expired = evaluate_forensic_policy(
        [
            _decision(
                request,
                effective_at=NOW - timedelta(days=2),
                expires_at=NOW - timedelta(days=1),
            )
        ],
        request,
    )
    original = _decision(request)
    first_replacement = _decision(
        request,
        supersedes=original.decision_id,
    )
    second_replacement = _decision(
        request,
        supersedes=original.decision_id,
    )
    conflict = evaluate_forensic_policy(
        [original, first_replacement, second_replacement], request
    )

    assert (missing.state, missing.audit_record.code) == (
        PolicyState.HOLD,
        "forensic_policy_missing_hold",
    )
    assert (expired.state, expired.audit_record.code) == (
        PolicyState.HOLD,
        "forensic_policy_expired_hold",
    )
    assert (conflict.state, conflict.audit_record.code) == (
        PolicyState.HOLD,
        "forensic_policy_conflict_hold",
    )


def test_version_source_and_rights_mismatches_are_redacted_holds() -> None:
    request = _request()
    decision = _decision(request)
    unsupported_version = evaluate_forensic_policy(
        [decision],
        replace(request, taxonomy_version="forensic-data-class/v0"),
    )
    source_mismatch = evaluate_forensic_policy(
        [decision],
        replace(request, source="connector:other-destination:other-endpoint"),
    )
    rights_mismatch = evaluate_forensic_policy(
        [decision],
        replace(request, source_rights=frozenset({"unspecified"})),
    )

    assert (unsupported_version.state, unsupported_version.audit_record.code) == (
        PolicyState.HOLD,
        "forensic_policy_version_mismatch",
    )
    assert (source_mismatch.state, source_mismatch.audit_record.code) == (
        PolicyState.HOLD,
        "forensic_source_scope_mismatch_hold",
    )
    assert (rights_mismatch.state, rights_mismatch.audit_record.code) == (
        PolicyState.HOLD,
        "forensic_source_rights_mismatch_hold",
    )
    assert "other-destination" not in str(source_mismatch.audit_record)
    assert "unspecified" not in str(rights_mismatch.audit_record)


def test_expired_replacement_never_revives_the_superseded_allow() -> None:
    original_request = _request()
    original = _decision(original_request, expires_at=NOW + timedelta(days=10))
    replacement = _decision(
        original_request,
        effective_at=NOW - timedelta(hours=1),
        expires_at=NOW + timedelta(hours=1),
        supersedes=original.decision_id,
    )

    result = evaluate_forensic_policy(
        [original, replacement],
        replace(original_request, requested_at=NOW + timedelta(days=2)),
    )

    assert result.state is PolicyState.HOLD
    assert result.audit_record.code == "forensic_policy_expired_hold"


def test_deny_inherits_but_allow_does_not_escalate_to_a_more_sensitive_class() -> None:
    sensitive = _request(data_class=ForensicDataClass.SENSITIVE_PERSONAL)
    public_deny = _decision(
        sensitive,
        data_class=ForensicDataClass.PUBLIC,
        state=PolicyState.DENY,
    )
    sensitive_allow = _decision(
        sensitive,
        authorities=frozenset(
            {ReviewerAuthority.CASE_OWNER, ReviewerAuthority.COMPLIANCE_REVIEWER}
        ),
    )
    inherited = evaluate_forensic_policy([public_deny, sensitive_allow], sensitive)
    public_allow = _decision(
        _request(data_class=ForensicDataClass.PUBLIC)
    )
    exact_only = evaluate_forensic_policy(
        [public_allow],
        _request(data_class=ForensicDataClass.LICENSED),
    )

    assert inherited.state is PolicyState.DENY
    assert inherited.audit_record.code == "forensic_policy_denied"
    assert exact_only.state is PolicyState.HOLD
    assert exact_only.audit_record.code == "forensic_policy_missing_hold"


@pytest.mark.parametrize(
    ("data_class", "authorities", "state", "override_reference"),
    [
        (
            ForensicDataClass.PERSONAL,
            frozenset(
                {ReviewerAuthority.CASE_OWNER, ReviewerAuthority.PRIVACY_REVIEWER}
            ),
            PolicyState.ALLOW,
            None,
        ),
        (
            ForensicDataClass.SENSITIVE_PERSONAL,
            frozenset({ReviewerAuthority.CASE_OWNER, ReviewerAuthority.PRIVACY_REVIEWER}),
            PolicyState.ALLOW,
            None,
        ),
        (
            ForensicDataClass.RESTRICTED,
            frozenset(
                {ReviewerAuthority.CASE_OWNER, ReviewerAuthority.COMPLIANCE_REVIEWER}
            ),
            PolicyState.ALLOW,
            None,
        ),
        (
            ForensicDataClass.RESTRICTED,
            frozenset(
                {ReviewerAuthority.CASE_OWNER, ReviewerAuthority.COMPLIANCE_REVIEWER}
            ),
            PolicyState.OVERRIDE_ALLOW,
            None,
        ),
        (
            ForensicDataClass.RESTRICTED,
            frozenset(
                {ReviewerAuthority.CASE_OWNER, ReviewerAuthority.COMPLIANCE_REVIEWER}
            ),
            PolicyState.OVERRIDE_ALLOW,
            "override-1964",
        ),
    ],
)
def test_reviewer_thresholds_and_restricted_override_requirements(
    data_class: ForensicDataClass,
    authorities: frozenset[ReviewerAuthority],
    state: PolicyState,
    override_reference: str | None,
) -> None:
    request = _request(data_class=data_class)
    result = evaluate_forensic_policy(
        [
            _decision(
                request,
                state=state,
                authorities=authorities,
                override_reference=override_reference,
            )
        ],
        request,
    )

    if override_reference == "override-1964" or data_class is ForensicDataClass.PERSONAL:
        assert result.allowed is True
    else:
        assert result.state is PolicyState.HOLD


def test_ac_109_grants_are_registered_exact_expiring_and_research_only() -> None:
    request = _request()
    grant = authorize_collection([_decision(request)], request)
    forged = ResearchGrant(
        grant_id=uuid4(),
        case_reference=grant.case_reference,
        taxonomy_version=grant.taxonomy_version,
        data_class=grant.data_class,
        operation=grant.operation,
        evidence_envelope_id=grant.evidence_envelope_id,
        source=grant.source,
        source_rights=grant.source_rights,
        subject_id=grant.subject_id,
        override_reference=grant.override_reference,
        issued_at=grant.issued_at,
        expires_at=grant.expires_at,
        decision_id=grant.decision_id,
    )

    require_collection_grant(grant, request)
    for candidate, candidate_request, code in (
        (forged, request, "forensic_grant_invalid_hold"),
        (replace(grant), request, "forensic_grant_invalid_hold"),
        (
            grant,
            replace(request, source_rights=frozenset({"unspecified"})),
            "forensic_grant_invalid_hold",
        ),
        (
            grant,
            replace(request, requested_at=grant.expires_at),
            "forensic_grant_expired_hold",
        ),
    ):
        with pytest.raises(ForensicPolicyDenied) as error:
            require_collection_grant(candidate, candidate_request)
        assert error.value.code == code

    with pytest.raises(ForensicPolicyDenied) as action_error:
        require_action_plane_authority(grant)
    assert action_error.value.code == "forensic_action_plane_not_authorized"
    assert action_error.value.audit_record.state is PolicyState.DENY


def test_redacted_diagnostics_and_connector_capability_never_authorize() -> None:
    sensitive_source = "connector:customer-private:records"
    request = _request(source=sensitive_source)

    denied = evaluate_forensic_policy([], request)
    with pytest.raises(ForensicPolicyDenied) as error:
        authorize_connector_read([], replace(request, operation=ResearchOperation.CONNECTOR_READ))

    assert denied.state is PolicyState.HOLD
    assert sensitive_source not in str(denied.audit_record)
    assert str(request.evidence_envelope_id) not in str(denied.audit_record)
    assert error.value.code == "forensic_policy_missing_hold"


def test_stricter_authority_alone_satisfies_minimum_threshold() -> None:
    """COMPLIANCE_REVIEWER alone authorizes PUBLIC; CASE_OWNER alone fails PERSONAL."""
    public = _request(data_class=ForensicDataClass.PUBLIC)
    compliance_only = frozenset({ReviewerAuthority.COMPLIANCE_REVIEWER})
    allow = evaluate_forensic_policy(
        [_decision(public, authorities=compliance_only)],
        public,
    )
    assert allow.allowed is True

    privacy_only = frozenset({ReviewerAuthority.PRIVACY_REVIEWER})
    privacy_allow = evaluate_forensic_policy(
        [_decision(public, authorities=privacy_only)],
        public,
    )
    assert privacy_allow.allowed is True

    personal = _request(data_class=ForensicDataClass.PERSONAL)
    owner_only = frozenset({ReviewerAuthority.CASE_OWNER})
    personal_denied = evaluate_forensic_policy(
        [_decision(personal, authorities=owner_only)],
        personal,
    )
    assert personal_denied.state is PolicyState.HOLD
    assert personal_denied.audit_record.code == "forensic_reviewer_authority_insufficient"

    empty_authorities = frozenset[ReviewerAuthority]()
    empty = evaluate_forensic_policy(
        [_decision(public, authorities=empty_authorities)],
        public,
    )
    assert empty.state is PolicyState.HOLD
    assert empty.audit_record.code == "forensic_reviewer_authority_insufficient"


def test_licensed_class_requires_authorization_and_rejects_public_allow() -> None:
    """LICENSED needs its own explicit decision; a PUBLIC allow does not suffice."""
    licensed = _request(data_class=ForensicDataClass.LICENSED)
    public_allow = _decision(licensed, data_class=ForensicDataClass.PUBLIC)

    exact_only = evaluate_forensic_policy([public_allow], licensed)
    assert exact_only.state is PolicyState.HOLD
    assert exact_only.audit_record.code == "forensic_policy_missing_hold"

    licensed_allow = _decision(licensed)
    grant = authorize_collection([licensed_allow], licensed)
    assert grant.data_class is ForensicDataClass.LICENSED


def test_grant_rejects_every_bound_field_substitution() -> None:
    """A registered grant rejects case, class, operation, evidence, source,
    taxonomy, and decision-id substitutions."""
    collection = _request(ResearchOperation.COLLECTION)
    artifact = _request(ResearchOperation.ARTIFACT_READ)
    collection_grant = authorize_collection([_decision(collection)], collection)

    substitutions = (
        (collection_grant, replace(collection, case_reference="case-other")),
        (collection_grant, replace(collection, data_class=ForensicDataClass.LICENSED)),
        (collection_grant, artifact),
        (
            collection_grant,
            replace(collection, evidence_envelope_id=uuid4()),
        ),
        (
            collection_grant,
            replace(collection, source="connector:other:other"),
        ),
        (
            collection_grant,
            replace(collection, taxonomy_version="forensic-data-class/v9"),
        ),
        (
            collection_grant,
            replace(
                collection,
                operation=ResearchOperation.CONNECTOR_READ,
            ),
        ),
    )

    for grant, request in substitutions:
        with pytest.raises(ForensicPolicyDenied) as error:
            require_collection_grant(grant, request)
        assert error.value.code in (
            "forensic_grant_invalid_hold",
            "forensic_grant_invalid_hold",
        )


def test_grant_field_substitution_is_rejected() -> None:
    """Every constructible grant field substitution produces forensic_grant_invalid_hold."""
    request = _request()
    grant = authorize_collection([_decision(request)], request)
    copy = replace(grant)
    assert require_collection_grant(grant, request) is grant

    other_uuid = uuid4()
    for mutated in (
        replace(grant, grant_id=uuid4()),
        replace(grant, case_reference="case-other"),
        replace(grant, data_class=ForensicDataClass.LICENSED),
        replace(grant, operation=ResearchOperation.ARTIFACT_READ),
        replace(grant, evidence_envelope_id=other_uuid),
        replace(grant, source="connector:other:other"),
        replace(grant, source_rights=frozenset({"other"})),
        replace(grant, subject_id="subject:other"),
        replace(grant, issued_at=grant.issued_at + timedelta(hours=1)),
        replace(grant, decision_id=other_uuid),
        replace(grant, override_reference="override-other"),
        replace(grant, expires_at=grant.expires_at + timedelta(days=1)),
    ):
        with pytest.raises(ForensicPolicyDenied) as error:
            require_collection_grant(mutated, request)
        assert error.value.code == "forensic_grant_invalid_hold"

    # An unregistered identical copy is also rejected
    with pytest.raises(ForensicPolicyDenied) as copy_error:
        require_collection_grant(copy, request)
    assert copy_error.value.code == "forensic_grant_invalid_hold"

    # Taxonomy substitution is constructor-level rejection
    with pytest.raises(ValueError, match="unsupported"):
        replace(grant, taxonomy_version="forensic-data-class/v9")

def test_action_plane_rejection_has_deny_state_in_audit() -> None:
    """require_action_plane_authority records DENY, not HOLD."""
    request = _request()
    grant = authorize_collection([_decision(request)], request)

    with pytest.raises(ForensicPolicyDenied) as error:
        require_action_plane_authority(grant)
    assert error.value.audit_record.state is PolicyState.DENY
    assert error.value.code == "forensic_action_plane_not_authorized"

    with pytest.raises(ForensicPolicyDenied) as non_grant_error:
        require_action_plane_authority("not-a-grant")
    assert non_grant_error.value.audit_record.state is PolicyState.DENY

def test_null_evidence_allowed_for_collection_but_held_for_read() -> None:
    """COLLECTION with null evidence issues a grant; ARTIFACT_READ with null evidence is held."""
    null_evidence_request = _request(
        ResearchOperation.COLLECTION,
        evidence_envelope_id=None,
    )
    decision = _decision(null_evidence_request)
    allow = evaluate_forensic_policy([decision], null_evidence_request)
    assert allow.allowed is True
    grant = authorize_collection([decision], null_evidence_request)
    assert grant.evidence_envelope_id is None
    assert require_collection_grant(grant, null_evidence_request) is grant

    null_read_request = _request(
        ResearchOperation.ARTIFACT_READ,
        evidence_envelope_id=None,
    )
    held = evaluate_forensic_policy([_decision(null_read_request)], null_read_request)
    assert held.state is PolicyState.HOLD
    assert held.audit_record.code == "forensic_policy_missing_hold"



def test_data_class_ancestors_are_ordered_and_exclude_the_terminal_class() -> None:
    assert data_class_ancestors(ForensicDataClass.PUBLIC) == ()
    assert data_class_ancestors(ForensicDataClass.RESTRICTED) == (
        ForensicDataClass.PUBLIC,
        ForensicDataClass.LICENSED,
        ForensicDataClass.PERSONAL,
        ForensicDataClass.SENSITIVE_PERSONAL,
    )


def test_data_class_ancestors_rejects_unknown_class() -> None:
    with pytest.raises(TypeError, match="data_class"):
        data_class_ancestors("restricted")  # type: ignore[arg-type]


def test_whitespace_source_is_an_auditable_hold() -> None:
    request = _request(source=" \t")

    result = evaluate_forensic_policy([], request)

    assert result.state is PolicyState.HOLD
    assert result.audit_record.code == "forensic_policy_missing_hold"
    assert request.source is not None
    assert request.source not in str(result.audit_record)


def test_policy_request_rejects_empty_source_rights() -> None:
    with pytest.raises(ValueError, match="source_rights"):
        _request(source_rights=frozenset())


def test_policy_request_rejects_naive_requested_at() -> None:
    with pytest.raises(TypeError, match="timezone-aware"):
        _request(requested_at=datetime(2026, 8, 8))


def test_research_grant_rejects_blank_override_reference() -> None:
    request = _request()
    grant = authorize_collection([_decision(request)], request)

    with pytest.raises(ValueError, match="override_reference"):
        replace(grant, override_reference=" ")


def test_hold_precedes_an_exact_allow_for_the_same_boundary() -> None:
    request = _request()
    allow = _decision(request)
    hold = _decision(request, state=PolicyState.HOLD)

    result = evaluate_forensic_policy([allow, hold], request)

    assert result.state is PolicyState.HOLD
    assert result.audit_record.code == "forensic_policy_held"
    assert result.decision is hold


def test_persisted_source_rights_mutation_invalidates_collection_grant() -> None:
    request = _request()
    grant = authorize_collection([_decision(request)], request)

    with pytest.raises(ForensicPolicyDenied) as error:
        require_collection_grant(
            grant,
            request,
            persisted_source=SOURCE,
            persisted_source_rights=frozenset({"unspecified"}),
        )

    assert error.value.code == "forensic_grant_invalid_hold"
    assert "unspecified" not in str(error.value.audit_record)


def test_collection_and_read_holds_redact_mismatched_provenance() -> None:
    collection = _request(source="connector:private-collection:records")
    artifact = _request(
        ResearchOperation.ARTIFACT_READ,
        source_rights=frozenset({"private-read-terms"}),
    )

    with pytest.raises(ForensicPolicyDenied) as collection_error:
        authorize_collection([_decision(_request())], collection)
    with pytest.raises(ForensicPolicyDenied) as artifact_error:
        authorize_artifact_read([_decision(_request(ResearchOperation.ARTIFACT_READ))], artifact)

    assert collection_error.value.code == "forensic_source_scope_mismatch_hold"
    assert artifact_error.value.code == "forensic_source_rights_mismatch_hold"
    assert "private-collection" not in str(collection_error.value.audit_record)
    assert "private-read-terms" not in str(artifact_error.value.audit_record)