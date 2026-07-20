"""store image for deferred game notifications

Revision ID: 0012
Revises: 0011
"""

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("deferred_notifications", sa.Column("image_url", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("deferred_notifications", "image_url")
