"""add referral system

Revision ID: 0022
Revises: 0021
"""

import sqlalchemy as sa
from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("onboarding_completed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("referral_code", sa.String(length=24), nullable=True))
    op.add_column("users", sa.Column("referred_by_user_id", sa.Integer(), nullable=True))
    op.add_column("users", sa.Column("referral_badge", sa.String(length=32), nullable=True))
    op.add_column(
        "users", sa.Column("referral_days_earned", sa.Integer(), server_default="0", nullable=False)
    )
    op.create_index(op.f("ix_users_referral_code"), "users", ["referral_code"], unique=True)
    op.create_index(op.f("ix_users_referred_by_user_id"), "users", ["referred_by_user_id"], unique=False)
    op.create_foreign_key(
        op.f("fk_users_referred_by_user_id_users"),
        "users",
        "users",
        ["referred_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_table(
        "referrals",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("inviter_user_id", sa.Integer(), nullable=False),
        sa.Column("referred_user_id", sa.Integer(), nullable=False),
        sa.Column("referral_code", sa.String(length=24), nullable=False),
        sa.Column("campaign_key", sa.String(length=50), nullable=False, server_default="permanent"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("registered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("onboarding_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["inviter_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["referred_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_referrals_inviter_user_id", "referrals", ["inviter_user_id"])
    op.create_index("ix_referrals_referred_user_id", "referrals", ["referred_user_id"], unique=True)
    op.create_index("ix_referrals_campaign_key", "referrals", ["campaign_key"])
    op.create_index("ix_referrals_status", "referrals", ["status"])
    op.create_table(
        "referral_rewards",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("referral_id", sa.Integer(), nullable=True),
        sa.Column("reward_key", sa.String(length=50), nullable=False),
        sa.Column("campaign_key", sa.String(length=50), nullable=False, server_default="permanent"),
        sa.Column("premium_days", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["referral_id"], ["referrals.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "referral_id",
            "campaign_key",
            "reward_key",
            name="uq_referral_reward_once",
        ),
    )
    op.create_index("ix_referral_rewards_user_id", "referral_rewards", ["user_id"])
    op.create_index("ix_referral_rewards_referral_id", "referral_rewards", ["referral_id"])
    op.create_index("ix_referral_rewards_campaign_key", "referral_rewards", ["campaign_key"])
    op.create_index("ix_referral_rewards_created_at", "referral_rewards", ["created_at"])
    op.create_table(
        "referral_milestone_awards",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("campaign_key", sa.String(length=50), nullable=False, server_default="permanent"),
        sa.Column("level_key", sa.String(length=50), nullable=False),
        sa.Column("premium_days", sa.Integer(), nullable=False),
        sa.Column("awarded_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "campaign_key", "level_key", name="uq_referral_milestone_once"),
    )
    op.create_index("ix_referral_milestone_awards_user_id", "referral_milestone_awards", ["user_id"])


def downgrade() -> None:
    op.drop_table("referral_milestone_awards")
    op.drop_table("referral_rewards")
    op.drop_table("referrals")
    op.drop_constraint(op.f("fk_users_referred_by_user_id_users"), "users", type_="foreignkey")
    op.drop_index(op.f("ix_users_referred_by_user_id"), table_name="users")
    op.drop_index(op.f("ix_users_referral_code"), table_name="users")
    op.drop_column("users", "referral_days_earned")
    op.drop_column("users", "referral_badge")
    op.drop_column("users", "referred_by_user_id")
    op.drop_column("users", "referral_code")
    op.drop_column("users", "onboarding_completed_at")
