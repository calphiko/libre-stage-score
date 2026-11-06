from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import tempfile
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.engine import make_url

from backend import models
from backend.database import SessionLocal, SQLALCHEMY_DATABASE_URL, engine
from backend.sync.identity import configured_node_id
from backend.sync.service import file_blob_path


_SNAPSHOT_FORMAT = 1
_BLOB_NAME = re.compile(r"^([0-9a-f]{64})\.pdf$")
_SNAPSHOT_FILES = {"database.sqlite3", "blob-index.json", "manifest.json"}


def _sqlite_path() -> Path:
    url = make_url(SQLALCHEMY_DATABASE_URL)
    if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
        raise RuntimeError("Snapshot operations require a file-backed SQLite DATABASE_URL")
    return Path(url.database).expanduser().resolve()


def prepare_database_directory() -> None:
    url = make_url(SQLALCHEMY_DATABASE_URL)
    if url.get_backend_name() == "sqlite" and url.database and url.database != ":memory:":
        Path(url.database).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)


def _blob_index() -> list[dict[str, int | str]]:
    blob_root = file_blob_path("0" * 64).parent.parent
    blobs = []
    for path in sorted(blob_root.glob("*/*.pdf")):
        match = _BLOB_NAME.fullmatch(path.name)
        if match is None or path.parent.name != match.group(1)[:2]:
            raise RuntimeError(f"Unexpected file in content-addressed blob storage: {path}")
        hasher = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                hasher.update(chunk)
        digest = hasher.hexdigest()
        if digest != match.group(1):
            raise RuntimeError(f"Blob does not match its SHA-256 filename: {path}")
        blobs.append({"sha256": digest, "size_bytes": path.stat().st_size})
    return blobs


