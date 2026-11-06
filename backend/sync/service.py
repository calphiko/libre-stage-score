from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
import time as time_module
import unicodedata
import uuid
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.inspection import inspect
from sqlalchemy.orm import Session

from backend import models
from backend.sync.clock import new_uuid7, next_hlc
from backend.sync.identity import node_id_for_connection
from backend.sync.outbox import _payload_for, changes_from_peer, including_deleted


PROTOCOL_VERSION = 1
SCHEMA_VERSION = 1
DEFAULT_BATCH_SIZE = 250
MAX_BATCH_SIZE = 1000
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
PDF_SYNC_STRATEGIES = {"all", "metadata", "on_access", "authorized"}
_REPLICATED_MODELS = {
    mapper.local_table.name: mapper.class_
    for mapper in models.Base.registry.mappers
    if issubclass(mapper.class_, models.ReplicatedMixin)
}
_TABLE_ORDER = (
    "users",
    "groups",
    "instruments",
    "collections",
    "ensembles",
    "kind_of_song",
    "difficulty_level",
    "song_rythm",
    "song_storage_physical",
    "songs",
    "scores",
    "user_groups",
    "user_instruments",
    "group_instruments",
    "song_collections",
    "document_access",
    "song_uploads",
)
_NAME_FIELDS = {
    "users": "user_name",
    "groups": "name",
    "instruments": "instrument_name",
    "collections": "name",
    "ensembles": "name",
    "kind_of_song": "name",
    "difficulty_level": "name",
    "song_rythm": "name",
}


class SyncImportError(ValueError):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


def _local_payload(record: models.ReplicatedMixin, session: Session) -> dict[str, Any]:
    return _payload_for(record, session.connection())


def _decode_foreign_keys(
    model: type[models.ReplicatedMixin], payload: dict[str, Any], session: Session
) -> dict[str, Any]:
    columns = {column.key: column for column in inspect(model).columns}
    values = {}
    for key, value in payload.items():
        column = columns.get(key)
        if column is None or (column.primary_key and not column.foreign_keys) or key == "id":
            raise SyncImportError(f"Unknown or immutable field: {key}")
        if value is None:
            values[key] = None
            continue
        if model is models.DocumentAccess and key == "document_id":
            document_model = {
                "song": models.Song,
                "score": models.Score,
            }.get(payload.get("document_type"))
            if document_model is None:
                raise SyncImportError("document_access requires a supported document_type")
            document = session.scalar(select(document_model).where(document_model.uuid == value))
            if document is None:
                raise SyncImportError("Missing referenced document", status_code=409)
            values[key] = document.id
            continue
        for foreign_key in column.foreign_keys:
            target = foreign_key.column
            target_model = _REPLICATED_MODELS.get(target.table.name)
            if target_model is None:
                continue
            referenced = session.scalar(select(target_model).where(target_model.uuid == value))
            if referenced is None:
                raise SyncImportError(
                    f"Missing referenced record for {key}",
                    status_code=409,
                )
            value = getattr(referenced, target.key)
            break
        python_type = column.type.python_type
        if python_type is datetime and isinstance(value, str):
            value = datetime.fromisoformat(value)
        elif python_type is date and isinstance(value, str):
            value = date.fromisoformat(value)
        elif python_type is time and isinstance(value, str):
            value = time.fromisoformat(value)
        elif python_type is bool and isinstance(value, bool) is False:
            raise SyncImportError(f"Invalid boolean value for {key}")
        elif python_type in (int, float, str) and value is not None and not isinstance(value, python_type):
            raise SyncImportError(f"Invalid value type for {key}")
        values[key] = value
    return values


def _normal_name(value: Any) -> str:
    return unicodedata.normalize("NFKC", str(value)).strip().casefold()


def _record_conflict(
    session: Session,
    table_name: str,
    record_uuid: str,
    local_payload: dict[str, Any] | None,
    remote_payload: dict[str, Any],
    remote_node_id: str,
    reason: str,
) -> None:
    session.add(
        models.SyncConflict(
            conflict_id=new_uuid7(),
            table_name=table_name,
            record_uuid=record_uuid,
            local_payload=local_payload,
            remote_payload=remote_payload,
            remote_node_id=remote_node_id,
            reason=reason,
        )
    )


