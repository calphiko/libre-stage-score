import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import auth, database, models
from backend.main import app
from backend.sync.outbox import soft_delete


@pytest.fixture
def api(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    models.Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, autoflush=False)
    monkeypatch.setattr(database, "SessionLocal", sessions)
    with TestClient(app) as client:
        yield client, sessions, engine
    engine.dispose()


def headers(username, role):
    token = auth.create_access_token({"sub": username, "role": role})
    return {"Authorization": f"Bearer {token}"}


def seed(sessions):
    with sessions() as db:
        users = [
            models.User(user_name=role, user_pw="hash", user_group=role, email=f"{role}@example.com", clear_name=role)
            for role in ("user", "editor", "admin")
        ]
        groups = [models.Group(name="Alpha", notes="Info"), models.Group(name="Beta"), models.Group(name="Other")]
        instruments = [
            models.Instrument(instrument_name=name, instrument_tuning="C")
            for name in ("Flute", "Horn", "Drums")
        ]
        songs = [models.Song(name="A", composer="Old"), models.Song(name="B"), models.Song(name="C")]
        db.add_all(users + groups + instruments + songs)
        db.flush()
        db.add_all(
            [models.UserGroupMembership(user_id=user.id, group_id=group.id)
             for user in users for group in groups[:2]]
            + [models.GroupInstrumentMembership(group_id=groups[0].id, instrument_id=instrument.id)
               for instrument in instruments[:2]]
            + [models.GroupInstrumentMembership(group_id=groups[1].id, instrument_id=instruments[0].id),
               models.GroupInstrumentMembership(group_id=groups[2].id, instrument_id=instruments[2].id)]
            + [models.Score(song_id=songs[0].id, instrument_id=instruments[0].id),
               models.Score(song_id=songs[0].id, instrument_id=instruments[1].id, storage_path="x.pdf"),
               models.Score(song_id=songs[1].id, instrument_id=instruments[2].id)]
        )
        db.commit()
        return {
            "songs": [song.id for song in songs],
            "groups": [group.id for group in groups],
            "instruments": [instrument.id for instrument in instruments],
        }


@pytest.mark.parametrize(
    ("role", "view", "names"),
    [
        ("user", None, ["A"]),
        ("user", "editor", ["A"]),
        ("editor", None, ["A"]),
        ("editor", "user", ["A"]),
        ("editor", "editor", ["A", "B", "C"]),
        ("admin", "user", ["A", "B", "C"]),
    ],
)
def test_song_groups_and_access_modes(api, role, view, names):
    client, sessions, _ = api
    ids = seed(sessions)
    response = client.get("/admin/songs", params={"view": view} if view else {}, headers=headers(role, role))
    assert response.status_code == 200
    songs = response.json()
    assert [song["name"] for song in songs] == names
    assert songs[0]["my_groups"] == [
        {"id": ids["groups"][0], "name": "Alpha", "notes": "Info"},
        {"id": ids["groups"][1], "name": "Beta", "notes": None},
    ]
    if role == "admin":
        assert songs[1]["my_groups"] == [{"id": ids["groups"][2], "name": "Other", "notes": None}]
        assert songs[2]["my_groups"] == []
    else:
        assert all(song["my_groups"] == [] for song in songs[1:])


@pytest.mark.parametrize(("role", "view", "count"), [("user", "editor", 0), ("editor", "editor", 3), ("admin", "user", 3)])
def test_missing_user_id_has_no_groups(api, role, view, count):
    client, sessions, _ = api
    seed(sessions)
    response = client.get("/admin/songs", params={"view": view}, headers=headers("nonexistent", role))
    assert response.status_code == 200
    assert len(response.json()) == count
    assert all(song["my_groups"] == [] for song in response.json())


