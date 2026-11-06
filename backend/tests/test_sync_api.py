import hashlib
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import auth, models
from backend import database as database_module
from backend.main import app
from backend.sync.clock import next_hlc
from backend.sync import service
from backend.sync.outbox import soft_delete
from backend.sync import peer_auth
import backend.main as main_module


@pytest.fixture
def sync_client(monkeypatch, tmp_path):
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
    monkeypatch.setattr(main_module, "engine", engine)
    local_node_id = str(uuid.uuid4())
    peer_id = str(uuid.uuid4())
    with testing_session_local() as session:
        session.add(models.NodeIdentity(id=1, node_id=local_node_id))
        session.add(
            models.SyncPeer(
                peer_id=peer_id,
                url="https://peer.example",
                credentials_reference="SYNC_TEST_PEER_TOKEN",
            )
        )
        session.commit()
    monkeypatch.setenv("SYNC_TEST_PEER_TOKEN", "test-peer-secret-for-sync-key-32bytes")
    client = TestClient(app, base_url="https://testserver")
    yield client, peer_id, local_node_id
    client.close()
    models.Base.metadata.drop_all(bind=engine)
    engine.dispose()


def _peer_headers(peer_id):
    with database_module.SessionLocal() as session:
        local_node_id = service.get_local_node_id(session)
    token = peer_auth.create_token(local_node_id, peer_id, "SYNC_TEST_PEER_TOKEN")
    headers = {
        "X-Sync-Node-ID": peer_id,
        "X-Sync-Protocol-Version": "1",
        "X-Sync-Schema-Version": "1",
    }
    headers["Authorization"] = f"Bearer {token}"
    return headers


def test_sync_endpoints_require_active_peer_authentication(sync_client):
    client, peer_id, _local_node_id = sync_client

    response = client.get("/sync/hello")
    assert response.status_code == 401

    headers = _peer_headers(peer_id)
    response = client.get("/sync/hello", headers=headers)
    assert response.status_code == 200
    assert response.json()["protocol_version"] == 1
    assert response.json()["cursor"] == 0
    invalid = client.get("/sync/hello", headers={**headers, "Authorization": "Bearer invalid"})
    assert invalid.status_code == 401
    incompatible = client.get(
        "/sync/changes",
        headers={**headers, "X-Sync-Protocol-Version": "999"},
    )
    assert incompatible.status_code == 426


def test_peer_key_rotation_accepts_previous_key_until_removed(sync_client, monkeypatch):
    client, peer_id, local_node_id = sync_client
    monkeypatch.setenv("SYNC_TEST_OLD_PEER_TOKEN", "old-peer-secret-for-sync-key-32bytes")
    with database_module.SessionLocal() as session:
        peer = session.get(models.SyncPeer, peer_id)
        peer.credentials_reference = "SYNC_TEST_PEER_TOKEN,SYNC_TEST_OLD_PEER_TOKEN"
        session.commit()

    old_token = peer_auth.create_token(local_node_id, peer_id, "SYNC_TEST_OLD_PEER_TOKEN")
    response = client.get(
        "/sync/hello",
        headers={
            "Authorization": f"Bearer {old_token}",
            "X-Sync-Node-ID": peer_id,
        },
    )
    assert response.status_code == 200


def test_sync_requires_tls_outside_loopback(sync_client):
    _client, peer_id, _local_node_id = sync_client
    with TestClient(app, base_url="http://peer.example") as insecure_client:
        response = insecure_client.get("/sync/hello", headers=_peer_headers(peer_id))
    assert response.status_code == 400


def test_sync_rate_limit_applies_per_authenticated_peer(sync_client, monkeypatch):
    client, peer_id, _local_node_id = sync_client
    monkeypatch.setenv("SYNC_RATE_LIMIT_PER_MINUTE", "1")
    headers = _peer_headers(peer_id)

    assert client.get("/sync/hello", headers=headers).status_code == 200
    limited = client.get("/sync/hello", headers=headers)
    assert limited.status_code == 429
    assert int(limited.headers["retry-after"]) > 0


