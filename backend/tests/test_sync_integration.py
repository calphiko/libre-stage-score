import hashlib
import io
import os
import time
import uuid
from types import SimpleNamespace

import httpx
import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend import auth, models
from backend.sync import peer_auth, service
from backend.sync.clock import next_hlc
from backend.sync.outbox import including_deleted, soft_delete
import backend.main as main_module
import backend.sync.worker as worker


_PEER_KEY_REFERENCE = "SYNC_INTEGRATION_PEER_KEY"


@pytest.fixture
def sync_cluster(monkeypatch, tmp_path):
    monkeypatch.setenv(_PEER_KEY_REFERENCE, "integration-peer-signing-key-32-bytes")
    monkeypatch.setenv("SYNC_PDF_STRATEGY", "metadata")
    nodes = {}
    node_ids = {name: str(uuid.uuid4()) for name in ("left", "right")}
    original_overrides = main_module.app.dependency_overrides.copy()

    def get_test_db(request: Request):
        node_name = request.url.hostname.removesuffix(".test")
        session_factory = nodes[node_name].session_local
        with session_factory() as session:
            yield session

    main_module.app.dependency_overrides[auth.get_db] = get_test_db

    for name, node_id in node_ids.items():
        engine = create_engine(
            f"sqlite:///{tmp_path / f'{name}.db'}",
            connect_args={"check_same_thread": False},
        )
        models.Base.metadata.create_all(bind=engine)
        session_local = sessionmaker(autocommit=False, autoflush=False, bind=engine)

        test_client = TestClient(main_module.app, base_url=f"https://{name}.test")
        nodes[name] = SimpleNamespace(
            name=name,
            node_id=node_id,
            engine=engine,
            session_local=session_local,
            client=test_client,
        )

    for node in nodes.values():
        remote_name = "right" if node.name == "left" else "left"
        with node.session_local() as session:
            session.add(models.NodeIdentity(id=1, node_id=node.node_id))
            session.add(
                models.SyncPeer(
                    peer_id=node_ids[remote_name],
                    url=f"https://{remote_name}.test",
                    credentials_reference=_PEER_KEY_REFERENCE,
                )
            )
            session.commit()

    yield nodes

    for node in nodes.values():
        node.client.close()
        models.Base.metadata.drop_all(bind=node.engine)
        node.engine.dispose()
    main_module.app.dependency_overrides.clear()
    main_module.app.dependency_overrides.update(original_overrides)


def _peer_headers(sender, receiver):
    token = peer_auth.create_token(receiver.node_id, sender.node_id, _PEER_KEY_REFERENCE)
    return {
        "Authorization": f"Bearer {token}",
        "X-Sync-Node-ID": sender.node_id,
        "X-Sync-Protocol-Version": str(service.PROTOCOL_VERSION),
        "X-Sync-Schema-Version": str(service.SCHEMA_VERSION),
    }


def _forwarding_client(nodes, fail_first_post=False):
    failed = False

    def forward(request):
        nonlocal failed
        if fail_first_post and request.method == "POST" and not failed:
            failed = True
            return httpx.Response(503, json={"detail": "simulated interrupted batch"}, request=request)
        target = nodes[request.url.host.removesuffix(".test")]
        headers = dict(request.headers)
        headers.pop("host", None)
        response = target.client.request(
            request.method,
            request.url.raw_path.decode("ascii"),
            headers=headers,
            content=request.read(),
        )
        response_headers = dict(response.headers)
        response_headers.pop("content-encoding", None)
        response_headers.pop("content-length", None)
        return httpx.Response(
            response.status_code,
            headers=response_headers,
            content=response.content,
            request=request,
        )

    return httpx.Client(transport=httpx.MockTransport(forward))


def _sync(source, target, nodes, *, fail_first_post=False):
    peer = models.SyncPeer(
        peer_id=target.node_id,
        url=f"https://{target.name}.test",
        credentials_reference=_PEER_KEY_REFERENCE,
    )
    original_session_local = worker.SessionLocal
    try:
        worker.SessionLocal = source.session_local
        with _forwarding_client(nodes, fail_first_post=fail_first_post) as client:
            worker.sync_peer_once(client, peer, source.node_id)
    finally:
        worker.SessionLocal = original_session_local


def _change(seq, table_name, record_uuid, node_id, values, operation="insert", deleted_at=None):
    hlc = next_hlc()
    payload = {
        **values,
        "uuid": record_uuid,
        "updated_at": hlc,
        "updated_by_node": node_id,
        "version": 1,
        "deleted_at": deleted_at,
    }
    return {
        "seq": seq,
        "table_name": table_name,
        "record_uuid": record_uuid,
        "operation": operation,
        "payload": payload,
        "hlc": hlc,
        "node_id": node_id,
    }


