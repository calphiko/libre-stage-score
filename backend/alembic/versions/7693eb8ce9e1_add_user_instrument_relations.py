"""add songs, scores, and remove legacy user fields

Revision ID: 7693eb8ce9e1
Revises: 20260825_0002
Create Date: 2026-08-25 09:50:09.361197
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "7693eb8ce9e1"
down_revision: Union[str, Sequence[str], None] = "20260825_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "songs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=512), nullable=False),
        sa.Column("tune", sa.String(length=32), nullable=True),
        sa.Column("composer", sa.String(length=1024), nullable=True),
        sa.Column("arrangement", sa.String(length=1024), nullable=True),
        sa.Column("length", sa.Time(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("songs") as batch_op:
        batch_op.create_index(batch_op.f("ix_songs_arrangement"), ["arrangement"], unique=False)
        batch_op.create_index(batch_op.f("ix_songs_composer"), ["composer"], unique=False)
        batch_op.create_index(batch_op.f("ix_songs_id"), ["id"], unique=False)
        batch_op.create_index(batch_op.f("ix_songs_name"), ["name"], unique=False)

    op.create_table(
        "scores",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("song_id", sa.Integer(), nullable=False),
        sa.Column("instrument_id", sa.Integer(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["instrument_id"], ["instruments.id"]),
        sa.ForeignKeyConstraint(["song_id"], ["songs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("scores") as batch_op:
        batch_op.create_index(batch_op.f("ix_scores_id"), ["id"], unique=False)

    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("musician")
        batch_op.drop_column("mm_username")
        batch_op.drop_column("is_singer")


def downgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(sa.Column("is_singer", sa.Boolean(), server_default=sa.text("0"), nullable=False))
        batch_op.add_column(sa.Column("mm_username", sa.String(length=512), nullable=True))
        batch_op.add_column(sa.Column("musician", sa.Boolean(), server_default=sa.text("0"), nullable=False))

    with op.batch_alter_table("scores") as batch_op:
        batch_op.drop_index(batch_op.f("ix_scores_id"))
    op.drop_table("scores")

    with op.batch_alter_table("songs") as batch_op:
        batch_op.drop_index(batch_op.f("ix_songs_name"))
        batch_op.drop_index(batch_op.f("ix_songs_id"))
        batch_op.drop_index(batch_op.f("ix_songs_composer"))
        batch_op.drop_index(batch_op.f("ix_songs_arrangement"))
    op.drop_table("songs")
