from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import os
import random
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx
from sqlalchemy import select

from backend import models
from backend.database import SessionLocal
from backend.sync import service
from backend.sync.clock import next_hlc
from backend.sync.identity import configured_node_id
from backend.sync import peer_auth


logger = logging.getLogger(__name__)
_INITIAL_BACKOFF_SECONDS = 5
_MAX_BACKOFF_SECONDS = 300
_BLOB_CHUNK_BYTES = 8 * 1024 * 1024


def sync_enabled() -> bool:
    value = os.getenv("SYNC_ENABLED", "false").strip().casefold()
    if value not in {"true", "false", "1", "0", "yes", "no", "on", "off"}:
        raise RuntimeError("SYNC_ENABLED must be a boolean")
    return value in {"true", "1", "yes", "on"}


def sync_interval_seconds() -> float:
    try:
        value = float(os.getenv("SYNC_INTERVAL_SECONDS", "60"))
    except ValueError as exc:
        raise RuntimeError("SYNC_INTERVAL_SECONDS must be a number") from exc
    if not math.isfinite(value) or value <= 0:
        raise RuntimeError("SYNC_INTERVAL_SECONDS must be positive")
    return value


def configured_peer_ids() -> set[str] | None:
    raw_ids = os.getenv("SYNC_PEERS", "").strip()
    if not raw_ids:
        return None
    peer_ids = set()
    for raw_id in raw_ids.split(","):
        try:
            peer_ids.add(str(uuid.UUID(raw_id.strip())))
        except ValueError as exc:
            raise RuntimeError("SYNC_PEERS must be a comma-separated list of peer UUIDs") from exc
    return peer_ids


