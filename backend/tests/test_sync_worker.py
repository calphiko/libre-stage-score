import hashlib
import time
import uuid

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import models
from backend import database as database_module
from backend.sync.clock import next_hlc
from backend.sync import service
import backend.sync.worker as worker


@pytest.fixture
def worker_database(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    session_local = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    models.Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(database_module, "SessionLocal", session_local)
    monkeypatch.setattr(worker, "SessionLocal", session_local)
    local_node_id = str(uuid.uuid4())
    peer_id = str(uuid.uuid4())
    with session_local() as session:
        session.add(models.NodeIdentity(id=1, node_id=local_node_id))
        session.add(
            models.SyncPeer(
                peer_id=peer_id,
                url="https://peer.example",
                credentials_reference="SYNC_TEST_PEER_TOKEN",
            )
        )
        session.add(models.Group(name="Local group"))
        session.commit()
    monkeypatch.setenv("SYNC_TEST_PEER_TOKEN", "test-peer-secret-for-sync-key-32bytes")
    yield session_local, peer_id, local_node_id
    models.Base.metadata.drop_all(bind=engine)
    engine.dispose()


def test_worker_pushes_and_pulls_changes_with_confirmed_cursors(worker_database):
    session_local, peer_id, local_node_id = worker_database
    remote_group_uuid = str(uuid.uuid4())
    remote_hlc = next_hlc()
    requests = []

    def handle(request):
        requests.append(request)
        if request.url.path == "/sync/hello":
            return httpx.Response(
                200,
                json={
                    "node_id": peer_id,
                    "protocol_version": 1,
                    "schema_version": 1,
                    "cursor": 0,
                    "peer_id": local_node_id,
                },
            )
        if request.method == "POST" and request.url.path == "/sync/changes":
            import json

            body = json.loads(request.read())
            return httpx.Response(200, json={"accepted_through": body["changes"][-1]["seq"], "accepted_count": 1})
        if request.method == "GET" and request.url.path == "/sync/changes":
            return httpx.Response(
                200,
                json={
                    "changes": [
                        {
                            "seq": 7,
                            "table_name": "groups",
                            "record_uuid": remote_group_uuid,
                            "operation": "insert",
                            "payload": {
                                "uuid": remote_group_uuid,
                                "name": "Remote group",
                                "notes": None,
                                "updated_at": remote_hlc,
                                "updated_by_node": peer_id,
                                "version": 1,
                                "deleted_at": None,
                            },
                            "hlc": remote_hlc,
                            "node_id": peer_id,
                        }
                    ],
                    "next_cursor": 7,
                    "has_more": False,
                },
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    transport = httpx.MockTransport(handle)
    peer = models.SyncPeer(
        peer_id=peer_id,
        url="https://peer.example",
        credentials_reference="SYNC_TEST_PEER_TOKEN",
    )
    with httpx.Client(transport=transport) as client:
        worker.sync_peer_once(client, peer, local_node_id)

    with session_local() as session:
        cursor = session.get(models.SyncCursor, peer_id)
        assert cursor is not None
        assert cursor.last_sent == 1
        assert cursor.last_received == 7
        assert cursor.last_success_at is not None
        assert session.scalar(select(models.Group).where(models.Group.uuid == remote_group_uuid)).name == "Remote group"
    assert [request.url.path for request in requests] == [
        "/sync/hello",
        "/sync/changes",
        "/sync/changes",
    ]
    assert all(request.headers["x-sync-node-id"] == local_node_id for request in requests)
    assert all(request.headers["authorization"].startswith("Bearer ") for request in requests)


def test_worker_uploads_chunked_blob_and_reconciles_local_status(worker_database, monkeypatch, tmp_path):
    session_local, peer_id, local_node_id = worker_database
    contents = b"%PDF-1.7\nworker blob content\n"
    digest = hashlib.sha256(contents).hexdigest()
    blob_path = tmp_path / "score.pdf"
    blob_path.write_bytes(contents)
    monkeypatch.setattr(worker, "_BLOB_CHUNK_BYTES", 10)
    monkeypatch.setattr(worker, "_referenced_blob_hashes", lambda _strategy=None: [digest])
    monkeypatch.setattr(worker, "_local_blob_path", lambda _digest: blob_path)
    received = bytearray()

    def handle(request):
        if request.method == "GET":
            assert request.headers["range"] == "bytes=0-0"
            return httpx.Response(404)
        assert request.method == "PUT"
        assert request.url.path == f"/sync/blobs/{digest}"
        content_range = request.headers["content-range"]
        range_values, total_value = content_range.removeprefix("bytes ").split("/")
        first, last = map(int, range_values.split("-"))
        total = int(total_value)
        body = request.read()
        marker = b"\r\n\r\n"
        payload = body.split(marker, 1)[1].rsplit(b"\r\n--", 1)[0]
        assert total == len(contents)
        assert first == len(received)
        received.extend(payload)
        assert last + 1 == len(received)
        return httpx.Response(200, json={"next_offset": len(received), "status": "present"})

    peer = models.SyncPeer(
        peer_id=peer_id,
        url="https://peer.example",
        credentials_reference="SYNC_TEST_PEER_TOKEN",
    )
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        worker._sync_blobs(client, peer, local_node_id)

    assert bytes(received) == contents
    upload_count = len(received)
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                206,
                content=contents[:1],
                headers={"ETag": f'"{digest}"', "Content-Range": f"bytes 0-0/{len(contents)}"},
            )
        )
    ) as client:
        worker._sync_blobs(client, peer, "local-node")
    assert len(received) == upload_count
    with session_local() as session:
        blob = session.get(models.FileBlob, digest)
        assert blob is not None
        assert blob.status == "present"
        assert blob.size_bytes == len(contents)


