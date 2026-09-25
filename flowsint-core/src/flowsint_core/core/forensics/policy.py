"""Fail-closed forensic data-class authorization for research boundaries."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import cast
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from threading import Lock
from uuid import UUID, uuid4
from weakref import WeakValueDictionary

FORENSIC_DATA_CLASS_POLICY_VERSION = "forensic-data-class/v1"


class ForensicDataClass(StrEnum):
    """Sensitivity classes, ordered from least to most restrictive."""

    PUBLIC = "public"
    LICENSED = "licensed"
    PERSONAL = "personal"
    SENSITIVE_PERSONAL = "sensitive_personal"
    RESTRICTED = "restricted"


class ResearchOperation(StrEnum):
    """Read-only research boundaries governed by this policy."""

    COLLECTION = "collection"
    CONNECTOR_READ = "connector_read"
    ARTIFACT_READ = "artifact_read"
    PROJECTION_READ = "projection_read"
    API_VIEW = "api_view"
    QUERY = "query"


class ReviewerAuthority(StrEnum):
    """Review authorities that may approve a data-class decision."""

    CASE_OWNER = "case_owner"
    PRIVACY_REVIEWER = "privacy_reviewer"
    COMPLIANCE_REVIEWER = "compliance_reviewer"


class PolicyState(StrEnum):
    """An immutable policy decision's disposition."""

    ALLOW = "allow"
    DENY = "deny"
    HOLD = "hold"
    OVERRIDE_ALLOW = "override_allow"


@dataclass(frozen=True, slots=True)
class ForensicPolicyDecision:
    """One immutable, time-bound authorization decision for a forensic case."""

    case_reference: str
    taxonomy_version: str
    data_class: ForensicDataClass
    operations: frozenset[ResearchOperation]
    source_scope: frozenset[str]
    source_rights: frozenset[str]
    effective_at: datetime
    expires_at: datetime
    reviewer_authorities: frozenset[ReviewerAuthority]
    rationale: str
    state: PolicyState
    supersedes: UUID | None = None
    override_reference: str | None = None
    decision_id: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        _require_nonempty_text(self.case_reference, "case_reference")
        if self.taxonomy_version != FORENSIC_DATA_CLASS_POLICY_VERSION:
            raise ValueError("taxonomy_version is unsupported")
        if not isinstance(self.data_class, ForensicDataClass):
            raise TypeError("data_class must be a ForensicDataClass")
        _require_enum_set(self.operations, ResearchOperation, "operations")
        _require_text_set(self.source_scope, "source_scope")
        _require_text_set(self.source_rights, "source_rights")
        _require_aware_datetime(self.effective_at, "effective_at")
        _require_aware_datetime(self.expires_at, "expires_at")
        if self.expires_at <= self.effective_at:
            raise ValueError("expires_at must be after effective_at")
        _require_enum_set(
            self.reviewer_authorities,
            ReviewerAuthority,
            "reviewer_authorities",
            allow_empty=True,
        )
        _require_nonempty_text(self.rationale, "rationale")
        if not isinstance(self.state, PolicyState):
            raise TypeError("state must be a PolicyState")
        if self.supersedes is not None and not isinstance(self.supersedes, UUID):
            raise TypeError("supersedes must be a UUID or None")
        if self.override_reference is not None:
            _require_nonempty_text(self.override_reference, "override_reference")
        if not isinstance(self.decision_id, UUID):
            raise TypeError("decision_id must be a UUID")


@dataclass(frozen=True, slots=True)
class PolicyAccessRequest:
    """A policy subject evaluated before a read-only research operation."""

    case_reference: str | None
    taxonomy_version: str | None
    data_class: ForensicDataClass | None
    operation: ResearchOperation | None
    evidence_envelope_id: UUID | None
    source: str | None
    source_rights: frozenset[str] | None
    subject_id: str | None
    requested_at: datetime

    def __post_init__(self) -> None:
        if self.case_reference is not None and not isinstance(self.case_reference, str):
            raise TypeError("case_reference must be a string or None")
        if self.taxonomy_version is not None and not isinstance(
            self.taxonomy_version, str
        ):
            raise TypeError("taxonomy_version must be a string or None")
        if self.data_class is not None and not isinstance(
            self.data_class, ForensicDataClass
        ):
            raise TypeError("data_class must be a ForensicDataClass or None")
        if self.operation is not None and not isinstance(
            self.operation, ResearchOperation
        ):
            raise TypeError("operation must be a ResearchOperation or None")
        if self.evidence_envelope_id is not None and not isinstance(
            self.evidence_envelope_id, UUID
        ):
            raise TypeError("evidence_envelope_id must be a UUID or None")
        if self.source is not None and not isinstance(self.source, str):
            raise TypeError("source must be a string or None")
        if self.source_rights is not None:
            _require_text_set(self.source_rights, "source_rights")
        if self.subject_id is not None and not isinstance(self.subject_id, str):
            raise TypeError("subject_id must be a string or None")
        _require_aware_datetime(self.requested_at, "requested_at")


