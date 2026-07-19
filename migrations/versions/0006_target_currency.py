"""store currency of a target price

Revision ID: 0006
Revises: 0005
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("watch_rules", sa.Column("target_currency", sa.String(3), nullable=True))
    op.execute("""
        UPDATE watch_rules wr SET target_currency = r.currency
        FROM users u
        JOIN (VALUES ('RU','RUB'),('BY','USD'),('KZ','KZT'),('UA','UAH'),('AM','USD'),
          ('AZ','USD'),('GE','USD'),('KG','USD'),('MD','USD'),('TJ','USD'),('TM','USD'),('UZ','USD'))
          AS r(country_code, currency) ON r.country_code = u.country_code
        WHERE wr.user_id = u.id AND wr.max_price IS NOT NULL
    """)


def downgrade() -> None:
    op.drop_column("watch_rules", "target_currency")
