import os
from pathlib import Path
import queue
import sqlite3
import subprocess
import sys
import threading
import uuid

import bcrypt
import httpx
import pytest


ROOT = Path(__file__).resolve().parents[2]


def start_desktop(data_dir, frontend):
    env = os.environ.copy()
    for key in ("NODE_ID", "NODE_NAME", "DESKTOP_SNAPSHOT", "DESKTOP_HUB_URL",
                "DESKTOP_HUB_CREDENTIALS_REFERENCE", "SYNC_PEERS"):
        env.pop(key, None)
    env["PYTHONPATH"] = str(ROOT)
    sidecar = env.pop("DESKTOP_TEST_SIDECAR", None)
    command = [sidecar] if sidecar else [sys.executable, "-m", "backend.desktop"]
    process = subprocess.Popen(
        [*command, "--data-dir", str(data_dir), "--frontend", str(frontend)],
        cwd=ROOT, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    lines = queue.Queue()

    def read_output():
        for line in process.stdout:
            lines.put(line)
        lines.put(None)

    threading.Thread(target=read_output, daemon=True).start()
    while True:
        line = lines.get(timeout=30)
        if line is None:
            error = process.stderr.read()
            process.wait()
            process.stdin.close()
            process.stdout.close()
            process.stderr.close()
            raise RuntimeError(f"Desktop service exited: {error}")
        if line.startswith("LIBRE_STAGE_READY="):
            return process, f"http://127.0.0.1:{int(line.split('=', 1)[1])}"


def stop_desktop(process):
    process.stdin.close()
    try:
        code = process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        raise
    finally:
        process.stdout.close()
        process.stderr.close()
    assert code == 0


def test_desktop_service_persists_data_and_serves_frontend(tmp_path):
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "index.html").write_text("<html>desktop</html>", encoding="utf-8")
    (frontend / "asset.js").write_text("ok", encoding="utf-8")
    data = tmp_path / "data"

    process, url = start_desktop(data, frontend)
    try:
        with httpx.Client(base_url=url) as client:
            assert client.get("/health").json()["sync"]["enabled"] is True
            assert client.post("/login", json={"username": "admin", "password": "Admin1234!"}).status_code == 200
            assert client.get("/songs", headers={"Accept": "text/html"}).text == "<html>desktop</html>"
            assert client.get("/asset.js").text == "ok"
            assert client.get("/missing.js").status_code == 404
            assert client.get("/health", headers={"Host": "invalid.example"}).status_code == 400
        env_content = (data / ".env").read_text(encoding="utf-8")
        assert (data / "backend" / "db" / "app.db").is_file()
    finally:
        stop_desktop(process)

    with sqlite3.connect(data / "backend/db/app.db") as db:
        assert db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 5
        assert db.execute("SELECT COUNT(*) FROM songs").fetchone()[0] == 2
        db.execute("UPDATE users SET clear_name = 'Changed' WHERE user_name = 'admin'")

    process, url = start_desktop(data, frontend)
    try:
        assert httpx.get(f"{url}/health").status_code == 200
        assert (data / ".env").read_text(encoding="utf-8") == env_content
    finally:
        stop_desktop(process)
    with sqlite3.connect(data / "backend/db/app.db") as db:
        assert db.execute("SELECT clear_name FROM users WHERE user_name = 'admin'").fetchone()[0] == "Changed"


