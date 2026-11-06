from __future__ import annotations

import gzip
import hashlib
import json
import re
import threading
import time
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend import auth, models
from backend.sync import peer_auth, service
from backend.sync.clock import next_hlc


router = APIRouter(prefix="/sync", tags=["sync"])
_CONTENT_RANGE_RE = re.compile(r"^bytes (\d+)-(\d+)/(\d+)$")
_RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")
_MAX_UPLOAD_CHUNK_BYTES = 16 * 1024 * 1024
_blob_locks_guard = threading.Lock()
_blob_locks: dict[str, threading.Lock] = {}


class SyncChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    seq: int = Field(gt=0, strict=True)
    table_name: str = Field(min_length=1, max_length=128, strict=True)
    record_uuid: str = Field(min_length=36, max_length=36, strict=True)
    operation: str = Field(strict=True)
    payload: dict[str, Any]
    hlc: int = Field(gt=0, strict=True)
    node_id: str = Field(min_length=36, max_length=36, strict=True)


class ChangeBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    changes: list[SyncChange] = Field(max_length=service.MAX_BATCH_SIZE)


def _authenticated_peer(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    x_sync_node_id: Annotated[str | None, Header()] = None,
    db: Session = Depends(auth.get_db),
) -> models.SyncPeer:
    return peer_auth.authenticate_peer(request, authorization, x_sync_node_id, db)


def _compatible_peer(
    x_sync_protocol_version: Annotated[int | None, Header()] = None,
    x_sync_schema_version: Annotated[int | None, Header()] = None,
    peer: models.SyncPeer = Depends(_authenticated_peer),
) -> models.SyncPeer:
    if (
        x_sync_protocol_version != service.PROTOCOL_VERSION
        or x_sync_schema_version != service.SCHEMA_VERSION
    ):
        raise HTTPException(status_code=426, detail="Incompatible sync protocol or schema version")
    return peer


def _http_error_from_import(error: service.SyncImportError) -> HTTPException:
    return HTTPException(status_code=error.status_code, detail=str(error))


def _wire_change(entry: models.ChangeLog) -> dict[str, Any]:
    return {
        "seq": entry.seq,
        "table_name": entry.table_name,
        "record_uuid": entry.record_uuid,
        "operation": entry.operation,
        "payload": entry.payload,
        "hlc": entry.hlc,
        "node_id": entry.node_id,
    }


def _accepts_gzip(accept_encoding: str | None) -> bool:
    if not accept_encoding:
        return False
    for item in accept_encoding.split(","):
        parts = [part.strip() for part in item.split(";")]
        if parts[0].casefold() == "gzip":
            quality = next(
                (part.partition("=")[2] for part in parts[1:] if part.casefold().startswith("q=")),
                "1",
            )
            try:
                return float(quality) > 0
            except ValueError:
                return False
    return False


def _changes_response(data: dict[str, Any], accept_encoding: str | None):
    headers = {"Vary": "Accept-Encoding"}
    body = json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if _accepts_gzip(accept_encoding) and len(body) >= 500:
        headers["Content-Encoding"] = "gzip"
        return Response(gzip.compress(body, mtime=0), media_type="application/json", headers=headers)
    return JSONResponse(data, headers=headers)


def _range_bounds(range_header: str | None, size: int) -> tuple[int, int, int]:
    if range_header is None:
        return 0, size - 1, 200
    match = _RANGE_RE.fullmatch(range_header.strip())
    if match is None or size == 0:
        raise HTTPException(
            status_code=416,
            detail="Invalid byte range",
            headers={"Content-Range": f"bytes */{size}"},
        )
    first, last = match.groups()
    if not first:
        if not last:
            raise HTTPException(status_code=416, headers={"Content-Range": f"bytes */{size}"})
        suffix_length = int(last)
        if suffix_length <= 0:
            raise HTTPException(status_code=416, headers={"Content-Range": f"bytes */{size}"})
        start = max(size - suffix_length, 0)
        end = size - 1
    else:
        start = int(first)
        end = min(int(last), size - 1) if last else size - 1
    if start >= size or end < start:
        raise HTTPException(
            status_code=416,
            detail="Invalid byte range",
            headers={"Content-Range": f"bytes */{size}"},
        )
    return start, end, 206