def _find_name_collision(
    session: Session,
    model: type[models.ReplicatedMixin],
    table_name: str,
    values: dict[str, Any],
    record_uuid: str,
):
    name_field = _NAME_FIELDS.get(table_name)
    if name_field is None or name_field not in values or values[name_field] is None:
        return None
    wanted = _normal_name(values[name_field])
    with including_deleted(session):
        candidates = session.scalars(
            select(model).where(model.uuid != record_uuid, getattr(model, name_field).is_not(None))
        ).all()
    return next((candidate for candidate in candidates if _normal_name(getattr(candidate, name_field)) == wanted), None)


def apply_changes(session: Session, source_node_id: str, changes: list[dict[str, Any]]) -> int:
    ordered = sorted(
        changes,
        key=lambda change: (
            _TABLE_ORDER.index(change["table_name"])
            if change["table_name"] in _TABLE_ORDER
            else len(_TABLE_ORDER),
            change["seq"],
        ),
    )
    for change in ordered:
        if change.get("table_name") == "scores":
            payload = change.get("payload")
            file_hash = payload.get("file_hash") if isinstance(payload, dict) else None
            if not isinstance(file_hash, str) or not _SHA256_RE.fullmatch(file_hash):
                raise SyncImportError("synchronized scores require a valid SHA-256 file_hash")
    applied_through = 0
    with changes_from_peer(session, source_node_id):
        for change in ordered:
            table_name = change["table_name"]
            model = _REPLICATED_MODELS.get(table_name)
            if model is None:
                raise SyncImportError(f"Unsupported replicated table: {table_name}")
            record_uuid = change["record_uuid"]
            try:
                record_uuid = str(uuid.UUID(record_uuid))
            except ValueError as exc:
                raise SyncImportError("record_uuid must be a UUID") from exc
            if change["operation"] not in {"insert", "update", "delete"}:
                raise SyncImportError("operation must be insert, update, or delete")
            payload = change["payload"]
            if not isinstance(payload, dict):
                raise SyncImportError("payload must be an object")
            if payload.get("uuid") != record_uuid:
                raise SyncImportError("payload UUID does not match record_uuid")
            if payload.get("updated_by_node") != source_node_id:
                raise SyncImportError("change origin does not match authenticated peer", status_code=403)
            try:
                remote_hlc = int(change["hlc"])
                if remote_hlc <= 0:
                    raise ValueError
                remote_node = str(uuid.UUID(change["node_id"]))
                version = payload["version"]
                if isinstance(version, bool) or not isinstance(version, int) or version < 1:
                    raise ValueError
                deleted_at = payload["deleted_at"]
                if deleted_at is not None and (isinstance(deleted_at, bool) or not isinstance(deleted_at, int)):
                    raise ValueError
            except (KeyError, TypeError, ValueError, AttributeError) as exc:
                raise SyncImportError("invalid change metadata") from exc
            if remote_node != source_node_id:
                raise SyncImportError("change origin does not match authenticated peer", status_code=403)
            payload_hlc = payload.get("updated_at")
            if isinstance(payload_hlc, bool) or not isinstance(payload_hlc, int) or payload_hlc != remote_hlc:
                raise SyncImportError("payload HLC does not match change HLC")
            next_hlc(remote_hlc)

            existing_values = {
                key: value
                for key, value in payload.items()
                if key not in {"uuid", "updated_at", "updated_by_node", "version", "deleted_at"}
            }
            values = _decode_foreign_keys(model, existing_values, session)
            if model is models.Score:
                file_hash = values.get("file_hash")
                if not isinstance(file_hash, str) or not _SHA256_RE.fullmatch(file_hash):
                    raise SyncImportError("synchronized scores require a valid SHA-256 file_hash")
            session.add(
                models.SyncAudit(
                    remote_node_id=source_node_id,
                    change_seq=change["seq"],
                    table_name=table_name,
                    record_uuid=record_uuid,
                    operation=change["operation"],
                    hlc=remote_hlc,
                )
            )
            values.update(
                uuid=record_uuid,
                updated_at=remote_hlc,
                updated_by_node=remote_node,
                version=version,
                deleted_at=deleted_at,
            )
            if change["operation"] == "delete" and values["deleted_at"] is None:
                raise SyncImportError("delete operation requires a tombstone")
            if values["version"] < 1:
                raise SyncImportError("version must be positive")

            with including_deleted(session):
                local = session.scalar(select(model).where(model.uuid == record_uuid))
            local_payload = _local_payload(local, session) if local is not None else None
            if local is not None:
                user_status_tie = False
                if local.deleted_at is not None and values["deleted_at"] is None:
                    _record_conflict(
                        session, table_name, record_uuid, local_payload, payload, source_node_id, "tombstone_wins"
                    )
                    applied_through = max(applied_through, int(change["seq"]))
                    continue
                user_status_tie = (
                    table_name == "users"
                    and local.status != values.get("status")
                    and values.get("status") in {"active", "deactivated"}
                    and remote_hlc == local.updated_at
                )
                if user_status_tie:
                    _record_conflict(
                        session,
                        table_name,
                        record_uuid,
                        local_payload,
                        payload,
                        source_node_id,
                        "restrictive_change_wins",
                    )
                    if values.get("status") != "deactivated":
                        applied_through = max(applied_through, int(change["seq"]))
                        continue
                if values["deleted_at"] is not None and local.deleted_at is None:
                    pass
                elif not user_status_tie and (remote_hlc, remote_node) <= (local.updated_at, local.updated_by_node):
                    if remote_hlc != local.updated_at or remote_node != local.updated_by_node:
                        _record_conflict(
                            session, table_name, record_uuid, local_payload, payload, source_node_id, "local_wins"
                        )
                    applied_through = max(applied_through, int(change["seq"]))
                    continue

            collision = _find_name_collision(session, model, table_name, values, record_uuid)
            if collision is not None:
                _record_conflict(
                    session,
                    table_name,
                    record_uuid,
                    _local_payload(collision, session),
                    payload,
                    source_node_id,
                    "unique_name_collision",
                )
                applied_through = max(applied_through, int(change["seq"]))
                continue

            try:
                with session.begin_nested():
                    if local is None:
                        session.add(model(**values))
                    else:
                        for key, value in values.items():
                            setattr(local, key, value)
                    session.flush()
            except IntegrityError as exc:
                error_message = str(exc.orig).lower()
                if "unique" not in error_message and "duplicate key" not in error_message:
                    raise SyncImportError("record violates a database constraint") from exc
                _record_conflict(
                    session, table_name, record_uuid, local_payload, payload, source_node_id, "constraint_collision"
                )
            applied_through = max(applied_through, int(change["seq"]))
    return applied_through