def peer_url_is_allowed(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme == "https":
        return bool(parsed.hostname) and parsed.username is None and parsed.password is None
    return (
        parsed.scheme == "http"
        and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        and parsed.username is None
        and parsed.password is None
    )


def _load_peers() -> list[models.SyncPeer]:
    selected_ids = configured_peer_ids()
    with SessionLocal() as session:
        all_peers = session.scalars(
            select(models.SyncPeer).order_by(models.SyncPeer.peer_id)
        ).all()
        if selected_ids is not None:
            known_ids = {peer.peer_id for peer in all_peers}
            unknown_ids = selected_ids - known_ids
            if unknown_ids:
                logger.warning(
                    "SYNC_PEERS contains peer IDs not present in the database: %s",
                    ", ".join(sorted(unknown_ids)),
                )
            peers = [
                peer for peer in all_peers
                if peer.active and peer.peer_id in selected_ids
            ]
        else:
            peers = [peer for peer in all_peers if peer.active]
        result = [
            models.SyncPeer(
                peer_id=peer.peer_id,
                url=peer.url,
                credentials_reference=peer.credentials_reference,
                active=peer.active,
            )
            for peer in peers
        ]
    for peer in result:
        try:
            peer_auth.validation_keys(peer.credentials_reference)
        except RuntimeError as exc:
            raise RuntimeError(f"Peer {peer.peer_id} has invalid signing-key configuration: {exc}") from exc
        if not peer_url_is_allowed(peer.url):
            raise RuntimeError(f"Peer {peer.peer_id} URL must use HTTPS (HTTP is allowed only on loopback)")
    return result


def _local_node_id() -> str:
    with SessionLocal() as session:
        node_id = service.get_local_node_id(session)
    if node_id == "unconfigured":
        raise RuntimeError("Local node identity is not configured")
    configured_id = configured_node_id()
    if configured_id is not None and node_id != configured_id:
        raise RuntimeError("NODE_ID does not match the persisted node identity")
    return node_id


def load_worker_configuration() -> tuple[float, list[models.SyncPeer], str]:
    return sync_interval_seconds(), _load_peers(), _local_node_id()


def _headers(peer: models.SyncPeer, node_id: str) -> dict[str, str]:
    token = peer_auth.create_token(peer.peer_id, node_id, peer.credentials_reference)
    return {
        "Authorization": f"Bearer {token}",
        "X-Sync-Node-ID": node_id,
        "X-Sync-Protocol-Version": str(service.PROTOCOL_VERSION),
        "X-Sync-Schema-Version": str(service.SCHEMA_VERSION),
        "Accept-Encoding": "gzip",
    }


def _cursor_values(peer_id: str) -> tuple[int, int]:
    with SessionLocal() as session:
        cursor = session.get(models.SyncCursor, peer_id)
        return (cursor.last_sent, cursor.last_received) if cursor else (0, 0)


def _advance_cursor(peer_id: str, *, sent: int | None = None, received: int | None = None) -> None:
    with SessionLocal() as session:
        cursor = session.get(models.SyncCursor, peer_id)
        if cursor is None:
            cursor = models.SyncCursor(peer_id=peer_id, last_sent=0, last_received=0)
            session.add(cursor)
        if sent is not None:
            cursor.last_sent = max(cursor.last_sent, sent)
        if received is not None:
            cursor.last_received = max(cursor.last_received, received)
        session.commit()


def _mark_sync_success(peer_id: str) -> None:
    with SessionLocal() as session:
        cursor = session.get(models.SyncCursor, peer_id)
        if cursor is None:
            cursor = models.SyncCursor(peer_id=peer_id, last_sent=0, last_received=0)
            session.add(cursor)
        cursor.last_success_at = datetime.now(timezone.utc)
        session.commit()


def _pull_changes(client: httpx.Client, peer: models.SyncPeer, node_id: str) -> None:
    headers = _headers(peer, node_id)
    while True:
        _last_sent, since = _cursor_values(peer.peer_id)
        response = client.get(
            f"{peer.url.rstrip('/')}/sync/changes",
            params={"since": since, "limit": service.sync_max_batch_size()},
            headers=headers,
        )
        response.raise_for_status()
        batch = response.json()
        changes = batch.get("changes")
        next_cursor = batch.get("next_cursor")
        has_more = batch.get("has_more")
        if (
            not isinstance(changes, list)
            or isinstance(next_cursor, bool)
            or not isinstance(next_cursor, int)
            or not isinstance(has_more, bool)
        ):
            raise RuntimeError(f"Peer {peer.peer_id} returned an invalid changes response")
        if not changes:
            if next_cursor != since:
                raise RuntimeError(f"Peer {peer.peer_id} advanced an empty changes page")
            return
        if next_cursor <= since:
            raise RuntimeError(f"Peer {peer.peer_id} did not advance its changes cursor")
        with SessionLocal() as session:
            received_through = service.apply_changes(session, peer.peer_id, changes)
            if received_through != next_cursor:
                raise RuntimeError(f"Peer {peer.peer_id} returned a non-contiguous changes page")
            service.record_received_cursor(session, peer.peer_id, received_through)
            session.commit()
        if not has_more:
            return


def _push_changes(client: httpx.Client, peer: models.SyncPeer, node_id: str) -> None:
    headers = _headers(peer, node_id)
    while True:
        last_sent, _last_received = _cursor_values(peer.peer_id)
        with SessionLocal() as session:
            entries = session.scalars(
                select(models.ChangeLog)
                .where(models.ChangeLog.seq > last_sent)
                .order_by(models.ChangeLog.seq.asc())
                .limit(service.sync_max_batch_size() + 1)
            ).all()
            has_more = len(entries) > service.sync_max_batch_size()
            entries = entries[: service.sync_max_batch_size()]
            changes = [
                {
                    "seq": entry.seq,
                    "table_name": entry.table_name,
                    "record_uuid": entry.record_uuid,
                    "operation": entry.operation,
                    "payload": entry.payload,
                    "hlc": entry.hlc,
                    "node_id": entry.node_id,
                }
                for entry in entries
            ]
        if not changes:
            return
        next_cursor = changes[-1]["seq"]
        accepted = client.post(
            f"{peer.url.rstrip('/')}/sync/changes",
            headers=headers,
            json={"changes": changes},
        )
        accepted.raise_for_status()
        acknowledgement = accepted.json()
        accepted_through = acknowledgement.get("accepted_through")
        if accepted_through != next_cursor:
            raise RuntimeError(f"Peer {peer.peer_id} did not acknowledge the complete changes page")
        _advance_cursor(peer.peer_id, sent=accepted_through)
        if not has_more:
            return


def _referenced_blob_hashes(strategy: str | None = None) -> list[str]:
    with SessionLocal() as session:
        return service.score_blob_hashes(session, strategy or service.pdf_sync_strategy())


def _local_blob_path(digest: str) -> Path | None:
    with SessionLocal() as session:
        return service.local_score_blob(session, digest)


def _mark_blob_present(digest: str, path: Path) -> None:
    size = path.stat().st_size
    with SessionLocal() as session:
        blob = session.get(models.FileBlob, digest)
        if blob is None:
            session.add(models.FileBlob(sha256=digest, size_bytes=size, status="present"))
        elif blob.size_bytes != size or blob.status != "present":
            blob.size_bytes = size
            blob.status = "present"
            blob.updated_at = next_hlc(blob.updated_at or 0)
        session.commit()


def _upload_blob(client: httpx.Client, peer: models.SyncPeer, headers: dict[str, str], digest: str, path: Path) -> None:
    size = path.stat().st_size
    if size > service.sync_max_blob_bytes():
        raise RuntimeError(f"Local blob {digest} exceeds SYNC_MAX_BLOB_BYTES")
    with path.open("rb") as source:
        offset = 0
        started_at = time.monotonic()
        while offset < size:
            source.seek(offset)
            chunk = source.read(min(_BLOB_CHUNK_BYTES, size - offset))
            end = offset + len(chunk) - 1
            response = client.put(
                f"{peer.url.rstrip('/')}/sync/blobs/{digest}",
                headers={
                    **headers,
                    "Content-Range": f"bytes {offset}-{end}/{size}",
                },
                files={"file": (f"{digest}.pdf", chunk, "application/pdf")},
            )
            if response.status_code == 409:
                try:
                    detail = response.json().get("detail", "")
                except ValueError:
                    detail = ""
                expected_prefix = "Expected upload offset "
                if isinstance(detail, str) and detail.startswith(expected_prefix):
                    try:
                        expected_offset = int(detail[len(expected_prefix) :])
                    except ValueError:
                        expected_offset = -1
                    if 0 <= expected_offset <= size:
                        offset = expected_offset
                        continue
            response.raise_for_status()
            payload = response.json()
            next_offset = payload.get("next_offset")
            if next_offset != end + 1:
                raise RuntimeError(f"Peer {peer.peer_id} did not acknowledge the uploaded blob chunk")
            offset = next_offset
            service.throttle_transfer(started_at, offset)


def _remote_blob_available(
    client: httpx.Client, peer: models.SyncPeer, headers: dict[str, str], digest: str
) -> bool:
    response = client.get(
        f"{peer.url.rstrip('/')}/sync/blobs/{digest}",
        headers={**headers, "Range": "bytes=0-0"},
    )
    if response.status_code == 404:
        return False
    response.raise_for_status()
    if response.status_code != 206 or response.headers.get("etag", "").strip('"') != digest:
        raise RuntimeError(f"Peer {peer.peer_id} returned an invalid blob availability response")
    return True


def _download_blob(client: httpx.Client, peer: models.SyncPeer, headers: dict[str, str], digest: str) -> None:
    path = service.file_blob_path(digest)
    path.parent.mkdir(parents=True, exist_ok=True)
    incoming_dir = path.parent / ".incoming"
    incoming_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=incoming_dir, prefix=f"{digest}.", suffix=".part", delete=False) as handle:
        temporary_path = Path(handle.name)
    try:
        with client.stream(
            "GET",
            f"{peer.url.rstrip('/')}/sync/blobs/{digest}",
            headers=headers,
        ) as response:
            if response.status_code == 404:
                return
            response.raise_for_status()
            digest_hash = hashlib.sha256()
            total_bytes = 0
            started_at = time.monotonic()
            with temporary_path.open("wb") as target:
                for chunk in response.iter_bytes(chunk_size=1024 * 1024):
                    target.write(chunk)
                    digest_hash.update(chunk)
                    total_bytes += len(chunk)
                    if total_bytes > service.sync_max_blob_bytes():
                        raise RuntimeError(f"Peer {peer.peer_id} supplied a blob exceeding SYNC_MAX_BLOB_BYTES")
                    service.throttle_transfer(started_at, total_bytes)
        if digest_hash.hexdigest() != digest:
            raise RuntimeError(f"Peer {peer.peer_id} supplied a blob with an invalid SHA-256")
        temporary_path.replace(path)
        with SessionLocal() as session:
            blob = session.get(models.FileBlob, digest)
            if blob is None:
                blob = models.FileBlob(sha256=digest, size_bytes=total_bytes, status="present")
                session.add(blob)
            elif blob.size_bytes != total_bytes or blob.status != "present":
                blob.size_bytes = total_bytes
                blob.status = "present"
                blob.updated_at = next_hlc(blob.updated_at or 0)
            session.commit()
    finally:
        temporary_path.unlink(missing_ok=True)