@dataclass(frozen=True, slots=True)
class PolicyAuditRecord:
    """Redacted, machine-readable outcome of one policy evaluation."""

    case_reference: str | None
    data_class: ForensicDataClass | None
    operation: ResearchOperation | None
    state: PolicyState
    code: str
    taxonomy_version: str | None
    subject_id: str | None
    evaluated_at: datetime


@dataclass(frozen=True, slots=True)
class PolicyEvaluation:
    """A deterministic policy result with a redacted audit record."""

    state: PolicyState
    audit_record: PolicyAuditRecord
    decision: ForensicPolicyDecision | None = None

    @property
    def allowed(self) -> bool:
        return self.state in {PolicyState.ALLOW, PolicyState.OVERRIDE_ALLOW}


class ForensicPolicyDenied(RuntimeError):
    """Stable, redacted policy denial safe for boundary propagation."""

    def __init__(self, audit_record: PolicyAuditRecord) -> None:
        super().__init__("Forensic research authorization is unavailable.")
        self.audit_record = audit_record
        self.code = audit_record.code
        self.safe_message = "Forensic research authorization is unavailable."


@dataclass(frozen=True, slots=True, weakref_slot=True)
class ResearchGrant:
    """An identity-registered, exact-scope research capability."""

    grant_id: UUID
    case_reference: str
    taxonomy_version: str
    data_class: ForensicDataClass
    operation: ResearchOperation
    evidence_envelope_id: UUID | None
    source: str
    source_rights: frozenset[str]
    subject_id: str
    override_reference: str | None
    issued_at: datetime
    expires_at: datetime
    decision_id: UUID
    def __post_init__(self) -> None:
        if not isinstance(self.grant_id, UUID):
            raise TypeError("grant_id must be a UUID")
        _require_nonempty_text(self.case_reference, "case_reference")
        if self.taxonomy_version != FORENSIC_DATA_CLASS_POLICY_VERSION:
            raise ValueError("taxonomy_version is unsupported")
        if not isinstance(self.data_class, ForensicDataClass):
            raise TypeError("data_class must be a ForensicDataClass")
        if not isinstance(self.operation, ResearchOperation):
            raise TypeError("operation must be a ResearchOperation")
        if self.evidence_envelope_id is not None and not isinstance(
            self.evidence_envelope_id, UUID
        ):
            raise TypeError("evidence_envelope_id must be a UUID or None")
        _require_nonempty_text(self.source, "source")
        _require_text_set(self.source_rights, "source_rights")
        _require_nonempty_text(self.subject_id, "subject_id")
        if self.override_reference is not None:
            _require_nonempty_text(self.override_reference, "override_reference")
        _require_aware_datetime(self.issued_at, "issued_at")
        _require_aware_datetime(self.expires_at, "expires_at")
        if self.expires_at <= self.issued_at:
            raise ValueError("expires_at must be after issued_at")
        if not isinstance(self.decision_id, UUID):
            raise TypeError("decision_id must be a UUID")


_REGISTERED_GRANTS: WeakValueDictionary[UUID, ResearchGrant] = WeakValueDictionary()
_REGISTERED_GRANTS_LOCK = Lock()


def data_class_ancestors(data_class: ForensicDataClass) -> tuple[ForensicDataClass, ...]:
    """Return the ancestor classes whose DENY and HOLD rules inherit downward."""
    if not isinstance(data_class, ForensicDataClass):
        raise TypeError("data_class must be a ForensicDataClass")
    classes = tuple(ForensicDataClass)
    return classes[: classes.index(data_class)]


