import importlib.util
import uuid
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session

from backend import models
from backend.sync.clock import new_uuid7, next_hlc


def test_uuid7_generator_returns_version_seven_uuid():
    generated = uuid.UUID(new_uuid7())

    assert generated.version == 7
    assert generated.variant == uuid.RFC_4122


def test_hlc_values_are_monotonic_and_observe_remote_clock():
    first = next_hlc()
    observed = next_hlc(first + (1 << 16))
    following = next_hlc()

    assert first < observed < following


def test_replicated_entities_have_identity_metadata_and_local_auth_tables_do_not():
    replicated_tables = {
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
    }
    metadata_columns = {"uuid", "updated_at", "updated_by_node", "version", "deleted_at"}

    for table_name in replicated_tables:
        assert metadata_columns <= set(models.Base.metadata.tables[table_name].columns.keys())

    assert not metadata_columns & set(models.Base.metadata.tables["refresh_tokens"].columns.keys())


def test_replicated_model_defaults_are_populated_on_insert():
    engine = create_engine("sqlite://")
    models.Base.metadata.create_all(bind=engine)
    node_id = str(uuid.uuid4())

    with Session(engine) as session:
        session.add(models.NodeIdentity(id=1, node_id=node_id))
        session.flush()
        group = models.Group(name="Choir")
        session.add(group)
        session.flush()

        assert uuid.UUID(group.uuid).version == 7
        assert group.updated_at > 0
        assert group.updated_by_node == node_id
        assert group.version == 1
        assert group.deleted_at is None

    engine.dispose()


def test_migration_backfills_existing_rows_and_uses_configured_node_id(monkeypatch):
    node_id = str(uuid.uuid4())
    monkeypatch.setenv("NODE_ID", node_id)
    migration_path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "20261030_0007_add_sync_replication_schema.py"
    )
    spec = importlib.util.spec_from_file_location("sync_schema_migration", migration_path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE users (id INTEGER PRIMARY KEY, user_name VARCHAR(128) NOT NULL)"
        )
        connection.exec_driver_sql("INSERT INTO users (id, user_name) VALUES (17, 'legacy-user')")
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            migration.upgrade()

        replicated_row = connection.exec_driver_sql(
            "SELECT uuid, updated_at, updated_by_node, version, deleted_at FROM users WHERE id = 17"
        ).one()
        persisted_node_id = connection.exec_driver_sql(
            "SELECT node_id FROM node_identity WHERE id = 1"
        ).scalar_one()

        assert uuid.UUID(replicated_row.uuid).version == 7
        assert replicated_row.updated_at > 0
        assert replicated_row.updated_by_node == node_id
        assert replicated_row.version == 1
        assert replicated_row.deleted_at is None
        assert persisted_node_id == node_id

        with Session(bind=connection) as session:
            group = models.Group(name="Migrated node")
            session.add(group)
            session.flush()
            assert group.updated_by_node == node_id

    engine.dispose()


def test_sync_cursor_success_time_migration_adds_nullable_timestamp():
    migration_path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "20261030_0008_add_sync_cursor_success_time.py"
    )
    spec = importlib.util.spec_from_file_location("sync_cursor_migration", migration_path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE sync_cursors (peer_id VARCHAR(36) PRIMARY KEY, last_sent BIGINT NOT NULL, "
            "last_received BIGINT NOT NULL)"
        )
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            migration.upgrade()
        cursor_columns = {column["name"]: column for column in inspect(connection).get_columns("sync_cursors")}
        assert cursor_columns["last_success_at"]["nullable"] is True
    engine.dispose()
