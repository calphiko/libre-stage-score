import os
from pathlib import Path
import subprocess
import sys

from backend.paths import data_root


def test_default_data_root_is_project_directory(monkeypatch):
    monkeypatch.delenv("LIBRE_STAGE_DATA_ROOT", raising=False)
    assert data_root() == Path(__file__).resolve().parents[2]


def test_configured_storage_paths_are_shared_by_api_and_sync(tmp_path):
    root = tmp_path / "local data"
    env = {
        **os.environ,
        "LIBRE_STAGE_DATA_ROOT": str(root),
        "DATABASE_URL": "sqlite://",
        "SECRET_KEY": "test-key-with-at-least-32-bytes-long",
    }
    script = """
import hashlib
import os
from pathlib import Path
from backend import main
from backend.routers import admin
from backend.sync import service

root = Path(os.environ["LIBRE_STAGE_DATA_ROOT"])
scores = root / "backend" / "storage" / "scores"
assert main.PROJECT_ROOT == admin.PROJECT_ROOT == root
assert main.SCORES_STORAGE_ROOT == admin.SCORES_UPLOAD_DIR == scores
assert admin.ORIGINAL_UPLOAD_DIR == root / "backend" / "storage" / "upload"
assert service.file_blob_path("ab" * 32) == root / "backend" / "storage" / "blobs" / "ab" / (("ab" * 32) + ".pdf")
scores.mkdir(parents=True)
pdf = scores / "score.pdf"
pdf.write_bytes(b"test")
assert main._resolve_pdf_path("score.pdf") == pdf
assert admin._absolute_score_storage_path("score.pdf") == pdf

class Result:
    def all(self):
        return ["score.pdf"]

class Session:
    def scalars(self, _query):
        return Result()

assert service.find_score_blob(Session(), hashlib.sha256(b"test").hexdigest()) == pdf
"""
    subprocess.run([sys.executable, "-c", script], env=env, check=True)
