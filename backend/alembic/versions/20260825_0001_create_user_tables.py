"""create user tables

Revision ID: 20260825_0001
Revises:
Create Date: 2026-08-25 08:50:00
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260825_0001"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_name", sa.String(length=128), nullable=False),
        sa.Column("user_pw", sa.String(length=512), nullable=False),
        sa.Column("user_group", sa.String(length=128), nullable=False),
        sa.Column("email", sa.String(length=512), nullable=False),
        sa.Column("clear_name", sa.String(length=1024), nullable=False),
        sa.Column("musician", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("is_singer", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("mm_username", sa.String(length=512), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("users") as batch_op:
        batch_op.create_index(batch_op.f("ix_users_id"), ["id"], unique=False)
        batch_op.create_index(batch_op.f("ix_users_user_name"), ["user_name"], unique=True)

    op.create_table(
        "used_password_reset_tokens",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("used_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("used_password_reset_tokens") as batch_op:
        batch_op.create_index(batch_op.f("ix_used_password_reset_tokens_id"), ["id"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_used_password_reset_tokens_token_hash"), ["token_hash"], unique=True
        )

    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("revoked", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("refresh_tokens") as batch_op:
        batch_op.create_index(batch_op.f("ix_refresh_tokens_id"), ["id"], unique=False)
        batch_op.create_index(batch_op.f("ix_refresh_tokens_token_hash"), ["token_hash"], unique=True)

    op.create_table(
        "token_blacklist",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("blacklisted_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("token_blacklist") as batch_op:
        batch_op.create_index(batch_op.f("ix_token_blacklist_id"), ["id"], unique=False)
        batch_op.create_index(batch_op.f("ix_token_blacklist_token_hash"), ["token_hash"], unique=True)


def downgrade() -> None:
    op.drop_table("token_blacklist")
    op.drop_table("refresh_tokens")
    op.drop_table("used_password_reset_tokens")
    op.drop_table("users")

