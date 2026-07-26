"""add admin user setting audit

Revision ID: 0018
Revises: 0017
"""

import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "admin_user_audits",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("admin_telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("field", sa.String(length=32), nullable=False),
        sa.Column("old_value", sa.String(length=500), nullable=True),
        sa.Column("new_value", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_admin_user_audits_user_id", "admin_user_audits", ["user_id"])
    op.create_index(
        "ix_admin_user_audits_admin_telegram_id",
        "admin_user_audits",
        ["admin_telegram_id"],
    )
    op.create_index("ix_admin_user_audits_created_at", "admin_user_audits", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_admin_user_audits_created_at", table_name="admin_user_audits")
    op.drop_index("ix_admin_user_audits_admin_telegram_id", table_name="admin_user_audits")
    op.drop_index("ix_admin_user_audits_user_id", table_name="admin_user_audits")
    op.drop_table("admin_user_audits")
