import base64
import json
import struct
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import uuid

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import auth, models
from backend import database as database_module
from backend.database import SessionLocal
from backend.main import app
from backend.routers import admin

import backend.main as main_module


@pytest.fixture(autouse=True)
def use_in_memory_database(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    testing_session_local = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    models.Base.metadata.create_all(bind=engine)

    monkeypatch.setattr(database_module, "engine", engine)
    monkeypatch.setattr(database_module, "SessionLocal", testing_session_local)
    monkeypatch.setattr(auth.database, "engine", engine)
    monkeypatch.setattr(auth.database, "SessionLocal", testing_session_local)
    monkeypatch.setattr(main_module, "engine", engine)
    globals()["SessionLocal"] = testing_session_local

    yield

    models.Base.metadata.drop_all(bind=engine)


client = TestClient(app)


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["sync"]["pending_changes"] == 0


def test_health_reports_sync_backlog_and_oldest_peer_sync(monkeypatch):
    peer_id = str(uuid.uuid4())
    old_success = datetime.now(timezone.utc) - timedelta(minutes=5)
    monkeypatch.setenv("SYNC_ENABLED", "true")
    with SessionLocal() as session:
        session.add(
            models.SyncPeer(
                peer_id=peer_id,
                url="https://peer.example",
                credentials_reference="SYNC_TEST_PEER_TOKEN",
            )
        )
        session.add(
            models.SyncCursor(
                peer_id=peer_id,
                last_sent=1,
                last_received=1,
                last_success_at=old_success,
            )
        )
        for sequence in (1, 2, 3):
            session.add(
                models.ChangeLog(
                    seq=sequence,
                    table_name="groups",
                    record_uuid=str(uuid.uuid4()),
                    operation="insert",
                    payload={},
                    hlc=sequence,
                    node_id=str(uuid.uuid4()),
                )
            )
        session.commit()

    sync = client.get("/health").json()["sync"]
    assert sync["enabled"] is True
    assert sync["active_peers"] == 1
    assert sync["pending_changes"] == 2
    assert sync["peers_without_success"] == 0
    assert sync["last_sync_age_seconds"] >= 300


def test_extract_song_metadata_from_pdf_falls_back_to_ocr_when_text_is_empty(monkeypatch, tmp_path):
    class DummyPage:
        def __init__(self, text):
            self._text = text

        def extract_text(self):
            return self._text

    class DummyReader:
        def __init__(self, texts):
            self.pages = [DummyPage(text) for text in texts]
            self.metadata = None

    pdf_path = tmp_path / "ocr_scan.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")
    reader = DummyReader(["", ""])
    monkeypatch.setattr(admin, "_ocr_pdf_first_pages", lambda path, page_limit=2: "THE GREAT SYMPHONY\nArrangeur: Max Muster\nKomponist: Anna Beispiel\n")

    metadata = admin._extract_song_metadata_from_pdf(reader, "ocr_scan.pdf", source_file=pdf_path)

    assert metadata["name"] == "THE GREAT SYMPHONY"
    assert metadata["composer"] == "Anna Beispiel"
    assert metadata["arrangement"] == "Max Muster"


def test_extract_song_metadata_from_pdf_prefers_title_and_arrangement_lines():
    class DummyPage:
        def __init__(self, text):
            self._text = text

        def extract_text(self):
            return self._text

    class DummyReader:
        def __init__(self, texts):
            self.pages = [DummyPage(text) for text in texts]
            self.metadata = None

    reader = DummyReader([
        "Arrangement: Max Muster\nComposer: Anna Beispiel\n\nThe Great Symphony\n",
        "Page 2\n",
    ])
    metadata = admin._extract_song_metadata_from_pdf(reader, "random_file_name.pdf")

    assert metadata["name"] == "The Great Symphony"
    assert metadata["composer"] == "Anna Beispiel"
    assert metadata["arrangement"] == "Max Muster"