def _stream_file(path: Path, start: int, length: int):
    with path.open("rb") as handle:
        handle.seek(start)
        remaining = length
        transferred = 0
        started_at = time.monotonic()
        while remaining:
            chunk = handle.read(min(1024 * 1024, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            transferred += len(chunk)
            service.throttle_transfer(started_at, transferred)
            yield chunk


def _upload_lock(sha256: str) -> threading.Lock:
    with _blob_locks_guard:
        return _blob_locks.setdefault(sha256, threading.Lock())


@router.get("/hello")
def hello(
    peer: models.SyncPeer = Depends(_authenticated_peer),
    db: Session = Depends(auth.get_db),
):
    try:
        node_id = service.get_local_node_id(db)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail="Local node identity is not configured") from exc
    cursor = db.scalar(select(func.max(models.ChangeLog.seq))) or 0
    return {
        "node_id": node_id,
        "protocol_version": service.PROTOCOL_VERSION,
        "schema_version": service.SCHEMA_VERSION,
        "cursor": cursor,
        "peer_id": peer.peer_id,
    }


@router.get("/changes")
def get_changes(
    since: int = Query(default=0, ge=0),
    limit: int | None = Query(default=None, ge=1, le=service.MAX_BATCH_SIZE),
    accept_encoding: Annotated[str | None, Header()] = None,
    peer: models.SyncPeer = Depends(_compatible_peer),
    db: Session = Depends(auth.get_db),
):
    configured_limit = service.sync_max_batch_size()
    limit = limit or configured_limit
    if limit > configured_limit:
        raise HTTPException(status_code=400, detail=f"limit cannot exceed configured maximum {configured_limit}")
    service.record_acknowledged_cursor(db, peer.peer_id, since)
    entries = db.scalars(
        select(models.ChangeLog)
        .where(models.ChangeLog.seq > since)
        .order_by(models.ChangeLog.seq.asc())
        .limit(limit + 1)
    ).all()
    has_more = len(entries) > limit
    entries = entries[:limit]
    return _changes_response({
        "changes": [_wire_change(entry) for entry in entries],
        "next_cursor": entries[-1].seq if entries else since,
        "has_more": has_more,
    }, accept_encoding)


@router.post("/changes")
def receive_changes(
    batch: ChangeBatch,
    peer: models.SyncPeer = Depends(_compatible_peer),
    db: Session = Depends(auth.get_db),
):
    changes = [change.model_dump() for change in batch.changes]
    if len(changes) > service.sync_max_batch_size():
        raise HTTPException(status_code=413, detail="Batch exceeds configured maximum")
    sequence_numbers = [change["seq"] for change in changes]
    if sequence_numbers != sorted(set(sequence_numbers)):
        raise HTTPException(status_code=422, detail="Change sequence numbers must be unique and increasing")
    try:
        received_through = service.apply_changes(db, peer.peer_id, changes)
    except service.SyncImportError as exc:
        db.rollback()
        raise _http_error_from_import(exc) from exc
    service.record_received_cursor(db, peer.peer_id, received_through)
    db.commit()
    return {"accepted_through": received_through, "accepted_count": len(changes)}


@router.get("/status")
def sync_status(
    peer: models.SyncPeer = Depends(_compatible_peer),
    db: Session = Depends(auth.get_db),
):
    cursor = db.get(models.SyncCursor, peer.peer_id)
    last_sent = cursor.last_sent if cursor else 0
    last_received = cursor.last_received if cursor else 0
    pending = db.scalar(
        select(func.count()).select_from(models.ChangeLog).where(models.ChangeLog.seq > last_sent)
    )
    open_conflicts = db.scalar(
        select(func.count()).select_from(models.SyncConflict).where(models.SyncConflict.status == "open")
    )
    return {
        "node_id": service.get_local_node_id(db),
        "peer_id": peer.peer_id,
        "pending_changes": pending or 0,
        "last_sent": last_sent,
        "last_received": last_received,
        "last_successful_sync": cursor.last_success_at.isoformat() if cursor and cursor.last_success_at else None,
        "open_conflicts": open_conflicts or 0,
    }


@router.get("/blobs/{sha256}")
def get_blob(
    sha256: str,
    range_header: Annotated[str | None, Header(alias="Range")] = None,
    peer: models.SyncPeer = Depends(_compatible_peer),
    db: Session = Depends(auth.get_db),
):
    try:
        sha256 = service.validate_sha256(sha256)
    except service.SyncImportError as exc:
        raise _http_error_from_import(exc) from exc
    canonical_path = service.file_blob_path(sha256)
    path = canonical_path
    if not path.is_file():
        path = service.find_score_blob(db, sha256)
    if path is None or not path.is_file():
        raise HTTPException(status_code=404, detail="Blob not found")
    blob = db.get(models.FileBlob, sha256)
    if path == canonical_path and blob is not None and blob.status == "present":
        if path.stat().st_size != blob.size_bytes:
            raise HTTPException(status_code=409, detail="Stored blob size does not match its metadata")
    elif _hash_path(path) != sha256:
        raise HTTPException(status_code=409, detail="Stored blob failed SHA-256 verification")
    size = path.stat().st_size
    start, end, status_code = _range_bounds(range_header, size)
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(end - start + 1),
        "ETag": f'"{sha256}"',
    }
    if status_code == 206:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    return StreamingResponse(
        _stream_file(path, start, end - start + 1),
        status_code=status_code,
        media_type="application/pdf",
        headers=headers,
    )


