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
    db.query(models.UserInstrument).delete()
    db.query(models.Score).delete()
    db.query(models.RefreshToken).delete()
    db.query(models.TokenBlacklist).delete()
    db.query(models.UsedPasswordResetToken).delete()
    db.query(models.Collection).delete()
    db.query(models.Song).delete()
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
            status="active",
        ),
        models.User(
            user_name="alice",
            user_pw=demo_pw,
            user_group="editor",
            email="alice@example.com",
            clear_name="Alice",
            status="active",
        ),
        models.User(
            user_name="bob",
            user_pw=demo_pw,
            user_group="editor",
            email="bob@example.com",
            clear_name="Bob",
            status="active",
        ),
        models.User(
            user_name="carol",
            user_pw=demo_pw,
            user_group="user",
            email="carol@example.com",
            clear_name="Carol",
            status="active",
        ),
        models.User(
            user_name="dave",
            user_pw=demo_pw,
            user_group="user",
            email="dave@example.com",
            clear_name="Dave",
            status="active",
        ),
    ]

    db.add_all(users)
    db.flush()

    instruments = [
        models.Instrument(instrument_name="Piccoloflöte", instrument_tuning="C", notes="Demo-Instrument"),
        models.Instrument(instrument_name="1. Flöte", instrument_tuning="C", notes="Demo-Instrument"),
        models.Instrument(instrument_name="2. Flöte", instrument_tuning="C", notes="Demo-Instrument"),
        models.Instrument(instrument_name="3. Flöte", instrument_tuning="C", notes="Demo-Instrument"),
        models.Instrument(instrument_name="1. Oboe", instrument_tuning="C", notes="Demo-Instrument"),
        models.Instrument(instrument_name="2. Oboe", instrument_tuning="C", notes="Demo-Instrument"),
        models.Instrument(instrument_name="1. Klarinette", instrument_tuning="Bb", notes="Demo-Instrument"),
        models.Instrument(instrument_name="2. Klarinette", instrument_tuning="Bb", notes="Demo-Instrument"),
        models.Instrument(instrument_name="Bassklarinette", instrument_tuning="Bb", notes="Demo-Instrument"),
        models.Instrument(instrument_name="1. Alt-Saxophon", instrument_tuning="Eb", notes="Demo-Instrument"),
        models.Instrument(instrument_name="2. Alt-Saxophon", instrument_tuning="Eb", notes="Demo-Instrument"),
        models.Instrument(instrument_name="Tenor-Saxophon", instrument_tuning="Bb", notes="Demo-Instrument"),
        models.Instrument(instrument_name="1. Trompete", instrument_tuning="Bb", notes="Demo-Instrument"),
        models.Instrument(instrument_name="2. Trompete", instrument_tuning="Bb", notes="Demo-Instrument"),
        models.Instrument(instrument_name="3. Trompete", instrument_tuning="Bb", notes="Demo-Instrument"),
        models.Instrument(instrument_name="1. Flügelhorn", instrument_tuning="Bb", notes="Demo-Instrument"),
        models.Instrument(instrument_name="2. Flügelhorn", instrument_tuning="Bb", notes="Demo-Instrument"),
        models.Instrument(instrument_name="1. Horn", instrument_tuning="F", notes="Demo-Instrument"),
        models.Instrument(instrument_name="2. Horn", instrument_tuning="F", notes="Demo-Instrument"),
        models.Instrument(instrument_name="3. Horn", instrument_tuning="F", notes="Demo-Instrument"),
        models.Instrument(instrument_name="1. Tenorhorn", instrument_tuning="Bb", notes="Demo-Instrument"),
        models.Instrument(instrument_name="2. Tenorhorn", instrument_tuning="Bb", notes="Demo-Instrument"),
        models.Instrument(instrument_name="1. Posaune", instrument_tuning="Bb", notes="Demo-Instrument"),
        models.Instrument(instrument_name="2. Posaune", instrument_tuning="Bb", notes="Demo-Instrument"),
        models.Instrument(instrument_name="3. Posaune", instrument_tuning="Bb", notes="Demo-Instrument"),
        models.Instrument(instrument_name="Euphonium", instrument_tuning="Bb", notes="Demo-Instrument"),
        models.Instrument(instrument_name="Tuba", instrument_tuning="Bb", notes="Demo-Instrument"),
        models.Instrument(instrument_name="Schlagzeug", instrument_tuning="Standard", notes="Demo-Instrument"),
    ]
    db.add_all(instruments)
    db.flush()

    user_lookup = {user.user_name: user for user in users}
    assignments = [
        ("admin", ["1. Trompete", "1. Horn", "1. Tenorhorn", "Tuba"]),
        ("alice", ["1. Flöte", "2. Flöte", "1. Oboe", "1. Klarinette"]),
        ("bob", ["2. Klarinette", "1. Posaune", "2. Posaune", "Schlagzeug"]),
        ("carol", ["1. Alt-Saxophon", "2. Horn", "3. Trompete"]),
        ("dave", ["2. Trompete", "Euphonium", "2. Tenorhorn", "Schlagzeug"]),
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

    songs = [
        models.Song(name="Highland Cathedral", tune="Bb", composer="Korb/Korb", arrangement="Blasorchester"),
        models.Song(name="Amazing Grace", tune="F", composer="Traditional", arrangement="Blasorchester"),
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
        (songs[0], instrument_lookup["1. Trompete"], "Highland_Cathedral/1._Trompete.pdf", "Lead"),
        (songs[0], instrument_lookup["1. Tenorhorn"], "Highland_Cathedral/1._Tenorhorn.pdf", "Mittelstimme"),
        (songs[1], instrument_lookup["1. Flöte"], "Amazing_Grace/1._Floete.pdf", "Melodie"),
        (songs[1], instrument_lookup["Euphonium"], "Amazing_Grace/Euphonium.pdf", "Begleitung"),
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
    print("Seeded demo collections and song assignments")
    print("Seeded demo songs and scores")


if __name__ == "__main__":
    run()