def test_render_page_top_preview_accepts_zero_padded_page_filename(monkeypatch, tmp_path):
    source_file = tmp_path / "source.pdf"
    source_file.write_bytes(b"%PDF-test")
    output_bytes = b"preview-image"

    def render_preview(args, **kwargs):
        output_prefix = Path(args[-1])
        (output_prefix.parent / f"{output_prefix.name}-001.png").write_bytes(output_bytes)
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(admin.shutil, "which", lambda _: "/usr/bin/pdftoppm")
    monkeypatch.setattr(admin.subprocess, "run", render_preview)
    page = SimpleNamespace(mediabox=SimpleNamespace(width=600, height=850))

    preview = admin._render_page_top_preview(source_file, 0, page)

    assert preview == f"data:image/png;base64,{base64.b64encode(output_bytes).decode('ascii')}"


def test_extract_song_metadata_from_pdf_handles_first_title_line_and_arrangeur_label():
    class DummyPage:
        def __init__(self, text):
            self._text = text

        def extract_text(self):
            return self._text

    class DummyReader:
        def __init__(self, texts):
            self.pages = [DummyPage(text) for text in texts]
            self.metadata = None

    reader = DummyReader([
        "THE GREAT SYMPHONY\nArrangeur: Max Muster\nKomponist: Anna Beispiel\n",
        "Page 2\n",
    ])
    metadata = admin._extract_song_metadata_from_pdf(reader, "random_file_name.pdf")

    assert metadata["name"] == "THE GREAT SYMPHONY"
    assert metadata["composer"] == "Anna Beispiel"
    assert metadata["arrangement"] == "Max Muster"


def test_match_instrument_by_outline_title_does_not_confuse_same_number_different_family():
    instruments = {
        name: models.Instrument(instrument_name=name, instrument_tuning=tuning, notes=None)
        for name, tuning in {
            "Flöte 2 C": "C",
            "Posaune 2 C": "C",
        }.items()
    }
    normalized = {admin._normalize_part_name(name): instrument for name, instrument in instruments.items()}

    assert admin._score_instrument_match("Flöte 2", "Posaune 2 C") < 0.7
    assert admin._score_instrument_match("Flöte 2 in C", "Posaune 2 in C") < 0.7
    assert admin._match_instrument_by_outline_title("Flöte 2", normalized).instrument_name == "Flöte 2 C"
    assert admin._match_instrument_by_outline_title("Flöte 2 in C", normalized).instrument_name == "Flöte 2 C"


def test_match_instrument_by_outline_title_handles_aliases_and_numbers():
    instruments = {
        name: models.Instrument(instrument_name=name, instrument_tuning=tuning, notes=None)
        for name, tuning in {
            "Flöte 1 C": "C",
            "Flöte 2 C": "C",
            "Piccoloflöte 1 C": "C",
            "Klarinette 2 Bb": "Bb",
            "Alt-Saxophon 1 Eb": "Eb",
            "Fagott 1 C": "C",
        }.items()
    }
    normalized = {admin._normalize_part_name(name): instrument for name, instrument in instruments.items()}

    assert admin._match_instrument_by_outline_title("Fl. 1", normalized).instrument_name == "Flöte 1 C"
    assert admin._match_instrument_by_outline_title("Picc. 1", normalized).instrument_name == "Piccoloflöte 1 C"
    assert admin._match_instrument_by_outline_title("Klar. 2", normalized).instrument_name == "Klarinette 2 Bb"
    assert admin._match_instrument_by_outline_title("Alt-Sax 1", normalized).instrument_name == "Alt-Saxophon 1 Eb"
    assert admin._match_instrument_by_outline_title("Flöte 2", normalized).instrument_name == "Flöte 2 C"
    assert admin._match_instrument_by_outline_title("Clarinet 2", normalized).instrument_name == "Klarinette 2 Bb"
    assert admin._match_instrument_by_outline_title("Alto Saxophone 1", normalized).instrument_name == "Alt-Saxophon 1 Eb"
    assert admin._match_instrument_by_outline_title("Flute 1", normalized).instrument_name == "Flöte 1 C"
    assert admin._match_instrument_by_outline_title("Bassoon 1", normalized).instrument_name == "Fagott 1 C"


