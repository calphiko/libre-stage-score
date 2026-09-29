"""add group instruments

Revision ID: 20260929_0006
Revises: 20260826_0005
Create Date: 2026-09-29 14:55:00
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260929_0006"
down_revision: Union[str, Sequence[str], None] = "20260826_0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("groups"):
        op.create_table(
            "groups",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(length=128), nullable=False),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("name"),
        )
        op.create_index(op.f("ix_groups_id"), "groups", ["id"], unique=False)
        op.create_index(op.f("ix_groups_name"), "groups", ["name"], unique=False)

    if not inspector.has_table("group_instruments"):
        op.create_table(
            "group_instruments",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("group_id", sa.Integer(), nullable=False),
            sa.Column("instrument_id", sa.Integer(), nullable=False),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.ForeignKeyConstraint(["group_id"], ["groups.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["instrument_id"], ["instruments.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("group_id", "instrument_id", name="uq_group_instrument"),
        )
        op.create_index(op.f("ix_group_instruments_id"), "group_instruments", ["id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_group_instruments_id"), table_name="group_instruments")
    op.drop_table("group_instruments")
