"""disable unsolicited giveaway alerts for migrated users

Revision ID: 0016
Revises: 0015
"""

import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("UPDATE users SET giveaway_notifications_enabled = false"))
    op.alter_column("users", "giveaway_notifications_enabled", server_default=sa.false())


def downgrade() -> None:
    op.alter_column("users", "giveaway_notifications_enabled", server_default=sa.true())
