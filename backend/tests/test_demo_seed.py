import uuid
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import Session

from backend import database, models
from backend.migrations import init_demo_users as demo_seed
from backend.routers import admin
from backend.sync import service as sync_service


def test_demo_seed_upgrades_legacy_schema_before_seeding(monkeypatch, tmp_path):
    database_path = tmp_path / "demo.sqlite3"
    database_url = f"sqlite:///{database_path}"
    monkeypatch.setattr(database, "SQLALCHEMY_DATABASE_URL", database_url)
    monkeypatch.setattr(demo_seed, "SQLALCHEMY_DATABASE_URL", database_url)
    monkeypatch.setattr(admin, "SCORES_UPLOAD_DIR", tmp_path / "scores")
    monkeypatch.setattr(sync_service, "file_blob_path", lambda _digest: tmp_path / "blobs" / "demo.pdf")
    monkeypatch.delenv("NODE_ID", raising=False)

    # Recreate the schema state immediately before the replication migration.
    project_config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    project_config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(project_config, "20260929_0006")

    legacy_engine = create_engine(database_url)
    with legacy_engine.begin() as connection:
        connection.exec_driver_sql("ALTER TABLE users ADD COLUMN musician BOOLEAN NOT NULL DEFAULT 0")
        connection.exec_driver_sql("ALTER TABLE users ADD COLUMN is_singer BOOLEAN NOT NULL DEFAULT 0")
        connection.exec_driver_sql("ALTER TABLE users ADD COLUMN mm_username VARCHAR(128)")
        assert "uuid" not in {column["name"] for column in inspect(connection).get_columns("users")}
    legacy_engine.dispose()

    demo_seed.run()

    engine = create_engine(database_url)
    with Session(engine) as session:
        columns = {column["name"] for column in inspect(engine).get_columns("users")}
        assert {"uuid", "updated_at", "updated_by_node", "version", "deleted_at"} <= columns

        users = session.scalars(select(models.User).order_by(models.User.user_name)).all()
        assert {user.user_name for user in users} == {"alice", "admin", "bob", "carol", "dave"}
        assert all(uuid.UUID(user.uuid).version == 7 for user in users)
        assert all(user.updated_by_node != "unconfigured" for user in users)

        song_collection = session.scalar(select(models.SongCollection))
        assert song_collection is not None
        assert session.scalar(
            select(models.ChangeLog.seq).where(models.ChangeLog.table_name == "song_collections")
        ) is not None

        score = session.scalar(select(models.Score))
        assert score is not None
        assert len(score.file_hash) == 64
        blob = session.get(models.FileBlob, score.file_hash)
        assert blob is not None
        assert blob.status == "present"
        assert blob.size_bytes > 0

    engine.dispose()