def _sync_blobs(client: httpx.Client, peer: models.SyncPeer, node_id: str) -> None:
    strategy = service.pdf_sync_strategy()
    if strategy in {"metadata", "on_access"}:
        return
    headers = _headers(peer, node_id)
    for digest in _referenced_blob_hashes(strategy):
        local_path = _local_blob_path(digest)
        if local_path is not None:
            _mark_blob_present(digest, local_path)
            if not _remote_blob_available(client, peer, headers, digest):
                _upload_blob(client, peer, headers, digest, local_path)
        else:
            _download_blob(client, peer, headers, digest)


def fetch_blob_from_peers(digest: str) -> bool:
    digest = service.validate_sha256(digest)
    if service.pdf_sync_strategy() == "metadata":
        return False
    peers = _load_peers()
    if not peers:
        return False

    timeout = httpx.Timeout(connect=10, read=120, write=120, pool=10)
    failures: list[tuple[str, Exception]] = []
    node_id = _local_node_id()
    with httpx.Client(timeout=timeout, transport=httpx.HTTPTransport(retries=0)) as client:
        for peer in peers:
            headers = _headers(peer, node_id)
            try:
                response = client.get(f"{peer.url.rstrip('/')}/sync/hello", headers=headers)
                response.raise_for_status()
                try:
                    hello = response.json()
                except ValueError as exc:
                    raise RuntimeError(f"Peer {peer.peer_id} returned an invalid hello response") from exc
                if (
                    not isinstance(hello, dict)
                    or hello.get("node_id") != peer.peer_id
                    or hello.get("peer_id") != node_id
                    or hello.get("protocol_version") != service.PROTOCOL_VERSION
                    or hello.get("schema_version") != service.SCHEMA_VERSION
                ):
                    raise RuntimeError(f"Peer {peer.peer_id} has an incompatible identity or sync version")
                _download_blob(client, peer, headers, digest)
                with SessionLocal() as session:
                    if service.local_score_blob(session, digest) is not None:
                        return True
            except (httpx.HTTPError, RuntimeError) as exc:
                failures.append((peer.peer_id, exc))
                logger.warning("Could not fetch blob %s from peer %s: %s", digest, peer.peer_id, exc)
    if failures and len(failures) == len(peers):
        logger.error("Blob %s could not be fetched from any configured peer", digest)
    return False


