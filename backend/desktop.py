"""Local service used by the desktop application."""

import argparse
import json
import logging
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading

from dotenv import load_dotenv

logger = logging.getLogger(__name__)


def open_in_system_viewer(path: Path) -> None:
    if sys.platform == "win32":
        os.startfile(path)
    elif sys.platform == "darwin":
        subprocess.run(["open", str(path)], check=True, capture_output=True)
    else:
        subprocess.run(["xdg-open", str(path)], check=True, capture_output=True)


def open_score_copy(source: Path, data_dir: Path) -> None:
    destination = data_dir / "opened-pdfs"
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = None
    try:
        with tempfile.NamedTemporaryFile(prefix="stimme-", suffix=".pdf", dir=destination, delete=False) as file:
            path = Path(file.name)
            with source.open("rb") as input_file:
                shutil.copyfileobj(input_file, file)
    except OSError:
        if path is not None:
            path.unlink(missing_ok=True)
        raise
    open_in_system_viewer(path)


def resource_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parents[1]


def configure(data_dir: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    os.chdir(data_dir)
    env_file = data_dir / ".env"
    if not env_file.exists():
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        try:
            fd = os.open(env_file, flags, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                file.write(
                    f"SECRET_KEY={secrets.token_urlsafe(48)}\n"
                    "DATABASE_URL=sqlite:///./backend/db/app.db\n"
                    "SYNC_ENABLED=true\n"
                    "SYNC_PDF_STRATEGY=all\n"
                    "AUTH_COOKIE_SECURE=false\n"
                )
    load_dotenv(env_file, override=True)
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    os.environ["LIBRE_STAGE_DATA_ROOT"] = str(data_dir.resolve())


def desktop_router():
    from fastapi import APIRouter, Depends
    from sqlalchemy.orm import Session
    from starlette.exceptions import HTTPException
    from backend import auth, main, models
    from backend.paths import data_root

    router = APIRouter()

    @router.post("/desktop/scores/{score_id}/open")
    def open_score(
        score_id: int,
        current: models.User = Depends(auth.get_current_user),
        db: Session = Depends(auth.get_db),
    ):
        response = main.get_score_document(score_id, current, db)
        try:
            open_score_copy(Path(response.path), data_root())
        except (OSError, subprocess.SubprocessError) as exc:
            logger.exception("Could not open score %s in the system PDF viewer", score_id)
            raise HTTPException(status_code=500, detail="PDF konnte nicht im Standardprogramm geöffnet werden") from exc
        return {"status": "opened"}

    return router


def create_app(frontend: Path):
    from starlette.exceptions import HTTPException
    from starlette.middleware.trustedhost import TrustedHostMiddleware
    from starlette.staticfiles import StaticFiles
    from backend.main import app

    if not (frontend / "index.html").is_file():
        raise RuntimeError(f"Desktop frontend not found: {frontend}")

    class FrontendFiles(StaticFiles):
        async def get_response(self, path, scope):
            try:
                return await super().get_response(path, scope)
            except HTTPException as exc:
                if exc.status_code != 404 or scope["method"] not in {"GET", "HEAD"}:
                    raise
                headers = dict(scope.get("headers", []))
                if b"text/html" not in headers.get(b"accept", b""):
                    raise
                return await super().get_response("index.html", scope)

    app.include_router(desktop_router())
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])
    app.mount("/", FrontendFiles(directory=frontend, html=True), name="desktop-frontend")
    return app


def initialize_standalone_identity() -> None:
    from sqlalchemy import select

    from backend import models
    from backend.database import SessionLocal
    from backend.sync.clock import new_uuid7
    from backend.sync.identity import configured_node_id
    from backend.sync.worker import sync_enabled

    with SessionLocal() as session:
        if session.scalar(select(models.NodeIdentity.id).limit(1)) is not None:
            return
        if sync_enabled() or os.getenv("SYNC_PEERS", "").strip():
            raise RuntimeError(
                "Node identity is missing; automatic initialization requires "
                "SYNC_ENABLED=false and no configured SYNC_PEERS"
            )
        if any(
            session.scalar(select(model.peer_id).limit(1)) is not None
            for model in (models.SyncPeer, models.SyncCursor)
        ):
            raise RuntimeError(
                "Node identity is missing but the database contains sync peers or cursors; "
                "use snapshot onboarding instead"
            )
        node_id = configured_node_id() or new_uuid7()
        node_name = os.getenv("NODE_NAME") or "local-node"
        if not node_name.strip() or len(node_name) > 128:
            raise RuntimeError("Node name must contain between 1 and 128 characters")
        session.add(models.NodeIdentity(node_id=node_id, name=node_name.strip()))
        session.commit()


def run(data_dir: Path, frontend: Path) -> None:
    configure(data_dir)
    from alembic import command
    from alembic.config import Config

    root = resource_root()
    from backend.operations import _sqlite_path, initialize_node, prepare_database_directory, restore_snapshot

    prepare_database_directory()
    snapshot = os.getenv("DESKTOP_SNAPSHOT")
    if snapshot:
        hub_url = os.getenv("DESKTOP_HUB_URL")
        credential_reference = os.getenv("DESKTOP_HUB_CREDENTIALS_REFERENCE")
        if not hub_url or not credential_reference:
            raise RuntimeError("DESKTOP_SNAPSHOT requires DESKTOP_HUB_URL and DESKTOP_HUB_CREDENTIALS_REFERENCE")
        marker = data_dir / ".snapshot-restored.json"
        identity = {
            "snapshot": str(Path(snapshot).expanduser().resolve()),
            "hub_url": hub_url,
            "node_id": os.getenv("NODE_ID"),
        }
        if _sqlite_path().exists():
            if not marker.is_file() or json.loads(marker.read_text(encoding="utf-8")) != identity:
                raise RuntimeError("Snapshot restore requires an empty database; existing database was not changed")
        else:
            restore_snapshot(Path(snapshot).expanduser(), hub_url, credential_reference)
            marker.write_text(json.dumps(identity), encoding="utf-8")
    else:
        from backend.bootstrap import initialize_missing_database

        initialize_missing_database()
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "backend" / "alembic"))
    command.upgrade(config, "head")
    initialize_standalone_identity()
    initialize_node()
    app = create_app(frontend)

    import uvicorn

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        port = listener.getsockname()[1]
        class DesktopServer(uvicorn.Server):
            async def startup(self, sockets=None):
                await super().startup(sockets=sockets)
                if self.started:
                    print(f"LIBRE_STAGE_READY={port}", flush=True)

        server = DesktopServer(uvicorn.Config(app, log_level="info", access_log=False))

        def watch_parent():
            sys.stdin.buffer.read()
            server.should_exit = True

        threading.Thread(target=watch_parent, daemon=True).start()
        server.run(sockets=[listener])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--frontend", type=Path)
    args = parser.parse_args()
    run(args.data_dir.resolve(), args.frontend or resource_root() / "frontend" / "build")


if __name__ == "__main__":
    main()