def get_local_node_id(session: Session) -> str:
    return node_id_for_connection(session.connection())


def record_acknowledged_cursor(session: Session, peer_id: str, since: int) -> None:
    cursor = session.get(models.SyncCursor, peer_id)
    if cursor is None:
        cursor = models.SyncCursor(peer_id=peer_id, last_sent=0, last_received=0)
        session.add(cursor)
    cursor.last_sent = max(cursor.last_sent, since)
    cursor.last_success_at = datetime.now(timezone.utc)
    session.commit()


def record_received_cursor(session: Session, peer_id: str, received_through: int) -> None:
    cursor = session.get(models.SyncCursor, peer_id)
    if cursor is None:
        cursor = models.SyncCursor(peer_id=peer_id, last_sent=0, last_received=0)
        session.add(cursor)
    cursor.last_received = max(cursor.last_received, received_through)
    cursor.last_success_at = datetime.now(timezone.utc)


def file_blob_path(sha256: str) -> Path:
    root = Path(__file__).resolve().parents[1] / "storage" / "blobs"
    return root / sha256[:2] / f"{sha256}.pdf"


def local_score_blob(session: Session, sha256: str) -> Path | None:
    canonical_path = file_blob_path(sha256)
    if canonical_path.is_file() and _file_sha256(canonical_path) == sha256:
        return canonical_path
    return find_score_blob(session, sha256)


