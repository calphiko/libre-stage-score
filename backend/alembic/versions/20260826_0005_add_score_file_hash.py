"""add score file hash

Revision ID: 20260826_0005
Revises: 20260826_0004
Create Date: 2026-08-26 10:18:00
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260826_0005"
down_revision: Union[str, Sequence[str], None] = "20260826_0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("scores") as batch_op:
        batch_op.add_column(sa.Column("file_hash", sa.String(length=64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("scores") as batch_op:
        batch_op.drop_column("file_hash")