def test_offline_changes_converge_and_tombstones_win_concurrent_updates(sync_cluster):
    left, right = sync_cluster["left"], sync_cluster["right"]
    with left.session_local() as session:
        session.add(models.Group(name="Created offline on left"))
        session.commit()
    with right.session_local() as session:
        session.add(models.Group(name="Created offline on right"))
        session.commit()

    _sync(left, right, sync_cluster)
    _sync(right, left, sync_cluster)

    for node in (left, right):
        with node.session_local() as session:
            assert session.scalar(
                select(models.Group).where(models.Group.name == "Created offline on left")
            )
            assert session.scalar(
                select(models.Group).where(models.Group.name == "Created offline on right")
            )

    with left.session_local() as session:
        group = session.scalar(select(models.Group).where(models.Group.name == "Created offline on left"))
        shared_uuid = group.uuid
        group.name = "Edited while disconnected"
        session.commit()
    with right.session_local() as session:
        group = session.scalar(select(models.Group).where(models.Group.uuid == shared_uuid))
        soft_delete(session, group)
        session.commit()

    _sync(left, right, sync_cluster)
    _sync(right, left, sync_cluster)

    for node in (left, right):
        with node.session_local() as session:
            assert session.scalar(select(models.Group).where(models.Group.uuid == shared_uuid)) is None
            with including_deleted(session):
                tombstone = session.scalar(select(models.Group).where(models.Group.uuid == shared_uuid))
            assert tombstone is not None
            assert tombstone.deleted_at is not None
    with right.session_local() as session:
        assert session.scalar(
            select(models.SyncConflict).where(models.SyncConflict.reason == "tombstone_wins")
        )


def test_interrupted_push_retries_without_advancing_cursor_before_ack(sync_cluster):
    left, right = sync_cluster["left"], sync_cluster["right"]
    with left.session_local() as session:
        session.add(models.Group(name="Retry after interrupted push"))
        session.commit()

    with pytest.raises(httpx.HTTPStatusError):
        _sync(left, right, sync_cluster, fail_first_post=True)

    with left.session_local() as session:
        cursor = session.get(models.SyncCursor, right.node_id)
        assert cursor is None or cursor.last_sent == 0
    with right.session_local() as session:
        assert session.scalar(
            select(models.Group).where(models.Group.name == "Retry after interrupted push")
        ) is None

    _sync(left, right, sync_cluster)
    with left.session_local() as session:
        assert session.get(models.SyncCursor, right.node_id).last_sent > 0
    with right.session_local() as session:
        assert session.scalar(
            select(models.Group).where(models.Group.name == "Retry after interrupted push")
        )


def test_protocol_and_schema_mismatches_are_rejected(sync_cluster):
    sender, receiver = sync_cluster["left"], sync_cluster["right"]
    headers = _peer_headers(sender, receiver)

    protocol_mismatch = receiver.client.get(
        "/sync/changes",
        headers={**headers, "X-Sync-Protocol-Version": "999"},
    )
    schema_mismatch = receiver.client.get(
        "/sync/changes",
        headers={**headers, "X-Sync-Schema-Version": "999"},
    )

    assert protocol_mismatch.status_code == 426
    assert schema_mismatch.status_code == 426


def test_reordered_duplicate_batch_is_atomic_and_idempotent(sync_cluster):
    sender, receiver = sync_cluster["left"], sync_cluster["right"]
    song_uuid, collection_uuid, link_uuid = (str(uuid.uuid4()) for _ in range(3))
    changes = [
        _change(
            1,
            "song_collections",
            link_uuid,
            sender.node_id,
            {"song_id": song_uuid, "collection_id": collection_uuid},
        ),
        _change(2, "songs", song_uuid, sender.node_id, {"name": "Reordered song"}),
        _change(3, "collections", collection_uuid, sender.node_id, {"name": "Reordered collection"}),
    ]
    invalid_batch = [dict(change) for change in changes]
    invalid_batch[-1] = {**invalid_batch[-1], "operation": "unsupported"}
    headers = _peer_headers(sender, receiver)

    failed = receiver.client.post("/sync/changes", headers=headers, json={"changes": invalid_batch})
    assert failed.status_code == 422
    with receiver.session_local() as session:
        assert session.scalar(select(models.Song).where(models.Song.uuid == song_uuid)) is None
        assert session.scalar(select(models.SyncCursor).where(models.SyncCursor.peer_id == sender.node_id)) is None
        assert session.scalar(select(models.SyncAudit.change_seq)) is None

    first = receiver.client.post("/sync/changes", headers=headers, json={"changes": changes})
    duplicate = receiver.client.post("/sync/changes", headers=headers, json={"changes": changes})
    assert first.status_code == duplicate.status_code == 200
    assert first.json()["accepted_through"] == duplicate.json()["accepted_through"] == 3
    with receiver.session_local() as session:
        song = session.scalar(select(models.Song).where(models.Song.uuid == song_uuid))
        collection = session.scalar(select(models.Collection).where(models.Collection.uuid == collection_uuid))
        link = session.scalar(select(models.SongCollection).where(models.SongCollection.uuid == link_uuid))
        assert song is not None and collection is not None and link is not None
        assert link.song_id == song.id
        assert link.collection_id == collection.id
        assert session.scalar(select(models.ChangeLog.seq)) is None


