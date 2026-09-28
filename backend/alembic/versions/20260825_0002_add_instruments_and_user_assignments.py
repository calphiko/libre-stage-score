"""add instruments and user assignments

Revision ID: 20260825_0002
Revises: 20260825_0001
Create Date: 2026-08-25 10:00:00
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260825_0002"
down_revision: Union[str, Sequence[str], None] = "20260825_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "instruments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("instrument_name", sa.String(length=128), nullable=False),
        sa.Column("instrument_tuning", sa.String(length=32), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("instruments") as batch_op:
        batch_op.create_index(batch_op.f("ix_instruments_id"), ["id"], unique=False)
        batch_op.create_index(batch_op.f("ix_instruments_instrument_name"), ["instrument_name"], unique=True)

    op.create_table(
        "user_instruments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("instrument_id", sa.Integer(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["instrument_id"], ["instruments.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "instrument_id", name="uq_user_instrument"),
    )
    with op.batch_alter_table("user_instruments") as batch_op:
        batch_op.create_index(batch_op.f("ix_user_instruments_id"), ["id"], unique=False)


def downgrade() -> None:
    op.drop_table("user_instruments")
    op.drop_table("instruments")