def test_desktop_snapshot_bootstrap_and_restart(tmp_path):
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "index.html").write_text("desktop", encoding="utf-8")
    source = tmp_path / "source"
    process, _ = start_desktop(source, frontend)
    stop_desktop(process)
    snapshot = tmp_path / "snapshot.zip"
    env = {
        **os.environ,
        "PYTHONPATH": str(ROOT),
        "PYTHON_DOTENV_DISABLED": "1",
        "DATABASE_URL": f"sqlite:///{source / 'backend/db/app.db'}",
        "LIBRE_STAGE_DATA_ROOT": str(source),
    }
    subprocess.run(
        [sys.executable, "-m", "backend.operations", "export-snapshot", "--output", str(snapshot)],
        cwd=ROOT, env=env, check=True, capture_output=True,
    )
    target = tmp_path / "target"
    target.mkdir()
    node_id = str(uuid.uuid4())
    (target / ".env").write_text(
        "SECRET_KEY=desktop-test-secret-key-at-least-32-bytes\n"
        "DATABASE_URL=sqlite:///./backend/db/app.db\n"
        "SYNC_ENABLED=false\n"
        f"NODE_ID={node_id}\n"
        "NODE_NAME=desktop-test\n"
        f'DESKTOP_SNAPSHOT="{snapshot}"\n'
        "DESKTOP_HUB_URL=https://hub.example\n"
        "DESKTOP_HUB_CREDENTIALS_REFERENCE=HUB_TOKEN\n"
        "HUB_TOKEN=test-peer-secret-with-at-least-32-bytes\n",
        encoding="utf-8",
    )
    process, _ = start_desktop(target, frontend)
    stop_desktop(process)
    with sqlite3.connect(target / "backend/db/app.db") as db:
        assert db.execute("SELECT node_id FROM node_identity").fetchone()[0] == node_id
        assert db.execute("SELECT url FROM sync_peers").fetchone()[0] == "https://hub.example"
    process, _ = start_desktop(target, frontend)
    stop_desktop(process)


def test_desktop_snapshot_refuses_existing_database(tmp_path):
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "index.html").write_text("desktop", encoding="utf-8")
    data = tmp_path / "data"
    process, _ = start_desktop(data, frontend)
    stop_desktop(process)
    with (data / ".env").open("a", encoding="utf-8") as file:
        file.write(
            "DESKTOP_SNAPSHOT=not-opened.zip\n"
            "DESKTOP_HUB_URL=https://hub.example\n"
            "DESKTOP_HUB_CREDENTIALS_REFERENCE=HUB_TOKEN\n"
        )
    with pytest.raises(RuntimeError, match="requires an empty database"):
        start_desktop(data, frontend)


def prepare_database_without_identity(tmp_path, *, peer=False):
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "index.html").write_text("desktop", encoding="utf-8")
    data = tmp_path / "data"
    (data / "backend/db").mkdir(parents=True)
    env = {
        **os.environ,
        "PYTHONPATH": str(ROOT),
        "PYTHON_DOTENV_DISABLED": "1",
        "DATABASE_URL": f"sqlite:///{data / 'backend/db/app.db'}",
    }
    env.pop("NODE_ID", None)
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ROOT, env=env, check=True, capture_output=True,
    )
    (data / ".env").write_text(
        "SECRET_KEY=desktop-test-secret-key-at-least-32-bytes\n"
        "DATABASE_URL=sqlite:///./backend/db/app.db\n"
        "NODE_NAME=desktop-spoke\n"
        "SYNC_ENABLED=false\n",
        encoding="utf-8",
    )
    password_hash = bcrypt.hashpw(b"test-password", bcrypt.gensalt()).decode()
    with sqlite3.connect(data / "backend/db/app.db") as db:
        db.execute(
            "INSERT INTO users (user_name, user_pw, user_group, email, clear_name, musician, "
            "is_singer, status, uuid, updated_at, updated_by_node, version) "
            "VALUES (?, ?, 'admin', '', 'Test', 0, 0, 'active', ?, 1, 'unconfigured', 1)",
            ("test-user", password_hash, str(uuid.uuid4())),
        )
        db.execute(
            "INSERT INTO change_log (table_name, record_uuid, operation, payload, hlc, node_id, created_at) "
            "VALUES ('users', ?, 'update', '{}', 1, 'unconfigured', CURRENT_TIMESTAMP)",
            (str(uuid.uuid4()),),
        )
        db.execute("DELETE FROM node_identity")
        if peer:
            db.execute(
                "INSERT INTO sync_peers (peer_id, url, credentials_reference, active) "
                "VALUES (?, 'https://hub.example', 'HUB_KEY', 1)",
                (str(uuid.uuid4()),),
            )
    return data, frontend