def test_interrupted_blob_upload_resumes_at_acknowledged_offset(sync_cluster, monkeypatch, tmp_path):
    sender, receiver = sync_cluster["left"], sync_cluster["right"]
    contents = b"%PDF-1.7\ninterrupted transfer\n" * 100
    digest = hashlib.sha256(contents).hexdigest()
    blob_root = tmp_path / "right-blobs"
    monkeypatch.setattr(service, "file_blob_path", lambda _digest: blob_root / digest[:2] / f"{digest}.pdf")
    headers = _peer_headers(sender, receiver)
    split = len(contents) // 2

    first = receiver.client.put(
        f"/sync/blobs/{digest}",
        headers={**headers, "Content-Range": f"bytes 0-{split - 1}/{len(contents)}"},
        files={"file": ("sample.pdf", contents[:split], "application/pdf")},
    )
    assert first.status_code == 200
    assert first.json()["status"] == "transferring"

    wrong_offset = receiver.client.put(
        f"/sync/blobs/{digest}",
        headers={**headers, "Content-Range": f"bytes 0-{split - 1}/{len(contents)}"},
        files={"file": ("sample.pdf", contents[:split], "application/pdf")},
    )
    assert wrong_offset.status_code == 409

    resumed = receiver.client.put(
        f"/sync/blobs/{digest}",
        headers={**headers, "Content-Range": f"bytes {split}-{len(contents) - 1}/{len(contents)}"},
        files={"file": ("sample.pdf", contents[split:], "application/pdf")},
    )
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "present"
    assert blob_root.joinpath(digest[:2], f"{digest}.pdf").read_bytes() == contents


def test_normalized_unique_name_collision_is_visible_between_instances(sync_cluster):
    left, right = sync_cluster["left"], sync_cluster["right"]
    with left.session_local() as session:
        session.add(models.Group(name="Same choir"))
        session.commit()
    with right.session_local() as session:
        session.add(models.Group(name=" same choir "))
        session.commit()

    _sync(right, left, sync_cluster)

    with left.session_local() as session:
        same_name_groups = session.scalars(select(models.Group)).all()
        assert [group.name for group in same_name_groups] == ["Same choir"]
        assert session.scalar(
            select(models.SyncConflict).where(models.SyncConflict.reason == "unique_name_collision")
        )


def test_document_permission_and_access_survive_replication(sync_cluster, monkeypatch, tmp_path):
    left, right = sync_cluster["left"], sync_cluster["right"]
    contents = b"%PDF-1.7\npermission after sync\n"
    digest = hashlib.sha256(contents).hexdigest()
    allowed_user_name = "replicated-member"
    denied_user_name = "unrelated-member"
    with left.session_local() as session:
        user = models.User(
            user_name=allowed_user_name,
            user_pw="not-used",
            user_group="user",
            email="replicated-member@example.test",
            clear_name="Replicated Member",
        )
        group = models.Group(name="Replicated access group")
        instrument = models.Instrument(instrument_name="Replicated flute", instrument_tuning="C")
        song = models.Song(name="Replicated access song")
        session.add_all((user, group, instrument, song))
        session.flush()
        score = models.Score(song_id=song.id, instrument_id=instrument.id, file_hash=digest)
        session.add_all(
            (
                score,
                models.UserGroupMembership(user_id=user.id, group_id=group.id),
                models.GroupInstrumentMembership(group_id=group.id, instrument_id=instrument.id),
            )
        )
        session.commit()

    with right.session_local() as session:
        session.add(
            models.User(
                user_name=denied_user_name,
                user_pw="not-used",
                user_group="user",
                email="unrelated-member@example.test",
                clear_name="Unrelated Member",
            )
        )
        session.commit()
    _sync(left, right, sync_cluster)

    blob_root = tmp_path / "right-document-blobs"
    blob_path = blob_root / digest[:2] / f"{digest}.pdf"
    blob_path.parent.mkdir(parents=True)
    blob_path.write_bytes(contents)
    monkeypatch.setattr(service, "file_blob_path", lambda _digest: blob_path)
    with right.session_local() as session:
        score_id = session.scalar(select(models.Score.id).where(models.Score.uuid.is_not(None)))
        assert score_id is not None

    allowed_token = auth.create_access_token({"sub": allowed_user_name, "role": "user"})
    denied_token = auth.create_access_token({"sub": denied_user_name, "role": "user"})
    allowed = right.client.get(
        f"/scores/{score_id}/document",
        headers={"Authorization": f"Bearer {allowed_token}"},
    )
    denied = right.client.get(
        f"/scores/{score_id}/document",
        headers={"Authorization": f"Bearer {denied_token}"},
    )
    assert allowed.status_code == 200
    assert allowed.content == contents
    assert denied.status_code == 403


