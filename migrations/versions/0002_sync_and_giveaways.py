"""Add synchronization state, errors and giveaway source metadata.

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("last_seen_at", sa.DateTime(timezone=True)))
    op.add_column("giveaways", sa.Column("source", sa.String(50), nullable=False, server_default="manual"))
    op.add_column("giveaways", sa.Column("external_id", sa.String(100)))
    op.add_column("giveaways", sa.Column("image_url", sa.Text()))
    op.add_column("giveaways", sa.Column("last_seen_at", sa.DateTime(timezone=True)))
    op.create_unique_constraint("uq_giveaways_external_id", "giveaways", ["external_id"])
    op.create_table(
        "sync_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.String(50), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("processed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_sync_runs_kind", "sync_runs", ["kind"])
    op.create_index("ix_sync_runs_status", "sync_runs", ["status"])
    op.create_table(
        "system_errors",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("component", sa.String(80), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_system_errors_component", "system_errors", ["component"])
    op.create_index("ix_system_errors_occurred_at", "system_errors", ["occurred_at"])


def downgrade() -> None:
    op.drop_table("system_errors")
    op.drop_table("sync_runs")
    op.drop_constraint("uq_giveaways_external_id", "giveaways", type_="unique")
    op.drop_column("giveaways", "last_seen_at")
    op.drop_column("giveaways", "image_url")
    op.drop_column("giveaways", "external_id")
    op.drop_column("giveaways", "source")
    op.drop_column("users", "last_seen_at")
