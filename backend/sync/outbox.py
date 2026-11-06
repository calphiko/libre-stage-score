from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, time
from typing import Iterator

from sqlalchemy import delete, event, select
from sqlalchemy.inspection import inspect
from sqlalchemy.orm import Session, with_loader_criteria

from backend import models
from backend.sync.clock import new_uuid7, next_hlc
from backend.sync.identity import node_id_for_connection


_PENDING_CHANGES = "_sync_pending_changes"
_REMOTE_SOURCE = "_sync_source_node"
_TRACKED_TABLES = {
    mapper.local_table.name
    for mapper in models.Base.registry.mappers
    if issubclass(mapper.class_, models.ReplicatedMixin)
}


def _json_value(value):
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def _payload_for(record, connection) -> dict:
    state = inspect(record)
    payload = {}
    for attribute in state.mapper.column_attrs:
        column = attribute.columns[0]
        if column.primary_key and not column.foreign_keys:
            continue
        value = getattr(record, attribute.key)
        if value is not None:
            if isinstance(record, models.DocumentAccess) and attribute.key == "document_id":
                target_model = {
                    "song": models.Song,
                    "score": models.Score,
                }.get(record.document_type)
                if target_model is None:
                    raise ValueError(f"Unsupported document type for synchronization: {record.document_type}")
                value = connection.execute(
                    select(target_model.uuid).where(target_model.id == value)
                ).scalar_one()
            for foreign_key in column.foreign_keys:
                target = foreign_key.column
                if target.table.name not in _TRACKED_TABLES:
                    continue
                value = connection.execute(
                    select(target.table.c.uuid).where(target == value)
                ).scalar_one()
                break
        payload[attribute.key] = _json_value(value)
    return payload


@event.listens_for(Session, "before_flush")
def _prepare_replicated_changes(session: Session, _flush_context, _instances) -> None:
    if session.info.get(_REMOTE_SOURCE):
        session.info.pop(_PENDING_CHANGES, None)
        return

    candidates = (*session.new, *session.dirty, *session.deleted)
    if not any(isinstance(record, models.ReplicatedMixin) for record in candidates):
        session.info.pop(_PENDING_CHANGES, None)
        return

    pending = {}
    connection = session.connection()
    local_node_id = node_id_for_connection(connection)

    for record in list(session.deleted):
        if not isinstance(record, models.ReplicatedMixin):
            continue
        record.deleted_at = next_hlc(record.updated_at or 0)
        record.updated_at = record.deleted_at
        record.updated_by_node = local_node_id
        record.version = (record.version or 0) + 1
        pending[id(record)] = (record, "delete", _payload_for(record, connection))

    for record in session.new:
        if not isinstance(record, models.ReplicatedMixin):
            continue
        record.uuid = record.uuid or new_uuid7()
        record.updated_at = record.updated_at or next_hlc()
        record.updated_by_node = local_node_id
        record.version = record.version or 1
        pending[id(record)] = (record, "insert", None)

    for record in session.dirty:
        if not isinstance(record, models.ReplicatedMixin) or id(record) in pending:
            continue
        if not session.is_modified(record, include_collections=False):
            continue
        record.updated_at = next_hlc(record.updated_at or 0)
        record.updated_by_node = local_node_id
        record.version = (record.version or 0) + 1
        operation = "delete" if record.deleted_at is not None else "update"
        pending[id(record)] = (record, operation, None)

    session.info[_PENDING_CHANGES] = pending


@event.listens_for(Session, "after_flush")
def _write_change_log(session: Session, _flush_context) -> None:
    pending = session.info.pop(_PENDING_CHANGES, {})
    if not pending or session.info.get(_REMOTE_SOURCE):
        return

    connection = session.connection()
    entries = []
    for record, operation, cached_payload in pending.values():
        entries.append(
            {
                "table_name": inspect(record).mapper.local_table.name,
                "record_uuid": record.uuid,
                "operation": operation,
                "payload": cached_payload or _payload_for(record, connection),
                "hlc": record.updated_at,
                "node_id": record.updated_by_node,
            }
        )
    connection.execute(models.ChangeLog.__table__.insert(), entries)