@pytest.mark.load
def test_throttled_pdf_sync_load(sync_cluster, monkeypatch, tmp_path):
    source, target = sync_cluster["left"], sync_cluster["right"]
    pdf_count = int(os.getenv("SYNC_LOAD_PDF_COUNT", "4"))
    pdf_size = int(os.getenv("SYNC_LOAD_PDF_SIZE_BYTES", str(2 * 1024 * 1024)))
    bandwidth = int(os.getenv("SYNC_LOAD_BANDWIDTH_LIMIT_BYTES_PER_SECOND", str(4 * 1024 * 1024)))
    assert pdf_count > 0
    assert pdf_size >= 1024
    assert bandwidth > 0
    monkeypatch.setenv("SYNC_PDF_STRATEGY", "all")
    monkeypatch.setenv("SYNC_BANDWIDTH_LIMIT_BYTES_PER_SECOND", str(bandwidth))
    monkeypatch.setenv("SYNC_MAX_BLOB_BYTES", str(pdf_size + 1024))
    monkeypatch.setattr(worker, "_BLOB_CHUNK_BYTES", 1024 * 1024)

    source_blob_root = tmp_path / "source-pdfs"
    target_blob_root = tmp_path / "target-pdfs"
    source_files = {}
    with source.session_local() as session:
        song = models.Song(name="Load-test song")
        instrument = models.Instrument(instrument_name="Load-test instrument", instrument_tuning="C")
        session.add_all((song, instrument))
        session.flush()
        for index in range(pdf_count):
            writer = PdfWriter()
            writer.add_blank_page(width=612, height=792)
            output = io.BytesIO()
            writer.write(output)
            base_pdf = output.getvalue()
            prefix = base_pdf.rsplit(b"%%EOF", 1)[0] + f"\n% file {index}\n%".encode()
            suffix = b"\n%%EOF\n"
            assert pdf_size > len(prefix) + len(suffix)
            content = prefix + b"x" * (pdf_size - len(prefix) - len(suffix)) + suffix
            digest = hashlib.sha256(content).hexdigest()
            path = source_blob_root / digest[:2] / f"{digest}.pdf"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            source_files[digest] = path
            session.add(models.Score(song_id=song.id, instrument_id=instrument.id, file_hash=digest))
        session.commit()

    monkeypatch.setattr(service, "file_blob_path", lambda digest: target_blob_root / digest[:2] / f"{digest}.pdf")
    monkeypatch.setattr(worker, "_local_blob_path", lambda digest: source_files.get(digest))
    original_session_local = worker.SessionLocal
    peer = models.SyncPeer(
        peer_id=target.node_id,
        url=f"https://{target.name}.test",
        credentials_reference=_PEER_KEY_REFERENCE,
    )
    try:
        worker.SessionLocal = source.session_local
        started = time.monotonic()
        with _forwarding_client(sync_cluster) as client:
            worker._sync_blobs(client, peer, source.node_id)
        elapsed = time.monotonic() - started
    finally:
        worker.SessionLocal = original_session_local

    expected_hashes = set(source_files)
    assert {path.stem for path in target_blob_root.glob("*/*.pdf")} == expected_hashes
    assert sum(path.stat().st_size for path in target_blob_root.glob("*/*.pdf")) == pdf_count * pdf_size
    assert elapsed >= (pdf_count * pdf_size / bandwidth) * 0.8
