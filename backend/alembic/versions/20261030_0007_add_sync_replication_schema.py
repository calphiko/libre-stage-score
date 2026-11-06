"""add replication identity and synchronization state

Revision ID: 20261030_0007
Revises: f9b8ee453dab
Create Date: 2026-10-30
"""

import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from backend import models
from backend.sync.clock import new_uuid7, next_hlc
from backend.sync.identity import configured_node_id


revision: str = "20261030_0007"
down_revision: Union[str, Sequence[str], None] = "f9b8ee453dab"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


REPLICATED_TABLES = (
    "users",
    "groups",
    "user_groups",
    "document_access",
    "instruments",
    "song_collections",
    "collections",
    "ensembles",
    "song_storage_physical",
    "kind_of_song",
    "difficulty_level",
    "song_rythm",
    "song_uploads",
    "songs",
    "scores",
    "user_instruments",
    "group_instruments",
)


def upgrade() -> None:
    connection = op.get_bind()
    configured_id = configured_node_id()
    existing_tables = set(sa.inspect(connection).get_table_names())
    missing_replicated_tables = set(REPLICATED_TABLES) - existing_tables
    if missing_replicated_tables:
        models.Base.metadata.create_all(
            bind=connection,
            tables=[models.Base.metadata.tables[name] for name in missing_replicated_tables],
        )
        existing_tables.update(missing_replicated_tables)

    if "node_identity" not in existing_tables:
        op.create_table(
            "node_identity",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("node_id", sa.String(length=36), nullable=False, unique=True),
            sa.Column("name", sa.String(length=128), nullable=False),
            sa.Column("key_reference", sa.String(length=512), nullable=True),
        )
        existing_tables.add("node_identity")
    node_identity = sa.Table("node_identity", sa.MetaData(), autoload_with=connection)
    node_id = connection.execute(sa.select(node_identity.c.node_id).limit(1)).scalar_one_or_none()
    if node_id is None:
        node_id = configured_id or new_uuid7()
        connection.execute(
            node_identity.insert().values(id=1, node_id=node_id, name="local-node", key_reference=None)
        )
    elif configured_id is not None and str(uuid.UUID(node_id)) != configured_id:
        raise ValueError("NODE_ID does not match the persisted node identity")

    if "sync_peers" not in existing_tables:
        op.create_table(
            "sync_peers",
            sa.Column("peer_id", sa.String(length=36), primary_key=True),
            sa.Column("url", sa.String(length=2048), nullable=False),
            sa.Column("credentials_reference", sa.String(length=512), nullable=True),
            sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        )
        existing_tables.add("sync_peers")
    if "sync_cursors" not in existing_tables:
        op.create_table(
            "sync_cursors",
            sa.Column(
                "peer_id",
                sa.String(length=36),
                sa.ForeignKey("sync_peers.peer_id", ondelete="CASCADE"),
                primary_key=True,
            ),
            sa.Column("last_sent", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("last_received", sa.BigInteger(), nullable=False, server_default="0"),
        )
        existing_tables.add("sync_cursors")
    if "change_log" not in existing_tables:
        op.create_table(
            "change_log",
            sa.Column("seq", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("table_name", sa.String(length=128), nullable=False),
            sa.Column("record_uuid", sa.String(length=36), nullable=False),
            sa.Column("operation", sa.String(length=16), nullable=False),
            sa.Column("payload", sa.JSON(), nullable=False),
            sa.Column("hlc", sa.BigInteger(), nullable=False),
            sa.Column("node_id", sa.String(length=36), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        existing_tables.add("change_log")
    change_log_indexes = {index["name"] for index in sa.inspect(connection).get_indexes("change_log")}
    if "ix_change_log_table_name" not in change_log_indexes:
        op.create_index("ix_change_log_table_name", "change_log", ["table_name"])
    if "ix_change_log_record_uuid" not in change_log_indexes:
        op.create_index("ix_change_log_record_uuid", "change_log", ["record_uuid"])
    if "sync_conflicts" not in existing_tables:
        op.create_table(
            "sync_conflicts",
            sa.Column("conflict_id", sa.String(length=36), primary_key=True),
            sa.Column("table_name", sa.String(length=128), nullable=False),
            sa.Column("record_uuid", sa.String(length=36), nullable=False),
            sa.Column("local_payload", sa.JSON(), nullable=True),
            sa.Column("remote_payload", sa.JSON(), nullable=True),
            sa.Column("remote_node_id", sa.String(length=36), nullable=False),
            sa.Column("reason", sa.String(length=128), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False),
            sa.Column("resolution", sa.Text(), nullable=True),
            sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        )
        existing_tables.add("sync_conflicts")
    if "file_blobs" not in existing_tables:
        op.create_table(
            "file_blobs",
            sa.Column("sha256", sa.String(length=64), primary_key=True),
            sa.Column("size_bytes", sa.BigInteger(), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False),
            sa.Column("updated_at", sa.BigInteger(), nullable=False),
            sa.CheckConstraint("status IN ('missing', 'present', 'transferring')", name="ck_file_blobs_status"),
        )
        existing_tables.add("file_blobs")

    metadata_columns = (
        ("uuid", sa.String(length=36), None),
        ("updated_at", sa.BigInteger(), None),
        ("updated_by_node", sa.String(length=36), None),
        ("version", sa.Integer(), "1"),
        ("deleted_at", sa.BigInteger(), None),
    )
    for table_name in REPLICATED_TABLES:
        if table_name in missing_replicated_tables:
            continue
        existing_columns = {column["name"] for column in sa.inspect(connection).get_columns(table_name)}
        with op.batch_alter_table(table_name) as batch_op:
            for name, column_type, server_default in metadata_columns:
                if name not in existing_columns:
                    batch_op.add_column(
                        sa.Column(name, column_type, nullable=True, server_default=server_default)
                    )

    for table_name in REPLICATED_TABLES:
        if table_name in missing_replicated_tables:
            continue
        table = sa.Table(table_name, sa.MetaData(), autoload_with=connection)
        primary_key_columns = tuple(table.primary_key.columns)
        metadata_names = ("uuid", "updated_at", "updated_by_node", "version", "deleted_at")
        primary_key_rows = connection.execute(sa.select(*primary_key_columns, *(table.c[name] for name in metadata_names))).all()
        for primary_key_values in primary_key_rows:
            existing_values = primary_key_values[len(primary_key_columns) :]
            values_to_backfill = {}
            if existing_values[0] is None:
                values_to_backfill["uuid"] = new_uuid7()
            if existing_values[1] is None:
                values_to_backfill["updated_at"] = next_hlc()
            if existing_values[2] is None or existing_values[2] == "unconfigured":
                values_to_backfill["updated_by_node"] = node_id
            if existing_values[3] is None:
                values_to_backfill["version"] = 1
            if not values_to_backfill:
                continue
            primary_key_filter = sa.and_(
                *(
                    column == value
                    for column, value in zip(
                        primary_key_columns,
                        primary_key_values[: len(primary_key_columns)],
                        strict=True,
                    )
                )
            )
            connection.execute(
                table.update().where(primary_key_filter).values(**values_to_backfill)
            )

        column_nullability = {
            column["name"]: column["nullable"] for column in sa.inspect(connection).get_columns(table_name)
        }
        with op.batch_alter_table(table_name) as batch_op:
            for name, existing_type in (
                ("uuid", sa.String(length=36)),
                ("updated_at", sa.BigInteger()),
                ("updated_by_node", sa.String(length=36)),
                ("version", sa.Integer()),
            ):
                if column_nullability[name]:
                    batch_op.alter_column(name, existing_type=existing_type, nullable=False)
        index_names = {index["name"] for index in sa.inspect(connection).get_indexes(table_name)}
        if f"ix_{table_name}_uuid" not in index_names:
            op.create_index(f"ix_{table_name}_uuid", table_name, ["uuid"], unique=True)


def downgrade() -> None:
    for table_name in reversed(REPLICATED_TABLES):
        with op.batch_alter_table(table_name) as batch_op:
            batch_op.drop_index(f"ix_{table_name}_uuid")
            batch_op.drop_column("deleted_at")
            batch_op.drop_column("version")
            batch_op.drop_column("updated_by_node")
            batch_op.drop_column("updated_at")
            batch_op.drop_column("uuid")

    op.drop_table("file_blobs")
    op.drop_table("sync_conflicts")
    op.drop_index("ix_change_log_record_uuid", table_name="change_log")
    op.drop_index("ix_change_log_table_name", table_name="change_log")
    op.drop_table("change_log")
    op.drop_table("sync_cursors")
    op.drop_table("sync_peers")
    op.drop_table("node_identity")
    # ORM tables absent from earlier Alembic revisions are retained on downgrade.
