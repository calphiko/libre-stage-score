"""Restore user profile columns used by the current application.

Revision ID: 20261107_0010
Revises: 20261030_0009
"""

import sqlalchemy as sa
from alembic import op


revision = "20261107_0010"
down_revision = "20261030_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("users")}
    columns = (
        sa.Column("musician", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_singer", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("mm_username", sa.String(length=128), nullable=True),
    )
    for column in columns:
        if column.name not in existing:
            op.add_column("users", column)


def downgrade() -> None:
    # Older application versions may already have populated these columns.
    pass
