import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
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
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
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
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("profiles.id", onupdate="CASCADE", ondelete="SET NULL"),
        nullable=True,
    )
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    input_digest: Mapped[str] = mapped_column(String(64), nullable=False)
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
    source: Mapped[str] = mapped_column(String(255), nullable=False)
    request_url_pattern: Mapped[str | None] = mapped_column(Text, nullable=True)
    artifact_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    artifact_reference: Mapped[str | None] = mapped_column(Text, nullable=True)
    observed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    source_rights: Mapped[str | None] = mapped_column(String(64), nullable=True)
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


@event.listens_for(EvidenceEnvelopeRecord, "before_update")
def _reject_evidence_update(mapper, connection, target) -> None:
    raise TypeError("EvidenceEnvelopeRecord rows are append-only")


@event.listens_for(EvidenceEnvelopeRecord, "before_delete")
def _reject_evidence_delete(mapper, connection, target) -> None:
    raise TypeError("EvidenceEnvelopeRecord rows are append-only")
