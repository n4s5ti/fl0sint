"""add graph projection jobs

Revision ID: d4e5f6a7b8c9
Revises: c3f4e5d6a7b8
Create Date: 2026-08-01 00:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, None] = "c3f4e5d6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "graph_projection_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("evidence_envelope_id", sa.Uuid(), nullable=False),
        sa.Column("profile_id", sa.String(length=128), nullable=False),
        sa.Column("profile_revision", sa.Integer(), nullable=False),
        sa.Column("profile_digest", sa.String(length=64), nullable=False),
        sa.Column("profile_snapshot", sa.JSON(), nullable=False),
        sa.Column("source_attempt", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lease_owner", sa.String(length=255), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
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
            ["evidence_envelope_id"],
            ["evidence_envelope_records.id"],
            onupdate="CASCADE",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "evidence_envelope_id",
            "profile_id",
            "profile_revision",
            name="uq_graph_projection_jobs_evidence_profile",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'retry', 'succeeded', 'failed')",
            name="ck_graph_projection_jobs_status",
        ),
        sa.CheckConstraint("attempt >= 0", name="ck_graph_projection_jobs_attempt"),
        sa.CheckConstraint(
            "source_attempt >= 0",
            name="ck_graph_projection_jobs_source_attempt",
        ),
    )
    op.create_index(
        "idx_graph_projection_jobs_status_next_attempt",
        "graph_projection_jobs",
        ["status", "next_attempt_at"],
    )
    op.create_index(
        "idx_graph_projection_jobs_lease_expires_at",
        "graph_projection_jobs",
        ["lease_expires_at"],
    )
    op.create_index(
        "idx_graph_projection_jobs_evidence_envelope_id",
        "graph_projection_jobs",
        ["evidence_envelope_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_graph_projection_jobs_evidence_envelope_id",
        table_name="graph_projection_jobs",
    )
    op.drop_index(
        "idx_graph_projection_jobs_lease_expires_at",
        table_name="graph_projection_jobs",
    )
    op.drop_index(
        "idx_graph_projection_jobs_status_next_attempt",
        table_name="graph_projection_jobs",
    )
    op.drop_table("graph_projection_jobs")