def test_sync_rejects_oversized_request_body(sync_client):
    client, _peer_id, _local_node_id = sync_client
    response = client.post("/sync/changes", content=b"x" * (17 * 1024 * 1024 + 1))
    assert response.status_code == 413


def test_changes_are_imported_idempotently_and_cursor_is_acknowledged(sync_client):
    client, peer_id, _local_node_id = sync_client
    group_uuid = str(uuid.uuid4())
    remote_hlc = next_hlc()
    change = {
        "seq": 1,
        "table_name": "groups",
        "record_uuid": group_uuid,
        "operation": "insert",
        "payload": {
            "uuid": group_uuid,
            "name": "Remote choir",
            "notes": None,
            "updated_at": remote_hlc,
            "updated_by_node": peer_id,
            "version": 1,
            "deleted_at": None,
        },
        "hlc": remote_hlc,
        "node_id": peer_id,
    }

    response = client.post("/sync/changes", headers=_peer_headers(peer_id), json={"changes": [change]})
    assert response.status_code == 200
    assert response.json() == {"accepted_through": 1, "accepted_count": 1}

    repeated = client.post("/sync/changes", headers=_peer_headers(peer_id), json={"changes": [change]})
    assert repeated.status_code == 200
    assert repeated.json()["accepted_through"] == 1
    with database_module.SessionLocal() as session:
        audits = session.query(models.SyncAudit).all()
        assert len(audits) == 2
        assert {audit.remote_node_id for audit in audits} == {peer_id}
        assert {audit.table_name for audit in audits} == {"groups"}
    response = client.get("/sync/status", headers=_peer_headers(peer_id))
    assert response.status_code == 200
    assert response.json()["last_received"] == 1
    assert response.json()["last_successful_sync"] is not None


def test_outgoing_changes_are_paginated_and_acknowledgements_recorded(sync_client):
    client, peer_id, local_node_id = sync_client
    with database_module.SessionLocal() as session:
        session.add(models.Group(name="Local group"))
        session.commit()

    response = client.get("/sync/changes?since=0&limit=1", headers=_peer_headers(peer_id))
    assert response.status_code == 200
    body = response.json()
    assert len(body["changes"]) == 1
    assert body["changes"][0]["node_id"] == local_node_id
    assert body["next_cursor"] == body["changes"][0]["seq"]
    assert body["has_more"] is False

    acknowledged = client.get(
        f"/sync/changes?since={body['next_cursor']}&limit=1",
        headers=_peer_headers(peer_id),
    )
    assert acknowledged.status_code == 200
    assert acknowledged.json()["changes"] == []
    with database_module.SessionLocal() as session:
        cursor = session.get(models.SyncCursor, peer_id)
        assert cursor is not None
        assert cursor.last_sent == body["next_cursor"]


def test_import_resolves_composite_and_polymorphic_foreign_keys(sync_client):
    client, peer_id, _local_node_id = sync_client
    song_uuid = str(uuid.uuid4())
    collection_uuid = str(uuid.uuid4())
    link_uuid = str(uuid.uuid4())
    access_uuid = str(uuid.uuid4())
    changes = []
    for seq, table_name, record_uuid, values in (
        (1, "songs", song_uuid, {"name": "Remote song"}),
        (2, "collections", collection_uuid, {"name": "Remote collection"}),
        (
            3,
            "song_collections",
            link_uuid,
            {"song_id": song_uuid, "collection_id": collection_uuid},
        ),
        (
            4,
            "document_access",
            access_uuid,
            {"document_type": "song", "document_id": song_uuid, "user_id": None, "group_id": None},
        ),
    ):
        hlc = next_hlc()
        payload = {
            **values,
            "uuid": record_uuid,
            "updated_at": hlc,
            "updated_by_node": peer_id,
            "version": 1,
            "deleted_at": None,
        }
        changes.append(
            {
                "seq": seq,
                "table_name": table_name,
                "record_uuid": record_uuid,
                "operation": "insert",
                "payload": payload,
                "hlc": hlc,
                "node_id": peer_id,
            }
        )

    response = client.post(
        "/sync/changes",
        headers=_peer_headers(peer_id),
        json={"changes": changes},
    )
    assert response.status_code == 200
    with database_module.SessionLocal() as session:
        song = session.query(models.Song).filter_by(uuid=song_uuid).one()
        collection = session.query(models.Collection).filter_by(uuid=collection_uuid).one()
        link = session.query(models.SongCollection).filter_by(uuid=link_uuid).one()
        access = session.query(models.DocumentAccess).filter_by(uuid=access_uuid).one()
        assert link.song_id == song.id
        assert link.collection_id == collection.id
        assert access.document_id == song.id