@router.put("/blobs/{sha256}")
def put_blob(
    sha256: str,
    file: UploadFile,
    content_range: Annotated[str | None, Header(alias="Content-Range")] = None,
    peer: models.SyncPeer = Depends(_compatible_peer),
    db: Session = Depends(auth.get_db),
):
    try:
        sha256 = service.validate_sha256(sha256)
    except service.SyncImportError as exc:
        raise _http_error_from_import(exc) from exc
    match = _CONTENT_RANGE_RE.fullmatch(content_range or "")
    if match is None:
        raise HTTPException(status_code=400, detail="Content-Range is required")
    start, end, total = map(int, match.groups())
    chunk_length = end - start + 1
    if total <= 0 or start < 0 or end < start or end >= total:
        raise HTTPException(status_code=400, detail="Invalid Content-Range")
    max_blob_bytes = service.sync_max_blob_bytes()
    if total > max_blob_bytes:
        raise HTTPException(status_code=413, detail="Blob exceeds configured size limit")
    if chunk_length > _MAX_UPLOAD_CHUNK_BYTES:
        raise HTTPException(status_code=413, detail="Upload chunk exceeds 16 MiB")
    final_path = service.file_blob_path(sha256)
    part_path = final_path.parent / ".incoming" / f"{sha256}.part"

    with _upload_lock(sha256):
        if final_path.is_file():
            if final_path.stat().st_size == total and _hash_path(final_path) == sha256:
                return {"sha256": sha256, "size_bytes": total, "status": "present", "next_offset": total}
            raise HTTPException(status_code=409, detail="A different blob already exists for this hash")
        part_path.parent.mkdir(parents=True, exist_ok=True)
        current_size = part_path.stat().st_size if part_path.exists() else 0
        if current_size != start:
            raise HTTPException(status_code=409, detail=f"Expected upload offset {current_size}")
        blob = db.get(models.FileBlob, sha256)
        if blob is not None and blob.size_bytes != total:
            raise HTTPException(status_code=409, detail="Upload total does not match the existing transfer")
        written = 0
        mode = "ab" if start else "wb"
        with part_path.open(mode) as target:
            started_at = time.monotonic()
            while True:
                chunk = file.file.read(min(1024 * 1024, chunk_length - written + 1))
                if not chunk:
                    break
                written += len(chunk)
                if written > chunk_length:
                    break
                target.write(chunk)
                service.throttle_transfer(started_at, written)
        if written != chunk_length:
            if start == 0:
                part_path.unlink(missing_ok=True)
            else:
                with part_path.open("r+b") as partial:
                    partial.truncate(start)
            raise HTTPException(status_code=400, detail="Upload chunk length does not match Content-Range")
        current_size = part_path.stat().st_size
        if current_size != end + 1:
            raise HTTPException(status_code=409, detail="Upload chunk did not append at the requested offset")
        if blob is None:
            blob = models.FileBlob(sha256=sha256, size_bytes=total, status="transferring")
            db.add(blob)
        else:
            blob.status = "transferring"
        blob.updated_at = next_hlc(blob.updated_at or 0)
        if current_size < total:
            db.commit()
            return {"sha256": sha256, "size_bytes": total, "status": "transferring", "next_offset": current_size}
        if _hash_path(part_path) != sha256:
            part_path.unlink(missing_ok=True)
            blob.status = "missing"
            db.commit()
            raise HTTPException(status_code=422, detail="Uploaded blob does not match its SHA-256")
        final_path.parent.mkdir(parents=True, exist_ok=True)
        part_path.replace(final_path)
        blob.status = "present"
        db.commit()
    return {"sha256": sha256, "size_bytes": total, "status": "present", "next_offset": total}


def _hash_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