@event.listens_for(Session, "do_orm_execute")
def _hide_deleted_replicated_rows(execute_state) -> None:
    if execute_state.is_select and not execute_state.session.info.get("include_deleted"):
        execute_state.statement = execute_state.statement.options(
            with_loader_criteria(
                models.ReplicatedMixin,
                lambda entity: entity.deleted_at.is_(None),
                include_aliases=True,
            )
        )


@contextmanager
def changes_from_peer(session: Session, source_node_id: str) -> Iterator[None]:
    previous_source = session.info.get(_REMOTE_SOURCE)
    session.info[_REMOTE_SOURCE] = source_node_id
    try:
        yield
    finally:
        if previous_source is None:
            session.info.pop(_REMOTE_SOURCE, None)
        else:
            session.info[_REMOTE_SOURCE] = previous_source


@contextmanager
def including_deleted(session: Session) -> Iterator[None]:
    previous_value = session.info.get("include_deleted")
    session.info["include_deleted"] = True
    try:
        yield
    finally:
        if previous_value is None:
            session.info.pop("include_deleted", None)
        else:
            session.info["include_deleted"] = previous_value


def restore_tombstone(session: Session, model, **filters):
    with including_deleted(session):
        record = session.query(model).filter_by(**filters).first()
    if record is not None and record.deleted_at is not None:
        record.deleted_at = None
        return record
    return None


def soft_delete(session: Session, record: models.ReplicatedMixin) -> None:
    if record.deleted_at is None:
        record.deleted_at = next_hlc(record.updated_at or 0)
    if isinstance(record, models.Group):
        children = (
            session.query(models.UserGroupMembership).filter_by(group_id=record.id).all()
            + session.query(models.GroupInstrumentMembership).filter_by(group_id=record.id).all()
            + session.query(models.DocumentAccess).filter_by(group_id=record.id).all()
        )
    elif isinstance(record, models.User):
        children = (
            session.query(models.UserGroupMembership).filter_by(user_id=record.id).all()
            + session.query(models.UserInstrument).filter_by(user_id=record.id).all()
            + session.query(models.DocumentAccess).filter_by(user_id=record.id).all()
            + session.query(models.SongUploads).filter_by(uploaded_by_user_id=record.id).all()
        )
    elif isinstance(record, models.Instrument):
        children = (
            session.query(models.UserInstrument).filter_by(instrument_id=record.id).all()
            + session.query(models.GroupInstrumentMembership).filter_by(instrument_id=record.id).all()
            + session.query(models.Score).filter_by(instrument_id=record.id).all()
        )
    elif isinstance(record, models.Collection):
        children = session.query(models.SongCollection).filter_by(collection_id=record.id).all()
    elif isinstance(record, models.Song):
        children = (
            session.query(models.Score).filter_by(song_id=record.id).all()
            + session.query(models.SongCollection).filter_by(song_id=record.id).all()
            + session.query(models.SongUploads).filter_by(song_id=record.id).all()
            + session.query(models.DocumentAccess)
            .filter_by(document_type="song", document_id=record.id)
            .all()
        )
    elif isinstance(record, models.Score):
        children = session.query(models.DocumentAccess).filter_by(
            document_type="score", document_id=record.id
        ).all()
    else:
        children = []
    for child in children:
        soft_delete(session, child)


def compact_change_log(session: Session, older_than: datetime, limit: int = 1000) -> int:
    if limit <= 0:
        raise ValueError("limit must be greater than zero")

    active_peer_ids = session.scalars(
        select(models.SyncPeer.peer_id).where(models.SyncPeer.active.is_(True))
    ).all()
    if not active_peer_ids:
        return 0

    acknowledged_by_peer = dict(
        session.execute(
            select(models.SyncCursor.peer_id, models.SyncCursor.last_sent).where(
                models.SyncCursor.peer_id.in_(active_peer_ids)
            )
        ).all()
    )
    acknowledged_through = min(acknowledged_by_peer.get(peer_id, 0) for peer_id in active_peer_ids)
    eligible_sequences = (
        select(models.ChangeLog.seq)
        .where(
            models.ChangeLog.seq <= acknowledged_through,
            models.ChangeLog.created_at < older_than,
        )
        .order_by(models.ChangeLog.seq.asc())
        .limit(limit)
    )
    result = session.execute(delete(models.ChangeLog).where(models.ChangeLog.seq.in_(eligible_sequences)))
    return result.rowcount or 0
