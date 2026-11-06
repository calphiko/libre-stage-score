import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import auth, models
from backend import database as database_module
from backend.main import app
from backend.routers import admin


@pytest.fixture
def sync_admin_client(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    testing_session_local = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    models.Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(database_module, "engine", engine)
    monkeypatch.setattr(database_module, "SessionLocal", testing_session_local)
    monkeypatch.setattr(auth.database, "engine", engine)
    monkeypatch.setattr(auth.database, "SessionLocal", testing_session_local)

    def get_test_db():
        with testing_session_local() as session:
            yield session

    app.dependency_overrides[auth.get_db] = get_test_db
    app.dependency_overrides[admin.user_is_admin] = lambda: {"user_group": "admin"}
    app.dependency_overrides[admin.user_is_editor_or_admin] = lambda: {"user_group": "admin"}
    local_node_id = str(uuid.uuid4())
    with testing_session_local() as session:
        session.add(models.NodeIdentity(id=1, node_id=local_node_id))
        session.commit()
    monkeypatch.setenv("SYNC_ENABLED", "false")
    monkeypatch.setenv("SYNC_INTERVAL_SECONDS", "30")
    monkeypatch.setenv("SYNC_TEST_ADMIN_PEER_KEY", "admin-test-secret-that-is-at-least-32-bytes")
    with TestClient(app, base_url="https://testserver") as client:
        yield client, local_node_id, testing_session_local
    app.dependency_overrides.clear()
    models.Base.metadata.drop_all(bind=engine)
    engine.dispose()


def test_sync_admin_can_create_pause_and_remove_peers(sync_admin_client):
    client, local_node_id, session_local = sync_admin_client
    peer_id = str(uuid.uuid4())
    create = client.post(
        "/admin/sync/peers",
        json={
            "peer_id": peer_id,
            "url": "https://peer.example",
            "credentials_reference": "SYNC_TEST_ADMIN_PEER_KEY",
        },
    )
    assert create.status_code == 201
    assert create.json()["peer_id"] == peer_id
    assert "secret" not in create.text

    paused = client.put(f"/admin/sync/peers/{peer_id}", json={"active": False})
    assert paused.status_code == 200
    assert paused.json()["active"] is False
    status = client.get("/admin/sync/status")
    assert status.status_code == 200
    assert status.json()["enabled"] is False
    assert status.json()["peers"][0]["reachability"] == "paused"

    removed = client.delete(f"/admin/sync/peers/{peer_id}")
    assert removed.status_code == 200
    assert removed.json()["deleted"] is True
    with session_local() as session:
        assert session.get(models.NodeIdentity, 1).node_id == local_node_id
        assert session.get(models.SyncPeer, peer_id) is None


def test_sync_admin_rejects_insecure_remote_peer_urls(sync_admin_client):
    client, _local_node_id, _session_local = sync_admin_client
    response = client.post(
        "/admin/sync/peers",
        json={
            "peer_id": str(uuid.uuid4()),
            "url": "http://peer.example",
            "credentials_reference": "SYNC_TEST_ADMIN_PEER_KEY",
        },
    )
    assert response.status_code == 422


def test_sync_admin_conflict_resolution_hides_payload_and_records_note(sync_admin_client):
    client, local_node_id, session_local = sync_admin_client
    conflict_id = str(uuid.uuid4())
    with session_local() as session:
        session.add(
            models.SyncConflict(
                conflict_id=conflict_id,
                table_name="users",
                record_uuid=str(uuid.uuid4()),
                local_payload={"user_name": "local-user", "user_pw": "sensitive-hash"},
                remote_payload={"user_name": "remote-user", "user_pw": "other-sensitive-hash"},
                remote_node_id=local_node_id,
                reason="local_wins",
            )
        )
        session.commit()

    listing = client.get("/admin/sync/conflicts")
    assert listing.status_code == 200
    assert len(listing.json()) == 1
    assert "sensitive-hash" not in listing.text
    resolved = client.post(
        f"/admin/sync/conflicts/{conflict_id}/resolve",
        json={"resolution": "Manuell geprüft"},
    )
    assert resolved.status_code == 200
    with session_local() as session:
        conflict = session.get(models.SyncConflict, conflict_id)
        assert conflict.status == "resolved"
        assert conflict.resolution == "Manuell geprüft"
        assert conflict.resolved_at is not None


def test_sync_admin_lists_score_blobs_missing_from_local_storage(sync_admin_client, monkeypatch):
    client, _local_node_id, session_local = sync_admin_client
    digest = "a" * 64
    monkeypatch.setattr(admin.sync_service, "file_blob_path", lambda _digest: admin.PROJECT_ROOT / "missing.pdf")
    monkeypatch.setattr(admin.sync_service, "find_score_blob", lambda _session, _digest: None)
    with session_local() as session:
        song = models.Song(name="Missing PDF")
        instrument = models.Instrument(instrument_name="Flute", instrument_tuning="C")
        session.add_all([song, instrument])
        session.flush()
        score = models.Score(
            song_id=song.id,
            instrument_id=instrument.id,
            storage_path="missing.pdf",
            file_hash=digest,
        )
        session.add(score)
        session.commit()
        score_id = score.id

    response = client.get("/admin/sync/blobs")
    assert response.status_code == 200
    assert response.json() == [{
        "sha256": digest,
        "status": "missing",
        "size_bytes": None,
        "score_ids": [score_id],
    }]