def test_editor_in_user_mode_only_sees_assigned_instruments():
    db = SessionLocal()
    username = "editor_user_mode"
    group_name = "Klarinetten"
    allowed_instrument_name = "Klarinette 1 C"
    blocked_instrument_name = "Flöte 1 C"
    try:
        existing_user = db.query(models.User).filter(models.User.user_name == username).first()
        if existing_user:
            db.query(models.UserGroupMembership).filter(models.UserGroupMembership.user_id == existing_user.id).delete()
            db.delete(existing_user)
            db.commit()

        group = db.query(models.Group).filter(models.Group.name == group_name).first()
        if group:
            db.query(models.GroupInstrumentMembership).filter(models.GroupInstrumentMembership.group_id == group.id).delete()
            db.delete(group)
            db.commit()

        group = models.Group(name=group_name, notes=None)
        allowed_instrument = models.Instrument(instrument_name=allowed_instrument_name, instrument_tuning="C", notes=None)
        blocked_instrument = models.Instrument(instrument_name=blocked_instrument_name, instrument_tuning="C", notes=None)
        user = models.User(
            user_name=username,
            user_pw="hashedpw",
            user_group="editor",
            email="editor-user-mode@example.com",
            clear_name="Editor User Mode",
            musician=True,
            status="active",
        )
        db.add_all([group, allowed_instrument, blocked_instrument, user])
        db.flush()
        db.add(models.UserGroupMembership(user_id=user.id, group_id=group.id))
        db.add(models.GroupInstrumentMembership(group_id=group.id, instrument_id=allowed_instrument.id))
        db.commit()

        token = auth.create_access_token({"sub": user.user_name, "role": user.user_group})
        response = client.get("/admin/instruments?view=user", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200
        returned_names = [entry["instrument_name"] for entry in response.json()]
        assert allowed_instrument_name in returned_names
        assert blocked_instrument_name not in returned_names
    finally:
        db.rollback()
        user = db.query(models.User).filter(models.User.user_name == username).first()
        if user:
            db.query(models.UserGroupMembership).filter(models.UserGroupMembership.user_id == user.id).delete()
            db.delete(user)
        group = db.query(models.Group).filter(models.Group.name == group_name).first()
        if group:
            db.query(models.GroupInstrumentMembership).filter(models.GroupInstrumentMembership.group_id == group.id).delete()
            db.delete(group)
        for instrument_name in [allowed_instrument_name, blocked_instrument_name]:
            instrument = db.query(models.Instrument).filter(models.Instrument.instrument_name == instrument_name).first()
            if instrument:
                db.delete(instrument)
        db.commit()
        db.close()


def test_user_without_group_cannot_access_scores_via_direct_user_instrument_assignment():
    db = SessionLocal()
    username = "group_only_user"
    instrument_name = "Solo Flöte"
    try:
        existing_user = db.query(models.User).filter(models.User.user_name == username).first()
        if existing_user:
            db.query(models.UserInstrument).filter(models.UserInstrument.user_id == existing_user.id).delete()
            db.delete(existing_user)
            db.commit()

        user = models.User(
            user_name=username,
            user_pw="hashedpw",
            user_group="user",
            email="group-only@example.com",
            clear_name="Group Only User",
            musician=True,
            status="active",
        )
        instrument = models.Instrument(instrument_name=instrument_name, instrument_tuning="C", notes=None)
        song = models.Song(name="Solo Access Song", tune="C", composer="Composer", arrangement=None, length=None, notes=None)
        db.add_all([user, instrument, song])
        db.flush()
        db.add(models.UserInstrument(user_id=user.id, instrument_id=instrument.id, notes="Direkt"))
        db.add(models.Score(song_id=song.id, instrument_id=instrument.id, storage_path="group-only/solo.pdf", file_hash="c", notes=None))
        db.commit()

        current = {"user_id": user.id, "user_name": user.user_name, "user_group": user.user_group}
        assert auth.user_can_access_document(db, current, "score", song_id := song.id) is False
        token = auth.create_access_token({"sub": user.user_name, "role": user.user_group})
        response = client.get(f"/admin/scores?song_id={song.id}", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200
        assert response.json() == []
    finally:
        db.rollback()
        user = db.query(models.User).filter(models.User.user_name == username).first()
        if user:
            db.query(models.UserInstrument).filter(models.UserInstrument.user_id == user.id).delete()
            db.delete(user)
        instrument = db.query(models.Instrument).filter(models.Instrument.instrument_name == instrument_name).first()
        if instrument:
            db.delete(instrument)
        song = db.query(models.Song).filter(models.Song.name == "Solo Access Song").first()
        if song:
            db.query(models.Score).filter(models.Score.song_id == song.id).delete()
            db.delete(song)
        db.commit()
        db.close()


def test_user_instrument_backpopulation():
    db = SessionLocal()
    test_username = "instrument_user"
    test_instrument_name = "Flöte-Test"
    try:
        existing_user = db.query(models.User).filter(models.User.user_name == test_username).first()
        if existing_user:
            db.query(models.UserInstrument).filter(models.UserInstrument.user_id == existing_user.id).delete()
            db.delete(existing_user)

        existing_instrument = (
            db.query(models.Instrument).filter(models.Instrument.instrument_name == test_instrument_name).first()
        )
        if existing_instrument:
            db.query(models.UserInstrument).filter(models.UserInstrument.instrument_id == existing_instrument.id).delete()
            db.delete(existing_instrument)
        db.commit()

        user = models.User(
            user_name=test_username,
            user_pw="hashedpw",
            user_group="user",
            email="instrument@example.com",
            clear_name="Instrument User",
            status="active",
        )
        instrument = models.Instrument(
            instrument_name=test_instrument_name,
            instrument_tuning="C",
            notes="Testinstrument",
        )
        db.add_all([user, instrument])
        db.commit()
        db.refresh(user)
        db.refresh(instrument)

        assignment = models.UserInstrument(user_id=user.id, instrument_id=instrument.id, notes="Primär")
        db.add(assignment)
        db.commit()
        db.refresh(assignment)

        assert assignment.user_id == user.id
        assert assignment.instrument_id == instrument.id
        assert [item.instrument_id for item in user.user_instruments] == [instrument.id]
        assert user.user_instruments[0].instrument.instrument_name == test_instrument_name
    finally:
        user = db.query(models.User).filter(models.User.user_name == test_username).first()
        if user:
            db.query(models.UserInstrument).filter(models.UserInstrument.user_id == user.id).delete()
            db.delete(user)

        instrument = db.query(models.Instrument).filter(models.Instrument.instrument_name == test_instrument_name).first()
        if instrument:
            db.query(models.UserInstrument).filter(models.UserInstrument.instrument_id == instrument.id).delete()
            db.delete(instrument)
        db.commit()
        db.close()


def test_scores_are_filtered_to_user_group_instruments():
    db = SessionLocal()
    username = "group_filtered_user"
    try:
        existing_user = db.query(models.User).filter(models.User.user_name == username).first()
        if existing_user:
            db.query(models.UserGroupMembership).filter(models.UserGroupMembership.user_id == existing_user.id).delete()
            db.delete(existing_user)
            db.commit()

        group = db.query(models.Group).filter(models.Group.name == "Filtered Group").first()
        if group:
            db.query(models.GroupInstrumentMembership).filter(models.GroupInstrumentMembership.group_id == group.id).delete()
            db.delete(group)
            db.commit()

        group = models.Group(name="Filtered Group", notes=None)
        allowed_instrument = models.Instrument(instrument_name="Filtered Flöte 1", instrument_tuning="C", notes=None)
        blocked_instrument = models.Instrument(instrument_name="Filtered Trompete 1", instrument_tuning="Bb", notes=None)
        song = models.Song(name="Filtered Song", tune="C", composer="Composer", arrangement=None, length=None, notes=None)
        user = models.User(
            user_name=username,
            user_pw="hashedpw",
            user_group="user",
            email="filtered@example.com",
            clear_name="Filtered User",
            musician=True,
            status="active",
        )

        db.add_all([group, allowed_instrument, blocked_instrument, song, user])
        db.flush()
        db.add(models.UserGroupMembership(user_id=user.id, group_id=group.id))
        db.add(models.GroupInstrumentMembership(group_id=group.id, instrument_id=allowed_instrument.id))
        db.add_all([
            models.Score(song_id=song.id, instrument_id=allowed_instrument.id, storage_path="filtered-song/Flote_1.pdf", file_hash="a", notes="allowed"),
            models.Score(song_id=song.id, instrument_id=blocked_instrument.id, storage_path="filtered-song/Trompete_1.pdf", file_hash="b", notes="blocked"),
        ])
        db.commit()

        token = auth.create_access_token({"sub": user.user_name, "role": user.user_group})
        response = client.get(f"/admin/scores?song_id={song.id}", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200
        returned_ids = [entry["instrument_id"] for entry in response.json()]
        assert returned_ids == [allowed_instrument.id]
    finally:
        db.rollback()
        user = db.query(models.User).filter(models.User.user_name == username).first()
        if user:
            db.query(models.UserGroupMembership).filter(models.UserGroupMembership.user_id == user.id).delete()
            db.delete(user)
        group = db.query(models.Group).filter(models.Group.name == "Filtered Group").first()
        if group:
            db.query(models.GroupInstrumentMembership).filter(models.GroupInstrumentMembership.group_id == group.id).delete()
            db.delete(group)
        db.query(models.Score).filter(models.Score.notes.in_(["allowed", "blocked"])).delete()
        for instrument_name in ["Filtered Flöte 1", "Filtered Trompete 1"]:
            instrument = db.query(models.Instrument).filter(models.Instrument.instrument_name == instrument_name).first()
            if instrument:
                db.delete(instrument)
        song = db.query(models.Song).filter(models.Song.name == "Filtered Song").first()
        if song:
            db.delete(song)
        db.commit()
        db.close()


def test_user_can_access_score_when_group_has_instrument():
    db = SessionLocal()
    username = "document_access_user"
    try:
        current_user = db.query(models.User).filter(models.User.user_name == username).first()
        if current_user:
            db.query(models.UserGroupMembership).filter(models.UserGroupMembership.user_id == current_user.id).delete()
            db.delete(current_user)
            db.commit()

        group = models.Group(name="Document Access Group", notes=None)
        instrument = models.Instrument(instrument_name="Dokument Flöte 1", instrument_tuning="C", notes=None)
        song = models.Song(name="Document Access Song", tune="C", composer="Composer", arrangement=None, length=None, notes=None)
        user = models.User(
            user_name=username,
            user_pw="hashedpw",
            user_group="user",
            email="document@example.com",
            clear_name="Document User",
            musician=True,
            status="active",
        )
        db.add_all([group, instrument, song, user])
        db.flush()
        db.add(models.UserGroupMembership(user_id=user.id, group_id=group.id))
        db.add(models.GroupInstrumentMembership(group_id=group.id, instrument_id=instrument.id))
        score = models.Score(song_id=song.id, instrument_id=instrument.id, storage_path="document-access/score.pdf", file_hash="abc", notes=None)
        db.add(score)
        db.commit()

        current = {"user_id": user.id, "user_name": user.user_name, "user_group": user.user_group}
        assert auth.user_can_access_document(db, current, "score", score.id) is True
    finally:
        db.rollback()
        user = db.query(models.User).filter(models.User.user_name == username).first()
        if user:
            db.query(models.UserGroupMembership).filter(models.UserGroupMembership.user_id == user.id).delete()
            db.delete(user)
        group = db.query(models.Group).filter(models.Group.name == "Document Access Group").first()
        if group:
            db.query(models.GroupInstrumentMembership).filter(models.GroupInstrumentMembership.group_id == group.id).delete()
            db.delete(group)
        instrument = db.query(models.Instrument).filter(models.Instrument.instrument_name == "Dokument Flöte 1").first()
        if instrument:
            db.delete(instrument)
        song = db.query(models.Song).filter(models.Song.name == "Document Access Song").first()
        if song:
            db.delete(song)
        db.query(models.Score).filter(models.Score.notes == None).delete()
        db.commit()
        db.close()


def test_user_without_group_cannot_access_score_via_direct_instrument_assignment():
    db = SessionLocal()
    username = "document_direct_user"
    try:
        user = db.query(models.User).filter(models.User.user_name == username).first()
        if user:
            db.query(models.UserInstrument).filter(models.UserInstrument.user_id == user.id).delete()
            db.delete(user)
            db.commit()

        instrument = models.Instrument(instrument_name="Direkt Flöte 1", instrument_tuning="C", notes=None)
        song = models.Song(name="Direct Access Song", tune="C", composer="Composer", arrangement=None, length=None, notes=None)
        user = models.User(
            user_name=username,
            user_pw="hashedpw",
            user_group="user",
            email="direct@example.com",
            clear_name="Direct User",
            musician=True,
            status="active",
        )
        db.add_all([instrument, song, user])
        db.flush()
        db.add(models.UserInstrument(user_id=user.id, instrument_id=instrument.id, notes="Direkt"))
        score = models.Score(song_id=song.id, instrument_id=instrument.id, storage_path="direct-access/score.pdf", file_hash="xyz", notes=None)
        db.add(score)
        db.commit()

        current = {"user_id": user.id, "user_name": user.user_name, "user_group": user.user_group}
        assert auth.user_can_access_document(db, current, "score", score.id) is False
        assert auth.user_can_access_document(db, current, "score", score.id + 9999) is False
    finally:
        db.rollback()
        user = db.query(models.User).filter(models.User.user_name == username).first()
        if user:
            db.query(models.UserInstrument).filter(models.UserInstrument.user_id == user.id).delete()
            db.delete(user)
        instrument = db.query(models.Instrument).filter(models.Instrument.instrument_name == "Direkt Flöte 1").first()
        if instrument:
            db.delete(instrument)
        song = db.query(models.Song).filter(models.Song.name == "Direct Access Song").first()
        if song:
            db.delete(song)
        db.query(models.Score).filter(models.Score.notes == None).delete()
        db.commit()
        db.close()


def test_delete_song_tombstones_scores_and_retains_files():
    db = SessionLocal()
    song_name = "Delete Song Storage Test"
    storage_dir = None
    score_path = None
    try:
        song = db.query(models.Song).filter(models.Song.name == song_name).first()
        if song:
            db.query(models.Score).filter(models.Score.song_id == song.id).delete()
            db.delete(song)
            db.commit()

        song = models.Song(name=song_name, tune="C", composer="Composer", arrangement=None, length=None, notes=None)
        db.add(song)
        db.flush()

        instrument = models.Instrument(instrument_name="Delete Song Instrument", instrument_tuning="C", notes=None)
        db.add(instrument)
        db.flush()

        storage_dir = admin.SCORES_UPLOAD_DIR / admin._build_song_storage_folder_name(song, db)
        storage_dir.mkdir(parents=True, exist_ok=True)
        score_path = storage_dir / "Delete_Song_Instrument.pdf"
        score_path.write_bytes(b"%PDF-1.4\n")

        score = models.Score(
            song_id=song.id,
            instrument_id=instrument.id,
            storage_path=f"{admin._build_song_storage_folder_name(song, db)}/{admin._safe_filename_part(instrument.instrument_name)}.pdf",
            file_hash="abc",
            notes="delete-test",
        )
        db.add(score)
        db.commit()

        admin.delete_song(song.id, db)

        assert score_path.exists()
        assert storage_dir.exists()
        assert db.query(models.Song).filter(models.Song.id == song.id).first() is None
        session_info = db.info
        session_info["include_deleted"] = True
        try:
            assert db.query(models.Song).filter(models.Song.id == song.id).one().deleted_at is not None
            assert db.query(models.Score).filter(models.Score.notes == "delete-test").one().deleted_at is not None
        finally:
            session_info.pop("include_deleted")
    finally:
        db.query(models.Score).filter(models.Score.notes == "delete-test").delete()
        song = db.query(models.Song).filter(models.Song.name == song_name).first()
        if song:
            db.delete(song)
        instrument = db.query(models.Instrument).filter(models.Instrument.instrument_name == "Delete Song Instrument").first()
        if instrument:
            db.delete(instrument)
        db.commit()
        if score_path and score_path.exists():
            score_path.unlink()
        if storage_dir and storage_dir.exists():
            storage_dir.rmdir()
        db.close()


def test_storage_rescrape_updates_changed_hash():
    db = SessionLocal()
    song_name = "Storage Song Test"
    instrument_name = "1. Teststimme Drift"
    original_storage_dir = admin.SCORES_UPLOAD_DIR
    with TemporaryDirectory() as tmpdir:
        admin.SCORES_UPLOAD_DIR = Path(tmpdir)
        try:
            db.query(models.Score).filter(models.Score.notes == "storage-test").delete()

            song = db.query(models.Song).filter(models.Song.name == song_name).first()
            if not song:
                song = models.Song(name=song_name, tune="C", composer="Composer", arrangement=None, length=None, notes=None)
                db.add(song)
                db.flush()

            instrument = db.query(models.Instrument).filter(models.Instrument.instrument_name == instrument_name).first()
            if not instrument:
                instrument = models.Instrument(instrument_name=instrument_name, instrument_tuning="C", notes=None)
                db.add(instrument)
                db.flush()

            storage_path = f"{admin._build_song_storage_folder_name(song, db)}/{admin._safe_filename_part(instrument.instrument_name)}.pdf"
            file_path = admin._absolute_score_storage_path(storage_path)
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_bytes(b"%PDF-1.4\nstorage-test\n")

            score = models.Score(
                song_id=song.id,
                instrument_id=instrument.id,
                storage_path=storage_path,
                file_hash="outdated",
                notes="storage-test",
            )
            db.add(score)
            db.commit()

            status_before = admin.get_storage_status(db)
            assert status_before.has_changes is False
            assert status_before.changed_files == []

            status_after = admin.rescrape_storage(db)
            assert status_after.has_changes is False
            assert status_after.created_scores == 0
            assert status_after.updated_scores == 0
            assert status_after.removed_scores == 0

            db.refresh(score)
            assert score.file_hash == "outdated"
        finally:
            db.rollback()
            admin.SCORES_UPLOAD_DIR = original_storage_dir
            db.query(models.Score).filter(models.Score.notes == "storage-test").delete()
            song = db.query(models.Song).filter(models.Song.name == song_name).first()
            if song:
                db.delete(song)
            instrument = db.query(models.Instrument).filter(models.Instrument.instrument_name == instrument_name).first()
            if instrument:
                db.delete(instrument)
            db.commit()
            db.close()


def test_song_pdf_upload_archives_original_and_records_metadata(monkeypatch, tmp_path):
    db = SessionLocal()
    original_scores_dir = admin.SCORES_UPLOAD_DIR
    original_upload_dir = admin.ORIGINAL_UPLOAD_DIR
    admin.SCORES_UPLOAD_DIR = tmp_path / "scores"
    admin.ORIGINAL_UPLOAD_DIR = tmp_path / "upload"
    try:
        user = models.User(
            user_name="original_upload_admin",
            user_pw="hashedpw",
            user_group="admin",
            email="original-upload@example.com",
            clear_name="Original Upload Admin",
            status="active",
        )
        db.add(user)
        db.commit()
        token = auth.create_access_token({"sub": user.user_name, "role": user.user_group})
        monkeypatch.setattr(
            admin,
            "_preview_chapters_from_pdf",
            lambda reader, db, song_name: [
                {"chapter_title": "Flute", "start_page": 0, "end_page": 1}
            ],
        )

        writer = PdfWriter()
        writer.add_blank_page(width=595, height=842)
        pdf_buffer = BytesIO()
        writer.write(pdf_buffer)
        original_pdf = pdf_buffer.getvalue()
        headers = {"Authorization": f"Bearer {token}"}
        preview = client.post(
            "/admin/songs/upload/preview",
            headers=headers,
            files={"file": ("original-score.pdf", original_pdf, "application/pdf")},
        )
        assert preview.status_code == 200
        preview_image = preview.json()["chapters"][0]["top_preview_data_url"]
        assert preview_image.startswith("data:image/png;base64,")
        image_bytes = base64.b64decode(preview_image.partition(",")[2])
        assert struct.unpack(">II", image_bytes[16:24]) == (900, 255)

        response = client.post(
            "/admin/songs/upload/commit",
            headers=headers,
            json={
                "upload_token": preview.json()["upload_token"],
                "name": "Original Upload Test Song",
                "mappings": [
                    {
                        "chapter_index": 0,
                        "include": True,
                        "create_instrument_name": "Original Upload Test Flute",
                        "create_instrument_tuning": "C",
                    }
                ],
            },
        )
        assert response.status_code == 200

        record = db.query(models.SongUploads).filter(models.SongUploads.uploaded_by_user_id == user.id).one()
        original_file = admin.ORIGINAL_UPLOAD_DIR / Path(record.upload_path).name
        upload_notes = json.loads(record.notes)
        assert upload_notes["original_filename"] == "original-score.pdf"
        assert upload_notes["sha256"] == admin._hash_file(original_file)
        assert original_file.read_bytes() == original_pdf
        assert response.json()["song"]["id"] == record.song_id
        assert record.uploaded_at is not None

        score_preview = client.post(
            "/admin/scores/upload/preview",
            headers=headers,
            data={"song_id": str(record.song_id)},
            files={"file": ("additional-score.pdf", original_pdf, "application/pdf")},
        )
        assert score_preview.status_code == 200
        assert score_preview.json()["chapters"][0]["top_preview_data_url"].startswith("data:image/png;base64,")
        score_upload = client.post(
            "/admin/scores/upload/commit",
            headers=headers,
            json={
                "song_id": record.song_id,
                "upload_token": score_preview.json()["upload_token"],
                "mappings": [
                    {
                        "chapter_index": 0,
                        "include": True,
                        "instrument_id": response.json()["scores"][0]["instrument_id"],
                    }
                ],
            },
        )
        assert score_upload.status_code == 200
        records = db.query(models.SongUploads).filter(models.SongUploads.uploaded_by_user_id == user.id).all()
        assert len(records) == 2
        uploaded_names = {json.loads(upload.notes)["original_filename"] for upload in records}
        assert uploaded_names == {"original-score.pdf", "additional-score.pdf"}
        assert all((admin.ORIGINAL_UPLOAD_DIR / Path(upload.upload_path).name).exists() for upload in records)
    finally:
        db.rollback()
        admin.SCORES_UPLOAD_DIR = original_scores_dir
        admin.ORIGINAL_UPLOAD_DIR = original_upload_dir
        db.close()
