"""Initialize a missing file-backed SQLite database with the demo dataset."""

import logging
from pathlib import Path

from sqlalchemy.engine import make_url

from backend.database import SQLALCHEMY_DATABASE_URL

logger = logging.getLogger(__name__)


def initialize_missing_database() -> None:
    url = make_url(SQLALCHEMY_DATABASE_URL)
    if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
        return
    if url.query.get("uri", "").lower() in {"true", "1", "yes"}:
        return

    path = Path(url.database).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_name(path.name + ".initializing")
    try:
        lock.mkdir()
    except FileExistsError as exc:
        raise RuntimeError(f"Database initialization is already in progress or was interrupted: {lock}") from exc

    try:
        if path.exists():
            return

        from backend.migrations import init_demo_users
        from backend.routers import admin

        demo_paths = [
            admin.SCORES_UPLOAD_DIR / "Highland_Cathedral" / "Trompete_1.pdf",
            admin.SCORES_UPLOAD_DIR / "Highland_Cathedral" / "Tenorhorn_1.pdf",
            admin.SCORES_UPLOAD_DIR / "Amazing_Grace" / "Floete_1.pdf",
            admin.SCORES_UPLOAD_DIR / "Amazing_Grace" / "Euphonium_1.pdf",
        ]
        existing = [pdf for pdf in demo_paths if pdf.exists()]
        if existing:
            raise RuntimeError(f"Cannot initialize demo database; score file already exists: {existing[0]}")

        with path.open("xb"):
            pass
        created_files: list[Path] = []
        try:
            logger.warning("Creating a new database with demo data and known demo passwords: %s", path)
            init_demo_users.run(reset=False, created_files=created_files)
        except Exception:
            for pdf in created_files:
                pdf.unlink(missing_ok=True)
            for suffix in ("-wal", "-shm", ""):
                path.with_name(path.name + suffix).unlink(missing_ok=True)
            raise
    finally:
        lock.rmdir()
