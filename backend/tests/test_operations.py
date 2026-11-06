import json
import uuid
import zipfile
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend import models
import backend.operations as operations


def test_snapshot_export_and_restore_starts_new_node_from_snapshot(monkeypatch, tmp_path):
    source_path = tmp_path / "hub.sqlite3"
    source_engine = create_engine(f"sqlite:///{source_path}")
    source_sessions = sessionmaker(bind=source_engine)
    models.Base.metadata.create_all(source_engine)
    source_node_id = str(uuid.uuid4())
    target_node_id = str(uuid.uuid4())
    with source_sessions() as session:
        session.add(models.NodeIdentity(id=1, node_id=source_node_id, name="Hub"))
        session.add(
            models.SyncPeer(
                peer_id=target_node_id,
                url="https://node.example",
                credentials_reference="NODE_SYNC_TOKEN",
            )
        )
        session.add(models.Group(name="Snapshot group"))
        session.commit()
        snapshot_cursor = session.scalar(select(models.ChangeLog.seq).order_by(models.ChangeLog.seq.desc()))

    monkeypatch.setattr(operations, "engine", source_engine)
    monkeypatch.setattr(operations, "SessionLocal", source_sessions)
    monkeypatch.setattr(operations, "SQLALCHEMY_DATABASE_URL", f"sqlite:///{source_path}")
    monkeypatch.setattr(operations, "file_blob_path", lambda _digest: tmp_path / "blobs" / "00" / "blob.pdf")
    output = tmp_path / "hub-snapshot.zip"
    operations.export_snapshot(output)
    target_path = tmp_path / "node.sqlite3"
    target_path = tmp_path / "node.sqlite3"
    monkeypatch.setattr(operations, "SQLALCHEMY_DATABASE_URL", f"sqlite:///{target_path}")
    monkeypatch.setenv("NODE_ID", target_node_id)
    restored_id = operations.restore_snapshot(
        output,
        "https://hub.example",
        "HUB_SYNC_TOKEN",
        "Concert hall",
    )

    assert restored_id == target_node_id
    with zipfile.ZipFile(output) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["source_node_id"] == source_node_id
        assert manifest["change_cursor"] == snapshot_cursor
        assert manifest["blob_content_included"] is False
        assert json.loads(archive.read("blob-index.json")) == []

    restored_engine = create_engine(f"sqlite:///{target_path}")
    restored_sessions = sessionmaker(bind=restored_engine)
    with restored_sessions() as session:
        identity = session.scalar(select(models.NodeIdentity))
        assert identity.node_id == target_node_id
        assert identity.name == "Concert hall"
        assert session.scalar(select(models.Group.name)) == "Snapshot group"
        assert session.scalar(select(models.ChangeLog.seq)) is None
        peer = session.get(models.SyncPeer, source_node_id)
        assert peer is not None
        assert peer.url == "https://hub.example"
        cursor = session.get(models.SyncCursor, source_node_id)
        assert cursor.last_received == snapshot_cursor
        assert cursor.last_sent == 0

    monkeypatch.setattr(operations, "SessionLocal", source_sessions)
    operations.acknowledge_snapshot(target_node_id, snapshot_cursor)
    with source_sessions() as session:
        hub_cursor = session.get(models.SyncCursor, target_node_id)
        assert hub_cursor.last_sent == snapshot_cursor
    restored_engine.dispose()
    source_engine.dispose()


def test_restore_refuses_to_overwrite_a_nonempty_database(monkeypatch, tmp_path):
    target_path = tmp_path / "existing.sqlite3"
    with create_engine(f"sqlite:///{target_path}").begin() as connection:
        connection.exec_driver_sql("CREATE TABLE protected_data (value TEXT)")
    monkeypatch.setattr(operations, "SQLALCHEMY_DATABASE_URL", f"sqlite:///{target_path}")

    try:
        operations.restore_snapshot(
            tmp_path / "not-opened.zip",
            "https://hub.example",
            "HUB_SYNC_TOKEN",
        )
    except RuntimeError as exc:
        assert "Refusing to overwrite" in str(exc)
    else:
        raise AssertionError("restore should reject a populated database")
