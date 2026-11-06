"""track the last successful peer exchange

Revision ID: 20261030_0008
Revises: 20261030_0007
Create Date: 2026-10-30
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20261030_0008"
down_revision: Union[str, Sequence[str], None] = "20261030_0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "sync_cursors",
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("sync_cursors", "last_success_at")
