"""add detailed premium payment fields

Revision ID: 0017
Revises: 0016
"""

import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("payments", sa.Column("duration_days", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("payments", sa.Column("tariff", sa.String(length=32), nullable=False, server_default="premium"))
    op.add_column(
        "payments",
        sa.Column("status", sa.String(length=20), nullable=False, server_default="successful"),
    )
    op.add_column(
        "payments",
        sa.Column("source", sa.String(length=32), nullable=False, server_default="telegram_stars"),
    )
    op.add_column("payments", sa.Column("username", sa.String(length=64), nullable=True))
    op.add_column("payments", sa.Column("display_name", sa.String(length=128), nullable=True))
    op.execute(sa.text("UPDATE payments SET duration_days = months * 30 WHERE duration_days = 0"))
    op.create_index("ix_payments_status", "payments", ["status"])


def downgrade() -> None:
    op.drop_index("ix_payments_status", table_name="payments")
    op.drop_column("payments", "display_name")
    op.drop_column("payments", "username")
    op.drop_column("payments", "source")
    op.drop_column("payments", "status")
    op.drop_column("payments", "tariff")
    op.drop_column("payments", "duration_days")
