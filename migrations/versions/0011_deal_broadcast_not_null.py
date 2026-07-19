"""Make broadcast recipient references non-null.

Revision ID: 0011
Revises: 0010
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("deal_broadcast_recipients", "run_id", nullable=False)
    op.alter_column("deal_broadcast_recipients", "user_id", nullable=False)


def downgrade() -> None:
    op.alter_column("deal_broadcast_recipients", "user_id", nullable=True)
    op.alter_column("deal_broadcast_recipients", "run_id", nullable=True)
