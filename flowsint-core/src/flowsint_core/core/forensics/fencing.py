"""Fail-closed boundary between legacy canvas execution and forensic cases."""

from __future__ import annotations

import string
from datetime import datetime, timezone
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from celery import Task

from contextvars import ContextVar
from dataclasses import dataclass
from enum import StrEnum
from functools import wraps
from inspect import iscoroutinefunction
from typing import Any
from uuid import UUID

from flowsint_core.core.forensics.authority import (
    ForensicLedgerSession,
    LedgerLineageError,
    require_ledger_evidence,
)
from flowsint_core.core.forensics.policy import (
    FORENSIC_DATA_CLASS_POLICY_VERSION,
    ForensicDataClass,
    ForensicPolicyDenied,
    PolicyAccessRequest,
    ResearchGrant,
    ResearchOperation,
    require_collection_grant,
)


class LegacyExecutionMode(StrEnum):
    """Execution context requesting access to the legacy graph and canvas."""

    LEGACY_CANVAS = "legacy_canvas"
    FORENSIC_CASE = "forensic_case"


class LegacyPathDisposition(StrEnum):
    """Forensic disposition for a legacy execution surface."""

    FENCED = "fenced"
    STRUCTURED_REINGESTION_REQUIRED = "structured_reingestion_required"
    OUT_OF_SCOPE = "out_of_scope"


@dataclass(frozen=True)
class LegacyPathInventoryEntry:
    """A reviewed legacy surface and its fixed forensic disposition."""

    path: str
    disposition: LegacyPathDisposition
    rationale: str


LEGACY_PATH_INVENTORY_V1: tuple[LegacyPathInventoryEntry, ...] = (
    LegacyPathInventoryEntry(
        path="GraphService",
        disposition=LegacyPathDisposition.FENCED,
        rationale="Forensic cases must not construct a legacy graph service.",
    ),
    LegacyPathInventoryEntry(
        path="create_graph_service",
        disposition=LegacyPathDisposition.FENCED,
        rationale="Forensic cases must not construct a Neo4j repository or driver.",
    ),
    LegacyPathInventoryEntry(
        path="legacy_neo4j_graph_repository",
        disposition=LegacyPathDisposition.FENCED,
        rationale="Forensic cases cannot construct or operate the mutable Neo4j repository.",
    ),
    LegacyPathInventoryEntry(
        path="legacy_enricher_endpoint",
        disposition=LegacyPathDisposition.STRUCTURED_REINGESTION_REQUIRED,
        rationale="Legacy endpoint output is admissible only after structured ledger ingestion.",
    ),
    LegacyPathInventoryEntry(
        path="legacy_enricher_execution",
        disposition=LegacyPathDisposition.STRUCTURED_REINGESTION_REQUIRED,
        rationale="Legacy execution cannot provide forensic lineage directly.",
    ),
    LegacyPathInventoryEntry(
        path="flow_canvas_mutation",
        disposition=LegacyPathDisposition.FENCED,
        rationale="Forensic cases cannot mutate the legacy flow canvas.",
    ),
    LegacyPathInventoryEntry(
        path="sketch_canvas_mutation",
        disposition=LegacyPathDisposition.FENCED,
        rationale="Forensic cases cannot mutate the legacy sketch canvas.",
    ),
    LegacyPathInventoryEntry(
        path="legacy_investigation_service",
        disposition=LegacyPathDisposition.FENCED,
        rationale="Forensic cases cannot read or mutate legacy investigation canvas state.",
    ),
    LegacyPathInventoryEntry(
        path="legacy_canvas_presentation",
        disposition=LegacyPathDisposition.OUT_OF_SCOPE,
        rationale="Legacy presentation remains outside the forensic execution boundary.",
    ),
)

_FORENSIC_CASE_REFERENCE: ContextVar[str | None] = ContextVar(
    "forensic_case_reference",
    default=None,
)


@contextmanager
def forensic_case_scope(case_reference: str) -> Iterator[None]:
    """Fence legacy graph access for the duration of one forensic case."""
    if not isinstance(case_reference, str) or not case_reference.strip():
        raise ValueError("case_reference is required")
    token = _FORENSIC_CASE_REFERENCE.set(case_reference)
    try:
        yield
    finally:
        _FORENSIC_CASE_REFERENCE.reset(token)


def _effective_legacy_execution(
    mode: LegacyExecutionMode,
    case_reference: str | None,
) -> tuple[LegacyExecutionMode, str | None]:
    active_case_reference = _FORENSIC_CASE_REFERENCE.get()
    if active_case_reference is not None:
        return LegacyExecutionMode.FORENSIC_CASE, active_case_reference
    return mode, case_reference


@dataclass(frozen=True)
class LegacyFenceDecision:
    """Auditable result of an attempted legacy graph access."""

    mode: LegacyExecutionMode
    operation: str
    case_reference: str | None
    disposition: LegacyPathDisposition
    allowed: bool
    code: str | None = None