def register_score_blob(source_path: Path, sha256: str, session: Session) -> Path:
    sha256 = validate_sha256(sha256)
    if _file_sha256(source_path) != sha256:
        raise ValueError("Score file does not match its SHA-256")
    target_path = file_blob_path(sha256)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    if not target_path.is_file():
        with tempfile.NamedTemporaryFile(dir=target_path.parent, prefix=f".{sha256}.", delete=False) as handle:
            temporary_path = Path(handle.name)
        try:
            shutil.copyfile(source_path, temporary_path)
            if _file_sha256(temporary_path) != sha256:
                raise ValueError("Copied score file does not match its SHA-256")
            temporary_path.replace(target_path)
        finally:
            temporary_path.unlink(missing_ok=True)
    elif _file_sha256(target_path) != sha256:
        raise ValueError("Existing blob does not match its SHA-256")
    size = target_path.stat().st_size
    blob = session.get(models.FileBlob, sha256)
    if blob is None:
        session.add(models.FileBlob(sha256=sha256, size_bytes=size, status="present"))
    else:
        blob.size_bytes = size
        blob.status = "present"
        blob.updated_at = next_hlc(blob.updated_at or 0)
    return target_path


def pdf_sync_strategy() -> str:
    strategy = os.getenv("SYNC_PDF_STRATEGY", "all").strip().casefold()
    if strategy not in PDF_SYNC_STRATEGIES:
        raise RuntimeError(
            "SYNC_PDF_STRATEGY must be one of: all, metadata, on_access, authorized"
        )
    return strategy


def score_blob_hashes(session: Session, strategy: str) -> list[str]:
    if strategy in {"metadata", "on_access"}:
        return []
    scores = select(models.Score.file_hash).where(
        models.Score.file_hash.is_not(None),
        models.Score.deleted_at.is_(None),
    )
    if strategy == "authorized":
        accessible_score_ids = (
            select(models.Score.id)
            .join(models.Instrument, models.Instrument.id == models.Score.instrument_id)
            .join(
                models.GroupInstrumentMembership,
                models.GroupInstrumentMembership.instrument_id == models.Instrument.id,
            )
            .join(
                models.UserGroupMembership,
                models.UserGroupMembership.group_id == models.GroupInstrumentMembership.group_id,
            )
            .join(models.User, models.User.id == models.UserGroupMembership.user_id)
            .where(models.User.status == "active")
        )
        explicitly_accessible_score_ids = (
            select(models.DocumentAccess.document_id)
            .join(models.UserGroupMembership, models.UserGroupMembership.group_id == models.DocumentAccess.group_id)
            .join(models.User, models.User.id == models.UserGroupMembership.user_id)
            .where(
                models.DocumentAccess.document_type == "score",
                models.User.status == "active",
            )
        )
        scores = scores.where(
            models.Score.id.in_(accessible_score_ids.union(explicitly_accessible_score_ids))
        )
        has_privileged_user = session.scalar(
            select(models.User.id).where(
                models.User.status == "active",
                func.lower(models.User.user_group).in_(("admin", "editor")),
            ).limit(1)
        )
        if has_privileged_user is not None:
            scores = select(models.Score.file_hash).where(
                models.Score.file_hash.is_not(None),
                models.Score.deleted_at.is_(None),
            )
    return sorted(
        {
            digest.lower()
            for digest in session.scalars(scores).all()
            if isinstance(digest, str) and _SHA256_RE.fullmatch(digest.lower())
        }
    )


