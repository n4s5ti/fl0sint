"""add grievances table

Revision ID: 14b9218242a4
Revises: a1f2b3c4d5e6
Create Date: 2026-06-15 15:02:26.057474

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '14b9218242a4'
down_revision: Union[str, None] = 'a1f2b3c4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "grievances",
        sa.Column("id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("install_id", sa.String(), nullable=False, index=True),
        sa.Column("tool_name", sa.String(), nullable=False),
        sa.Column("report", sa.Text(), nullable=False),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("grievances")
