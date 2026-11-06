"""add audit records for incoming sync changes

Revision ID: 20261030_0009
Revises: 20261030_0008
Create Date: 2026-10-30
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20261030_0009"
down_revision: Union[str, Sequence[str], None] = "20261030_0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "sync_audit",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("remote_node_id", sa.String(length=36), nullable=False),
        sa.Column("change_seq", sa.BigInteger(), nullable=False),
        sa.Column("table_name", sa.String(length=128), nullable=False),
        sa.Column("record_uuid", sa.String(length=36), nullable=False),
        sa.Column("operation", sa.String(length=16), nullable=False),
        sa.Column("hlc", sa.BigInteger(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_sync_audit_remote_node_id", "sync_audit", ["remote_node_id"])


def downgrade() -> None:
    op.drop_index("ix_sync_audit_remote_node_id", table_name="sync_audit")
    op.drop_table("sync_audit")
