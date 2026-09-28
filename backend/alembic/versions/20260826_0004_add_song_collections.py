"""add song collections

Revision ID: 20260826_0004
Revises: 20260825_0003
Create Date: 2026-08-26 10:05:00
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260826_0004"
down_revision: Union[str, Sequence[str], None] = "20260825_0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "collections",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("collections") as batch_op:
        batch_op.create_index(batch_op.f("ix_collections_id"), ["id"], unique=False)
        batch_op.create_index(batch_op.f("ix_collections_name"), ["name"], unique=True)

    op.create_table(
        "song_collections",
        sa.Column("song_id", sa.Integer(), nullable=False),
        sa.Column("collection_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["collection_id"], ["collections.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["song_id"], ["songs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("song_id", "collection_id"),
    )


def downgrade() -> None:
    op.drop_table("song_collections")
    with op.batch_alter_table("collections") as batch_op:
        batch_op.drop_index(batch_op.f("ix_collections_name"))
        batch_op.drop_index(batch_op.f("ix_collections_id"))
    op.drop_table("collections")
