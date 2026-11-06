import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import auth, desktop, models, paths
import backend.main as main


@pytest.fixture
def pdf_client(monkeypatch, tmp_path):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    models.Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as db:
        db.add(models.NodeIdentity(node_id=str(uuid.uuid4()), name="test"))
        user = models.User(user_name="admin", user_pw=auth.hash_pw("password"), user_group="admin", email="a", clear_name="a")
        restricted = models.User(user_name="user", user_pw=auth.hash_pw("password"), user_group="user", email="b", clear_name="b")
        song = models.Song(name="Test")
        instrument = models.Instrument(instrument_name="Flute", instrument_tuning="C")
        db.add_all([user, restricted, song, instrument])
        db.flush()
        db.add(models.Score(id=1, song_id=song.id, instrument_id=instrument.id, storage_path="test.pdf"))
        db.commit()
    scores = tmp_path / "backend" / "storage" / "scores"
    scores.mkdir(parents=True)
    (scores / "test.pdf").write_bytes(b"%PDF-1.4 test")
    monkeypatch.setattr(main, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(main, "SCORES_STORAGE_ROOT", scores)
    monkeypatch.setattr(paths, "data_root", lambda: tmp_path)
    opened = []
    monkeypatch.setattr(desktop, "open_in_system_viewer", lambda path: opened.append(path))
    app = FastAPI()
    app.include_router(desktop.desktop_router())

    def get_db():
        with sessions() as db:
            yield db

    app.dependency_overrides[auth.get_db] = get_db
    with TestClient(app) as client:
        yield client, opened, tmp_path
    engine.dispose()


def headers(username):
    token = auth.create_access_token({"sub": username, "role": "admin" if username == "admin" else "user"})
    return {"Authorization": f"Bearer {token}"}


def test_open_pdf_requires_authorization_and_keeps_copy(pdf_client):
    client, opened, root = pdf_client
    assert client.post("/desktop/scores/1/open").status_code == 401
    assert client.post("/desktop/scores/1/open", headers=headers("user")).status_code == 403
    assert client.post("/desktop/scores/999/open", headers=headers("admin")).status_code == 404
    assert not opened
    response = client.post("/desktop/scores/1/open", headers=headers("admin"))
    assert response.status_code == 200
    assert len(opened) == 1
    assert opened[0].parent == root / "opened-pdfs"
    assert opened[0].suffix == ".pdf"
    assert opened[0].read_bytes() == b"%PDF-1.4 test"
    assert (root / "backend/storage/scores/test.pdf").read_bytes() == opened[0].read_bytes()


def test_open_pdf_reports_missing_file_and_viewer_errors(pdf_client, monkeypatch):
    client, opened, root = pdf_client
    (root / "backend/storage/scores/test.pdf").unlink()
    assert client.post("/desktop/scores/1/open", headers=headers("admin")).status_code == 503
    assert not opened
    (root / "backend/storage/scores/test.pdf").write_bytes(b"%PDF-1.4 test")

    def fail(_path):
        raise OSError("no viewer installed")

    monkeypatch.setattr(desktop, "open_in_system_viewer", fail)
    response = client.post("/desktop/scores/1/open", headers=headers("admin"))
    assert response.status_code == 500
    assert "Standardprogramm" in response.json()["detail"]