def test_group_assignments_follow_changes_and_tombstones(api):
    client, sessions, _ = api
    ids = seed(sessions)
    request_headers = headers("editor", "editor")

    def groups():
        return client.get("/admin/songs?view=editor", headers=request_headers).json()[0]["my_groups"]

    with sessions() as db:
        score = db.query(models.Score).filter_by(song_id=ids["songs"][0], instrument_id=ids["instruments"][0]).one()
        soft_delete(db, score)
        db.commit()
    assert [g["name"] for g in groups()] == ["Alpha"]
    with sessions() as db:
        membership = db.query(models.GroupInstrumentMembership).filter_by(
            group_id=ids["groups"][0], instrument_id=ids["instruments"][1]
        ).one()
        soft_delete(db, membership)
        db.commit()
    assert groups() == []
    with sessions() as db:
        db.add(models.Score(song_id=ids["songs"][0], instrument_id=ids["instruments"][2]))
        db.add(models.UserGroupMembership(
            user_id=db.query(models.User).filter_by(user_name="editor").one().id, group_id=ids["groups"][2]
        ))
        db.commit()
    assert [g["name"] for g in groups()] == ["Other"]
    with sessions() as db:
        membership = db.query(models.UserGroupMembership).filter_by(
            user_id=db.query(models.User).filter_by(user_name="editor").one().id, group_id=ids["groups"][2]
        ).one()
        soft_delete(db, membership)
        db.commit()
    assert groups() == []
    with sessions() as db:
        group = db.query(models.Group).filter_by(id=ids["groups"][0]).one()
        soft_delete(db, group)
        db.commit()
    assert groups() == []


@pytest.mark.parametrize(
    ("model", "criteria", "expected"),
    [
        (models.Score, lambda ids: {"song_id": ids["songs"][0], "instrument_id": ids["instruments"][0]}, ["Alpha"]),
        (models.Instrument, lambda ids: {"id": ids["instruments"][0]}, ["Alpha"]),
        (models.GroupInstrumentMembership, lambda ids: {"group_id": ids["groups"][1]}, ["Alpha"]),
        (models.UserGroupMembership, lambda ids: {"group_id": ids["groups"][1]}, ["Alpha"]),
        (models.Group, lambda ids: {"id": ids["groups"][1]}, ["Alpha"]),
    ],
)
def test_group_query_excludes_individual_tombstones(api, model, criteria, expected):
    client, sessions, _ = api
    ids = seed(sessions)
    with sessions() as db:
        query = db.query(model).filter_by(**criteria(ids))
        if model is models.UserGroupMembership:
            query = query.filter_by(user_id=db.query(models.User).filter_by(user_name="editor").one().id)
        record = query.one()
        # A replicated tombstone may arrive independently of its related records.
        record.deleted_at = 1
        db.commit()
    result = client.get("/admin/songs?view=editor", headers=headers("editor", "editor")).json()
    assert [group["name"] for group in result[0]["my_groups"]] == expected