def evaluate_forensic_policy(
    decisions: Sequence[ForensicPolicyDecision], request: PolicyAccessRequest | object
) -> PolicyEvaluation:
    """Evaluate all research boundaries through one fail-closed evaluator."""
    if not isinstance(request, PolicyAccessRequest):
        return _hold(None, None, None, None, None, "forensic_policy_missing_hold")
    missing_code = _request_missing_code(request)
    if missing_code is not None:
        return _hold_for_request(request, missing_code)
    if request.taxonomy_version != FORENSIC_DATA_CLASS_POLICY_VERSION:
        return _hold_for_request(request, "forensic_policy_version_mismatch")
    if not _decisions_are_valid(decisions):
        return _hold_for_request(request, "forensic_policy_missing_hold")
    case_decisions = tuple(
        decision for decision in decisions if decision.case_reference == request.case_reference
    )
    if not case_decisions:
        return _hold_for_request(request, "forensic_policy_missing_hold")
    if _has_supersession_conflict(case_decisions):
        return _hold_for_request(request, "forensic_policy_conflict_hold")

    unsuperseded = _without_superseded_decisions(case_decisions)
    matching_shape = tuple(
        decision
        for decision in unsuperseded
        if decision.data_class
        in data_class_ancestors(cast(ForensicDataClass, request.data_class))
        + (cast(ForensicDataClass, request.data_class),)
        and request.operation in decision.operations
    )
    if not matching_shape:
        return _hold_for_request(request, "forensic_policy_missing_hold")
    versioned = tuple(
        decision
        for decision in matching_shape
        if decision.taxonomy_version == request.taxonomy_version
    )
    if not versioned:
        return _hold_for_request(request, "forensic_policy_version_mismatch")
    scoped = tuple(
        decision for decision in versioned if request.source in decision.source_scope
    )
    if not scoped:
        return _hold_for_request(request, "forensic_source_scope_mismatch_hold")
    rights_bound = tuple(
        decision for decision in scoped if request.source_rights == decision.source_rights
    )
    if not rights_bound:
        return _hold_for_request(request, "forensic_source_rights_mismatch_hold")
    active = tuple(
        decision
        for decision in rights_bound
        if decision.effective_at <= request.requested_at < decision.expires_at
    )
    if not active:
        return _hold_for_request(request, "forensic_policy_expired_hold")

    inherited = data_class_ancestors(
        cast(ForensicDataClass, request.data_class)
    ) + (cast(ForensicDataClass, request.data_class),)
    denials = tuple(
        decision
        for decision in active
        if decision.state is PolicyState.DENY and decision.data_class in inherited
    )
    if denials:
        return _evaluation(PolicyState.DENY, request, "forensic_policy_denied", denials[0])
    holds = tuple(
        decision
        for decision in active
        if decision.state is PolicyState.HOLD and decision.data_class in inherited
    )
    if holds:
        return _evaluation(PolicyState.HOLD, request, "forensic_policy_held", holds[0])

    exact_allows = tuple(
        decision
        for decision in active
        if decision.data_class is request.data_class
        and decision.state is PolicyState.ALLOW
    )
    exact_overrides = tuple(
        decision
        for decision in active
        if decision.data_class is request.data_class
        and decision.state is PolicyState.OVERRIDE_ALLOW
    )
    candidates = (
        exact_overrides
        if request.data_class is ForensicDataClass.RESTRICTED
        else exact_allows
    )
    if not candidates:
        return _hold_for_request(request, "forensic_policy_missing_hold")
    approved = tuple(
        decision for decision in candidates if _reviewer_threshold_met(decision)
    )
    if not approved:
        return _hold_for_request(request, "forensic_reviewer_authority_insufficient")
    if request.data_class is ForensicDataClass.RESTRICTED:
        with_reference = tuple(
            decision for decision in approved if decision.override_reference is not None
        )
        if not with_reference:
            return _hold_for_request(request, "forensic_override_invalid_hold")
        return _evaluation(
            PolicyState.OVERRIDE_ALLOW,
            request,
            "forensic_policy_allowed",
            with_reference[0],
        )
    return _evaluation(PolicyState.ALLOW, request, "forensic_policy_allowed", approved[0])


