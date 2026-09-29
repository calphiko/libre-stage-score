from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from backend import auth, models
from backend.database import SessionLocal
from backend.main import app
from backend.routers import admin


client = TestClient(app)


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


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
        }.items()
    }
    normalized = {admin._normalize_part_name(name): instrument for name, instrument in instruments.items()}

    assert admin._match_instrument_by_outline_title("Fl. 1", normalized).instrument_name == "Flöte 1 C"
    assert admin._match_instrument_by_outline_title("Picc. 1", normalized).instrument_name == "Piccoloflöte 1 C"
    assert admin._match_instrument_by_outline_title("Klar. 2", normalized).instrument_name == "Klarinette 2 Bb"
    assert admin._match_instrument_by_outline_title("Alt-Sax 1", normalized).instrument_name == "Alt-Saxophon 1 Eb"
    assert admin._match_instrument_by_outline_title("Flöte 2", normalized).instrument_name == "Flöte 2 C"


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


def test_user_can_access_score_when_directly_assigned_to_instrument():
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
        assert auth.user_can_access_document(db, current, "score", score.id) is True
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


def test_delete_song_removes_score_files_from_storage():
    db = SessionLocal()
    song_name = "Delete Song Storage Test"
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

        assert not score_path.exists()
        assert not storage_dir.exists()
        assert db.query(models.Song).filter(models.Song.id == song.id).first() is None
    finally:
        db.query(models.Score).filter(models.Score.notes == "delete-test").delete()
        song = db.query(models.Song).filter(models.Song.name == song_name).first()
        if song:
            db.delete(song)
        instrument = db.query(models.Instrument).filter(models.Instrument.instrument_name == "Delete Song Instrument").first()
        if instrument:
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