def test_import_rejects_score_without_content_hash(sync_client):
    client, peer_id, _local_node_id = sync_client
    song_uuid = str(uuid.uuid4())
    instrument_uuid = str(uuid.uuid4())
    score_uuid = str(uuid.uuid4())
    changes = []
    records = (
        ("songs", song_uuid, {"name": "Hash-required song"}),
        ("instruments", instrument_uuid, {"instrument_name": "Hash-required instrument", "instrument_tuning": "C"}),
        (
            "scores",
            score_uuid,
            {
                "song_id": song_uuid,
                "instrument_id": instrument_uuid,
                "storage_path": None,
                "file_hash": None,
                "notes": None,
            },
        ),
    )
    for seq, (table_name, record_uuid, values) in enumerate(records, start=1):
        hlc = next_hlc()
        changes.append(
            {
                "seq": seq,
                "table_name": table_name,
                "record_uuid": record_uuid,
                "operation": "insert",
                "payload": {
                    **values,
                    "uuid": record_uuid,
                    "updated_at": hlc,
                    "updated_by_node": peer_id,
                    "version": 1,
                    "deleted_at": None,
                },
                "hlc": hlc,
                "node_id": peer_id,
            }
        )

    response = client.post("/sync/changes", headers=_peer_headers(peer_id), json={"changes": changes})
    assert response.status_code == 422
    with database_module.SessionLocal() as session:
        assert session.query(models.Song).filter_by(uuid=song_uuid).first() is None
        assert session.query(models.Instrument).filter_by(uuid=instrument_uuid).first() is None
        assert session.query(models.Score).filter_by(uuid=score_uuid).first() is None
        assert session.query(models.SyncAudit).count() == 0


def test_tombstones_and_normalized_name_conflicts_are_visible(sync_client):
    client, peer_id, _local_node_id = sync_client
    with database_module.SessionLocal() as session:
        deleted_group = models.Group(name="Deleted remotely")
        existing_group = models.Group(name="Mixed Name")
        session.add_all((deleted_group, existing_group))
        session.commit()
        deleted_uuid = deleted_group.uuid
        soft_delete(session, deleted_group)
        session.commit()
        deleted_hlc = deleted_group.deleted_at

    incoming = []
    for seq, record_uuid, name, hlc in (
        (1, deleted_uuid, "Deleted remotely", next_hlc(deleted_hlc)),
        (2, str(uuid.uuid4()), " mixed name ", next_hlc()),
    ):
        payload = {
            "uuid": record_uuid,
            "name": name,
            "notes": None,
            "updated_at": hlc,
            "updated_by_node": peer_id,
            "version": 2,
            "deleted_at": None,
        }
        incoming.append(
            {
                "seq": seq,
                "table_name": "groups",
                "record_uuid": record_uuid,
                "operation": "update",
                "payload": payload,
                "hlc": hlc,
                "node_id": peer_id,
            }
        )
    response = client.post(
        "/sync/changes",
        headers=_peer_headers(peer_id),
        json={"changes": incoming},
    )
    assert response.status_code == 200
    with database_module.SessionLocal() as session:
        assert session.query(models.Group).filter_by(uuid=deleted_uuid).first() is None
        conflicts = session.query(models.SyncConflict).order_by(models.SyncConflict.reason).all()
        assert {conflict.reason for conflict in conflicts} == {"tombstone_wins", "unique_name_collision"}
        assert {conflict.remote_node_id for conflict in conflicts} == {peer_id}