class LegacyExecutionFenced(RuntimeError):
    """A forensic case attempted to enter the mutable legacy graph path."""

    def __init__(self, decision: LegacyFenceDecision) -> None:
        super().__init__("Legacy execution is fenced for forensic cases.")
        self.decision = decision
        self.code = "legacy_execution_forensic_case_fenced"
        self.safe_message = "Legacy execution is unavailable to forensic cases."


@dataclass(frozen=True)
class StructuredLegacyReingestionRequest:
    """Request to admit one already-stored structured evidence envelope."""

    evidence_envelope_id: UUID
    case_reference: str
    data_class: ForensicDataClass
    source_rights: frozenset[str]
    subject_id: str
    collection_grant: ResearchGrant
    source_reference: object | None = None  # SourceReference or None; None → HOLD

    def __post_init__(self) -> None:
        if not isinstance(self.evidence_envelope_id, UUID):
            raise TypeError("evidence_envelope_id must be a UUID")
        if not isinstance(self.case_reference, str) or not self.case_reference.strip():
            raise ValueError("case_reference is required")
        if not isinstance(self.data_class, ForensicDataClass):
            raise TypeError("data_class must be a ForensicDataClass")
        if (
            not isinstance(self.source_rights, frozenset)
            or not self.source_rights
            or any(
                not isinstance(source_right, str) or not source_right.strip()
                for source_right in self.source_rights
            )
        ):
            raise ValueError("source_rights must be a nonempty frozenset")
        if not isinstance(self.subject_id, str) or not self.subject_id.strip():
            raise ValueError("subject_id is required")
        if not isinstance(self.collection_grant, ResearchGrant):
            raise TypeError("collection_grant must be a ResearchGrant")
        # B4: source_reference validated at admission boundary, not here

@dataclass(frozen=True)
class StructuredLegacyReingestionAdmission:
    """Read-only admission whose lineage is only the ledger evidence UUID."""

    evidence_envelope_id: UUID


class StructuredLegacyReingestionDenied(RuntimeError):
    """A stable, redacted failure to admit legacy-derived evidence."""

    def __init__(self, code: str, safe_message: str) -> None:
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message


def require_legacy_graph_access(
    mode: LegacyExecutionMode,
    operation: str,
    case_reference: str | None = None,
) -> LegacyFenceDecision:
    """Allow legacy canvas execution only outside a forensic case."""
    mode, case_reference = _effective_legacy_execution(mode, case_reference)
    if mode is not LegacyExecutionMode.LEGACY_CANVAS:
        decision = LegacyFenceDecision(
            mode=mode,
            operation=operation,
            case_reference=case_reference,
            disposition=LegacyPathDisposition.FENCED,
            allowed=False,
            code="legacy_execution_forensic_case_fenced",
        )
        raise LegacyExecutionFenced(decision)
    return LegacyFenceDecision(
        mode=mode,
        operation=operation,
        case_reference=case_reference,
        disposition=LegacyPathDisposition.OUT_OF_SCOPE,
        allowed=True,
    )


