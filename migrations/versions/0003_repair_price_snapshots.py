"""Repair contradictory historical price snapshots.

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-18
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        DELETE FROM price_snapshots
        WHERE initial_price < 0 OR final_price < 0 OR final_price > initial_price
    """)
    op.execute("""
        UPDATE price_snapshots
        SET discount_percent = CASE
            WHEN initial_price = 0 THEN 0
            ELSE GREATEST(0, LEAST(100,
                ROUND(((initial_price - final_price) / initial_price) * 100)::integer
            ))
        END
    """)


def downgrade() -> None:
    # Corrected historical values cannot be reconstructed safely.
    pass