def authorize_collection(
    decisions: Sequence[ForensicPolicyDecision], request: PolicyAccessRequest
) -> ResearchGrant:
    """Evaluate and issue an identity-registered collection capability."""
    return _authorize_research_operation(decisions, request, ResearchOperation.COLLECTION)


def authorize_connector_read(
    decisions: Sequence[ForensicPolicyDecision], request: PolicyAccessRequest
) -> ResearchGrant:
    """Evaluate and issue an identity-registered connector-read capability."""
    return _authorize_research_operation(decisions, request, ResearchOperation.CONNECTOR_READ)


def authorize_artifact_read(
    decisions: Sequence[ForensicPolicyDecision], request: PolicyAccessRequest
) -> ResearchGrant:
    """Evaluate and issue an identity-registered artifact-read capability."""
    return _authorize_research_operation(decisions, request, ResearchOperation.ARTIFACT_READ)


def authorize_projection_read(
    decisions: Sequence[ForensicPolicyDecision], request: PolicyAccessRequest
) -> ResearchGrant:
    """Evaluate and issue an identity-registered projection-read capability."""
    return _authorize_research_operation(decisions, request, ResearchOperation.PROJECTION_READ)


def authorize_api_view(
    decisions: Sequence[ForensicPolicyDecision], request: PolicyAccessRequest
) -> ResearchGrant:
    """Evaluate and issue an identity-registered API-view capability."""
    return _authorize_research_operation(decisions, request, ResearchOperation.API_VIEW)


def authorize_query(
    decisions: Sequence[ForensicPolicyDecision], request: PolicyAccessRequest
) -> ResearchGrant:
    """Evaluate and issue an identity-registered query capability."""
    return _authorize_research_operation(decisions, request, ResearchOperation.QUERY)


def require_collection_grant(
    grant: ResearchGrant | object,
    request: PolicyAccessRequest,
    *,
    persisted_source: str | None = None,
    persisted_source_rights: frozenset[str] | None = None,
) -> ResearchGrant:
    """Require an exact, live collection grant before or after a ledger read."""
    return _require_research_grant(
        grant,
        request,
        ResearchOperation.COLLECTION,
        persisted_source=persisted_source,
        persisted_source_rights=persisted_source_rights,
    )

def require_action_plane_authority(grant: ResearchGrant | object) -> None:
    """Reject research-only grants at the action-plane boundary."""
    if isinstance(grant, ResearchGrant):
        audit = _audit(
            grant.case_reference,
            grant.data_class,
            grant.operation,
            PolicyState.DENY,
            "forensic_action_plane_not_authorized",
            grant.taxonomy_version,
            grant.subject_id,
            datetime.now(timezone.utc),
        )
    else:
        audit = _audit(
            None,
            None,
            None,
            PolicyState.DENY,
            "forensic_action_plane_not_authorized",
            None,
            None,
            datetime.now(timezone.utc),
        )
    raise ForensicPolicyDenied(audit)


def _authorize_research_operation(
    decisions: Sequence[ForensicPolicyDecision],
    request: PolicyAccessRequest,
    operation: ResearchOperation,
) -> ResearchGrant:
    if not isinstance(request, PolicyAccessRequest) or request.operation is not operation:
        audit = _audit(
            request.case_reference if isinstance(request, PolicyAccessRequest) else None,
            request.data_class if isinstance(request, PolicyAccessRequest) else None,
            request.operation if isinstance(request, PolicyAccessRequest) else None,
            PolicyState.HOLD,
            "forensic_grant_invalid_hold",
            request.taxonomy_version if isinstance(request, PolicyAccessRequest) else None,
            request.subject_id if isinstance(request, PolicyAccessRequest) else None,
            request.requested_at if isinstance(request, PolicyAccessRequest) else datetime.now(timezone.utc),
        )
        raise ForensicPolicyDenied(audit)
    evaluation = evaluate_forensic_policy(decisions, request)
    if not evaluation.allowed or evaluation.decision is None:
        raise ForensicPolicyDenied(evaluation.audit_record)
    grant = ResearchGrant(
        grant_id=uuid4(),
        case_reference=cast(str, request.case_reference),
        taxonomy_version=cast(str, request.taxonomy_version),
        data_class=cast(ForensicDataClass, request.data_class),
        operation=operation,
        evidence_envelope_id=request.evidence_envelope_id,
        source=cast(str, request.source),
        source_rights=cast(frozenset[str], request.source_rights),
        subject_id=cast(str, request.subject_id),
        issued_at=request.requested_at,
        expires_at=evaluation.decision.expires_at,
        decision_id=evaluation.decision.decision_id,
        override_reference=evaluation.decision.override_reference,
    )
    with _REGISTERED_GRANTS_LOCK:
        _REGISTERED_GRANTS[grant.grant_id] = grant
    return grant


