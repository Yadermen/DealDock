"""add full Steam search catalogue

Revision ID: 0021
Revises: 0020
"""

import sqlalchemy as sa
from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.create_table(
        "steam_catalog_apps",
        sa.Column("steam_app_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("search_text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("steam_app_id", name=op.f("pk_steam_catalog_apps")),
    )
    op.create_index(
        "ix_steam_catalog_apps_name_trgm",
        "steam_catalog_apps",
        ["name"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"name": "gin_trgm_ops"},
    )
    op.create_index(
        "ix_steam_catalog_apps_search_text_trgm",
        "steam_catalog_apps",
        ["search_text"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"search_text": "gin_trgm_ops"},
    )


def downgrade() -> None:
    op.drop_index("ix_steam_catalog_apps_search_text_trgm", table_name="steam_catalog_apps")
    op.drop_index("ix_steam_catalog_apps_name_trgm", table_name="steam_catalog_apps")
    op.drop_table("steam_catalog_apps")
