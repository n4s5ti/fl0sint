"""replace URL provenance with connector policy identifiers

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-08-01 00:35:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "flow_runs",
        sa.Column("operation_digest", sa.String(length=64), nullable=True),
    )
    op.execute(
        sa.text(
            "UPDATE flow_runs SET operation_digest = input_digest "
            "WHERE operation_digest IS NULL"
        )
    )
    with op.batch_alter_table("flow_runs") as batch_op:
        batch_op.alter_column(
            "operation_digest",
            existing_type=sa.String(length=64),
            nullable=False,
        )
        batch_op.create_check_constraint(
            "ck_flow_runs_operation_digest_length",
            "length(operation_digest) = 64",
        )
    op.add_column(
        "evidence_envelope_records",
        sa.Column("destination_id", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "evidence_envelope_records",
        sa.Column("endpoint_id", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "evidence_envelope_records",
        sa.Column("capability", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "evidence_envelope_records",
        sa.Column("policy_version", sa.String(length=64), nullable=True),
    )
    op.drop_column("evidence_envelope_records", "request_url_pattern")


def downgrade() -> None:
    with op.batch_alter_table("flow_runs") as batch_op:
        batch_op.drop_constraint(
            "ck_flow_runs_operation_digest_length",
            type_="check",
        )
        batch_op.drop_column("operation_digest")
    op.add_column(
        "evidence_envelope_records",
        sa.Column("request_url_pattern", sa.Text(), nullable=True),
    )
    op.drop_column("evidence_envelope_records", "policy_version")
    op.drop_column("evidence_envelope_records", "capability")
    op.drop_column("evidence_envelope_records", "endpoint_id")
    op.drop_column("evidence_envelope_records", "destination_id")
