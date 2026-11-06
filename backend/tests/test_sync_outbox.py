import uuid
from datetime import datetime, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from backend import models
from backend.sync.clock import next_hlc
from backend.sync.outbox import changes_from_peer, compact_change_log, soft_delete


def _session_with_node():
    engine = create_engine("sqlite://")
    models.Base.metadata.create_all(bind=engine)
    session = Session(engine)
    node_id = str(uuid.uuid4())
    session.add(models.NodeIdentity(id=1, node_id=node_id))
    session.commit()
    return engine, session, node_id


def test_outbox_records_insert_and_update_with_uuid_foreign_keys():
    engine, session, node_id = _session_with_node()
    song = models.Song(name="Example")
    instrument = models.Instrument(instrument_name="Flute", instrument_tuning="C")
    session.add_all([song, instrument])
    session.flush()
    score = models.Score(song_id=song.id, instrument_id=instrument.id)
    session.add(score)
    session.commit()

    insert_entry = session.scalar(
        select(models.ChangeLog).where(models.ChangeLog.record_uuid == score.uuid)
    )
    assert insert_entry is not None
    assert insert_entry.operation == "insert"
    assert insert_entry.node_id == node_id
    assert insert_entry.payload["song_id"] == song.uuid
    assert insert_entry.payload["instrument_id"] == instrument.uuid
    assert "id" not in insert_entry.payload

    previous_version = score.version
    previous_hlc = score.updated_at
    score.notes = "Updated"
    session.commit()

    update_entry = session.scalar(
        select(models.ChangeLog)
        .where(models.ChangeLog.record_uuid == score.uuid, models.ChangeLog.operation == "update")
    )
    assert update_entry is not None
    assert update_entry.payload["notes"] == "Updated"
    assert score.version == previous_version + 1
    assert score.updated_at > previous_hlc
    session.close()
    engine.dispose()


def test_outbox_uses_uuids_for_composite_and_polymorphic_references():
    engine, session, _node_id = _session_with_node()
    song = models.Song(name="Referenced song")
    collection = models.Collection(name="Referenced collection")
    session.add_all((song, collection))
    session.flush()
    link = models.SongCollection(song_id=song.id, collection_id=collection.id)
    access = models.DocumentAccess(document_type="song", document_id=song.id)
    session.add_all((link, access))
    session.commit()

    link_change = session.scalar(
        select(models.ChangeLog).where(models.ChangeLog.record_uuid == link.uuid)
    )
    access_change = session.scalar(
        select(models.ChangeLog).where(models.ChangeLog.record_uuid == access.uuid)
    )
    assert link_change.payload["song_id"] == song.uuid
    assert link_change.payload["collection_id"] == collection.uuid
    assert access_change.payload["document_id"] == song.uuid
    session.close()
    engine.dispose()


def test_soft_delete_is_hidden_and_logged_as_a_tombstone():
    engine, session, _node_id = _session_with_node()
    group = models.Group(name="Choir")
    session.add(group)
    session.commit()

    soft_delete(session, group)
    session.commit()

    assert session.query(models.Group).filter_by(id=group.id).first() is None
    session.info["include_deleted"] = True
    try:
        assert session.query(models.Group).filter_by(id=group.id).one().deleted_at is not None
    finally:
        session.info.pop("include_deleted")
    tombstone = session.scalar(
        select(models.ChangeLog).where(
            models.ChangeLog.record_uuid == group.uuid,
            models.ChangeLog.operation == "delete",
            models.ChangeLog.payload["deleted_at"].as_integer().is_not(None),
        )
    )
    assert tombstone is not None
    session.close()
    engine.dispose()


def test_peer_changes_do_not_create_echo_outbox_events():
    engine, session, remote_node_id = _session_with_node()
    group = models.Group(name="Imported", uuid=str(uuid.uuid4()), updated_at=next_hlc(), updated_by_node=remote_node_id)
    with changes_from_peer(session, remote_node_id):
        session.add(group)
        session.commit()

    assert session.scalar(select(models.ChangeLog.seq)) is None
    assert group.updated_by_node == remote_node_id
    session.close()
    engine.dispose()


def test_outbox_rows_rollback_with_the_business_transaction():
    engine, session, _node_id = _session_with_node()
    group = models.Group(name="Rolled back")
    session.add(group)
    session.flush()
    assert session.scalar(
        select(models.ChangeLog.seq).where(models.ChangeLog.record_uuid == group.uuid)
    ) is not None

    session.rollback()

    assert session.query(models.Group).filter_by(name="Rolled back").first() is None
    assert session.scalar(
        select(models.ChangeLog.seq).where(models.ChangeLog.record_uuid == group.uuid)
    ) is None
    session.close()
    engine.dispose()


def test_soft_deleted_song_collection_links_are_not_loaded():
    engine, session, _node_id = _session_with_node()
    song = models.Song(name="Collection link")
    collection = models.Collection(name="Tombstoned link")
    session.add_all((song, collection))
    session.flush()
    link = models.SongCollection(song_id=song.id, collection_id=collection.id)
    session.add(link)
    session.commit()

    assert [item.id for item in song.collections] == [collection.id]
    soft_delete(session, link)
    session.commit()
    session.expire(song, ["collections"])

    assert song.collections == []
    session.close()
    engine.dispose()


def test_orm_hard_delete_is_logged():
    engine, session, _node_id = _session_with_node()
    group = models.Group(name="Retained")
    session.add(group)
    session.commit()
    session.delete(group)
    session.commit()

    assert session.query(models.Group).filter_by(id=group.id).first() is None
    assert session.scalar(
        select(models.ChangeLog.seq).where(
            models.ChangeLog.record_uuid == group.uuid,
            models.ChangeLog.operation == "delete",
        )
    ) is not None
    session.close()
    engine.dispose()


def test_change_log_compaction_waits_for_active_peer_acknowledgements():
    engine, session, _node_id = _session_with_node()
    group = models.Group(name="Compacted")
    session.add(group)
    session.commit()
    entry = session.scalar(select(models.ChangeLog).where(models.ChangeLog.record_uuid == group.uuid))
    entry_seq = entry.seq
    peer_id = str(uuid.uuid4())
    session.add(models.SyncPeer(peer_id=peer_id, url="https://peer.invalid"))
    session.commit()

    assert compact_change_log(session, datetime.now(timezone.utc), limit=100) == 0
    session.add(models.SyncCursor(peer_id=peer_id, last_sent=entry_seq))
    session.commit()

    assert compact_change_log(session, datetime.now(timezone.utc), limit=100) == 1
    session.commit()
    assert session.get(models.ChangeLog, entry_seq) is None
    session.close()
    engine.dispose()