def legacy_execution_boundary(
    operation: str,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Fence a legacy sync or async entrypoint while a forensic case is active."""

    def decorate(function: Callable[..., Any]) -> Callable[..., Any]:
        if iscoroutinefunction(function):

            @wraps(function)
            async def guarded_async(*args: Any, **kwargs: Any) -> Any:
                require_legacy_graph_access(
                    LegacyExecutionMode.LEGACY_CANVAS,
                    operation=operation,
                )
                return await function(*args, **kwargs)

            return guarded_async

        @wraps(function)
        def guarded(*args: Any, **kwargs: Any) -> Any:
            require_legacy_graph_access(
                LegacyExecutionMode.LEGACY_CANVAS,
                operation=operation,
            )
            return function(*args, **kwargs)

        return guarded

    return decorate


def legacy_execution_class_boundary(prefix: str) -> Callable[[type[Any]], type[Any]]:
    """Fence construction and every public method defined by a legacy class."""

    def decorate(cls: type[Any]) -> type[Any]:
        for name, member in tuple(cls.__dict__.items()):
            if name == "__init__":
                setattr(
                    cls,
                    name,
                    legacy_execution_boundary(f"{prefix}.{name}")(member),
                )
            elif name.startswith("_"):
                continue
            elif isinstance(member, staticmethod):
                setattr(
                    cls,
                    name,
                    staticmethod(
                        legacy_execution_boundary(f"{prefix}.{name}")(member.__func__)
                    ),
                )
            elif isinstance(member, classmethod):
                setattr(
                    cls,
                    name,
                    classmethod(
                        legacy_execution_boundary(f"{prefix}.{name}")(member.__func__)
                    ),
                )
            elif callable(member):
                setattr(
                    cls,
                    name,
                    legacy_execution_boundary(f"{prefix}.{name}")(member),
                )
        return cls

    return decorate


def dispatch_legacy_task(
    celery_app: Any,
    task_name: str,
    *,
    args: tuple[Any, ...] | list[Any] = (),
    kwargs: dict[str, Any] | None = None,
) -> Any:
    """Fence sanctioned legacy task publication before contacting the broker."""
    require_legacy_graph_access(
        LegacyExecutionMode.LEGACY_CANVAS,
        operation="legacy_task_publish",
    )
    return celery_app.send_task(task_name, args=args, kwargs=kwargs)


class LegacyExecutionTask(Task):
    """Fence Celery publication for legacy workers before broker contact."""

    def apply_async(self, *args: Any, **kwargs: Any) -> Any:
        require_legacy_graph_access(
            LegacyExecutionMode.LEGACY_CANVAS,
            operation="legacy_task_publish",
        )
        return super().apply_async(*args, **kwargs)


def admit_structured_legacy_evidence(
    session: ForensicLedgerSession,
    request: StructuredLegacyReingestionRequest,
) -> StructuredLegacyReingestionAdmission:
    """Admit a complete structured SQLite envelope without touching legacy state."""
    if not isinstance(request, StructuredLegacyReingestionRequest):
        raise StructuredLegacyReingestionDenied(
            "structured_legacy_reingestion_request_invalid",
            "Structured legacy evidence could not be admitted.",
        )
    policy_request = PolicyAccessRequest(
        case_reference=request.case_reference,
        taxonomy_version=FORENSIC_DATA_CLASS_POLICY_VERSION,
        data_class=request.data_class,
        operation=ResearchOperation.COLLECTION,
        evidence_envelope_id=request.evidence_envelope_id,
        source=request.collection_grant.source,
        source_rights=request.source_rights,
        subject_id=request.subject_id,
        requested_at=datetime.now(timezone.utc),
    )
    try:
        require_collection_grant(request.collection_grant, policy_request)
    except ForensicPolicyDenied as error:
        raise StructuredLegacyReingestionDenied(
            error.code,
            error.safe_message,
        ) from error
    try:
        with session.no_autoflush:
            evidence = require_ledger_evidence(session, request.evidence_envelope_id)
            if (
                evidence in session.new
                or evidence in session.dirty
                or evidence in session.deleted
                or session.is_modified(evidence, include_collections=True)
            ):
                raise StructuredLegacyReingestionDenied(
                    "structured_legacy_reingestion_evidence_unavailable",
                    "Structured legacy evidence could not be verified.",
                )
            session.refresh(evidence)
    except LedgerLineageError as error:
        raise StructuredLegacyReingestionDenied(
            "structured_legacy_reingestion_evidence_unavailable",
            "Structured legacy evidence could not be verified.",
        ) from error
    if not evidence.source or not evidence.source.strip():
        raise StructuredLegacyReingestionDenied(
            "structured_legacy_reingestion_provenance_missing",
            "Structured legacy evidence provenance is incomplete.",
        )
    if (
        not evidence.destination_id
        or not evidence.endpoint_id
        or not evidence.policy_version
        or evidence.capability != "enrich.read"
        or evidence.source
        != f"connector:{evidence.destination_id}:{evidence.endpoint_id}"
    ):
        raise StructuredLegacyReingestionDenied(
            "structured_legacy_reingestion_provenance_invalid",
            "Structured legacy evidence provenance is incomplete.",
        )
    if not evidence.source_rights or not evidence.source_rights.strip():
        raise StructuredLegacyReingestionDenied(
            "structured_legacy_reingestion_rights_missing",
            "Structured legacy evidence rights are incomplete.",
        )
    if not evidence.artifact_sha256 or not evidence.artifact_sha256.strip():
        raise StructuredLegacyReingestionDenied(
            "structured_legacy_reingestion_artifact_missing",
            "Structured legacy evidence artifact is incomplete.",
        )
    if (
        len(evidence.artifact_sha256) != 64
        or evidence.artifact_sha256 != evidence.artifact_sha256.lower()
        or any(
            character not in string.hexdigits for character in evidence.artifact_sha256
        )
    ):
        raise StructuredLegacyReingestionDenied(
            "structured_legacy_reingestion_artifact_invalid",
            "Structured legacy evidence artifact is incomplete.",
        )
    if not evidence.artifact_reference or not evidence.artifact_reference.strip():
        raise StructuredLegacyReingestionDenied(
            "structured_legacy_reingestion_artifact_missing",
            "Structured legacy evidence artifact is incomplete.",
        )
    if evidence.artifact_reference != f"body:sha256:{evidence.artifact_sha256}":
        raise StructuredLegacyReingestionDenied(
            "structured_legacy_reingestion_artifact_invalid",
            "Structured legacy evidence artifact is incomplete.",
        )
    try:
        require_collection_grant(
            request.collection_grant,
            policy_request,
            persisted_source=evidence.source,
            persisted_source_rights=frozenset({evidence.source_rights}),
        )
    except ForensicPolicyDenied as error:
        raise StructuredLegacyReingestionDenied(
            error.code,
            error.safe_message,
        ) from error
    return StructuredLegacyReingestionAdmission(
        evidence_envelope_id=evidence.id,
    )
