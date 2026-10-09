"""merge grievances and connector egress provenance heads

Revision ID: b2ae9121c94c
Revises: e5f6a7b8c9d0, 14b9218242a4
Create Date: 2026-10-09 22:00:00.000000

Both branches descend from a1f2b3c4d5e6 and touch disjoint tables, so the merge
carries no operations: databases at either head apply the other branch.
"""
from typing import Sequence, Union


# revision identifiers, used by Alembic.
revision: str = "b2ae9121c94c"
down_revision: Union[str, Sequence[str], None] = ("e5f6a7b8c9d0", "14b9218242a4")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