def test_worker_configuration_validates_peer_list_and_interval(monkeypatch):
    monkeypatch.setenv("SYNC_PEERS", "not-a-uuid")
    with pytest.raises(RuntimeError, match="comma-separated list"):
        worker.configured_peer_ids()

    monkeypatch.setenv("SYNC_PEERS", "")
    monkeypatch.setenv("SYNC_INTERVAL_SECONDS", "0")
    with pytest.raises(RuntimeError, match="positive"):
        worker.sync_interval_seconds()


def test_worker_peer_configuration_excludes_paused_peers(worker_database):
    session_local, peer_id, _local_node_id = worker_database
    assert [peer.peer_id for peer in worker._load_peers()] == [peer_id]

    with session_local() as session:
        peer = session.get(models.SyncPeer, peer_id)
        peer.active = False
        session.commit()

    assert worker._load_peers() == []


def test_authorized_pdf_strategy_selects_only_group_accessible_scores(worker_database):
    session_local, _peer_id, _local_node_id = worker_database
    with session_local() as session:
        group = session.query(models.Group).filter_by(name="Local group").one()
        user = models.User(
            user_name="local-member",
            user_pw="hashed",
            user_group="user",
            email="member@example.test",
            clear_name="Local Member",
        )
        instrument = models.Instrument(instrument_name="Flute", instrument_tuning="C")
        song = models.Song(name="Local song")
        hidden_instrument = models.Instrument(instrument_name="Oboe", instrument_tuning="C")
        session.add_all((user, instrument, song, hidden_instrument))
        session.flush()
        session.add_all(
            (
                models.UserGroupMembership(user_id=user.id, group_id=group.id),
                models.GroupInstrumentMembership(group_id=group.id, instrument_id=instrument.id),
                models.Score(song_id=song.id, instrument_id=instrument.id, file_hash="a" * 64),
                models.Score(song_id=song.id, instrument_id=hidden_instrument.id, file_hash="b" * 64),
            )
        )
        session.commit()

        assert service.score_blob_hashes(session, "authorized") == ["a" * 64]
        assert service.score_blob_hashes(session, "metadata") == []


def test_register_score_blob_uses_content_addressed_path(worker_database, monkeypatch, tmp_path):
    session_local, _peer_id, _local_node_id = worker_database
    contents = b"%PDF-1.7\ncontent addressed\n"
    digest = hashlib.sha256(contents).hexdigest()
    source = tmp_path / "legacy.pdf"
    source.write_bytes(contents)
    monkeypatch.setattr(service, "file_blob_path", lambda _digest: tmp_path / "blobs" / digest[:2] / f"{digest}.pdf")

    with session_local() as session:
        path = service.register_score_blob(source, digest, session)
        session.commit()
        assert path.read_bytes() == contents
        blob = session.get(models.FileBlob, digest)
        assert blob is not None
        assert blob.status == "present"
        assert blob.size_bytes == len(contents)


def test_blob_garbage_collection_respects_tombstone_retention(worker_database, monkeypatch, tmp_path):
    session_local, _peer_id, _local_node_id = worker_database
    monkeypatch.setattr(service, "file_blob_path", lambda digest: tmp_path / digest[:2] / f"{digest}.pdf")
    now_ms = time.time_ns() // 1_000_000
    expired_digest = "c" * 64
    retained_digest = "d" * 64
    for digest in (expired_digest, retained_digest):
        blob_path = service.file_blob_path(digest)
        blob_path.parent.mkdir(parents=True, exist_ok=True)
        blob_path.write_bytes(b"blob")

    with session_local() as session:
        song = models.Song(name="Retention test song")
        instrument = models.Instrument(instrument_name="Retention test instrument", instrument_tuning="C")
        session.add_all((song, instrument))
        session.flush()
        session.add_all(
            (
                models.Score(
                    song_id=song.id,
                    instrument_id=instrument.id,
                    file_hash=expired_digest,
                    deleted_at=(now_ms - 31 * 24 * 60 * 60 * 1000) << 16,
                ),
                models.Score(
                    song_id=song.id,
                    instrument_id=instrument.id,
                    file_hash=retained_digest,
                    deleted_at=(now_ms - 10 * 24 * 60 * 60 * 1000) << 16,
                ),
                models.FileBlob(sha256=expired_digest, size_bytes=4, status="present"),
                models.FileBlob(sha256=retained_digest, size_bytes=4, status="present"),
            )
        )
        session.commit()

        assert service.collect_garbage_blobs(session) == 1
        assert not service.file_blob_path(expired_digest).exists()
        assert service.file_blob_path(retained_digest).exists()
        assert session.get(models.FileBlob, expired_digest) is None
        assert session.get(models.FileBlob, retained_digest) is not None