def _require_research_grant(
    grant: ResearchGrant | object,
    request: PolicyAccessRequest,
    operation: ResearchOperation,
    *,
    persisted_source: str | None,
    persisted_source_rights: frozenset[str] | None,
) -> ResearchGrant:
    evaluated_at = request.requested_at if isinstance(request, PolicyAccessRequest) else datetime.now(timezone.utc)
    if not isinstance(grant, ResearchGrant):
        raise ForensicPolicyDenied(
            _audit(None, None, operation, PolicyState.HOLD, "forensic_grant_invalid_hold", None, None, evaluated_at)
        )
    with _REGISTERED_GRANTS_LOCK:
        registered_grant = _REGISTERED_GRANTS.get(grant.grant_id)
    if registered_grant is not grant:
        raise ForensicPolicyDenied(
            _audit(grant.case_reference, grant.data_class, operation, PolicyState.HOLD, "forensic_grant_invalid_hold", grant.taxonomy_version, grant.subject_id, evaluated_at)
        )
    if not isinstance(request, PolicyAccessRequest):
        raise ForensicPolicyDenied(
            _audit(grant.case_reference, grant.data_class, operation, PolicyState.HOLD, "forensic_policy_missing_hold", grant.taxonomy_version, grant.subject_id, evaluated_at)
        )
    if grant.expires_at <= request.requested_at:
        raise ForensicPolicyDenied(
            _audit(grant.case_reference, grant.data_class, operation, PolicyState.HOLD, "forensic_grant_expired_hold", grant.taxonomy_version, grant.subject_id, evaluated_at)
        )
    if (
        request.case_reference != grant.case_reference
        or request.taxonomy_version != grant.taxonomy_version
        or request.data_class is not grant.data_class
        or grant.operation is not operation
        or request.operation is not operation
        or request.evidence_envelope_id != grant.evidence_envelope_id
        or request.source != grant.source
        or request.source_rights != grant.source_rights
        or request.subject_id != grant.subject_id
    ):
        raise ForensicPolicyDenied(
            _audit(grant.case_reference, grant.data_class, operation, PolicyState.HOLD, "forensic_grant_invalid_hold", grant.taxonomy_version, grant.subject_id, evaluated_at)
        )
    if persisted_source is not None and persisted_source != grant.source:
        raise ForensicPolicyDenied(
            _audit(grant.case_reference, grant.data_class, operation, PolicyState.HOLD, "forensic_grant_invalid_hold", grant.taxonomy_version, grant.subject_id, evaluated_at)
        )
    if (
        persisted_source_rights is not None
        and persisted_source_rights != grant.source_rights
    ):
        raise ForensicPolicyDenied(
            _audit(grant.case_reference, grant.data_class, operation, PolicyState.HOLD, "forensic_grant_invalid_hold", grant.taxonomy_version, grant.subject_id, evaluated_at)
        )
    return grant


def _request_missing_code(request: PolicyAccessRequest) -> str | None:
    if not request.case_reference or not request.case_reference.strip():
        return "forensic_policy_missing_hold"
    if not request.taxonomy_version:
        return "forensic_policy_missing_hold"
    if request.data_class is None:
        return "forensic_policy_missing_hold"
    if request.operation is None:
        return "forensic_policy_missing_hold"
    if not request.subject_id or not request.subject_id.strip():
        return "forensic_policy_missing_hold"
    # Pre-collection allows null evidence; all other operations require it
    if request.operation is not ResearchOperation.COLLECTION and request.evidence_envelope_id is None:
        return "forensic_policy_missing_hold"
    if not request.source or not request.source.strip():
        return "forensic_policy_missing_hold"
    if not request.source_rights:
        return "forensic_source_rights_mismatch_hold"
    return None


def _decisions_are_valid(decisions: Sequence[ForensicPolicyDecision]) -> bool:
    return isinstance(decisions, Sequence) and all(
        isinstance(decision, ForensicPolicyDecision) for decision in decisions
    )