def sync_peer_once(client: httpx.Client, peer: models.SyncPeer, node_id: str) -> None:
    headers = _headers(peer, node_id)
    response = client.get(f"{peer.url.rstrip('/')}/sync/hello", headers=headers)
    response.raise_for_status()
    hello = response.json()
    if (
        hello.get("node_id") != peer.peer_id
        or hello.get("peer_id") != node_id
        or hello.get("protocol_version") != service.PROTOCOL_VERSION
        or hello.get("schema_version") != service.SCHEMA_VERSION
    ):
        raise RuntimeError(f"Peer {peer.peer_id} has an incompatible identity or sync version")
    _push_changes(client, peer, node_id)
    _pull_changes(client, peer, node_id)
    _sync_blobs(client, peer, node_id)
    _mark_sync_success(peer.peer_id)


async def _run_peer(client: httpx.Client, peer: models.SyncPeer, node_id: str, interval: float) -> None:
    attempts = 0
    while True:
        try:
            sync_task = asyncio.create_task(asyncio.to_thread(sync_peer_once, client, peer, node_id))
            try:
                await asyncio.shield(sync_task)
            except asyncio.CancelledError:
                try:
                    await sync_task
                except Exception:
                    logger.exception("Sync with peer %s failed during shutdown", peer.peer_id)
                raise
            attempts = 0
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Sync with peer %s failed", peer.peer_id)
            attempts += 1
            exponent = min(attempts - 1, 16)
            delay = min(_MAX_BACKOFF_SECONDS, _INITIAL_BACKOFF_SECONDS * (2**exponent))
            await asyncio.sleep(random.uniform(delay * 0.5, delay * 1.5))


