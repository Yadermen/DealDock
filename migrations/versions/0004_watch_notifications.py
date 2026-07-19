"""Add per-watch notification setting.

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "watch_rules",
        sa.Column("notifications_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.alter_column("watch_rules", "notifications_enabled", server_default=None)


def downgrade() -> None:
    op.drop_column("watch_rules", "notifications_enabled")