def _has_supersession_conflict(
    decisions: Iterable[ForensicPolicyDecision],
) -> bool:
    decisions = tuple(decisions)
    identifiers = {decision.decision_id for decision in decisions}
    superseded = [
        decision.supersedes
        for decision in decisions
        if decision.supersedes is not None
    ]
    return (
        any(identifier not in identifiers for identifier in superseded)
        or len(superseded) != len(set(superseded))
        or any(decision.decision_id == decision.supersedes for decision in decisions)
    )


def _without_superseded_decisions(
    decisions: Iterable[ForensicPolicyDecision],
) -> tuple[ForensicPolicyDecision, ...]:
    superseded = {
        decision.supersedes for decision in decisions if decision.supersedes is not None
    }
    return tuple(decision for decision in decisions if decision.decision_id not in superseded)


def _reviewer_threshold_met(decision: ForensicPolicyDecision) -> bool:
    authorities = decision.reviewer_authorities
    if not authorities:
        return False
    if decision.data_class in {ForensicDataClass.PUBLIC, ForensicDataClass.LICENSED}:
        return True
    if decision.data_class is ForensicDataClass.PERSONAL:
        return bool(
            {ReviewerAuthority.PRIVACY_REVIEWER, ReviewerAuthority.COMPLIANCE_REVIEWER}
            & authorities
        )
    if decision.data_class in {
        ForensicDataClass.SENSITIVE_PERSONAL,
        ForensicDataClass.RESTRICTED,
    }:
        return ReviewerAuthority.COMPLIANCE_REVIEWER in authorities
    return False


def _hold_for_request(request: PolicyAccessRequest, code: str) -> PolicyEvaluation:
    return _hold(
        request.case_reference,
        request.data_class,
        request.operation,
        request.taxonomy_version,
        request.subject_id,
        code,
        request.requested_at,
    )


def _hold(
    case_reference: str | None,
    data_class: ForensicDataClass | None,
    operation: ResearchOperation | None,
    taxonomy_version: str | None,
    subject_id: str | None,
    code: str,
    evaluated_at: datetime | None = None,
) -> PolicyEvaluation:
    return PolicyEvaluation(
        state=PolicyState.HOLD,
        audit_record=_audit(
            case_reference,
            data_class,
            operation,
            PolicyState.HOLD,
            code,
            taxonomy_version,
            subject_id,
            evaluated_at or datetime.now(timezone.utc),
        ),
    )


def _evaluation(
    state: PolicyState,
    request: PolicyAccessRequest,
    code: str,
    decision: ForensicPolicyDecision,
) -> PolicyEvaluation:
    return PolicyEvaluation(
        state=state,
        decision=decision,
        audit_record=_audit(
            request.case_reference,
            request.data_class,
            request.operation,
            state,
            code,
            request.taxonomy_version,
            request.subject_id,
            request.requested_at,
        ),
    )


def _audit(
    case_reference: str | None,
    data_class: ForensicDataClass | None,
    operation: ResearchOperation | None,
    state: PolicyState,
    code: str,
    taxonomy_version: str | None,
    subject_id: str | None,
    evaluated_at: datetime,
) -> PolicyAuditRecord:
    return PolicyAuditRecord(
        case_reference=case_reference,
        data_class=data_class,
        operation=operation,
        state=state,
        code=code,
        taxonomy_version=taxonomy_version,
        subject_id=subject_id,
        evaluated_at=evaluated_at,
    )


def _require_nonempty_text(value: object, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} is required")


def _require_text_set(value: object, name: str) -> None:
    if not isinstance(value, frozenset) or not value:
        raise ValueError(f"{name} must be a nonempty frozenset")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise TypeError(f"{name} must contain nonempty strings")


def _require_enum_set(
    value: object,
    enum_type: type[StrEnum],
    name: str,
    *,
    allow_empty: bool = False,
) -> None:
    if not isinstance(value, frozenset) or (not value and not allow_empty):
        raise ValueError(f"{name} must be a frozenset")
    if any(not isinstance(item, enum_type) for item in value):
        raise TypeError(f"{name} contains an unsupported value")


def _require_aware_datetime(value: object, name: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise TypeError(f"{name} must be timezone-aware")