async def run_sync_worker(
    interval: float,
    peers: list[models.SyncPeer],
    node_id: str,
) -> None:
    logger.info("Sync worker started for %d peer(s)", len(peers))
    timeout = httpx.Timeout(connect=10, read=120, write=120, pool=10)
    with httpx.Client(timeout=timeout, transport=httpx.HTTPTransport(retries=0)) as client:
        await asyncio.gather(
            _manage_peer_tasks(client, interval, peers, node_id),
            _run_blob_garbage_collection(),
        )


async def _manage_peer_tasks(
    client: httpx.Client,
    interval: float,
    peers: list[models.SyncPeer],
    node_id: str,
) -> None:
    tasks: dict[str, tuple[tuple[str, str | None, float], asyncio.Task]] = {}

    def signature(peer: models.SyncPeer, sync_interval: float):
        return peer.url, peer.credentials_reference, sync_interval

    async def cancel_peer(peer_id: str) -> None:
        _current_signature, task = tasks.pop(peer_id)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    try:
        for peer in peers:
            peer_signature = signature(peer, interval)
            tasks[peer.peer_id] = (
                peer_signature,
                asyncio.create_task(
                    _run_peer(client, peer, node_id, interval),
                    name=f"sync-peer-{peer.peer_id}",
                ),
            )

        while True:
            try:
                current_interval = await asyncio.to_thread(sync_interval_seconds)
                current_peers = await asyncio.to_thread(_load_peers)
            except Exception:
                logger.exception("Could not reload sync peer configuration")
                await asyncio.sleep(5)
                continue

            desired = {
                peer.peer_id: (peer, signature(peer, current_interval))
                for peer in current_peers
            }
            for peer_id, (current_signature, _task) in list(tasks.items()):
                wanted = desired.get(peer_id)
                if wanted is None or wanted[1] != current_signature:
                    await cancel_peer(peer_id)
            for peer_id, (peer, peer_signature) in desired.items():
                if peer_id not in tasks:
                    tasks[peer_id] = (
                        peer_signature,
                        asyncio.create_task(
                            _run_peer(client, peer, node_id, current_interval),
                            name=f"sync-peer-{peer_id}",
                        ),
                    )
            await asyncio.sleep(5)
    finally:
        remaining = list(tasks)
        for peer_id in remaining:
            await cancel_peer(peer_id)


async def _run_blob_garbage_collection() -> None:
    while True:
        try:
            removed = await asyncio.to_thread(_collect_blob_garbage)
            if removed:
                logger.info("Removed %d expired orphaned score blob(s)", removed)
        except Exception:
            logger.exception("Score blob garbage collection failed")
        await asyncio.sleep(24 * 60 * 60)


def _collect_blob_garbage() -> int:
    with SessionLocal() as session:
        return service.collect_garbage_blobs(session)


if __name__ == "__main__":
    asyncio.run(run_sync_worker(*load_worker_configuration()))