def test_blob_transfer_resumes_and_download_supports_ranges(sync_client, monkeypatch, tmp_path):
    client, peer_id, _local_node_id = sync_client
    contents = b"%PDF-1.7\nsample sync blob\n"
    digest = hashlib.sha256(contents).hexdigest()
    monkeypatch.setattr(
        "backend.sync.service.file_blob_path",
        lambda _sha256: tmp_path / digest[:2] / f"{digest}.pdf",
    )
    split = 10

    first = client.put(
        f"/sync/blobs/{digest}",
        headers={**_peer_headers(peer_id), "Content-Range": f"bytes 0-{split - 1}/{len(contents)}"},
        files={"file": ("chunk.pdf", contents[:split], "application/pdf")},
    )
    assert first.status_code == 200
    assert first.json()["status"] == "transferring"
    assert first.json()["next_offset"] == split

    second = client.put(
        f"/sync/blobs/{digest}",
        headers={**_peer_headers(peer_id), "Content-Range": f"bytes {split}-{len(contents) - 1}/{len(contents)}"},
        files={"file": ("chunk.pdf", contents[split:], "application/pdf")},
    )
    assert second.status_code == 200
    assert second.json()["status"] == "present"

    downloaded = client.get(
        f"/sync/blobs/{digest}",
        headers={**_peer_headers(peer_id), "Range": "bytes=4-8"},
    )
    assert downloaded.status_code == 206
    assert downloaded.content == contents[4:9]
    assert downloaded.headers["content-range"] == f"bytes 4-8/{len(contents)}"


def test_score_document_fetches_missing_blob_only_after_authorization(sync_client, monkeypatch, tmp_path):
    client, _peer_id, _local_node_id = sync_client
    contents = b"%PDF-1.7\non-demand score\n"
    digest = hashlib.sha256(contents).hexdigest()
    blob_path = tmp_path / digest[:2] / f"{digest}.pdf"
    monkeypatch.setattr(service, "file_blob_path", lambda _digest: blob_path)
    monkeypatch.setenv("SYNC_PDF_STRATEGY", "metadata")

    with database_module.SessionLocal() as session:
        user = models.User(
            user_name="document-admin",
            user_pw="hashed",
            user_group="admin",
            email="admin@example.test",
            clear_name="Document Admin",
        )
        song = models.Song(name="On-demand song")
        instrument = models.Instrument(instrument_name="On-demand instrument", instrument_tuning="C")
        session.add_all((user, song, instrument))
        session.flush()
        score = models.Score(song_id=song.id, instrument_id=instrument.id, file_hash=digest)
        session.add(score)
        session.commit()
        score_id = score.id
        token = auth.create_access_token({"sub": user.user_name, "role": user.user_group})

    fetches = []

    def fetch_blob(requested_digest):
        fetches.append(requested_digest)
        blob_path.parent.mkdir(parents=True, exist_ok=True)
        blob_path.write_bytes(contents)
        return True

    monkeypatch.setattr(main_module.sync_worker, "fetch_blob_from_peers", fetch_blob)
    headers = {"Authorization": f"Bearer {token}"}

    metadata_response = client.get(f"/scores/{score_id}/document", headers=headers)
    assert metadata_response.status_code == 503
    assert fetches == []

    monkeypatch.setenv("SYNC_PDF_STRATEGY", "on_access")
    response = client.get(f"/scores/{score_id}/document", headers=headers)
    assert response.status_code == 200
    assert response.content == contents
    assert fetches == [digest]
