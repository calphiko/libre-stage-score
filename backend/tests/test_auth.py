from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from backend import models
from backend.database import SessionLocal
from backend.main import app
from backend.routers import admin


client = TestClient(app)


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


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
            assert status_before.has_changes is True
            assert storage_path in status_before.changed_files

            status_after = admin.rescrape_storage(db)
            assert status_after.has_changes is False

            db.refresh(score)
            assert score.file_hash == admin._hash_file(file_path)
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
