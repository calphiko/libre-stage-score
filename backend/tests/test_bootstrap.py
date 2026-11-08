import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from backend import bootstrap
from backend.migrations import init_demo_users
from backend.routers import admin


ROOT = Path(__file__).resolve().parents[2]


def test_api_creates_demo_database_once(tmp_path):
    database = tmp_path / "nested" / "app.sqlite3"
    env = {
        **os.environ,
        "PYTHONPATH": str(ROOT),
        "PYTHON_DOTENV_DISABLED": "1",
        "DATABASE_URL": f"sqlite:///{database}",
        "LIBRE_STAGE_DATA_ROOT": str(tmp_path),
        "SECRET_KEY": "test-key-with-at-least-32-bytes-long",
        "SYNC_ENABLED": "false",
    }
    env.pop("NODE_ID", None)
    for attempt in range(2):
        subprocess.run([sys.executable, "-c", "from backend.main import app"], cwd=ROOT, env=env, check=True)
        with sqlite3.connect(database) as db:
            assert db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 5
            assert db.execute("SELECT COUNT(*) FROM songs").fetchone()[0] == 2
            assert db.execute("SELECT COUNT(*) FROM scores").fetchone()[0] == 4
            if attempt:
                assert db.execute("SELECT clear_name FROM users WHERE user_name = 'admin'").fetchone()[0] == "Changed"
            db.execute("UPDATE users SET clear_name = 'Changed' WHERE user_name = 'admin'")
    assert (tmp_path / "backend/storage/scores/Highland_Cathedral/Trompete_1.pdf").is_file()


def test_existing_database_is_not_seeded(monkeypatch, tmp_path):
    database = tmp_path / "existing.sqlite3"
    database.touch()
    monkeypatch.setattr(bootstrap, "SQLALCHEMY_DATABASE_URL", f"sqlite:///{database}")
    monkeypatch.setattr(init_demo_users, "run", lambda **kwargs: pytest.fail("Existing database was seeded"))
    bootstrap.initialize_missing_database()
    assert database.stat().st_size == 0


@pytest.mark.parametrize("url", ["sqlite://", "sqlite:///:memory:", "sqlite:///file:memdb?mode=memory&uri=true"])
def test_non_file_database_is_not_seeded(monkeypatch, url):
    monkeypatch.setattr(bootstrap, "SQLALCHEMY_DATABASE_URL", url)
    monkeypatch.setattr(init_demo_users, "run", lambda **kwargs: pytest.fail("Database was seeded"))
    bootstrap.initialize_missing_database()


def test_interrupted_initialization_is_reported(monkeypatch, tmp_path):
    database = tmp_path / "app.sqlite3"
    database.touch()
    (tmp_path / "app.sqlite3.initializing").mkdir()
    monkeypatch.setattr(bootstrap, "SQLALCHEMY_DATABASE_URL", f"sqlite:///{database}")
    with pytest.raises(RuntimeError, match="already in progress or was interrupted"):
        bootstrap.initialize_missing_database()
    assert database.exists()


def test_existing_score_is_not_overwritten(monkeypatch, tmp_path):
    database = tmp_path / "app.sqlite3"
    scores = tmp_path / "scores"
    pdf = scores / "Highland_Cathedral/Trompete_1.pdf"
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(b"existing")
    monkeypatch.setattr(bootstrap, "SQLALCHEMY_DATABASE_URL", f"sqlite:///{database}")
    monkeypatch.setattr(admin, "SCORES_UPLOAD_DIR", scores)
    with pytest.raises(RuntimeError, match="score file already exists"):
        bootstrap.initialize_missing_database()
    assert pdf.read_bytes() == b"existing"
    assert not database.exists()


def test_failed_seed_removes_only_new_files_and_database(monkeypatch, tmp_path):
    database = tmp_path / "app.sqlite3"
    scores = tmp_path / "scores"
    original = scores / "other.pdf"
    original.parent.mkdir()
    original.write_bytes(b"existing")
    monkeypatch.setattr(bootstrap, "SQLALCHEMY_DATABASE_URL", f"sqlite:///{database}")
    monkeypatch.setattr(admin, "SCORES_UPLOAD_DIR", scores)

    def fail_seed(*, reset, created_files):
        assert not reset
        pdf = scores / "new.pdf"
        pdf.write_bytes(b"new")
        created_files.append(pdf)
        raise RuntimeError("seed failed")

    monkeypatch.setattr(init_demo_users, "run", fail_seed)
    with pytest.raises(RuntimeError, match="seed failed"):
        bootstrap.initialize_missing_database()
    assert not database.exists()
    assert not (scores / "new.pdf").exists()
    assert original.read_bytes() == b"existing"
