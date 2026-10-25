import sys
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from pypdf import PdfWriter

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from backend import models  # noqa: E402
from backend.auth import hash_pw  # noqa: E402
from backend.database import SQLALCHEMY_DATABASE_URL  # noqa: E402
from backend.routers import admin  # noqa: E402


def _create_demo_pdf(target_path: Path) -> str:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    with target_path.open("wb") as handle:
        writer.write(handle)
    return admin._hash_file(target_path)


def run() -> None:
    engine = create_engine(
        SQLALCHEMY_DATABASE_URL,
        connect_args={"check_same_thread": False} if SQLALCHEMY_DATABASE_URL.startswith("sqlite") else {},
    )
    session_factory = sessionmaker(bind=engine)
    db = session_factory()

    models.Base.metadata.create_all(bind=engine)

    db.query(models.SongCollection).delete()
    db.query(models.GroupInstrumentMembership).delete()
    db.query(models.UserGroupMembership).delete()
    db.query(models.UserInstrument).delete()
    db.query(models.Score).delete()
    db.query(models.RefreshToken).delete()
    db.query(models.TokenBlacklist).delete()
    db.query(models.UsedPasswordResetToken).delete()
    db.query(models.Collection).delete()
    db.query(models.Group).delete()
    db.query(models.Song).delete()
    db.query(models.Ensemble).delete()
    db.query(models.SongStoragePhysical).delete()
    db.query(models.KindOfSong).delete()
    db.query(models.DifficultyLevel).delete()
    db.query(models.SongRythm).delete()
    db.query(models.Instrument).delete()
    db.query(models.User).delete()
    if admin.SCORES_UPLOAD_DIR.exists():
        for path in sorted(admin.SCORES_UPLOAD_DIR.rglob("*"), reverse=True):
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                path.rmdir()
    admin.SCORES_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    demo_pw = hash_pw("Demo1234!")
    users = [
        models.User(
            user_name="admin",
            user_pw=hash_pw("Admin1234!"),
            user_group="admin",
            email="admin@example.com",
            clear_name="Admin User",
            musician=False,
            status="active",
        ),
        models.User(
            user_name="alice",
            user_pw=demo_pw,
            user_group="editor",
            email="alice@example.com",
            clear_name="Alice",
            musician=True,
            status="active",
        ),
        models.User(
            user_name="bob",
            user_pw=demo_pw,
            user_group="editor",
            email="bob@example.com",
            clear_name="Bob",
            musician=True,
            status="active",
        ),
        models.User(
            user_name="carol",
            user_pw=demo_pw,
            user_group="user",
            email="carol@example.com",
            clear_name="Carol",
            musician=True,
            status="active",
        ),
        models.User(
            user_name="dave",
            user_pw=demo_pw,
            user_group="user",
            email="dave@example.com",
            clear_name="Dave",
            musician=True,
            status="active",
        ),
    ]

    db.add_all(users)
    db.flush()

    instruments = [
        models.Instrument(instrument_name="Piccoloflöte 1", instrument_tuning="C", notes="Demo-Instrument"),
        models.Instrument(instrument_name="Flöte 1", instrument_tuning="C", notes="Demo-Instrument"),
        models.Instrument(instrument_name="Flöte 2", instrument_tuning="C", notes="Demo-Instrument"),
        models.Instrument(instrument_name="Flöte 3", instrument_tuning="C", notes="Demo-Instrument"),
        models.Instrument(instrument_name="Oboe 1", instrument_tuning="C", notes="Demo-Instrument"),
        models.Instrument(instrument_name="Oboe 2", instrument_tuning="C", notes="Demo-Instrument"),
        models.Instrument(instrument_name="Klarinette 1", instrument_tuning="Bb", notes="Demo-Instrument"),
        models.Instrument(instrument_name="Klarinette 2", instrument_tuning="Bb", notes="Demo-Instrument"),
        models.Instrument(instrument_name="Bassklarinette 1", instrument_tuning="Bb", notes="Demo-Instrument"),
        models.Instrument(instrument_name="Alt-Saxophon 1", instrument_tuning="Eb", notes="Demo-Instrument"),
        models.Instrument(instrument_name="Alt-Saxophon 2", instrument_tuning="Eb", notes="Demo-Instrument"),
        models.Instrument(instrument_name="Tenor-Saxophon 1", instrument_tuning="Bb", notes="Demo-Instrument"),
        models.Instrument(instrument_name="Trompete 1", instrument_tuning="Bb", notes="Demo-Instrument"),
        models.Instrument(instrument_name="Trompete 2", instrument_tuning="Bb", notes="Demo-Instrument"),
        models.Instrument(instrument_name="Trompete 3", instrument_tuning="Bb", notes="Demo-Instrument"),
        models.Instrument(instrument_name="Flügelhorn 1", instrument_tuning="Bb", notes="Demo-Instrument"),
        models.Instrument(instrument_name="Flügelhorn 2", instrument_tuning="Bb", notes="Demo-Instrument"),
        models.Instrument(instrument_name="Horn 1", instrument_tuning="F", notes="Demo-Instrument"),
        models.Instrument(instrument_name="Horn 2", instrument_tuning="F", notes="Demo-Instrument"),
        models.Instrument(instrument_name="Horn 3", instrument_tuning="F", notes="Demo-Instrument"),
        models.Instrument(instrument_name="Tenorhorn 1", instrument_tuning="Bb", notes="Demo-Instrument"),
        models.Instrument(instrument_name="Tenorhorn 2", instrument_tuning="Bb", notes="Demo-Instrument"),
        models.Instrument(instrument_name="Posaune 1", instrument_tuning="Bb", notes="Demo-Instrument"),
        models.Instrument(instrument_name="Posaune 2", instrument_tuning="Bb", notes="Demo-Instrument"),
        models.Instrument(instrument_name="Posaune 3", instrument_tuning="Bb", notes="Demo-Instrument"),
        models.Instrument(instrument_name="Euphonium 1", instrument_tuning="Bb", notes="Demo-Instrument"),
        models.Instrument(instrument_name="Tuba 1", instrument_tuning="Bb", notes="Demo-Instrument"),
        models.Instrument(instrument_name="Schlagzeug 1", instrument_tuning="Standard", notes="Demo-Instrument"),
    ]
    db.add_all(instruments)
    db.flush()

    user_lookup = {user.user_name: user for user in users}
    assignments = [
        ("admin", ["Trompete 1", "Horn 1", "Tenorhorn 1", "Tuba 1"]),
        ("alice", ["Flöte 1", "Flöte 2", "Oboe 1", "Klarinette 1"]),
        ("bob", ["Klarinette 2", "Posaune 1", "Posaune 2", "Schlagzeug 1"]),
        ("carol", ["Alt-Saxophon 1", "Horn 2", "Trompete 3"]),
        ("dave", ["Trompete 2", "Euphonium 1", "Tenorhorn 2", "Schlagzeug 1"]),
    ]

    instrument_lookup = {instrument.instrument_name: instrument for instrument in instruments}
    for username, names in assignments:
        user = user_lookup[username]
        for name in names:
            db.add(
                models.UserInstrument(
                    user_id=user.id,
                    instrument_id=instrument_lookup[name].id,
                    notes="Demo-Zuweisung",
                )
            )

    groups = [
        models.Group(name="Flöten", notes="Flöten-Register inkl. Piccolo"),
        models.Group(name="Oboen", notes="Oboen-Register"),
        models.Group(name="Klarinetten", notes="Klarinetten-Register"),
        models.Group(name="Saxophone", notes="Saxophon-Register"),
        models.Group(name="Hohes Blech", notes="Trompeten und Flügelhörner"),
        models.Group(name="Hörner", notes="Horn-Register"),
        models.Group(name="Tiefes Blech", notes="Tenorhörner, Euphonium, Posaunen und Tuba"),
        models.Group(name="Schlagwerk", notes="Schlagwerk"),
    ]
    db.add_all(groups)
    db.flush()

    group_lookup = {group.name: group for group in groups}
    group_instrument_assignments = {
        "Flöten": ["Piccoloflöte 1", "Flöte 1", "Flöte 2", "Flöte 3"],
        "Oboen": ["Oboe 1", "Oboe 2"],
        "Klarinetten": ["Klarinette 1", "Klarinette 2", "Bassklarinette 1"],
        "Saxophone": ["Alt-Saxophon 1", "Alt-Saxophon 2", "Tenor-Saxophon 1"],
        "Hohes Blech": [
            "Trompete 1",
            "Trompete 2",
            "Trompete 3",
            "Flügelhorn 1",
            "Flügelhorn 2",
        ],
        "Hörner": ["Horn 1", "Horn 2", "Horn 3"],
        "Tiefes Blech": [
            "Tenorhorn 1",
            "Tenorhorn 2",
            "Euphonium 1",
            "Posaune 1",
            "Posaune 2",
            "Posaune 3",
            "Tuba 1",
        ],
        "Schlagwerk": ["Schlagzeug 1"],
    }
    for group_name, instrument_names in group_instrument_assignments.items():
        group = group_lookup[group_name]
        for instrument_name in instrument_names:
            db.add(
                models.GroupInstrumentMembership(
                    group_id=group.id,
                    instrument_id=instrument_lookup[instrument_name].id,
                    notes="Demo-Registerzuordnung",
                )
            )

    ensembles = [
        models.Ensemble(name="Blasorchester", notes="Demo-Besetzung"),
    ]
    physical_storages = [
        models.SongStoragePhysical(storage_path="Notenarchiv/Regal A-01", notes="Demo-Aufbewahrungsort"),
        models.SongStoragePhysical(storage_path="Notenarchiv/Regal K-01", notes="Demo-Aufbewahrungsort"),
    ]
    song_kinds = [
        models.KindOfSong(name="Marsch", notes="Demo-Kategorie"),
        models.KindOfSong(name="Kirchenmusik", notes="Demo-Kategorie"),
    ]
    difficulty_levels = [
        models.DifficultyLevel(name="Mittel", notes="Demo-Schwierigkeitsgrad"),
        models.DifficultyLevel(name="Leicht", notes="Demo-Schwierigkeitsgrad"),
    ]
    song_rythms = [
        models.SongRythm(name="Marsch", notes="Demo-Rhythmus"),
        models.SongRythm(name="4/4", notes="Demo-Rhythmus"),
    ]
    db.add_all(ensembles + physical_storages + song_kinds + difficulty_levels + song_rythms)
    db.flush()

    songs = [
        models.Song(
            name="Highland Cathedral",
            tune="Bb",
            composer="Korb/Korb",
            arrangement="Blasorchester",
            publisher="Demo Musikverlag",
            physical_storage_id=physical_storages[0].id,
            ensemble_id=ensembles[0].id,
            kind_of_song_id=song_kinds[0].id,
            difficulty_level_id=difficulty_levels[0].id,
            song_rythm_id=song_rythms[0].id,
            score_completed=False,
            song_with_solo=False,
        ),
        models.Song(
            name="Amazing Grace",
            tune="F",
            composer="Traditional",
            arrangement="Blasorchester",
            publisher="Gemeinfrei / traditionell",
            physical_storage_id=physical_storages[1].id,
            ensemble_id=ensembles[0].id,
            kind_of_song_id=song_kinds[1].id,
            difficulty_level_id=difficulty_levels[1].id,
            song_rythm_id=song_rythms[1].id,
            score_completed=False,
            song_with_solo=False,
        ),
    ]
    db.add_all(songs)
    db.flush()

    collections = [
        models.Collection(name="Marsch", notes="Traditionelle Marschmusik"),
        models.Collection(name="Konzert", notes="Konzertprogramm"),
        models.Collection(name="Kirche", notes="Getragene Stücke"),
    ]
    db.add_all(collections)
    db.flush()

    songs[0].collections.extend([collections[1]])
    songs[1].collections.extend([collections[2]])

    seeded_scores = [
        (songs[0], instrument_lookup["Trompete 1"], "Highland_Cathedral/Trompete_1.pdf", "Lead"),
        (songs[0], instrument_lookup["Tenorhorn 1"], "Highland_Cathedral/Tenorhorn_1.pdf", "Mittelstimme"),
        (songs[1], instrument_lookup["Flöte 1"], "Amazing_Grace/Floete_1.pdf", "Melodie"),
        (songs[1], instrument_lookup["Euphonium 1"], "Amazing_Grace/Euphonium_1.pdf", "Begleitung"),
    ]
    for song, instrument, storage_path, notes in seeded_scores:
        file_hash = _create_demo_pdf(admin.SCORES_UPLOAD_DIR / storage_path)
        db.add(
            models.Score(
                song_id=song.id,
                instrument_id=instrument.id,
                storage_path=storage_path,
                file_hash=file_hash,
                notes=notes,
            )
        )

    db.commit()
    print("Seeded demo users: admin, alice, bob, carol, dave")
    print("Seeded demo instruments and user assignments")
    print("Seeded demo register groups and group instrument assignments")
    print("Seeded demo collections and song assignments")
    print("Seeded demo song metadata, songs, and scores")


if __name__ == "__main__":
    run()