def find_score_blob(session: Session, sha256: str) -> Path | None:
    project_root = Path(__file__).resolve().parents[2]
    scores_root = project_root / "backend" / "storage" / "scores"
    score_paths = session.scalars(
        select(models.Score.storage_path).where(models.Score.file_hash == sha256, models.Score.storage_path.is_not(None))
    ).all()
    for storage_path in score_paths:
        candidate = Path(storage_path)
        if candidate.is_absolute():
            resolved = candidate.resolve()
        elif candidate.parts and candidate.parts[0] in {"backend", "scores"}:
            resolved = (project_root / candidate).resolve()
        else:
            resolved = (scores_root / candidate).resolve()
        try:
            resolved.relative_to(project_root)
        except ValueError:
            continue
        if resolved.is_file() and _file_sha256(resolved) == sha256:
            return resolved
    return None


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def collect_garbage_blobs(session: Session) -> int:
    try:
        retention_days = int(os.getenv("SYNC_BLOB_TOMBSTONE_RETENTION_DAYS", "30"))
    except ValueError as exc:
        raise RuntimeError("SYNC_BLOB_TOMBSTONE_RETENTION_DAYS must be an integer") from exc
    if retention_days <= 0:
        raise RuntimeError("SYNC_BLOB_TOMBSTONE_RETENTION_DAYS must be positive")

    blobs = session.scalars(
        select(models.FileBlob).where(models.FileBlob.status == "present")
    ).all()
    if not blobs:
        return 0
    digests = {blob.sha256 for blob in blobs}
    now_ms = time_module.time_ns() // 1_000_000
    cutoff_hlc = (now_ms - retention_days * 24 * 60 * 60 * 1000) << 16
    with including_deleted(session):
        score_refs = session.execute(
            select(models.Score.file_hash, models.Score.deleted_at).where(models.Score.file_hash.in_(digests))
        ).all()
    refs_by_digest: dict[str, list[int | None]] = {}
    for digest, deleted_at in score_refs:
        refs_by_digest.setdefault(digest, []).append(deleted_at)

    removed = 0
    for blob in blobs:
        if not _SHA256_RE.fullmatch(blob.sha256):
            continue
        references = refs_by_digest.get(blob.sha256, [])
        if any(deleted_at is None or deleted_at >= cutoff_hlc for deleted_at in references):
            continue
        file_blob_path(blob.sha256).unlink(missing_ok=True)
        session.delete(blob)
        removed += 1
    if removed:
        session.commit()
    return removed


def validate_sha256(sha256: str) -> str:
    sha256 = sha256.lower()
    if not _SHA256_RE.fullmatch(sha256):
        raise SyncImportError("sha256 must be 64 hexadecimal characters")
    return sha256


def sync_max_blob_bytes() -> int:
    try:
        value = int(os.getenv("SYNC_MAX_BLOB_BYTES", str(100 * 1024 * 1024)))
    except ValueError as exc:
        raise RuntimeError("SYNC_MAX_BLOB_BYTES must be an integer") from exc
    if value <= 0:
        raise RuntimeError("SYNC_MAX_BLOB_BYTES must be positive")
    return value


def sync_max_batch_size() -> int:
    try:
        value = int(os.getenv("SYNC_MAX_BATCH", str(DEFAULT_BATCH_SIZE)))
    except ValueError as exc:
        raise RuntimeError("SYNC_MAX_BATCH must be an integer") from exc
    if not 1 <= value <= MAX_BATCH_SIZE:
        raise RuntimeError(f"SYNC_MAX_BATCH must be between 1 and {MAX_BATCH_SIZE}")
    return value


def sync_bandwidth_limit() -> int:
    try:
        value = int(
            os.getenv(
                "SYNC_BANDWIDTH_LIMIT_BYTES_PER_SECOND",
                os.getenv("SYNC_BANDWIDTH_LIMIT", "0"),
            )
        )
    except ValueError as exc:
        raise RuntimeError("SYNC_BANDWIDTH_LIMIT must be an integer") from exc
    if value < 0:
        raise RuntimeError("SYNC_BANDWIDTH_LIMIT cannot be negative")
    return value


def throttle_transfer(started_at: float, transferred_bytes: int) -> None:
    limit = sync_bandwidth_limit()
    if limit:
        delay = transferred_bytes / limit - (time_module.monotonic() - started_at)
        if delay > 0:
            time_module.sleep(delay)