def test_groups_loaded_in_constant_number_of_queries(api):
    client, sessions, engine = api
    ids = seed(sessions)
    queries = []

    def record(_conn, _cursor, statement, _params, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            queries.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        assert client.get("/admin/songs?view=editor", headers=headers("editor", "editor")).status_code == 200
        initial = len(queries)
        with sessions() as db:
            db.add_all([models.Song(name=f"Extra {index:03}") for index in range(50)])
            db.commit()
        queries.clear()
        response = client.get("/admin/songs?view=editor", headers=headers("editor", "editor"))
        assert len(response.json()) == 53
        assert len(queries) == initial
        assert sum("group_instruments" in statement.lower() for statement in queries) == 1
    finally:
        event.remove(engine, "before_cursor_execute", record)


@pytest.mark.parametrize("role", ["editor", "admin"])
def test_patch_independent_updates_and_outbox(api, role):
    client, sessions, _ = api
    song_id = seed(sessions)["songs"][0]
    request_headers = headers(role, role)
    first = client.patch(f"/admin/songs/{song_id}", json={"composer": "New", "notes": "Saved"}, headers=request_headers)
    second = client.patch(f"/admin/songs/{song_id}", json={"tune": "Bb", "score_completed": True}, headers=request_headers)
    assert first.status_code == second.status_code == 200
    assert second.json()["composer"] == "New"
    assert second.json()["notes"] == "Saved"
    assert second.json()["tune"] == "Bb"
    assert second.json()["score_completed"] is True
    assert second.json()["my_groups"] == []
    with sessions() as db:
        song = db.query(models.Song).filter_by(id=song_id).one()
        changes = db.query(models.ChangeLog).filter_by(table_name="songs", record_uuid=song.uuid).order_by(models.ChangeLog.seq).all()
        assert [change.operation for change in changes] == ["insert", "update", "update"]
        assert changes[-1].payload["composer"] == "New"
        assert changes[-1].payload["tune"] == "Bb"
        assert changes[-1].payload["version"] == song.version == 3


def test_patch_validation_and_permissions(api):
    client, sessions, _ = api
    song_id = seed(sessions)["songs"][0]
    url = f"/admin/songs/{song_id}"
    assert client.patch(url, json={"name": "Forbidden"}, headers=headers("user", "user")).status_code == 403
    assert client.patch(url, json={"name": "Forbidden"}).status_code == 401
    admin_headers = headers("admin", "admin")
    for payload in [
        {"name": None}, {"name": ""}, {"name": "x" * 513}, {"score_completed": None},
        {"song_with_solo": None}, {"tune": "x" * 33}, {"ensemble_id": 0},
        {"id": 1}, {"my_groups": []}, {"collection_ids": []},
    ]:
        assert client.patch(url, json=payload, headers=admin_headers).status_code == 422, payload
    for field in ("physical_storage_id", "ensemble_id", "kind_of_song_id", "difficulty_level_id", "song_rythm_id"):
        assert client.patch(url, json={field: 9999}, headers=admin_headers).status_code == 404
    with sessions() as db:
        valid = models.Ensemble(name="Valid")
        ensemble = models.Ensemble(name="Deleted")
        db.add_all([valid, ensemble])
        db.commit()
        valid_id = valid.id
        ensemble_id = ensemble.id
        soft_delete(db, ensemble)
        db.commit()
    valid_response = client.patch(url, json={"ensemble_id": valid_id}, headers=admin_headers)
    assert valid_response.status_code == 200
    assert valid_response.json()["ensemble_id"] == valid_id
    assert client.patch(url, json={"ensemble_id": ensemble_id}, headers=admin_headers).status_code == 404
    assert client.patch("/admin/songs/9999", json={"name": "x"}, headers=admin_headers).status_code == 404
    response = client.patch(url, json={"composer": None, "notes": None, "ensemble_id": None}, headers=admin_headers)
    assert response.status_code == 200
    assert response.json()["name"] == "A"
    assert response.json()["composer"] is None
    with sessions() as db:
        song = db.query(models.Song).filter_by(id=song_id).one()
        soft_delete(db, song)
        db.commit()
    assert client.patch(url, json={"name": "x"}, headers=admin_headers).status_code == 404


def test_other_song_responses_default_to_empty_groups(api):
    client, sessions, _ = api
    song_id = seed(sessions)["songs"][0]
    request_headers = headers("editor", "editor")
    created = client.post("/admin/songs", json={"name": "New"}, headers=request_headers)
    updated = client.put(f"/admin/songs/{song_id}", json={"name": "Updated"}, headers=request_headers)
    collections = client.put(f"/admin/songs/{song_id}/collections", json={"collection_ids": []}, headers=request_headers)
    for response in (created, updated, collections):
        assert response.status_code == 200
        assert response.json()["my_groups"] == []


@pytest.mark.parametrize(
    ("category", "payload", "field"),
    [
        ("ensembles", {"name": "Duo", "notes": "Two players"}, "name"),
        ("kinds", {"name": "March"}, "name"),
        ("difficulty_levels", {"name": "Easy"}, "name"),
        ("rhythms", {"name": "Waltz"}, "name"),
        ("physical_storages", {"storage_path": "Shelf A"}, "storage_path"),
    ],
)
def test_song_metadata_can_be_created_updated_and_deleted(api, category, payload, field):
    client, _sessions, _ = api
    editor_headers = headers("editor", "editor")
    url = f"/admin/song-metadata/{category}"
    assert client.post(url, json=payload).status_code == 401

    created = client.post(url, json=payload, headers=editor_headers)
    assert created.status_code == 200
    item = created.json()
    assert item[field] == payload[field]

    updated_payload = {**payload, field: "Updated value"}
    updated = client.put(f"{url}/{item['id']}", json=updated_payload, headers=editor_headers)
    assert updated.status_code == 200
    assert updated.json()[field] == "Updated value"

    deleted = client.delete(f"{url}/{item['id']}", headers=editor_headers)
    assert deleted.status_code == 200
    assert all(row["id"] != item["id"] for row in client.get("/admin/song-metadata", headers=editor_headers).json()[category])


def test_song_metadata_cannot_be_deleted_while_used(api):
    client, sessions, _ = api
    song_id = seed(sessions)["songs"][0]
    with sessions() as db:
        ensemble = models.Ensemble(name="Used ensemble")
        db.add(ensemble)
        db.flush()
        db.query(models.Song).filter_by(id=song_id).update({"ensemble_id": ensemble.id})
        db.commit()
        ensemble_id = ensemble.id

    response = client.delete(
        f"/admin/song-metadata/ensembles/{ensemble_id}",
        headers=headers("editor", "editor"),
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "Song metadata is used by a song"