def export_snapshot(output: Path) -> None:
    database_path = _sqlite_path()
    if not database_path.is_file():
        raise RuntimeError(f"SQLite database does not exist: {database_path}")
    output = output.expanduser().resolve()
    if output == database_path:
        raise RuntimeError("Snapshot output must not overwrite the database file")
    output.parent.mkdir(parents=True, exist_ok=True)

    with SessionLocal() as session:
        node_id = session.scalar(select(models.NodeIdentity.node_id).order_by(models.NodeIdentity.id).limit(1))
        cursor = session.scalar(select(models.ChangeLog.seq).order_by(models.ChangeLog.seq.desc()).limit(1)) or 0
    if node_id is None:
        raise RuntimeError("Node identity is not initialized; run `task node:init` first")

    with tempfile.TemporaryDirectory(dir=output.parent) as temp_dir:
        database_copy = Path(temp_dir) / "database.sqlite3"
        with engine.connect() as connection, sqlite3.connect(database_copy) as destination:
            connection.connection.driver_connection.backup(destination)
        blobs = _blob_index()
        manifest = {
            "format_version": _SNAPSHOT_FORMAT,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source_node_id": node_id,
            "change_cursor": cursor,
            "blob_count": len(blobs),
            "blob_content_included": False,
        }
        staged_snapshot = Path(temp_dir) / "snapshot.zip"
        with zipfile.ZipFile(staged_snapshot, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.write(database_copy, "database.sqlite3")
            archive.writestr("blob-index.json", json.dumps(blobs, indent=2, sort_keys=True) + "\n")
            archive.writestr("manifest.json", json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        os.replace(staged_snapshot, output)


def _validate_snapshot(archive: zipfile.ZipFile) -> tuple[dict, list[dict], bytes]:
    names = set(archive.namelist())
    if names != _SNAPSHOT_FILES:
        raise RuntimeError("Snapshot must contain exactly database.sqlite3, blob-index.json, and manifest.json")
    manifest = json.loads(archive.read("manifest.json"))
    blobs = json.loads(archive.read("blob-index.json"))
    if not isinstance(manifest, dict) or manifest.get("format_version") != _SNAPSHOT_FORMAT:
        raise RuntimeError("Unsupported or invalid snapshot manifest")
    try:
        source_node_id = str(uuid.UUID(manifest["source_node_id"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("Snapshot manifest has an invalid source node ID") from exc
    cursor = manifest.get("change_cursor")
    if isinstance(cursor, bool) or not isinstance(cursor, int) or cursor < 0:
        raise RuntimeError("Snapshot manifest has an invalid change cursor")
    if not isinstance(blobs, list) or len(blobs) != manifest.get("blob_count"):
        raise RuntimeError("Snapshot blob index does not match its manifest")
    seen_hashes = set()
    for blob in blobs:
        if (
            not isinstance(blob, dict)
            or not isinstance(blob.get("sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", blob["sha256"])
            or isinstance(blob.get("size_bytes"), bool)
            or not isinstance(blob.get("size_bytes"), int)
            or blob["size_bytes"] < 0
        ):
            raise RuntimeError("Snapshot contains an invalid blob-index entry")
        if blob["sha256"] in seen_hashes:
            raise RuntimeError("Snapshot blob index contains duplicate hashes")
        seen_hashes.add(blob["sha256"])
    manifest["source_node_id"] = source_node_id
    database_bytes = archive.read("database.sqlite3")
    return manifest, blobs, database_bytes


def restore_snapshot(
    snapshot: Path,
    hub_url: str,
    hub_credentials_reference: str,
    name: str | None = None,
) -> str:
    database_path = _sqlite_path()
    if database_path.exists():
        with sqlite3.connect(database_path) as existing:
            tables = existing.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        if tables:
            raise RuntimeError(
                f"Refusing to overwrite a non-empty database: {database_path}; "
                "restore only into a new, empty node"
            )

    parsed_hub = urlparse(hub_url)
    if (
        parsed_hub.scheme not in {"https", "http"}
        or not parsed_hub.hostname
        or parsed_hub.username is not None
        or parsed_hub.password is not None
        or (parsed_hub.scheme == "http" and parsed_hub.hostname not in {"localhost", "127.0.0.1", "::1"})
    ):
        raise RuntimeError("Hub URL must use HTTPS (HTTP is allowed only on loopback)")
    if not hub_credentials_reference.strip() or len(hub_credentials_reference) > 512:
        raise RuntimeError("Hub credentials reference must name an environment variable")
    node_name = name or os.getenv("NODE_NAME") or "local-node"
    if not node_name.strip() or len(node_name) > 128:
        raise RuntimeError("Node name must contain between 1 and 128 characters")

    configured_id = configured_node_id()
    if configured_id is None:
        raise RuntimeError("Set NODE_ID to a new UUID before restoring a snapshot")

    with zipfile.ZipFile(snapshot) as archive:
        manifest, _blobs, database_bytes = _validate_snapshot(archive)
    if configured_id == manifest["source_node_id"]:
        raise RuntimeError("NODE_ID must differ from the snapshot source node ID")

    database_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=database_path.parent, suffix=".sqlite3", delete=False) as handle:
        staged_path = Path(handle.name)
    try:
        staged_path.write_bytes(database_bytes)
        with sqlite3.connect(staged_path) as connection:
            if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("Snapshot database failed SQLite integrity_check")
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
                )
            }
            required_tables = {"node_identity", "sync_peers", "sync_cursors", "change_log"}
            if not required_tables.issubset(tables):
                raise RuntimeError("Snapshot database is missing synchronization tables")

            for table in (
                "sync_cursors",
                "sync_peers",
                "change_log",
                "sync_audit",
                "refresh_tokens",
                "token_blacklist",
                "used_password_reset_tokens",
            ):
                if table in tables:
                    connection.execute(f'DELETE FROM "{table}"')
            if connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'sqlite_sequence'"
            ).fetchone():
                connection.execute("DELETE FROM sqlite_sequence WHERE name = 'change_log'")
            connection.execute(
                "UPDATE node_identity SET node_id = ?, name = ?, key_reference = NULL",
                (configured_id, node_name.strip()),
            )
            connection.execute(
                "INSERT INTO sync_peers (peer_id, url, credentials_reference, active) VALUES (?, ?, ?, 1)",
                (manifest["source_node_id"], hub_url.rstrip("/"), hub_credentials_reference.strip()),
            )
            connection.execute(
                "INSERT INTO sync_cursors (peer_id, last_sent, last_received, last_success_at) "
                "VALUES (?, 0, ?, NULL)",
                (manifest["source_node_id"], manifest["change_cursor"]),
            )
            if "file_blobs" in tables:
                connection.execute("UPDATE file_blobs SET status = 'missing'")
            connection.commit()
        os.replace(staged_path, database_path)
    finally:
        staged_path.unlink(missing_ok=True)
    return configured_id


def initialize_node(name: str | None = None) -> str:
    node_name = name or os.getenv("NODE_NAME")
    if node_name is not None and (not node_name.strip() or len(node_name) > 128):
        raise RuntimeError("Node name must contain between 1 and 128 characters")
    with SessionLocal() as session:
        identity = session.scalar(select(models.NodeIdentity).order_by(models.NodeIdentity.id).limit(1))
        if identity is None:
            raise RuntimeError("Node identity is missing; run `task db:migrate` first")
        configured_id = configured_node_id()
        if configured_id is not None and configured_id != identity.node_id:
            raise RuntimeError("NODE_ID does not match the persisted node identity")
        if node_name:
            identity.name = node_name.strip()
        session.commit()
        return identity.node_id


def acknowledge_snapshot(peer_id: str, cursor_position: int) -> None:
    try:
        peer_id = str(uuid.UUID(peer_id))
    except ValueError as exc:
        raise RuntimeError("Peer ID must be a UUID") from exc
    if isinstance(cursor_position, bool) or not isinstance(cursor_position, int) or cursor_position < 0:
        raise RuntimeError("Snapshot cursor must be a non-negative integer")
    with SessionLocal() as session:
        peer = session.get(models.SyncPeer, peer_id)
        if peer is None or not peer.active:
            raise RuntimeError("Snapshot peer must exist and be active on this node")
        latest_cursor = session.scalar(
            select(models.ChangeLog.seq).order_by(models.ChangeLog.seq.desc()).limit(1)
        ) or 0
        if cursor_position > latest_cursor:
            raise RuntimeError("Snapshot cursor is ahead of the local change log")
        cursor = session.get(models.SyncCursor, peer_id)
        if cursor is None:
            cursor = models.SyncCursor(peer_id=peer_id, last_sent=0, last_received=0)
            session.add(cursor)
        cursor.last_sent = max(cursor.last_sent, cursor_position)
        session.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description="Database and node operations")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("prepare-database")
    init_parser = subparsers.add_parser("init-node")
    init_parser.add_argument("--name", default=os.getenv("NODE_NAME") or None)

    export_parser = subparsers.add_parser("export-snapshot")
    export_parser.add_argument("--output", required=True, type=Path)

    restore_parser = subparsers.add_parser("restore-snapshot")
    restore_parser.add_argument("--snapshot", required=True, type=Path)
    restore_parser.add_argument("--hub-url", required=True)
    restore_parser.add_argument("--hub-credentials-reference", required=True)
    restore_parser.add_argument("--name", default=os.getenv("NODE_NAME") or None)

    ack_parser = subparsers.add_parser("ack-bootstrap")
    ack_parser.add_argument("--peer-id", required=True)
    ack_parser.add_argument("--cursor", required=True, type=int)

    args = parser.parse_args()
    if args.command == "prepare-database":
        prepare_database_directory()
    elif args.command == "init-node":
        print(f"Node initialized: {initialize_node(args.name)}")
    elif args.command == "export-snapshot":
        export_snapshot(args.output)
        print(f"Snapshot exported: {args.output}")
    elif args.command == "ack-bootstrap":
        acknowledge_snapshot(args.peer_id, args.cursor)
        print(f"Snapshot acknowledged for peer {args.peer_id} through cursor {args.cursor}")
    else:
        node_id = restore_snapshot(
            args.snapshot,
            args.hub_url,
            args.hub_credentials_reference,
            args.name,
        )
        print(f"Snapshot restored; node initialized: {node_id}")


if __name__ == "__main__":
    main()
