"""add durable execution records

Revision ID: c3f4e5d6a7b8
Revises: f4d42260273d
Create Date: 2026-08-01 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c3f4e5d6a7b8"
down_revision: Union[str, None] = "f4d42260273d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "flow_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("flow_id", sa.Uuid(), nullable=True),
        sa.Column("sketch_id", sa.Uuid(), nullable=True),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("lease_owner", sa.String(length=255), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("input_digest", sa.String(length=64), nullable=False),
        sa.Column("input_count", sa.Integer(), nullable=False),
        sa.Column("checkpoint", sa.JSON(), nullable=False),
        sa.Column("safe_error_code", sa.String(length=128), nullable=True),
        sa.Column("safe_error_diagnostic", sa.JSON(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["flow_id"], ["flows.id"], onupdate="CASCADE", ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["sketch_id"], ["sketches.id"], onupdate="CASCADE", ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["profiles.id"], onupdate="CASCADE", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "owner_id", "idempotency_key", name="uq_flow_runs_owner_idempotency_key"
        ),
    )
    op.create_index("idx_flow_runs_owner_id", "flow_runs", ["owner_id"])
    op.create_index("idx_flow_runs_status", "flow_runs", ["status"])

    op.create_table(
        "step_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("flow_run_id", sa.Uuid(), nullable=False),
        sa.Column("step_key", sa.String(length=255), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("retryable", sa.Boolean(), nullable=False),
        sa.Column("checkpoint", sa.JSON(), nullable=False),
        sa.Column("input_count", sa.Integer(), nullable=False),
        sa.Column("success_count", sa.Integer(), nullable=False),
        sa.Column("failure_count", sa.Integer(), nullable=False),
        sa.Column("hold_count", sa.Integer(), nullable=False),
        sa.Column("output_count", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["flow_run_id"],
            ["flow_runs.id"],
            onupdate="CASCADE",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("flow_run_id", "step_key", name="uq_step_runs_flow_run_step_key"),
    )
    op.create_index("idx_step_runs_flow_run_id", "step_runs", ["flow_run_id"])
    op.create_index("idx_step_runs_status", "step_runs", ["status"])

    op.create_table(
        "evidence_envelope_records",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("flow_run_id", sa.Uuid(), nullable=False),
        sa.Column("step_run_id", sa.Uuid(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("input_index", sa.Integer(), nullable=False),
        sa.Column("evidence_index", sa.Integer(), nullable=False),
        sa.Column("input_ref", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("retryable", sa.Boolean(), nullable=False),
        sa.Column("mapped_outputs", sa.JSON(), nullable=False),
        sa.Column("diagnostic", sa.JSON(), nullable=True),
        sa.Column("source", sa.String(length=256), nullable=False),
        sa.Column("request_url_pattern", sa.Text(), nullable=True),
        sa.Column("artifact_sha256", sa.String(length=64), nullable=True),
        sa.Column("artifact_reference", sa.Text(), nullable=True),
        sa.Column("event_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_rights", sa.String(length=128), nullable=True),
        sa.Column("schema_version", sa.String(length=64), nullable=True),
        sa.Column("parser_version", sa.String(length=64), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("verification_state", sa.String(length=64), nullable=True),
        sa.Column("supersedes_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["flow_run_id"],
            ["flow_runs.id"],
            onupdate="CASCADE",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["step_run_id"],
            ["step_runs.id"],
            onupdate="CASCADE",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_id"],
            ["evidence_envelope_records.id"],
            onupdate="CASCADE",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "step_run_id",
            "attempt",
            "input_index",
            "evidence_index",
            name="uq_evidence_step_attempt_input_envelope",
        ),
    )
    op.create_index(
        "idx_evidence_envelopes_flow_run_id",
        "evidence_envelope_records",
        ["flow_run_id"],
    )
    op.create_index(
        "idx_evidence_envelopes_step_run_id",
        "evidence_envelope_records",
        ["step_run_id"],
    )
    op.create_index(
        "idx_evidence_envelopes_supersedes_id",
        "evidence_envelope_records",
        ["supersedes_id"],
    )
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        op.execute(
            """
            CREATE TRIGGER trg_evidence_envelopes_reject_update
            BEFORE UPDATE ON evidence_envelope_records
            BEGIN
                SELECT RAISE(ABORT, 'evidence_envelope_records are append-only');
            END;
            """
        )
        op.execute(
            """
            CREATE TRIGGER trg_evidence_envelopes_reject_delete
            BEFORE DELETE ON evidence_envelope_records
            BEGIN
                SELECT RAISE(ABORT, 'evidence_envelope_records are append-only');
            END;
            """
        )
    elif dialect == "postgresql":
        op.execute(
            """
            CREATE FUNCTION reject_evidence_envelope_mutation()
            RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'evidence_envelope_records are append-only';
            END;
            $$ LANGUAGE plpgsql;
            """
        )
        op.execute(
            """
            CREATE TRIGGER trg_evidence_envelopes_reject_update
            BEFORE UPDATE ON evidence_envelope_records
            FOR EACH ROW EXECUTE FUNCTION reject_evidence_envelope_mutation();
            """
        )
        op.execute(
            """
            CREATE TRIGGER trg_evidence_envelopes_reject_delete
            BEFORE DELETE ON evidence_envelope_records
            FOR EACH ROW EXECUTE FUNCTION reject_evidence_envelope_mutation();
            """
        )


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS trg_evidence_envelopes_reject_delete")
        op.execute("DROP TRIGGER IF EXISTS trg_evidence_envelopes_reject_update")
    elif dialect == "postgresql":
        op.execute(
            "DROP TRIGGER IF EXISTS trg_evidence_envelopes_reject_delete "
            "ON evidence_envelope_records"
        )
        op.execute(
            "DROP TRIGGER IF EXISTS trg_evidence_envelopes_reject_update "
            "ON evidence_envelope_records"
        )
        op.execute("DROP FUNCTION IF EXISTS reject_evidence_envelope_mutation()")
    op.drop_index("idx_evidence_envelopes_supersedes_id", "evidence_envelope_records")
    op.drop_index("idx_evidence_envelopes_step_run_id", "evidence_envelope_records")
    op.drop_index("idx_evidence_envelopes_flow_run_id", "evidence_envelope_records")
    op.drop_table("evidence_envelope_records")
    op.drop_index("idx_step_runs_status", "step_runs")
    op.drop_index("idx_step_runs_flow_run_id", "step_runs")
    op.drop_table("step_runs")
    op.drop_index("idx_flow_runs_status", "flow_runs")
    op.drop_index("idx_flow_runs_owner_id", "flow_runs")
    op.drop_table("flow_runs")