def test_standalone_existing_database_initializes_identity_and_login(tmp_path):
    data, frontend = prepare_database_without_identity(tmp_path)
    process, url = start_desktop(data, frontend)
    try:
        response = httpx.post(f"{url}/login", json={"username": "test-user", "password": "test-password"})
        assert response.status_code == 200
    finally:
        stop_desktop(process)
    with sqlite3.connect(data / "backend/db/app.db") as db:
        identity = db.execute("SELECT node_id, name FROM node_identity").fetchone()
        assert str(uuid.UUID(identity[0])) == identity[0]
        assert identity[1] == "desktop-spoke"
        assert db.execute("SELECT count(*) FROM change_log").fetchone()[0] == 1
    process, _ = start_desktop(data, frontend)
    stop_desktop(process)
    with sqlite3.connect(data / "backend/db/app.db") as db:
        assert db.execute("SELECT node_id, name FROM node_identity").fetchall() == [identity]


@pytest.mark.skipif(sys.platform == "win32", reason="Uses a Unix test opener")
def test_desktop_opens_pdf_with_system_viewer(tmp_path, monkeypatch):
    data, frontend = prepare_database_without_identity(tmp_path)
    scores = data / "backend/storage/scores"
    scores.mkdir(parents=True)
    (scores / "test.pdf").write_bytes(b"%PDF-1.4 test")
    with sqlite3.connect(data / "backend/db/app.db") as db:
        db.execute(
            "INSERT INTO songs (id, name, score_completed, song_with_solo, uuid, updated_at, updated_by_node, version) "
            "VALUES (1, 'Test', 0, 0, ?, 1, 'unconfigured', 1)", (str(uuid.uuid4()),)
        )
        db.execute(
            "INSERT INTO instruments (id, instrument_name, instrument_tuning, uuid, updated_at, updated_by_node, version) "
            "VALUES (1, 'Flute', 'C', ?, 1, 'unconfigured', 1)", (str(uuid.uuid4()),)
        )
        db.execute(
            "INSERT INTO scores (id, song_id, instrument_id, storage_path, uuid, updated_at, updated_by_node, version) "
            "VALUES (1, 1, 1, 'test.pdf', ?, 1, 'unconfigured', 1)", (str(uuid.uuid4()),)
        )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    opener = bin_dir / ("open" if sys.platform == "darwin" else "xdg-open")
    opener.write_text('#!/bin/sh\nprintf "%s" "$1" > "$DESKTOP_TEST_OPENED"\n', encoding="utf-8")
    opener.chmod(0o755)
    result = tmp_path / "opened.txt"
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setenv("DESKTOP_TEST_OPENED", str(result))
    process, url = start_desktop(data, frontend)
    try:
        with httpx.Client(base_url=url) as client:
            login = client.post("/login", json={"username": "test-user", "password": "test-password"})
            assert login.status_code == 200
            response = client.post(
                "/desktop/scores/1/open",
                headers={"Origin": url, "X-CSRF-Token": client.cookies["csrf_token"]},
            )
            assert response.status_code == 200, response.text
        copied_pdf = Path(result.read_text())
        assert copied_pdf.parent == data / "opened-pdfs"
        assert copied_pdf.read_bytes() == b"%PDF-1.4 test"
    finally:
        stop_desktop(process)
    assert copied_pdf.is_file()


@pytest.mark.parametrize(
    ("extra", "peer", "error"),
    [
        ("NODE_ID=dieser-mac-hier\n", False, "NODE_ID must be a valid UUID"),
        ("SYNC_ENABLED=true\n", False, "automatic initialization requires"),
        ("", True, "database contains sync peers or cursors"),
    ],
)
def test_standalone_initialization_rejects_unsafe_state(tmp_path, extra, peer, error):
    data, frontend = prepare_database_without_identity(tmp_path, peer=peer)
    with (data / ".env").open("a", encoding="utf-8") as file:
        file.write(extra)
    with pytest.raises(RuntimeError, match=error):
        start_desktop(data, frontend)
    with sqlite3.connect(data / "backend/db/app.db") as db:
        assert db.execute("SELECT count(*) FROM node_identity").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM users").fetchone()[0] == 1
