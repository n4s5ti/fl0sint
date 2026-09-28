import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    CHAR,
    CheckConstraint,
    Column,
    DateTime,
    DDL,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    event,
    func,
)
from sqlalchemy import (
    Enum as SQLEnum,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, validates
from sqlalchemy.types import TypeDecorator

from flowsint_core.core.enums import EventLevel
from flowsint_core.core.types import Role


class RoleListType(TypeDecorator):
    """Stores a list of Role enums as a JSON string. Portable across dialects."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None:
            return json.dumps([r.value if isinstance(r, Role) else r for r in value])
        return "[]"

    def process_result_value(self, value, dialect):
        if value is not None:
            return [Role(r.lower()) for r in json.loads(value)]
        return []


class Base(DeclarativeBase):
    pass


class Grievance(Base):
    __tablename__ = "grievances"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    created_at = mapped_column(DateTime(timezone=True), server_default=func.now())
    install_id = mapped_column(String, nullable=False, index=True)
    tool_name = mapped_column(String, nullable=False)
    report = mapped_column(Text, nullable=False)


class Feedback(Base):
    __tablename__ = "feedbacks"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    created_at = mapped_column(DateTime(timezone=True), server_default=func.now())
    content = mapped_column(Text, nullable=True)
    owner_id = mapped_column(Uuid, ForeignKey("profiles.id"), nullable=True)


class Investigation(Base):
    __tablename__ = "investigations"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    created_at = mapped_column(DateTime(timezone=True), server_default=func.now())
    name = mapped_column(Text)
    description = mapped_column(Text)
    owner_id = mapped_column(
        Uuid,
        ForeignKey("profiles.id", onupdate="CASCADE", ondelete="CASCADE"),
        nullable=True,
    )
    last_updated_at = mapped_column(DateTime(timezone=True), server_default=func.now())
    status = mapped_column(String, server_default="active")
    sketches = relationship("Sketch", back_populates="investigation")
    analyses = relationship("Analysis", back_populates="investigation")
    chats = relationship("Chat", back_populates="investigation")
    owner = relationship("Profile", foreign_keys=[owner_id])
    user_roles = relationship(
        "InvestigationUserRole",
        back_populates="investigation",
        passive_deletes=True,
        cascade="save-update, merge",
    )
    __table_args__ = (
        Index("idx_investigations_id", "id"),
        Index("idx_investigations_owner_id", "owner_id"),
    )


class Log(Base):
    __tablename__ = "logs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    content = mapped_column(JSON, nullable=True)
    # Allow both server-side default and application-side timestamp
    created_at = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )
    sketch_id = mapped_column(
        Uuid,
        ForeignKey("sketches.id", onupdate="CASCADE", ondelete="CASCADE"),
        nullable=True,
    )
    type = Column(SQLEnum(EventLevel), default=EventLevel.INFO)


class Profile(Base):
    __tablename__ = "profiles"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    first_name = mapped_column(Text, nullable=True)
    last_name = mapped_column(Text, nullable=True)
    avatar_url = mapped_column(Text, nullable=True)
    email: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String, nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True)
    investigation_roles = relationship("InvestigationUserRole", back_populates="user")


class Scan(Base):
    __tablename__ = "scans"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    sketch_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("sketches.id", onupdate="CASCADE", ondelete="CASCADE"),
        nullable=True,
    )
    status = Column(SQLEnum(EventLevel), default=EventLevel.PENDING)
    started_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    completed_at = Column(DateTime, nullable=True)
    error = Column(Text, nullable=True)
    details = Column(JSON, nullable=True)

    # Relationships
    sketch = relationship("Sketch", back_populates="scans")

    def __repr__(self):
        return f"<Scan(id={self.id}, status={self.status})>"


class Sketch(Base):
    __tablename__ = "sketches"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    title = mapped_column(Text)
    description = mapped_column(Text)
    created_at = mapped_column(DateTime(timezone=True), server_default=func.now())
    owner_id = mapped_column(
        Uuid,
        ForeignKey("profiles.id", onupdate="CASCADE", ondelete="CASCADE"),
        nullable=True,
    )
    status = mapped_column(String, server_default="active")
    investigation_id = mapped_column(
        Uuid,
        ForeignKey("investigations.id", onupdate="CASCADE", ondelete="CASCADE"),
    )
    investigation = relationship("Investigation", back_populates="sketches")
    last_updated_at = mapped_column(DateTime(timezone=True), server_default=func.now())
    scans = relationship("Scan", back_populates="sketch")

    __table_args__ = (
        Index("idx_sketches_investigation_id", "investigation_id"),
        Index("idx_sketches_owner_id", "owner_id"),
    )


class SketchesProfiles(Base):
    __tablename__ = "sketches_profiles"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    created_at = mapped_column(DateTime(timezone=True), server_default=func.now())
    profile_id = mapped_column(
        Uuid,
        ForeignKey("profiles.id", onupdate="CASCADE", ondelete="CASCADE"),
    )
    sketch_id = mapped_column(
        Uuid,
        ForeignKey("sketches.id", onupdate="CASCADE", ondelete="CASCADE"),
    )
    role = mapped_column(String, server_default="editor")

    __table_args__ = (
        Index("idx_sketches_profiles_sketch_id", "sketch_id"),
        Index("idx_sketches_profiles_profile_id", "profile_id"),
        Index(
            "investigations_profiles_unique_profile_investigation",
            "profile_id",
            "sketch_id",
            unique=True,
        ),
    )


class Flow(Base):
    __tablename__ = "flows"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name = mapped_column(Text, nullable=False)
    description = mapped_column(Text, nullable=True)
    category = mapped_column(JSON, nullable=True)
    flow_schema = mapped_column(JSON, nullable=True)
    created_at = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_updated_at = mapped_column(DateTime(timezone=True), server_default=func.now())


class Analysis(Base):
    __tablename__ = "analyses"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    title = mapped_column(Text, nullable=False)
    description = mapped_column(Text, nullable=True)
    content = mapped_column(JSON, nullable=True)
    created_at = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_updated_at = mapped_column(DateTime(timezone=True), server_default=func.now())
    owner_id = mapped_column(
        Uuid,
        ForeignKey("profiles.id", onupdate="CASCADE", ondelete="CASCADE"),
        nullable=True,
    )
    investigation_id = mapped_column(
        Uuid,
        ForeignKey("investigations.id", onupdate="CASCADE", ondelete="CASCADE"),
        nullable=True,
    )
    investigation = relationship("Investigation", back_populates="analyses")

    __table_args__ = (
        Index("idx_analyses_owner_id", "owner_id"),
        Index("idx_analyses_investigation_id", "investigation_id"),
    )


class Chat(Base):
    __tablename__ = "chats"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    title = mapped_column(Text, nullable=False)
    description = mapped_column(Text, nullable=True)
    created_at = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_updated_at = mapped_column(DateTime(timezone=True), server_default=func.now())
    owner_id = mapped_column(
        Uuid,
        ForeignKey("profiles.id", onupdate="CASCADE", ondelete="CASCADE"),
        nullable=True,
    )
    investigation_id = mapped_column(
        Uuid,
        ForeignKey("investigations.id", onupdate="CASCADE", ondelete="CASCADE"),
        nullable=True,
    )
    investigation = relationship("Investigation", back_populates="chats")
    messages = relationship("ChatMessage", back_populates="chat", cascade="all, delete")
    __table_args__ = (
        Index("idx_chats_owner_id", "owner_id"),
        Index("idx_chats_investigation_id", "investigation_id"),
    )


class ChatMessage(Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    content = mapped_column(JSON, nullable=True)
    context = mapped_column(JSON, nullable=True)
    is_bot: Mapped[bool] = mapped_column(default=False)
    created_at = mapped_column(DateTime(timezone=True), server_default=func.now())
    chat_id = mapped_column(
        Uuid,
        ForeignKey("chats.id", onupdate="CASCADE", ondelete="CASCADE"),
        nullable=False,
    )
    chat = relationship("Chat", back_populates="messages")
    __table_args__ = (Index("idx_messages_chat_id", "chat_id"),)


class Key(Base):
    __tablename__ = "keys"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    name: Mapped[str] = mapped_column(String, nullable=False)  # ex: "shodan", "whocy"

    owner_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("profiles.id", onupdate="CASCADE", ondelete="CASCADE"),
        nullable=False,
    )

    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    iv: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)  # 12 bytes
    salt: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)  # 16/32 bytes
    key_version: Mapped[str] = mapped_column(String, nullable=False)  # ex: "V1"

    created_at: Mapped[str] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        Index("idx_keys_owner_id", "owner_id"),
        Index("idx_keys_service", "name"),
    )


class InvestigationUserRole(Base):
    __tablename__ = "investigation_user_roles"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid.uuid4,
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("profiles.id", onupdate="CASCADE", ondelete="CASCADE"),
        nullable=False,
    )

    investigation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("investigations.id", onupdate="CASCADE", ondelete="CASCADE"),
        nullable=False,
    )

    roles: Mapped[list[Role]] = mapped_column(
        RoleListType(),
        nullable=False,
        default=list,
    )

    # Relations ORM
    user = relationship(
        "Profile", back_populates="investigation_roles", passive_deletes=True
    )
    investigation = relationship(
        "Investigation", back_populates="user_roles", passive_deletes=True
    )

    __table_args__ = (
        UniqueConstraint("user_id", "investigation_id", name="uq_user_investigation"),
        Index("idx_investigation_roles_user_id", "user_id"),
        Index("idx_investigation_roles_investigation_id", "investigation_id"),
    )


class CustomType(Base):
    __tablename__ = "custom_types"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("profiles.id", onupdate="CASCADE", ondelete="CASCADE"),
        nullable=False,
    )
    schema: Mapped[dict] = mapped_column(JSON, nullable=False)
    icon: Mapped[str] = mapped_column(String, nullable=True)
    color: Mapped[str] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, server_default="draft", nullable=False)
    category: Mapped[str] = mapped_column(
        String, server_default="custom_types_category", nullable=False
    )
    checksum: Mapped[str] = mapped_column(String, nullable=True)
    description: Mapped[str] = mapped_column(Text, nullable=True)
    created_at = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    owner = relationship("Profile", foreign_keys=[owner_id])

    __table_args__ = (
        Index("idx_custom_types_owner_id", "owner_id"),
        Index("idx_custom_types_name", "name"),
        Index("idx_custom_types_status", "status"),
    )


class EnricherTemplate(Base):
    __tablename__ = "enricher_templates"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=True)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    content: Mapped[dict] = mapped_column(JSON, nullable=False)
    is_public: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("profiles.id", onupdate="CASCADE", ondelete="CASCADE"),
        nullable=False,
    )
    created_at = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    owner = relationship("Profile", foreign_keys=[owner_id])

    __table_args__ = (
        Index("idx_enricher_templates_owner_id", "owner_id"),
        Index("idx_enricher_templates_name", "name"),
        Index("idx_enricher_templates_category", "category"),
        Index("idx_enricher_templates_is_public", "is_public"),
    )


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class FlowRun(Base):
    """Durable, resumable execution of a flow or standalone template step."""

    __tablename__ = "flow_runs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    flow_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("flows.id", onupdate="CASCADE", ondelete="SET NULL"),
        nullable=True,
    )
    sketch_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("sketches.id", onupdate="CASCADE", ondelete="SET NULL"),
        nullable=True,
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("profiles.id", onupdate="CASCADE", ondelete="RESTRICT"),
        nullable=False,
    )
    lease_owner: Mapped[str | None] = mapped_column(String(255), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    input_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    operation_digest: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default=lambda context: context.get_current_parameters()["input_digest"],
    )
    input_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    checkpoint: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    safe_error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    safe_error_diagnostic: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        server_default=func.now(),
        onupdate=_utcnow,
    )

    flow = relationship("Flow", foreign_keys=[flow_id])
    sketch = relationship("Sketch", foreign_keys=[sketch_id])
    owner = relationship("Profile", foreign_keys=[owner_id])
    step_runs = relationship("StepRun", back_populates="flow_run")
    evidence_records = relationship(
        "EvidenceEnvelopeRecord", back_populates="flow_run", foreign_keys="EvidenceEnvelopeRecord.flow_run_id"
    )

    __table_args__ = (
        CheckConstraint(
            "length(operation_digest) = 64",
            name="ck_flow_runs_operation_digest_length",
        ),
        UniqueConstraint(
            "owner_id",
            "idempotency_key",
            name="uq_flow_runs_owner_idempotency_key",
        ),
        Index("idx_flow_runs_owner_id", "owner_id"),
        Index("idx_flow_runs_status", "status"),
    )


class StepRun(Base):
    """Current resumable state for a stable step within a flow run."""

    __tablename__ = "step_runs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    flow_run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("flow_runs.id", onupdate="CASCADE", ondelete="CASCADE"),
        nullable=False,
    )
    step_key: Mapped[str] = mapped_column(String(255), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    retryable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    checkpoint: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    input_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    success_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failure_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    hold_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        server_default=func.now(),
        onupdate=_utcnow,
    )

    flow_run = relationship("FlowRun", back_populates="step_runs")
    evidence_records = relationship(
        "EvidenceEnvelopeRecord", back_populates="step_run", foreign_keys="EvidenceEnvelopeRecord.step_run_id"
    )

    __table_args__ = (
        UniqueConstraint("flow_run_id", "step_key", name="uq_step_runs_flow_run_step_key"),
        Index("idx_step_runs_flow_run_id", "flow_run_id"),
        Index("idx_step_runs_status", "status"),
    )


class EvidenceEnvelopeRecord(Base):
    """Append-only evidence snapshot for one structured input outcome."""

    __tablename__ = "evidence_envelope_records"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    flow_run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("flow_runs.id", onupdate="CASCADE", ondelete="RESTRICT"),
        nullable=False,
    )
    step_run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("step_runs.id", onupdate="CASCADE", ondelete="RESTRICT"),
        nullable=False,
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    input_index: Mapped[int] = mapped_column(Integer, nullable=False)
    evidence_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    input_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    retryable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    mapped_outputs: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    diagnostic: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    source: Mapped[str] = mapped_column(String(256), nullable=False)
    destination_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    endpoint_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    capability: Mapped[str | None] = mapped_column(String(64), nullable=True)
    policy_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    artifact_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    artifact_reference: Mapped[str | None] = mapped_column(Text, nullable=True)
    event_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_rights: Mapped[str | None] = mapped_column(String(128), nullable=True)
    schema_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    parser_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    verification_state: Mapped[str | None] = mapped_column(String(64), nullable=True)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey(
            "evidence_envelope_records.id",
            onupdate="CASCADE",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )

    flow_run = relationship(
        "FlowRun",
        back_populates="evidence_records",
        foreign_keys=[flow_run_id],
    )
    step_run = relationship(
        "StepRun",
        back_populates="evidence_records",
        foreign_keys=[step_run_id],
    )
    supersedes = relationship(
        "EvidenceEnvelopeRecord",
        foreign_keys=[supersedes_id],
        remote_side=[id],
    )
    projection_jobs = relationship(
        "GraphProjectionJob",
        back_populates="evidence_record",
        foreign_keys="GraphProjectionJob.evidence_envelope_id",
    )

    @validates("event_at", "retrieved_at", "ingested_at")
    def normalize_evidence_timestamp(
        self, key: str, value: datetime | None
    ) -> datetime | None:
        if value is None:
            if key == "event_at":
                return None
            raise ValueError(f"{key} is required")
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(f"{key} must be timezone-aware")
        return value.astimezone(timezone.utc)

    __table_args__ = (
        UniqueConstraint(
            "step_run_id",
            "attempt",
            "input_index",
            "evidence_index",
            name="uq_evidence_step_attempt_input_envelope",
        ),
        Index("idx_evidence_envelopes_flow_run_id", "flow_run_id"),
        Index("idx_evidence_envelopes_step_run_id", "step_run_id"),
        Index("idx_evidence_envelopes_supersedes_id", "supersedes_id"),
    )

class GraphProjectionJob(Base):
    """Durable, lease-fenced outbox job for an approved graph projection."""

    __tablename__ = "graph_projection_jobs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    evidence_envelope_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey(
            "evidence_envelope_records.id",
            onupdate="CASCADE",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    profile_id: Mapped[str] = mapped_column(String(128), nullable=False)
    profile_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    profile_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    profile_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    source_attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lease_owner: Mapped[str | None] = mapped_column(String(255), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    safe_error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    safe_error_diagnostic: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        server_default=func.now(),
        onupdate=_utcnow,
    )

    evidence_record = relationship(
        "EvidenceEnvelopeRecord",
        back_populates="projection_jobs",
        foreign_keys=[evidence_envelope_id],
    )

    __table_args__ = (
        UniqueConstraint(
            "evidence_envelope_id",
            "profile_id",
            "profile_revision",
            name="uq_graph_projection_jobs_evidence_profile",
        ),
        CheckConstraint(
            "status IN ('pending', 'running', 'retry', 'succeeded', 'failed')",
            name="ck_graph_projection_jobs_status",
        ),
        CheckConstraint("attempt >= 0", name="ck_graph_projection_jobs_attempt"),
        CheckConstraint(
            "source_attempt >= 0",
            name="ck_graph_projection_jobs_source_attempt",
        ),
        Index(
            "idx_graph_projection_jobs_status_next_attempt",
            "status",
            "next_attempt_at",
        ),
        Index("idx_graph_projection_jobs_lease_expires_at", "lease_expires_at"),
        Index(
            "idx_graph_projection_jobs_evidence_envelope_id",
            "evidence_envelope_id",
        ),
    )


event.listen(
    EvidenceEnvelopeRecord.__table__,
    "after_create",
    DDL(
        """
        CREATE TRIGGER trg_evidence_envelopes_reject_update
        BEFORE UPDATE ON evidence_envelope_records
        BEGIN
            SELECT RAISE(ABORT, 'evidence_envelope_records are append-only');
        END
        """
    ).execute_if(dialect="sqlite"),
)
event.listen(
    EvidenceEnvelopeRecord.__table__,
    "after_create",
    DDL(
        """
        CREATE TRIGGER trg_evidence_envelopes_reject_delete
        BEFORE DELETE ON evidence_envelope_records
        BEGIN
            SELECT RAISE(ABORT, 'evidence_envelope_records are append-only');
        END
        """
    ).execute_if(dialect="sqlite"),
)
event.listen(
    EvidenceEnvelopeRecord.__table__,
    "after_create",
    DDL(
        """
        CREATE OR REPLACE FUNCTION reject_evidence_envelope_mutation()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'evidence_envelope_records are append-only';
        END;
        $$ LANGUAGE plpgsql
        """
    ).execute_if(dialect="postgresql"),
)
event.listen(
    EvidenceEnvelopeRecord.__table__,
    "after_create",
    DDL(
        """
        CREATE TRIGGER trg_evidence_envelopes_reject_update
        BEFORE UPDATE ON evidence_envelope_records
        FOR EACH ROW EXECUTE FUNCTION reject_evidence_envelope_mutation()
        """
    ).execute_if(dialect="postgresql"),
)
event.listen(
    EvidenceEnvelopeRecord.__table__,
    "after_create",
    DDL(
        """
        CREATE TRIGGER trg_evidence_envelopes_reject_delete
        BEFORE DELETE ON evidence_envelope_records
        FOR EACH ROW EXECUTE FUNCTION reject_evidence_envelope_mutation()
        """
    ).execute_if(dialect="postgresql"),
)
event.listen(
    EvidenceEnvelopeRecord.__table__,
    "after_drop",
    DDL(
        "DROP FUNCTION IF EXISTS reject_evidence_envelope_mutation()"
    ).execute_if(dialect="postgresql"),
)


@event.listens_for(EvidenceEnvelopeRecord, "before_update")
def _reject_evidence_update(mapper, connection, target) -> None:
    raise TypeError("EvidenceEnvelopeRecord rows are append-only")


@event.listens_for(EvidenceEnvelopeRecord, "before_delete")
def _reject_evidence_delete(mapper, connection, target) -> None:
    raise TypeError("EvidenceEnvelopeRecord rows are append-only")



class ForensicArtifactRecord(Base):
    """Immutable forensic artifact record (SQLite anchor)."""

    __tablename__ = "forensic_artifact_records"

    artifact_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    evidence_envelope_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    source_record_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("forensic_source_records.source_record_id", onupdate="RESTRICT", ondelete="RESTRICT"),
        nullable=True,
    )
    digest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    locator_reference: Mapped[str] = mapped_column(Text, nullable=False)
    locator_retrieval_context: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_type: Mapped[str] = mapped_column(String(64), nullable=False)
    content_version: Mapped[str | None] = mapped_column(String(256), nullable=True)
    access: Mapped[str] = mapped_column(String(32), nullable=False, default="public")
    rights: Mapped[str | None] = mapped_column(Text, nullable=True)
    retention_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    verified_unavailable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    unavailability_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    unavailable_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retrieved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    supersedes_artifact_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    supersedes_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    custody_subject: Mapped[str] = mapped_column(String(256), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("supersedes_artifact_id", name="uq_far_supersedes"),
        Index("idx_far_evidence_envelope_id", "evidence_envelope_id"),
        Index("idx_far_source_record_id", "source_record_id"),
    )


class ForensicSourceRecord(Base):
    """Binds a versioned source card to a forensic evidence envelope (SQLite anchor)."""

    __tablename__ = "forensic_source_records"

    source_record_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    source_card_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    source_card_version: Mapped[int] = mapped_column(Integer, nullable=False)
    evidence_envelope_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    rights: Mapped[str | None] = mapped_column(Text, nullable=True)
    supersedes_source_record_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    supersedes_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    custody_subject: Mapped[str] = mapped_column(String(256), nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, server_default=func.now()
    )

    artifacts = relationship(
        "ForensicArtifactRecord",
        back_populates="source_record",
        foreign_keys="ForensicArtifactRecord.source_record_id",
    )

    __table_args__ = (
        UniqueConstraint("supersedes_source_record_id", name="uq_fsr_supersedes"),
        Index("idx_fsr_evidence_envelope_id", "evidence_envelope_id"),
        Index("idx_fsr_source_card_id", "source_card_id"),
    )


ForensicArtifactRecord.source_record = relationship(
    "ForensicSourceRecord",
    back_populates="artifacts",
    foreign_keys=[ForensicArtifactRecord.source_record_id],
)


class ForensicClaimRecord(Base):
    """Append-only observed-value claim (B4/OBS-1968 SQLite ledger anchor)."""

    __tablename__ = "forensic_claim_records"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    claim_key: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    case_reference: Mapped[str | None] = mapped_column(String, nullable=True)
    subject_entity_key: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    predicate: Mapped[str] = mapped_column(String, nullable=False)
    object_entity_key: Mapped[str | None] = mapped_column(CHAR(64), nullable=True)
    observed_value_json: Mapped[str] = mapped_column(Text, nullable=False)
    value_digest: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    valid_from: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    valid_to: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    source_card_id: Mapped[str] = mapped_column(String, nullable=False)
    source_card_version: Mapped[int] = mapped_column(Integer, nullable=False)
    source_record_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey(
            "forensic_source_records.source_record_id",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )
    evidence_envelope_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey(
            "evidence_envelope_records.id",
            onupdate="CASCADE",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey(
            "forensic_claim_records.id",
            onupdate="CASCADE",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )
    supersedes_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        server_default=func.now(),
    )

    __table_args__ = (
        UniqueConstraint("claim_key", name="uq_fcr_claim_key"),
        UniqueConstraint("supersedes_id", name="uq_fcr_supersedes"),
        CheckConstraint(
            "length(predicate) <= 64"
            " AND substr(predicate, 1, 1) BETWEEN 'a' AND 'z'",
            name="ck_fcr_predicate_safe_name",
        ),
        CheckConstraint(
            "(supersedes_id IS NULL AND supersedes_reason IS NULL)"
            " OR (supersedes_id IS NOT NULL AND supersedes_reason IS NOT NULL"
            " AND length(supersedes_reason) > 0 AND supersedes_id != id)",
            name="ck_fcr_supersedes_reason",
        ),
        Index("idx_fcr_subject_entity_key", "subject_entity_key"),
        Index("idx_fcr_evidence_envelope_id", "evidence_envelope_id"),
        Index("idx_fcr_source_record_id", "source_record_id"),
        Index("idx_fcr_supersedes_id", "supersedes_id"),
    )


class ForensicClaimRelationRecord(Base):
    """Append-only relation between two claims (B4/OBS-1968).

    The claim pair is stored in normalized order (lexicographically smaller
    UUID hex first); the domain layer normalizes before persistence.
    """

    __tablename__ = "forensic_claim_relation_records"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    relation_key: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    claim_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey(
            "forensic_claim_records.id",
            onupdate="CASCADE",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    related_claim_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey(
            "forensic_claim_records.id",
            onupdate="CASCADE",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_envelope_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey(
            "evidence_envelope_records.id",
            onupdate="CASCADE",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey(
            "forensic_claim_relation_records.id",
            onupdate="CASCADE",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )
    supersedes_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        server_default=func.now(),
    )

    __table_args__ = (
        UniqueConstraint("relation_key", name="uq_fcrr_relation_key"),
        UniqueConstraint(
            "claim_id",
            "related_claim_id",
            "kind",
            name="uq_fcrr_claim_pair_kind",
        ),
        UniqueConstraint("supersedes_id", name="uq_fcrr_supersedes"),
        CheckConstraint(
            "claim_id != related_claim_id",
            name="ck_fcrr_not_self",
        ),
        CheckConstraint(
            "kind IN ('contradicts', 'compatible', 'duplicate_report')",
            name="ck_fcrr_kind",
        ),
        CheckConstraint("length(rationale) > 0", name="ck_fcrr_rationale"),
        CheckConstraint(
            "(supersedes_id IS NULL AND supersedes_reason IS NULL)"
            " OR (supersedes_id IS NOT NULL AND supersedes_reason IS NOT NULL"
            " AND length(supersedes_reason) > 0 AND supersedes_id != id)",
            name="ck_fcrr_supersedes_reason",
        ),
        Index("idx_fcrr_claim_id", "claim_id"),
        Index("idx_fcrr_related_claim_id", "related_claim_id"),
        Index("idx_fcrr_evidence_envelope_id", "evidence_envelope_id"),
        Index("idx_fcrr_supersedes_id", "supersedes_id"),
    )


class ForensicClaimAssessmentRecord(Base):
    """Append-only claim disposition or contradiction assessment (B4/OBS-1968).

    Exactly one scope is set: claim-scoped kinds (withdrawn,
    insufficient_support) reference a claim; relation-scoped kinds
    (unresolved, ambiguous, resolved_compatible, resolved_upheld)
    reference a relation. Assessments supersede only assessments.
    """

    __tablename__ = "forensic_claim_assessment_records"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    assessment_key: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    claim_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey(
            "forensic_claim_records.id",
            onupdate="CASCADE",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )
    relation_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey(
            "forensic_claim_relation_records.id",
            onupdate="CASCADE",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_envelope_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey(
            "evidence_envelope_records.id",
            onupdate="CASCADE",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey(
            "forensic_claim_assessment_records.id",
            onupdate="CASCADE",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )
    supersedes_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        server_default=func.now(),
    )

    __table_args__ = (
        UniqueConstraint("assessment_key", name="uq_fcra_assessment_key"),
        UniqueConstraint("supersedes_id", name="uq_fcra_supersedes"),
        CheckConstraint(
            "(claim_id IS NULL) <> (relation_id IS NULL)",
            name="ck_fcra_scope_exclusive",
        ),
        CheckConstraint(
            "(kind IN ('withdrawn', 'insufficient_support')"
            " AND claim_id IS NOT NULL AND relation_id IS NULL)"
            " OR (kind IN ('unresolved', 'ambiguous',"
            " 'resolved_compatible', 'resolved_upheld')"
            " AND relation_id IS NOT NULL AND claim_id IS NULL)",
            name="ck_fcra_scope_kind",
        ),
        CheckConstraint(
            "kind IN ('withdrawn', 'insufficient_support', 'unresolved',"
            " 'ambiguous', 'resolved_compatible', 'resolved_upheld')",
            name="ck_fcra_kind",
        ),
        CheckConstraint("length(rationale) > 0", name="ck_fcra_rationale"),
        CheckConstraint(
            "(supersedes_id IS NULL AND supersedes_reason IS NULL)"
            " OR (supersedes_id IS NOT NULL AND supersedes_reason IS NOT NULL"
            " AND length(supersedes_reason) > 0 AND supersedes_id != id)",
            name="ck_fcra_supersedes_reason",
        ),
        Index("idx_fcra_claim_id", "claim_id"),
        Index("idx_fcra_relation_id", "relation_id"),
        Index("idx_fcra_evidence_envelope_id", "evidence_envelope_id"),
        Index("idx_fcra_supersedes_id", "supersedes_id"),
    )


# ── Append-only enforcement ─────────────────────────────────────────────────


_APPEND_ONLY_DDL_SQLITE = """
    BEGIN
        SELECT RAISE(ABORT, 'forensic records are append-only');
    END
"""

event.listen(
    ForensicArtifactRecord.__table__, "after_create",
    DDL("CREATE TRIGGER trg_far_reject_update BEFORE UPDATE ON forensic_artifact_records " + _APPEND_ONLY_DDL_SQLITE).execute_if(dialect="sqlite"),
)
event.listen(
    ForensicArtifactRecord.__table__, "after_create",
    DDL("CREATE TRIGGER trg_far_reject_delete BEFORE DELETE ON forensic_artifact_records " + _APPEND_ONLY_DDL_SQLITE).execute_if(dialect="sqlite"),
)
event.listen(
    ForensicSourceRecord.__table__, "after_create",
    DDL("CREATE TRIGGER trg_fsr_reject_update BEFORE UPDATE ON forensic_source_records " + _APPEND_ONLY_DDL_SQLITE).execute_if(dialect="sqlite"),
)
event.listen(
    ForensicSourceRecord.__table__, "after_create",
    DDL("CREATE TRIGGER trg_fsr_reject_delete BEFORE DELETE ON forensic_source_records " + _APPEND_ONLY_DDL_SQLITE).execute_if(dialect="sqlite"),
)


# B4/OBS-1968 claim, relation, and assessment tables ------------------------


for _table, _tablename, _prefix in (
    (ForensicClaimRecord.__table__, "forensic_claim_records", "fcr"),
    (ForensicClaimRelationRecord.__table__, "forensic_claim_relation_records", "fcrr"),
    (ForensicClaimAssessmentRecord.__table__, "forensic_claim_assessment_records", "fcra"),
):
    event.listen(
        _table, "after_create",
        DDL(f"CREATE TRIGGER trg_{_prefix}_reject_update BEFORE UPDATE ON {_tablename} " + _APPEND_ONLY_DDL_SQLITE).execute_if(dialect="sqlite"),
    )
    event.listen(
        _table, "after_create",
        DDL(f"CREATE TRIGGER trg_{_prefix}_reject_delete BEFORE DELETE ON {_tablename} " + _APPEND_ONLY_DDL_SQLITE).execute_if(dialect="sqlite"),
    )
    event.listen(
        _table, "after_create",
        DDL(
            f"""
            CREATE OR REPLACE FUNCTION reject_{_tablename}_mutation()
            RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION '{_tablename} are append-only';
            END;
            $$ LANGUAGE plpgsql
            """
        ).execute_if(dialect="postgresql"),
    )
    event.listen(
        _table, "after_create",
        DDL(
            f"""
            CREATE TRIGGER trg_{_prefix}_reject_update
            BEFORE UPDATE ON {_tablename}
            FOR EACH ROW EXECUTE FUNCTION reject_{_tablename}_mutation()
            """
        ).execute_if(dialect="postgresql"),
    )
    event.listen(
        _table, "after_create",
        DDL(
            f"""
            CREATE TRIGGER trg_{_prefix}_reject_delete
            BEFORE DELETE ON {_tablename}
            FOR EACH ROW EXECUTE FUNCTION reject_{_tablename}_mutation()
            """
        ).execute_if(dialect="postgresql"),
    )
    event.listen(
        _table, "after_drop",
        DDL(f"DROP FUNCTION IF EXISTS reject_{_tablename}_mutation()").execute_if(dialect="postgresql"),
    )


del _table, _tablename, _prefix


@event.listens_for(ForensicClaimRecord, "before_update")
def _reject_claim_update(mapper, connection, target) -> None:
    raise TypeError("ForensicClaimRecord rows are append-only")


@event.listens_for(ForensicClaimRecord, "before_delete")
def _reject_claim_delete(mapper, connection, target) -> None:
    raise TypeError("ForensicClaimRecord rows are append-only")


@event.listens_for(ForensicClaimRelationRecord, "before_update")
def _reject_claim_relation_update(mapper, connection, target) -> None:
    raise TypeError("ForensicClaimRelationRecord rows are append-only")


@event.listens_for(ForensicClaimRelationRecord, "before_delete")
def _reject_claim_relation_delete(mapper, connection, target) -> None:
    raise TypeError("ForensicClaimRelationRecord rows are append-only")


@event.listens_for(ForensicClaimAssessmentRecord, "before_update")
def _reject_claim_assessment_update(mapper, connection, target) -> None:
    raise TypeError("ForensicClaimAssessmentRecord rows are append-only")


@event.listens_for(ForensicClaimAssessmentRecord, "before_delete")
def _reject_claim_assessment_delete(mapper, connection, target) -> None:
    raise TypeError("ForensicClaimAssessmentRecord rows are append-only")