"""add score storage path

Revision ID: 20260825_0003
Revises: 7693eb8ce9e1
Create Date: 2026-08-25 14:28:00
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260825_0003"
down_revision: Union[str, Sequence[str], None] = "7693eb8ce9e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("scores") as batch_op:
        batch_op.add_column(sa.Column("storage_path", sa.String(length=2048), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("scores") as batch_op:
        batch_op.drop_column("storage_path")
