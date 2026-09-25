"""Versioned forensic authority and SQLite evidence-lineage contract."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from threading import Lock
from weakref import WeakKeyDictionary
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import UnboundExecutionError
from sqlalchemy.orm import Session, sessionmaker

from flowsint_core.core.models import EvidenceEnvelopeRecord

AUTHORITY_CONTRACT_VERSION = "v1"


class AuthoritySystem(StrEnum):
    """Storage and presentation systems covered by the forensic contract."""

    SQLITE_LEDGER = "sqlite_ledger"
    LADYBUGDB_PROJECTION = "ladybugdb_projection"
    LEGACY_NEO4J_CANVAS = "legacy_neo4j_canvas"
    ARTIFACT_STORE = "artifact_store"
    DERIVED_VIEWS = "derived_views"


class AuthorityRole(StrEnum):
    """The forensic role a covered system is allowed to hold."""

    FORENSIC_AUTHORITY = "forensic_authority"
    REBUILDABLE_EVIDENCE_KEYED_READ_MODEL = "rebuildable_evidence_keyed_read_model"
    EVIDENCE_PAYLOAD_STORE = "evidence_payload_store"
    REBUILDABLE_DERIVED_VIEW = "rebuildable_derived_view"
    EXCLUDED_FROM_FORENSIC_TARGET = "excluded_from_forensic_target"


@dataclass(frozen=True)
class AuthorityRule:
    """Immutable authority assignment for one system."""

    role: AuthorityRole
    description: str


@dataclass(frozen=True)
class AuthorityMatrix:
    """Immutable, versioned assignments for every forensic system."""

    version: str
    rules: Mapping[AuthoritySystem, AuthorityRule]

    def __post_init__(self) -> None:
        expected = frozenset(AuthoritySystem)
        if self.version != AUTHORITY_CONTRACT_VERSION:
            raise ValueError("authority matrix version is unsupported")
        if frozenset(self.rules) != expected:
            raise ValueError("authority matrix must cover every authority system")
        object.__setattr__(self, "rules", MappingProxyType(dict(self.rules)))

    def __getitem__(self, system: AuthoritySystem) -> AuthorityRule:
        return self.rules[system]


class MigrationDisposition(StrEnum):
    """Fixed disposition for pre-authority migration work."""

    PRESERVE_STRUCTURED_SQLITE_EVIDENCE = "preserve_structured_sqlite_evidence"
    REBUILD_FROM_SQLITE_WITHOUT_NEO4J_AUTHORITY = (
        "rebuild_from_sqlite_without_neo4j_authority"
    )
    PRESERVE_REGISTRY_EGRESS_BOUNDARY = "preserve_registry_egress_boundary"


@dataclass(frozen=True)
class MigrationDecision:
    """An immutable, issue-keyed migration decision."""

    issue_id: str
    disposition: MigrationDisposition
    rationale: str


FORENSIC_AUTHORITY = AuthorityMatrix(
    version=AUTHORITY_CONTRACT_VERSION,
    rules={
        AuthoritySystem.SQLITE_LEDGER: AuthorityRule(
            role=AuthorityRole.FORENSIC_AUTHORITY,
            description="SQLite EvidenceEnvelopeRecord UUIDs are the sole forensic authority.",
        ),
        AuthoritySystem.LADYBUGDB_PROJECTION: AuthorityRule(
            role=AuthorityRole.REBUILDABLE_EVIDENCE_KEYED_READ_MODEL,
            description="LadybugDB is a rebuildable read model keyed by SQLite evidence UUID.",
        ),
        AuthoritySystem.LEGACY_NEO4J_CANVAS: AuthorityRule(
            role=AuthorityRole.EXCLUDED_FROM_FORENSIC_TARGET,
            description="No Neo4j runtime or canvas is a forensic target.",
        ),
        AuthoritySystem.ARTIFACT_STORE: AuthorityRule(
            role=AuthorityRole.EVIDENCE_PAYLOAD_STORE,
            description="Artifacts retain payloads but never establish forensic lineage.",
        ),
        AuthoritySystem.DERIVED_VIEWS: AuthorityRule(
            role=AuthorityRole.REBUILDABLE_DERIVED_VIEW,
            description="Derived views are disposable and never establish forensic lineage.",
        ),
    },
)

MIGRATION_DECISIONS: Mapping[str, MigrationDecision] = MappingProxyType(
    {
        "OBS-1782": MigrationDecision(
            issue_id="OBS-1782",
            disposition=MigrationDisposition.PRESERVE_STRUCTURED_SQLITE_EVIDENCE,
            rationale="Durable structured SQLite evidence remains the forensic ledger.",
        ),
        "OBS-1787": MigrationDecision(
            issue_id="OBS-1787",
            disposition=MigrationDisposition.REBUILD_FROM_SQLITE_WITHOUT_NEO4J_AUTHORITY,
            rationale="The fixed-label graph projection is rebuilt from SQLite without Neo4j authority.",
        ),
        "OBS-1788": MigrationDecision(
            issue_id="OBS-1788",
            disposition=MigrationDisposition.PRESERVE_REGISTRY_EGRESS_BOUNDARY,
            rationale="Registry-backed connector egress remains bounded without graph writes or duplicate evidence.",
        ),
    }
)


class ForensicLedgerSession(Session):
    """Session capability required to read SQLite forensic evidence."""


_FORENSIC_LEDGER_SESSIONS: WeakKeyDictionary[ForensicLedgerSession, Engine] = (
    WeakKeyDictionary()
)
_FORENSIC_LEDGER_SESSIONS_LOCK = Lock()


class _ForensicLedgerSessionFactory:
    def __init__(self, bind: Engine) -> None:
        self._bind = bind
        self._session_factory = sessionmaker(bind=bind, class_=ForensicLedgerSession)

    def __call__(self) -> ForensicLedgerSession:
        session = self._session_factory()
        with _FORENSIC_LEDGER_SESSIONS_LOCK:
            _FORENSIC_LEDGER_SESSIONS[session] = self._bind
        return session


@dataclass(frozen=True)
class ProjectionEvidenceReference:
    """Lineage carried by every projected fact; claim keys are additive only."""

    evidence_envelope_id: UUID
    claim_key: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.evidence_envelope_id, UUID):
            raise LedgerLineageError.missing_evidence_key()


class LedgerLineageError(RuntimeError):
    """A redacted, stable failure to establish SQLite-backed lineage."""

    def __init__(self, code: str, safe_message: str) -> None:
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message

    @classmethod
    def missing_evidence_key(cls) -> "LedgerLineageError":
        return cls(
            "forensic_evidence_key_missing",
            "SQLite evidence lineage is required for projected facts.",
        )

    @classmethod
    def ledger_backend_not_sqlite(cls) -> "LedgerLineageError":
        return cls(
            "forensic_ledger_backend_not_sqlite",
            "The forensic ledger backend is unavailable.",
        )

    @classmethod
    def evidence_not_found(cls) -> "LedgerLineageError":
        return cls(
            "forensic_evidence_not_found",
            "SQLite evidence lineage could not be verified.",
        )


def create_forensic_ledger_session_factory(
    bind: Engine,
) -> Callable[[], ForensicLedgerSession]:
    """Create the sole session capability allowed to read the SQLite ledger."""
    if not isinstance(bind, Engine) or bind.dialect.name != "sqlite":
        raise LedgerLineageError.ledger_backend_not_sqlite()
    return _ForensicLedgerSessionFactory(bind)


def require_ledger_evidence(
    session: ForensicLedgerSession | None, evidence_envelope_id: UUID | None
) -> EvidenceEnvelopeRecord:
    """Resolve projection lineage exclusively from the SQLite evidence ledger."""
    if session is None or not isinstance(session, ForensicLedgerSession):
        raise LedgerLineageError.ledger_backend_not_sqlite()
    with _FORENSIC_LEDGER_SESSIONS_LOCK:
        registered_bind = _FORENSIC_LEDGER_SESSIONS.get(session)
    if registered_bind is None:
        raise LedgerLineageError.ledger_backend_not_sqlite()
    if not isinstance(evidence_envelope_id, UUID):
        raise LedgerLineageError.missing_evidence_key()
    statement = select(EvidenceEnvelopeRecord).where(
        EvidenceEnvelopeRecord.id == evidence_envelope_id
    )
    try:
        bind = session.get_bind(mapper=EvidenceEnvelopeRecord, clause=statement)
    except UnboundExecutionError as error:
        raise LedgerLineageError.ledger_backend_not_sqlite() from error
    if bind is not registered_bind or bind.dialect.name != "sqlite":
        raise LedgerLineageError.ledger_backend_not_sqlite()

    record = session.scalar(statement, bind_arguments={"bind": registered_bind})
    if record is None:
        raise LedgerLineageError.evidence_not_found()
    return record
