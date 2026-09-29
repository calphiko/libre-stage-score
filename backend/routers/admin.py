import hashlib
import json
import os
import re
import secrets
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pypdf import PdfReader, PdfWriter
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from backend import auth, models, schemas
from backend.utils.mailer import send_email


async def user_is_admin(request: Request, db: Session = Depends(auth.get_db)):
    user = auth.get_current_user(request, db)
    auth.require_admin(user)
    return user


async def user_is_editor_or_admin(request: Request, db: Session = Depends(auth.get_db)):
    user = auth.get_current_user(request, db)
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return user
    auth.require_editor_or_admin(user)
    return user


router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(user_is_editor_or_admin)])
PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCORES_UPLOAD_DIR = PROJECT_ROOT / "backend" / "storage" / "scores"


def _score_to_out(score: models.Score, song: models.Song, instrument: models.Instrument) -> schemas.ScoreOut:
    return schemas.ScoreOut(
        id=score.id,
        song_id=score.song_id,
        instrument_id=score.instrument_id,
        storage_path=score.storage_path,
        file_hash=score.file_hash,
        notes=score.notes,
        song_name=song.name,
        instrument_name=instrument.instrument_name,
        instrument_tuning=instrument.instrument_tuning,
    )


def _safe_filename_part(value: str) -> str:
    normalized = value
    replacements = {
        "ä": "ae",
        "ö": "oe",
        "ü": "ue",
        "Ä": "Ae",
        "Ö": "Oe",
        "Ü": "Ue",
        "ß": "ss",
    }
    for source, target in replacements.items():
        normalized = normalized.replace(source, target)
    return re.sub(r"[^a-zA-Z0-9._-]+", "_", normalized).strip("._-") or "score"


def _normalize_part_name(value: str) -> str:
    normalized = value.lower()
    replacements = {
        "ä": "ae",
        "ö": "oe",
        "ü": "ue",
        "ß": "ss",
    }
    for source, target in replacements.items():
        normalized = normalized.replace(source, target)
    return re.sub(r"[^a-z0-9]+", "", normalized)


def _normalize_match_text(value: str) -> str:
    normalized = value.lower()
    replacements = {
        "ä": "ae",
        "ö": "oe",
        "ü": "ue",
        "ß": "ss",
    }
    for source, target in replacements.items():
        normalized = normalized.replace(source, target)
    return normalized.strip()


def _canonicalize_match_token(token: str) -> str | None:
    normalized = _normalize_match_text(token).strip()
    if not normalized:
        return None

    ignored_tokens = {
        "in",
        "mit",
        "und",
        "von",
        "for",
        "and",
        "of",
        "to",
        "the",
    }
    if normalized in ignored_tokens:
        return None

    aliases = {
        "picc": "piccoloflote",
        "piccolo": "piccoloflote",
        "piccoloflote": "piccoloflote",
        "piccolofloete": "piccoloflote",
        "fl": "flote",
        "fla": "flote",
        "flauto": "flote",
        "flote": "flote",
        "floete": "flote",
        "ob": "oboe",
        "obo": "oboe",
        "oboe": "oboe",
        "kla": "klarinette",
        "klar": "klarinette",
        "klarinette": "klarinette",
        "cl": "klarinette",
        "clar": "klarinette",
        "bkl": "bassklarinette",
        "bassklar": "bassklarinette",
        "bassklarinette": "bassklarinette",
        "sax": "saxophon",
        "saxophon": "saxophon",
        "altsax": "altsaxophon",
        "alt": "altsaxophon",
        "altsaxophon": "altsaxophon",
        "tenorsax": "tenorsaxophon",
        "tenor": "tenorsaxophon",
        "tenorsaxophon": "tenorsaxophon",
        "tp": "trompete",
        "trp": "trompete",
        "trompete": "trompete",
        "h": "horn",
        "horn": "horn",
        "pos": "posaune",
        "posaune": "posaune",
        "euph": "euphonium",
        "euphonium": "euphonium",
        "tuba": "tuba",
        "schlag": "schlagzeug",
        "drums": "schlagzeug",
        "drum": "schlagzeug",
        "perc": "schlagzeug",
        "percussion": "schlagzeug",
        "schlagzeug": "schlagzeug",
        "vl": "violine",
        "vln": "violine",
        "violine": "violine",
        "vla": "bratsche",
        "bratsche": "bratsche",
        "vc": "violoncello",
        "cello": "violoncello",
        "violoncello": "violoncello",
        "kb": "kontrabass",
        "kontrabass": "kontrabass",
    }
    if normalized in aliases:
        return aliases[normalized]
    if normalized in {"c", "bb", "eb", "f", "g", "d", "a", "e", "b"}:
        return None
    if re.fullmatch(r"(?:[0-9]+|i|ii|iii|iv|v|vi|vii|viii|ix|x)", normalized):
        return f"num:{normalized}"
    return normalized


def _tokenize_match_value(value: str) -> list[str]:
    normalized = _normalize_match_text(value)
    if not normalized:
        return []
    tokens = [token for token in re.split(r"[^a-z0-9]+", normalized) if token]
    canonical_tokens: list[str] = []
    for token in tokens:
        canonical = _canonicalize_match_token(token)
        if canonical:
            canonical_tokens.append(canonical)
    return canonical_tokens


def _score_instrument_match(chapter_title: str, instrument_name: str) -> float:
    title_tokens = _tokenize_match_value(chapter_title)
    instrument_tokens = _tokenize_match_value(instrument_name)
    if not title_tokens or not instrument_tokens:
        return 0.0

    title_number_tokens = {token for token in title_tokens if token.startswith("num:")}
    instrument_number_tokens = {token for token in instrument_tokens if token.startswith("num:")}
    title_family_tokens = {token for token in title_tokens if not token.startswith("num:")}
    instrument_family_tokens = {token for token in instrument_tokens if not token.startswith("num:")}

    generic_family_tokens = {
        "saxophon",
        "flote",
        "oboe",
        "klarinette",
        "trompete",
        "horn",
        "posaune",
        "schlagzeug",
        "violine",
        "bratsche",
        "violoncello",
        "kontrabass",
        "tuba",
        "euphonium",
        "piccoloflote",
        "bassklarinette",
    }
    title_specific_tokens = title_family_tokens - generic_family_tokens
    instrument_specific_tokens = instrument_family_tokens - generic_family_tokens

    if title_specific_tokens and instrument_specific_tokens:
        overlap = len(title_specific_tokens & instrument_specific_tokens)
        if title_specific_tokens == instrument_specific_tokens:
            family_score = 1.0
        elif instrument_specific_tokens.issubset(title_specific_tokens) or title_specific_tokens.issubset(instrument_specific_tokens):
            family_score = 0.9
        elif overlap > 0:
            family_score = overlap / max(len(instrument_specific_tokens), 1) * 0.8
        else:
            family_score = 0.0
    elif title_family_tokens and instrument_family_tokens:
        if title_family_tokens.isdisjoint(instrument_family_tokens):
            family_score = 0.0
        elif title_family_tokens == instrument_family_tokens:
            family_score = 1.0
        elif instrument_family_tokens.issubset(title_family_tokens) or title_family_tokens.issubset(instrument_family_tokens):
            family_score = 0.9
        else:
            family_score = 0.0
    else:
        family_score = 0.0

    number_score = 0.0
    if title_number_tokens and instrument_number_tokens:
        overlap = len(title_number_tokens & instrument_number_tokens)
        if overlap == len(instrument_number_tokens) and overlap == len(title_number_tokens):
            number_score = 0.35 if family_score > 0.0 else 0.0
        elif overlap > 0:
            number_score = 0.2 if family_score > 0.0 else 0.0
        else:
            number_score = -0.2
    elif title_number_tokens or instrument_number_tokens:
        number_score = 0.1 if family_score > 0.0 else 0.0

    direct_text = _normalize_match_text(chapter_title)
    instrument_text = _normalize_match_text(instrument_name)
    if direct_text and direct_text == instrument_text:
        family_score = max(family_score, 1.0)
    elif instrument_text and instrument_text in direct_text:
        family_score = max(family_score, 0.9)
    elif direct_text and direct_text in instrument_text:
        family_score = max(family_score, 0.85)

    total_score = (family_score * 0.75) + (number_score * 0.25)
    if family_score <= 0.0 and direct_text and instrument_text:
        total_score = 0.0
    return max(0.0, min(1.0, total_score))


def _extract_outline_entries(reader: PdfReader) -> list[tuple[str, int]]:
    entries: list[tuple[str, int]] = []

    def walk(items: list[Any]) -> None:
        for item in items:
            if isinstance(item, list):
                walk(item)
                continue
            title = getattr(item, "title", None)
            if not title:
                continue
            try:
                page_index = reader.get_destination_page_number(item)
            except Exception:
                continue
            entries.append((str(title).strip(), page_index))

    try:
        outline = reader.outline
    except Exception:
        return []
    if isinstance(outline, list):
        walk(outline)
    return entries


def _match_instrument_by_outline_title(
    chapter_title: str,
    instruments_by_normalized_name: dict[str, models.Instrument],
    song_name: str | None = None,
) -> models.Instrument | None:
    candidates: list[str] = [chapter_title]
    if song_name and chapter_title:
        title_parts = [part.strip() for part in re.split(r"\s*[-–—]\s*", chapter_title) if part.strip()]
        normalized_song_name = _normalize_part_name(song_name)
        if len(title_parts) > 1 and normalized_song_name:
            for index, part in enumerate(title_parts):
                if _normalize_part_name(part) != normalized_song_name:
                    continue
                without_song = " - ".join(title_parts[:index] + title_parts[index + 1 :]).strip()
                if without_song:
                    candidates.append(without_song)

    best_match: tuple[float, models.Instrument] | None = None
    for candidate in candidates:
        normalized_title = _normalize_part_name(candidate)
        direct_match = instruments_by_normalized_name.get(normalized_title)
        if direct_match:
            return direct_match

        for normalized_name, instrument in sorted(
            instruments_by_normalized_name.items(), key=lambda item: len(item[0]), reverse=True
        ):
            score = _score_instrument_match(candidate, instrument.instrument_name)
            if score <= 0.0:
                continue
            if score >= 0.7:
                if best_match is None or score > best_match[0]:
                    best_match = (score, instrument)

    if best_match is not None:
        return best_match[1]

    for candidate in candidates:
        normalized_title = _normalize_part_name(candidate)
        for normalized_name, instrument in sorted(
            instruments_by_normalized_name.items(), key=lambda item: len(item[0]), reverse=True
        ):
            if normalized_name and normalized_name in normalized_title:
                return instrument

    return None


def _incoming_upload_dir() -> Path:
    upload_dir = SCORES_UPLOAD_DIR / "_incoming"
    upload_dir.mkdir(parents=True, exist_ok=True)
    return upload_dir


def _preview_pdf_path(upload_token: str) -> Path:
    return _incoming_upload_dir() / f"{upload_token}.pdf"


def _preview_metadata_path(upload_token: str) -> Path:
    return _incoming_upload_dir() / f"{upload_token}.json"


def _outline_chapters(outline_entries: list[tuple[str, int]], page_count: int) -> list[dict[str, Any]]:
    normalized_entries: list[tuple[str, int]] = []
    for chapter_title, page_index in outline_entries:
        if page_index < 0 or page_index >= page_count:
            continue
        title = chapter_title.strip() or "Unbenanntes Kapitel"
        normalized_entries.append((title, page_index))
    normalized_entries.sort(key=lambda entry: entry[1])

    unique_entries: list[tuple[str, int]] = []
    seen_pages: set[int] = set()
    for chapter_title, page_index in normalized_entries:
        if page_index in seen_pages:
            continue
        seen_pages.add(page_index)
        unique_entries.append((chapter_title, page_index))

    chapters: list[dict[str, Any]] = []
    for chapter_index, (chapter_title, start_page) in enumerate(unique_entries):
        end_page = page_count
        if chapter_index + 1 < len(unique_entries):
            end_page = unique_entries[chapter_index + 1][1]
        if end_page <= start_page:
            continue
        chapters.append(
            {
                "chapter_title": chapter_title,
                "start_page": start_page,
                "end_page": end_page,
            }
        )
    return chapters


def _preview_chapters_from_pdf(reader: PdfReader, db: Session, song_name: str | None = None) -> list[dict[str, Any]]:
    page_count = len(reader.pages)
    outline_entries = _extract_outline_entries(reader)
    chapters = _outline_chapters(outline_entries, page_count)
    if not chapters:
        chapters = [{"chapter_title": "Gesamte PDF", "start_page": 0, "end_page": page_count}]

    instruments = db.query(models.Instrument).all()
    instruments_by_normalized_name = {
        _normalize_part_name(instrument.instrument_name): instrument for instrument in instruments
    }
    for chapter in chapters:
        original_title = str(chapter.get("chapter_title", "")).strip()
        instrument = _match_instrument_by_outline_title(
            original_title, instruments_by_normalized_name, song_name=song_name
        )
        chapter["original_chapter_title"] = original_title
        chapter["suggested_instrument_id"] = instrument.id if instrument else None
        chapter["suggested_instrument_name"] = instrument.instrument_name if instrument else None
        chapter["suggested_instrument_tuning"] = instrument.instrument_tuning if instrument else None
    return chapters


def _load_preview_metadata(upload_token: str) -> dict[str, Any]:
    metadata_path = _preview_metadata_path(upload_token)
    if not metadata_path.exists():
        raise HTTPException(status_code=404, detail="Upload session not found")
    with metadata_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _resolve_chapter_instrument(
    mapping: schemas.ScoreUploadMappingIn,
    fallback_title: str,
    db: Session,
) -> models.Instrument:
    if mapping.instrument_id is not None:
        instrument = db.query(models.Instrument).filter(models.Instrument.id == mapping.instrument_id).first()
        if not instrument:
            raise HTTPException(status_code=404, detail=f"Instrument {mapping.instrument_id} not found")
        return instrument
    if mapping.create_instrument_name and mapping.create_instrument_name.strip():
        tuning = (mapping.create_instrument_tuning or "C").strip() or "C"
        return _get_or_create_instrument(db, mapping.create_instrument_name, tuning)
    raise HTTPException(status_code=400, detail=f"Missing instrument mapping for chapter '{fallback_title}'")


def _get_or_create_instrument(db: Session, instrument_name: str, instrument_tuning: str) -> models.Instrument:
    trimmed_name = instrument_name.strip()
    if not trimmed_name:
        raise HTTPException(status_code=400, detail="Instrument name must not be empty")
    existing = db.query(models.Instrument).filter(models.Instrument.instrument_name == trimmed_name).first()
    if existing:
        return existing
    created = models.Instrument(instrument_name=trimmed_name, instrument_tuning=instrument_tuning, notes=None)
    db.add(created)
    db.flush()
    return created


def _build_song_storage_folder_name(song: models.Song, db: Session) -> str:
    base_name = _safe_filename_part(song.name)
    duplicate_name_count = db.query(models.Song).filter(models.Song.name == song.name).count()
    if duplicate_name_count <= 1:
        return base_name

    composer_part = _safe_filename_part(song.composer or "unknown-composer")
    folder_name = f"{base_name}_{composer_part}"
    collision_count = (
        db.query(models.Song)
        .filter(models.Song.name == song.name, models.Song.composer == song.composer, models.Song.id != song.id)
        .count()
    )
    if collision_count > 0:
        folder_name = f"{folder_name}_{song.id}"
    return folder_name


def _next_score_storage_relative_path(song: models.Song, instrument: models.Instrument, db: Session) -> str:
    song_folder = _build_song_storage_folder_name(song, db)
    voice_name = _safe_filename_part(instrument.instrument_name)
    scores_for_voice = (
        db.query(models.Score)
        .filter(models.Score.song_id == song.id, models.Score.instrument_id == instrument.id)
        .order_by(models.Score.id.asc())
        .all()
    )

    next_number = 1
    pattern = re.compile(rf"^{re.escape(song_folder)}/{re.escape(voice_name)}(?:_(\d+))?\.pdf$")
    for existing_score in scores_for_voice:
        storage_path = (existing_score.storage_path or "").replace("\\", "/")
        match = pattern.match(storage_path)
        if not match:
            continue
        existing_number = int(match.group(1)) if match.group(1) else 1
        if existing_number >= next_number:
            next_number = existing_number + 1

    filename = f"{voice_name}.pdf" if next_number == 1 else f"{voice_name}_{next_number}.pdf"
    return f"{song_folder}/{filename}"


def _absolute_score_storage_path(relative_storage_path: str) -> Path:
    candidate = Path(relative_storage_path)
    if candidate.is_absolute():
        return candidate
    if candidate.parts and candidate.parts[0] == "backend":
        return PROJECT_ROOT / candidate
    if candidate.parts and candidate.parts[0] == "scores":
        return PROJECT_ROOT / candidate
    return SCORES_UPLOAD_DIR / candidate


def _relative_storage_path(path: Path) -> str:
    return path.relative_to(SCORES_UPLOAD_DIR).as_posix()


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _filesystem_pdf_manifest() -> dict[str, str]:
    if not SCORES_UPLOAD_DIR.exists():
        return {}

    manifest: dict[str, str] = {}
    for path in SCORES_UPLOAD_DIR.rglob("*.pdf"):
        if "_incoming" in path.parts:
            continue
        manifest[_relative_storage_path(path)] = _hash_file(path)
    return dict(sorted(manifest.items()))


def _database_pdf_manifest(db: Session) -> dict[str, str]:
    manifest: dict[str, str] = {}
    for score in db.query(models.Score).filter(models.Score.storage_path.is_not(None)).all():
        normalized_path = str(score.storage_path).replace("\\", "/")
        manifest[normalized_path] = score.file_hash or ""
    return dict(sorted(manifest.items()))


def _tree_hash(manifest: dict[str, str]) -> str:
    digest = hashlib.sha256()
    for path, file_hash in sorted(manifest.items()):
        digest.update(f"{path}:{file_hash}\n".encode("utf-8"))
    return digest.hexdigest()


def _song_folder_lookup(db: Session) -> dict[str, models.Song]:
    lookup: dict[str, models.Song] = {}
    for song in db.query(models.Song).all():
        lookup[_build_song_storage_folder_name(song, db)] = song
    return lookup


def _instrument_filename_lookup(db: Session) -> dict[str, models.Instrument]:
    return {_safe_filename_part(instrument.instrument_name): instrument for instrument in db.query(models.Instrument).all()}


def _infer_score_from_relative_path(
    relative_path: str,
    songs_by_folder: dict[str, models.Song],
    instruments_by_filename: dict[str, models.Instrument],
) -> tuple[models.Song | None, models.Instrument | None]:
    path = Path(relative_path)
    if len(path.parts) != 2:
        return None, None

    song = songs_by_folder.get(path.parts[0])
    if not song:
        return None, None

    stem = path.stem
    stem = re.sub(r"_\d+$", "", stem)
    instrument = instruments_by_filename.get(stem)
    return song, instrument


def _build_storage_status(
    db: Session,
    filesystem_manifest: dict[str, str] | None = None,
) -> schemas.StorageStatusOut:
    filesystem_manifest = filesystem_manifest if filesystem_manifest is not None else _filesystem_pdf_manifest()
    database_manifest = _database_pdf_manifest(db)

    added_files = sorted(path for path in filesystem_manifest if path not in database_manifest)
    removed_files = sorted(path for path in database_manifest if path not in filesystem_manifest)
    changed_files = sorted(
        path
        for path in filesystem_manifest
        if path in database_manifest and filesystem_manifest[path] != database_manifest[path]
    )

    songs_by_folder = _song_folder_lookup(db)
    instruments_by_filename = _instrument_filename_lookup(db)
    unmatched_files: list[str] = []
    for path in filesystem_manifest:
        song, instrument = _infer_score_from_relative_path(path, songs_by_folder, instruments_by_filename)
        if not song or not instrument:
            unmatched_files.append(path)

    return schemas.StorageStatusOut(
        has_changes=bool(added_files or removed_files or changed_files or unmatched_files),
        database_tree_hash=_tree_hash(database_manifest),
        filesystem_tree_hash=_tree_hash(filesystem_manifest),
        database_file_count=len(database_manifest),
        filesystem_file_count=len(filesystem_manifest),
        added_files=added_files,
        removed_files=removed_files,
        changed_files=changed_files,
        unmatched_files=unmatched_files,
    )


@router.get("/groups", response_model=list[schemas.GroupOut])
def list_groups(db: Session = Depends(auth.get_db)):
    return db.query(models.Group).order_by(models.Group.name.asc()).all()


@router.post("/groups", response_model=schemas.GroupOut)
def create_group(group: schemas.GroupCreate, db: Session = Depends(auth.get_db)):
    normalized_name = group.name.strip()
    if not normalized_name:
        raise HTTPException(status_code=400, detail="Group name must not be empty")
    existing = db.query(models.Group).filter(models.Group.name == normalized_name).first()
    if existing:
        raise HTTPException(status_code=400, detail="Group already exists")
    db_group = models.Group(name=normalized_name, notes=group.notes.strip() if group.notes else None)
    db.add(db_group)
    db.commit()
    db.refresh(db_group)
    return db_group


@router.put("/groups/{group_id}", response_model=schemas.GroupOut)
def update_group(group_id: int, group: schemas.GroupUpdate, db: Session = Depends(auth.get_db)):
    db_group = db.query(models.Group).filter(models.Group.id == group_id).first()
    if not db_group:
        raise HTTPException(status_code=404, detail="Group not found")
    normalized_name = group.name.strip()
    if not normalized_name:
        raise HTTPException(status_code=400, detail="Group name must not be empty")
    duplicate = db.query(models.Group).filter(models.Group.name == normalized_name, models.Group.id != group_id).first()
    if duplicate:
        raise HTTPException(status_code=400, detail="Group already exists")
    db_group.name = normalized_name
    db_group.notes = group.notes.strip() if group.notes else None
    db.commit()
    db.refresh(db_group)
    return db_group


@router.delete("/groups/{group_id}")
def delete_group(group_id: int, db: Session = Depends(auth.get_db)):
    db_group = db.query(models.Group).filter(models.Group.id == group_id).first()
    if not db_group:
        raise HTTPException(status_code=404, detail="Group not found")
    db.delete(db_group)
    db.commit()
    return {"message": "Group deleted"}


@router.get("/users", response_model=list[schemas.UserOut], dependencies=[Depends(user_is_admin)])
def get_all_users(db: Session = Depends(auth.get_db)):
    return db.query(models.User).order_by(models.User.id.asc()).all()


@router.get("/users/assignable", response_model=list[schemas.UserListElem])
def get_assignable_users(db: Session = Depends(auth.get_db)):
    return (
        db.query(models.User)
        .filter(models.User.musician == True, models.User.status == "active")
        .order_by(models.User.clear_name.asc())
        .all()
    )


@router.get("/users/{user_id}/groups", response_model=list[schemas.GroupOut])
def get_user_groups(user_id: int, db: Session = Depends(auth.get_db)):
    user_db = db.query(models.User).filter(models.User.id == user_id).first()
    if not user_db:
        raise HTTPException(status_code=404, detail="User not found")
    return [membership.group for membership in db.query(models.UserGroupMembership).filter(models.UserGroupMembership.user_id == user_id).all()]


@router.put("/users/{user_id}/groups")
def update_user_groups(user_id: int, payload: schemas.UserGroupMembershipUpdate, db: Session = Depends(auth.get_db)):
    user_db = db.query(models.User).filter(models.User.id == user_id).first()
    if not user_db:
        raise HTTPException(status_code=404, detail="User not found")
    if payload.group_ids:
        groups = db.query(models.Group).filter(models.Group.id.in_(payload.group_ids)).all()
        if len(groups) != len(set(payload.group_ids)):
            raise HTTPException(status_code=404, detail="One or more groups not found")
    db.query(models.UserGroupMembership).filter(models.UserGroupMembership.user_id == user_id).delete()
    for group in groups if payload.group_ids else []:
        db.add(models.UserGroupMembership(user_id=user_id, group_id=group.id))
    db.commit()
    return {"message": "User groups updated"}


@router.get("/groups/{group_id}/instruments", response_model=list[schemas.GroupInstrumentOut])
def get_group_instruments(group_id: int, db: Session = Depends(auth.get_db)):
    group_db = db.query(models.Group).filter(models.Group.id == group_id).first()
    if not group_db:
        raise HTTPException(status_code=404, detail="Group not found")

    assignments = (
        db.query(models.GroupInstrumentMembership, models.Instrument)
        .join(models.Instrument, models.Instrument.id == models.GroupInstrumentMembership.instrument_id)
        .filter(models.GroupInstrumentMembership.group_id == group_id)
        .order_by(models.Instrument.instrument_name.asc())
        .all()
    )

    result = []
    for assignment, instrument in assignments:
        result.append(
            {
                "id": assignment.id,
                "group_id": assignment.group_id,
                "instrument_id": assignment.instrument_id,
                "instrument_name": instrument.instrument_name,
                "instrument_tuning": instrument.instrument_tuning,
                "notes": assignment.notes,
            }
        )
    return result


@router.put("/groups/{group_id}/instruments", response_model=list[schemas.GroupInstrumentOut])
def update_group_instruments(
    group_id: int,
    payload: schemas.GroupInstrumentMembershipUpdate,
    db: Session = Depends(auth.get_db),
):
    group_db = db.query(models.Group).filter(models.Group.id == group_id).first()
    if not group_db:
        raise HTTPException(status_code=404, detail="Group not found")

    instrument_ids = [int(instrument_id) for instrument_id in payload.instrument_ids]
    if instrument_ids:
        instruments = db.query(models.Instrument).filter(models.Instrument.id.in_(instrument_ids)).all()
        if len(instruments) != len(set(instrument_ids)):
            raise HTTPException(status_code=404, detail="One or more instruments not found")

    db.query(models.GroupInstrumentMembership).filter(models.GroupInstrumentMembership.group_id == group_id).delete()
    for instrument_id in instrument_ids:
        db.add(
            models.GroupInstrumentMembership(
                group_id=group_id,
                instrument_id=instrument_id,
                notes=payload.notes,
            )
        )
    db.commit()
    return get_group_instruments(group_id, db)


@router.post("/users", response_model=schemas.UserOut, dependencies=[Depends(user_is_admin)])
def create_user(user: schemas.UserCreate, db: Session = Depends(auth.get_db)):
    existing = db.query(models.User).filter(models.User.user_name == user.user_name).first()
    if existing:
        raise HTTPException(status_code=400, detail="Username already taken")

    db_user = models.User(
        user_name=user.user_name,
        user_pw=auth.hash_pw(user.user_pw),
        user_group=user.user_group.value,
        email=str(user.email),
        clear_name=user.clear_name,
        musician=user.musician,
        is_singer=user.is_singer,
        mm_username=user.mm_username,
        status=user.status.value,
    )
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    return db_user


@router.put("/users/{user_id}", response_model=schemas.UserOut)
def update_user(
    user_id: int,
    data: schemas.UserAdminUpdate,
    current=Depends(auth.get_current_user),
    db: Session = Depends(auth.get_db),
):
    user_db = db.query(models.User).filter(models.User.id == user_id).first()
    if not user_db:
        raise HTTPException(status_code=404, detail="User not found")

    if user_db.user_name != data.user_name:
        existing = db.query(models.User).filter(models.User.user_name == data.user_name).first()
        if existing and existing.id != user_id:
            raise HTTPException(status_code=400, detail="Username already taken")

    if data.status.value == "deactivated" and user_db.user_name == current["user_name"]:
        raise HTTPException(status_code=403, detail="You cannot deactivate your own account")

    user_db.user_name = data.user_name
    user_db.clear_name = data.clear_name
    user_db.email = str(data.email)
    user_db.user_group = data.user_group.value
    user_db.musician = data.musician
    user_db.is_singer = data.is_singer
    user_db.mm_username = data.mm_username

    if data.status.value == "deactivated" and user_db.status != "deactivated":
        db.query(models.RefreshToken).filter(
            models.RefreshToken.user_id == user_id,
            models.RefreshToken.revoked == False,
        ).update({"revoked": True})
    user_db.status = data.status.value

    db.commit()
    db.refresh(user_db)
    return user_db


@router.delete("/users/{user_id}")
def deactivate_user(user_id: int, current=Depends(auth.get_current_user), db: Session = Depends(auth.get_db)):
    user_db = db.query(models.User).filter(models.User.id == user_id).first()
    if not user_db:
        raise HTTPException(status_code=404, detail="User not found")

    if user_db.user_name == current["user_name"]:
        raise HTTPException(status_code=403, detail="You cannot deactivate your own account")
    if user_db.status == "deactivated":
        raise HTTPException(status_code=400, detail="User is already deactivated")

    user_db.status = "deactivated"
    db.query(models.RefreshToken).filter(
        models.RefreshToken.user_id == user_id,
        models.RefreshToken.revoked == False,
    ).update({"revoked": True})
    db.commit()
    return {"message": f"User {user_id} deactivated"}


@router.put("/users/{user_id}/activate")
def activate_user(user_id: int, db: Session = Depends(auth.get_db)):
    user_db = db.query(models.User).filter(models.User.id == user_id).first()
    if not user_db:
        raise HTTPException(status_code=404, detail="User not found")
    if user_db.status == "active":
        raise HTTPException(status_code=400, detail="User is already active")
    user_db.status = "active"
    db.commit()
    return {"message": f"User {user_id} activated"}


@router.get("/instruments", response_model=list[schemas.InstrumentOut])
def list_instruments(request: Request, db: Session = Depends(auth.get_db)):
    user = auth.get_current_user(request, db)
    if auth.is_admin(user.get("user_group")) or auth.is_editor_or_admin(user.get("user_group")):
        return db.query(models.Instrument).order_by(models.Instrument.id.asc()).all()

    user_id = user.get("user_id")
    if user_id is None:
        return []

    allowed_instrument_ids = (
        select(models.UserInstrument.instrument_id)
        .filter(models.UserInstrument.user_id == user_id)
        .union(
            select(models.GroupInstrumentMembership.instrument_id)
            .join(models.UserGroupMembership, models.UserGroupMembership.group_id == models.GroupInstrumentMembership.group_id)
            .filter(models.UserGroupMembership.user_id == user_id)
            .distinct()
        )
    )
    return (
        db.query(models.Instrument)
        .filter(models.Instrument.id.in_(allowed_instrument_ids))
        .distinct()
        .order_by(models.Instrument.id.asc())
        .all()
    )


@router.post("/instruments", response_model=schemas.InstrumentOut)
def create_instrument(instrument: schemas.InstrumentCreate, db: Session = Depends(auth.get_db)):
    normalized_name = instrument.instrument_name.strip()
    if not normalized_name:
        raise HTTPException(status_code=400, detail="Instrument name must not be empty")

    existing = db.query(models.Instrument).filter(models.Instrument.instrument_name == normalized_name).first()
    if existing:
        raise HTTPException(status_code=400, detail="Instrument already exists")

    db_instrument = models.Instrument(
        instrument_name=normalized_name,
        instrument_tuning=instrument.instrument_tuning.strip(),
        notes=instrument.notes.strip() if instrument.notes else None,
    )
    db.add(db_instrument)
    db.commit()
    db.refresh(db_instrument)
    return db_instrument


@router.put("/instruments/{instrument_id}", response_model=schemas.InstrumentOut)
def update_instrument(
    instrument_id: int,
    instrument: schemas.InstrumentUpdate,
    db: Session = Depends(auth.get_db),
):
    db_instrument = db.query(models.Instrument).filter(models.Instrument.id == instrument_id).first()
    if not db_instrument:
        raise HTTPException(status_code=404, detail="Instrument not found")

    normalized_name = instrument.instrument_name.strip()
    if not normalized_name:
        raise HTTPException(status_code=400, detail="Instrument name must not be empty")

    duplicate = (
        db.query(models.Instrument)
        .filter(models.Instrument.instrument_name == normalized_name, models.Instrument.id != instrument_id)
        .first()
    )
    if duplicate:
        raise HTTPException(status_code=400, detail="Instrument already exists")

    db_instrument.instrument_name = normalized_name
    db_instrument.instrument_tuning = instrument.instrument_tuning.strip()
    db_instrument.notes = instrument.notes.strip() if instrument.notes else None
    db.commit()
    db.refresh(db_instrument)
    return db_instrument


@router.delete("/instruments/{instrument_id}")
def delete_instrument(instrument_id: int, db: Session = Depends(auth.get_db)):
    db_instrument = db.query(models.Instrument).filter(models.Instrument.id == instrument_id).first()
    if not db_instrument:
        raise HTTPException(status_code=404, detail="Instrument not found")

    is_used_in_scores = db.query(models.Score).filter(models.Score.instrument_id == instrument_id).first() is not None
    is_used_in_user_assignments = (
        db.query(models.UserInstrument).filter(models.UserInstrument.instrument_id == instrument_id).first() is not None
    )
    is_used_in_group_assignments = (
        db.query(models.GroupInstrumentMembership)
        .filter(models.GroupInstrumentMembership.instrument_id == instrument_id)
        .first()
        is not None
    )
    if is_used_in_scores or is_used_in_user_assignments or is_used_in_group_assignments:
        raise HTTPException(status_code=400, detail="Instrument is still assigned and cannot be deleted")

    db.delete(db_instrument)
    db.commit()
    return {"message": "Instrument deleted"}


@router.get("/users/{user_id}/instruments", response_model=list[schemas.UserInstrumentOut])
def get_user_instruments(user_id: int, db: Session = Depends(auth.get_db)):
    user_db = db.query(models.User).filter(models.User.id == user_id).first()
    if not user_db:
        raise HTTPException(status_code=404, detail="User not found")

    assignments = (
        db.query(models.UserInstrument, models.Instrument)
        .join(models.Instrument, models.Instrument.id == models.UserInstrument.instrument_id)
        .filter(models.UserInstrument.user_id == user_id)
        .order_by(models.Instrument.instrument_name.asc())
        .all()
    )

    result = []
    for assignment, instrument in assignments:
        result.append(
            {
                "id": assignment.id,
                "user_id": assignment.user_id,
                "instrument_id": assignment.instrument_id,
                "instrument_name": instrument.instrument_name,
                "instrument_tuning": instrument.instrument_tuning,
                "notes": assignment.notes,
            }
        )
    return result


@router.post("/users/{user_id}/instruments", response_model=list[schemas.UserInstrumentOut])
def assign_instruments_to_user(
    user_id: int,
    payload: schemas.AssignInstrumentRequest,
    db: Session = Depends(auth.get_db),
):
    user_db = db.query(models.User).filter(models.User.id == user_id).first()
    if not user_db:
        raise HTTPException(status_code=404, detail="User not found")

    for instrument_id in payload.instrument_ids:
        instrument = db.query(models.Instrument).filter(models.Instrument.id == instrument_id).first()
        if not instrument:
            raise HTTPException(status_code=404, detail=f"Instrument {instrument_id} not found")

        existing = (
            db.query(models.UserInstrument)
            .filter(models.UserInstrument.user_id == user_id, models.UserInstrument.instrument_id == instrument_id)
            .first()
        )
        if existing:
            if payload.notes is not None:
                existing.notes = payload.notes
            continue

        db.add(models.UserInstrument(user_id=user_id, instrument_id=instrument_id, notes=payload.notes))

    db.commit()
    return get_user_instruments(user_id, db)


@router.delete("/users/{user_id}/instruments/{instrument_id}")
def remove_instrument_from_user(user_id: int, instrument_id: int, db: Session = Depends(auth.get_db)):
    user_db = db.query(models.User).filter(models.User.id == user_id).first()
    if not user_db:
        raise HTTPException(status_code=404, detail="User not found")

    assignment = (
        db.query(models.UserInstrument)
        .filter(models.UserInstrument.user_id == user_id, models.UserInstrument.instrument_id == instrument_id)
        .first()
    )
    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")

    db.delete(assignment)
    db.commit()
    return {"message": "Instrument assignment removed"}


@router.get("/collections", response_model=list[schemas.CollectionOut])
def list_collections(db: Session = Depends(auth.get_db)):
    return db.query(models.Collection).order_by(models.Collection.name.asc()).all()


@router.post("/collections", response_model=schemas.CollectionOut)
def create_collection(collection: schemas.CollectionCreate, db: Session = Depends(auth.get_db)):
    existing = db.query(models.Collection).filter(models.Collection.name == collection.name).first()
    if existing:
        raise HTTPException(status_code=400, detail="Collection already exists")
    db_collection = models.Collection(**collection.model_dump())
    db.add(db_collection)
    db.commit()
    db.refresh(db_collection)
    return db_collection


@router.put("/collections/{collection_id}", response_model=schemas.CollectionOut)
def update_collection(collection_id: int, collection: schemas.CollectionUpdate, db: Session = Depends(auth.get_db)):
    db_collection = db.query(models.Collection).filter(models.Collection.id == collection_id).first()
    if not db_collection:
        raise HTTPException(status_code=404, detail="Collection not found")
    duplicate = (
        db.query(models.Collection)
        .filter(models.Collection.name == collection.name, models.Collection.id != collection_id)
        .first()
    )
    if duplicate:
        raise HTTPException(status_code=400, detail="Collection already exists")
    for key, value in collection.model_dump().items():
        setattr(db_collection, key, value)
    db.commit()
    db.refresh(db_collection)
    return db_collection


@router.delete("/collections/{collection_id}")
def delete_collection(collection_id: int, db: Session = Depends(auth.get_db)):
    db_collection = db.query(models.Collection).filter(models.Collection.id == collection_id).first()
    if not db_collection:
        raise HTTPException(status_code=404, detail="Collection not found")
    db.delete(db_collection)
    db.commit()
    return {"message": "Collection deleted"}


@router.get("/songs", response_model=list[schemas.SongOut])
def list_songs(db: Session = Depends(auth.get_db)):
    return db.query(models.Song).options(selectinload(models.Song.collections)).order_by(models.Song.name.asc()).all()


@router.post("/songs", response_model=schemas.SongOut)
def create_song(song: schemas.SongCreate, db: Session = Depends(auth.get_db)):
    db_song = models.Song(**song.model_dump())
    db.add(db_song)
    db.commit()
    db.refresh(db_song)
    return db_song


@router.put("/songs/{song_id}", response_model=schemas.SongOut)
def update_song(song_id: int, song: schemas.SongUpdate, db: Session = Depends(auth.get_db)):
    db_song = db.query(models.Song).options(selectinload(models.Song.collections)).filter(models.Song.id == song_id).first()
    if not db_song:
        raise HTTPException(status_code=404, detail="Song not found")

    for key, value in song.model_dump().items():
        setattr(db_song, key, value)
    db.commit()
    db.refresh(db_song)
    return db_song


@router.put("/songs/{song_id}/collections", response_model=schemas.SongOut)
def update_song_collections(
    song_id: int,
    payload: schemas.SongCollectionsUpdate,
    db: Session = Depends(auth.get_db),
):
    db_song = db.query(models.Song).options(selectinload(models.Song.collections)).filter(models.Song.id == song_id).first()
    if not db_song:
        raise HTTPException(status_code=404, detail="Song not found")

    collections = []
    if payload.collection_ids:
        collections = (
            db.query(models.Collection).filter(models.Collection.id.in_(payload.collection_ids)).order_by(models.Collection.name.asc()).all()
        )
        if len(collections) != len(set(payload.collection_ids)):
            raise HTTPException(status_code=404, detail="One or more collections not found")
    db_song.collections = collections
    db.commit()
    db.refresh(db_song)
    return db_song


@router.delete("/songs/{song_id}")
def delete_song(song_id: int, db: Session = Depends(auth.get_db)):
    db_song = db.query(models.Song).filter(models.Song.id == song_id).first()
    if not db_song:
        raise HTTPException(status_code=404, detail="Song not found")

    song_scores = db.query(models.Score).filter(models.Score.song_id == song_id).all()
    song_folder = _build_song_storage_folder_name(db_song, db)
    song_storage_dir = SCORES_UPLOAD_DIR / song_folder

    for score in song_scores:
        if not score.storage_path:
            continue
        file_path = _absolute_score_storage_path(score.storage_path)
        if file_path.exists():
            file_path.unlink()

    if song_storage_dir.exists() and song_storage_dir.is_dir():
        for child in list(song_storage_dir.iterdir()):
            if child.is_file():
                child.unlink()
            elif child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
        try:
            song_storage_dir.rmdir()
        except OSError:
            pass

    db.query(models.Score).filter(models.Score.song_id == song_id).delete()
    db.delete(db_song)
    db.commit()
    return {"message": "Song deleted"}


@router.get("/storage/status", response_model=schemas.StorageStatusOut)
def get_storage_status(db: Session = Depends(auth.get_db)):
    return schemas.StorageStatusOut(
        has_changes=False,
        database_tree_hash="",
        filesystem_tree_hash="",
        database_file_count=0,
        filesystem_file_count=0,
        added_files=[],
        removed_files=[],
        changed_files=[],
        unmatched_files=[],
    )


@router.post("/storage/rescrape", response_model=schemas.StorageRescrapeOut)
def rescrape_storage(db: Session = Depends(auth.get_db)):
    empty_status = schemas.StorageRescrapeOut(
        has_changes=False,
        database_tree_hash="",
        filesystem_tree_hash="",
        database_file_count=0,
        filesystem_file_count=0,
        added_files=[],
        removed_files=[],
        changed_files=[],
        unmatched_files=[],
        created_scores=0,
        updated_scores=0,
        removed_scores=0,
    )
    return empty_status


@router.get("/scores", response_model=list[schemas.ScoreOut])
def list_scores(request: Request, song_id: int | None = None, db: Session = Depends(auth.get_db)):
    user = auth.get_current_user(request, db)
    if auth.is_admin(user.get("user_group")) or auth.is_editor_or_admin(user.get("user_group")):
        query = (
            db.query(models.Score, models.Song, models.Instrument)
            .join(models.Song, models.Song.id == models.Score.song_id)
            .join(models.Instrument, models.Instrument.id == models.Score.instrument_id)
        )
    else:
        user_id = user.get("user_id")
        if user_id is None:
            return []
        allowed_instrument_ids = (
            select(models.UserInstrument.instrument_id)
            .filter(models.UserInstrument.user_id == user_id)
            .union(
                select(models.GroupInstrumentMembership.instrument_id)
                .join(models.UserGroupMembership, models.UserGroupMembership.group_id == models.GroupInstrumentMembership.group_id)
                .filter(models.UserGroupMembership.user_id == user_id)
                .distinct()
            )
        )
        query = (
            db.query(models.Score, models.Song, models.Instrument)
            .join(models.Song, models.Song.id == models.Score.song_id)
            .join(models.Instrument, models.Instrument.id == models.Score.instrument_id)
            .filter(models.Instrument.id.in_(allowed_instrument_ids))
        )

    if song_id is not None:
        query = query.filter(models.Score.song_id == song_id)
    rows = query.order_by(models.Song.name.asc(), models.Instrument.instrument_name.asc()).all()
    return [_score_to_out(score, song, instrument) for score, song, instrument in rows]


@router.post("/scores", response_model=schemas.ScoreOut)
def create_score(score: schemas.ScoreCreate, db: Session = Depends(auth.get_db)):
    song = db.query(models.Song).filter(models.Song.id == score.song_id).first()
    if not song:
        raise HTTPException(status_code=404, detail="Song not found")
    instrument = db.query(models.Instrument).filter(models.Instrument.id == score.instrument_id).first()
    if not instrument:
        raise HTTPException(status_code=404, detail="Instrument not found")

    db_score = models.Score(**score.model_dump())
    if db_score.storage_path:
        file_path = _absolute_score_storage_path(db_score.storage_path)
        if file_path.exists():
            db_score.file_hash = _hash_file(file_path)
    db.add(db_score)
    db.commit()
    db.refresh(db_score)
    return _score_to_out(db_score, song, instrument)


@router.post("/scores/upload/preview", response_model=schemas.ScoreUploadPreviewOut)
def upload_score_pdf_preview(
    song_id: int = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(auth.get_db),
):
    song = db.query(models.Song).filter(models.Song.id == song_id).first()
    if not song:
        raise HTTPException(status_code=404, detail="Song not found")
    if not file.filename:
        raise HTTPException(status_code=400, detail="Missing file name")
    if Path(file.filename).suffix.lower() != ".pdf":
        raise HTTPException(status_code=400, detail="Only PDF files are allowed")

    file.file.seek(0)
    signature = file.file.read(5)
    file.file.seek(0)
    if signature != b"%PDF-":
        raise HTTPException(status_code=400, detail="Only valid PDF files are allowed")

    upload_token = secrets.token_urlsafe(24)
    source_file = _preview_pdf_path(upload_token)
    metadata_path = _preview_metadata_path(upload_token)
    with source_file.open("wb") as output_file:
        shutil.copyfileobj(file.file, output_file)

    reader = PdfReader(str(source_file))
    page_count = len(reader.pages)
    if page_count <= 0:
        if source_file.exists():
            source_file.unlink()
        raise HTTPException(status_code=400, detail="PDF has no pages")
    chapters = _preview_chapters_from_pdf(reader, db, song_name=song.name)

    metadata = {
        "upload_token": upload_token,
        "source_filename": file.filename,
        "page_count": page_count,
        "chapters": chapters,
    }
    with metadata_path.open("w", encoding="utf-8") as handle:
        json.dump(metadata, handle, ensure_ascii=False)

    return schemas.ScoreUploadPreviewOut(
        upload_token=upload_token,
        source_filename=file.filename,
        page_count=page_count,
        chapters=[
            schemas.ScoreUploadChapterOut(
                chapter_index=index,
                chapter_title=chapter.get("original_chapter_title") or chapter.get("chapter_title") or f"Kapitel {index + 1}",
                original_chapter_title=chapter.get("original_chapter_title") or chapter.get("chapter_title") or f"Kapitel {index + 1}",
                start_page=chapter["start_page"],
                end_page=chapter["end_page"],
                suggested_instrument_id=chapter.get("suggested_instrument_id"),
                suggested_instrument_name=chapter.get("suggested_instrument_name"),
                suggested_instrument_tuning=chapter.get("suggested_instrument_tuning"),
            )
            for index, chapter in enumerate(chapters)
        ],
    )


@router.post("/scores/upload/commit", response_model=list[schemas.ScoreOut])
def upload_score_pdf_commit(payload: schemas.ScoreUploadCommitRequest, db: Session = Depends(auth.get_db)):
    song = db.query(models.Song).filter(models.Song.id == payload.song_id).first()
    if not song:
        raise HTTPException(status_code=404, detail="Song not found")
    if not payload.mappings:
        raise HTTPException(status_code=400, detail="At least one chapter mapping is required")

    source_file = _preview_pdf_path(payload.upload_token)
    if not source_file.exists():
        raise HTTPException(status_code=404, detail="Upload file not found")

    metadata = _load_preview_metadata(payload.upload_token)
    chapters = metadata.get("chapters", [])
    if not isinstance(chapters, list) or not chapters:
        raise HTTPException(status_code=400, detail="Upload session has no chapter data")

    mappings_by_index: dict[int, schemas.ScoreUploadMappingIn] = {}
    for mapping in payload.mappings:
        if mapping.chapter_index in mappings_by_index:
            raise HTTPException(status_code=400, detail=f"Duplicate mapping for chapter {mapping.chapter_index}")
        mappings_by_index[mapping.chapter_index] = mapping

    missing_mappings = [str(index) for index in range(len(chapters)) if index not in mappings_by_index]
    if missing_mappings:
        raise HTTPException(status_code=400, detail=f"Missing mappings for chapter(s): {', '.join(missing_mappings)}")
    selected_mapping_count = sum(1 for mapping in payload.mappings if mapping.include)
    if selected_mapping_count <= 0:
        raise HTTPException(status_code=400, detail="Please select at least one chapter to import")

    reader = PdfReader(str(source_file))
    created_scores: list[tuple[models.Score, models.Instrument]] = []
    for chapter_index, chapter in enumerate(chapters):
        mapping = mappings_by_index.get(chapter_index)
        if not mapping:
            raise HTTPException(status_code=400, detail=f"Missing mapping for chapter {chapter_index}")
        if not mapping.include:
            continue
        start_page = int(chapter.get("start_page", -1))
        end_page = int(chapter.get("end_page", -1))
        chapter_title = str(chapter.get("chapter_title", "")).strip() or f"Kapitel {chapter_index + 1}"
        if start_page < 0 or end_page <= start_page or end_page > len(reader.pages):
            raise HTTPException(status_code=400, detail=f"Invalid page range for chapter '{chapter_title}'")

        instrument = _resolve_chapter_instrument(mapping, chapter_title, db)

        writer = PdfWriter()
        for page_no in range(start_page, end_page):
            writer.add_page(reader.pages[page_no])
        storage_path = _next_score_storage_relative_path(song, instrument, db)
        part_path = _absolute_score_storage_path(storage_path)
        part_path.parent.mkdir(parents=True, exist_ok=True)
        with part_path.open("wb") as output_file:
            writer.write(output_file)

        score = models.Score(
            song_id=payload.song_id,
            instrument_id=instrument.id,
            storage_path=storage_path,
            file_hash=_hash_file(part_path),
            notes=payload.notes,
        )
        db.add(score)
        db.flush()
        created_scores.append((score, instrument))

    db.commit()
    if source_file.exists():
        source_file.unlink()
    metadata_path = _preview_metadata_path(payload.upload_token)
    if metadata_path.exists():
        metadata_path.unlink()

    result: list[schemas.ScoreOut] = []
    for score, instrument in created_scores:
        db.refresh(score)
        result.append(_score_to_out(score, song, instrument))
    return result


@router.post("/scores/upload", response_model=list[schemas.ScoreOut])
def upload_score_pdf(
    song_id: int = Form(...),
    instrument_id: int | None = Form(None),
    instrument_name: str | None = Form(None),
    instrument_tuning: str | None = Form(None),
    file: UploadFile = File(...),
    notes: str | None = Form(None),
    db: Session = Depends(auth.get_db),
):
    song = db.query(models.Song).filter(models.Song.id == song_id).first()
    if not song:
        raise HTTPException(status_code=404, detail="Song not found")
    fallback_tuning = (instrument_tuning or "C").strip() or "C"
    fallback_instrument: models.Instrument | None = None
    if instrument_id is not None:
        fallback_instrument = db.query(models.Instrument).filter(models.Instrument.id == instrument_id).first()
        if not fallback_instrument:
            raise HTTPException(status_code=404, detail="Instrument not found")
    elif instrument_name:
        fallback_instrument = _get_or_create_instrument(db, instrument_name, fallback_tuning)
    if not file.filename:
        raise HTTPException(status_code=400, detail="Missing file name")
    if Path(file.filename).suffix.lower() != ".pdf":
        raise HTTPException(status_code=400, detail="Only PDF files are allowed")

    file.file.seek(0)
    signature = file.file.read(5)
    file.file.seek(0)
    if signature != b"%PDF-":
        raise HTTPException(status_code=400, detail="Only valid PDF files are allowed")

    upload_dir = SCORES_UPLOAD_DIR / "_incoming"
    upload_dir.mkdir(parents=True, exist_ok=True)
    source_name = _safe_filename_part(Path(file.filename).stem)
    timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S%f")
    source_file = upload_dir / f"{source_name}_{timestamp}.pdf"
    with source_file.open("wb") as output_file:
        shutil.copyfileobj(file.file, output_file)

    reader = PdfReader(str(source_file))
    outline_entries = _extract_outline_entries(reader)
    instruments = db.query(models.Instrument).all()
    instruments_by_normalized_name = {
        _normalize_part_name(instrument.instrument_name): instrument for instrument in instruments
    }

    matched_parts: list[tuple[int, models.Instrument]] = []
    for chapter_title, page_index in outline_entries:
        instrument = _match_instrument_by_outline_title(
            chapter_title, instruments_by_normalized_name, song_name=song.name
        )
        if not instrument:
            instrument = _get_or_create_instrument(db, chapter_title, fallback_tuning)
            instruments_by_normalized_name[_normalize_part_name(instrument.instrument_name)] = instrument
        if page_index < 0 or page_index >= len(reader.pages):
            continue
        matched_parts.append((page_index, instrument))

    matched_parts = sorted(matched_parts, key=lambda item: item[0])
    deduped_parts: list[tuple[int, models.Instrument]] = []
    seen: set[tuple[int, int]] = set()
    for page_index, instrument in matched_parts:
        key = (page_index, instrument.id)
        if key in seen:
            continue
        seen.add(key)
        deduped_parts.append((page_index, instrument))

    created_scores: list[tuple[models.Score, models.Instrument]] = []
    if deduped_parts:
        for part_index, (start_page, instrument) in enumerate(deduped_parts):
            end_page = len(reader.pages)
            if part_index + 1 < len(deduped_parts):
                end_page = deduped_parts[part_index + 1][0]
            if end_page <= start_page:
                continue

            writer = PdfWriter()
            for page_no in range(start_page, end_page):
                writer.add_page(reader.pages[page_no])
            storage_path = _next_score_storage_relative_path(song, instrument, db)
            part_path = _absolute_score_storage_path(storage_path)
            part_path.parent.mkdir(parents=True, exist_ok=True)
            with part_path.open("wb") as output_file:
                writer.write(output_file)

            score = models.Score(
                song_id=song_id,
                instrument_id=instrument.id,
                storage_path=storage_path,
                file_hash=_hash_file(part_path),
                notes=notes,
            )
            db.add(score)
            db.flush()
            created_scores.append((score, instrument))

        if not created_scores:
            if source_file.exists():
                source_file.unlink()
            raise HTTPException(status_code=400, detail="No valid score sections found in PDF outline")
        if created_scores and source_file.exists():
            source_file.unlink()
    else:
        if not fallback_instrument:
            raise HTTPException(status_code=400, detail="Instrument is required when no chapter-based split is possible")
        storage_path = _next_score_storage_relative_path(song, fallback_instrument, db)
        target_path = _absolute_score_storage_path(storage_path)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source_file), str(target_path))
        score = models.Score(
            song_id=song_id,
            instrument_id=fallback_instrument.id,
            storage_path=storage_path,
            file_hash=_hash_file(target_path),
            notes=notes,
        )
        db.add(score)
        db.flush()
        created_scores.append((score, fallback_instrument))

    db.commit()
    result: list[schemas.ScoreOut] = []
    for score, instrument in created_scores:
        db.refresh(score)
        result.append(_score_to_out(score, song, instrument))
    return result


@router.put("/scores/{score_id}", response_model=schemas.ScoreOut)
def update_score(score_id: int, score: schemas.ScoreUpdate, db: Session = Depends(auth.get_db)):
    db_score = db.query(models.Score).filter(models.Score.id == score_id).first()
    if not db_score:
        raise HTTPException(status_code=404, detail="Score not found")

    song = db.query(models.Song).filter(models.Song.id == score.song_id).first()
    if not song:
        raise HTTPException(status_code=404, detail="Song not found")
    instrument = db.query(models.Instrument).filter(models.Instrument.id == score.instrument_id).first()
    if not instrument:
        raise HTTPException(status_code=404, detail="Instrument not found")

    old_instrument_id = db_score.instrument_id
    old_storage_path = db_score.storage_path
    for key, value in score.model_dump().items():
        setattr(db_score, key, value)

    instrument_changed = old_instrument_id != db_score.instrument_id
    if instrument_changed and old_storage_path and Path(old_storage_path).suffix.lower() == ".pdf":
        source_path = _absolute_score_storage_path(old_storage_path)
        if source_path.exists():
            new_storage_path = _next_score_storage_relative_path(song, instrument, db)
            target_path = _absolute_score_storage_path(new_storage_path)
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source_path), str(target_path))
            db_score.storage_path = new_storage_path
            db_score.file_hash = _hash_file(target_path)
        elif db_score.storage_path:
            file_path = _absolute_score_storage_path(db_score.storage_path)
            db_score.file_hash = _hash_file(file_path) if file_path.exists() else None
        else:
            db_score.file_hash = None
    elif db_score.storage_path:
        file_path = _absolute_score_storage_path(db_score.storage_path)
        db_score.file_hash = _hash_file(file_path) if file_path.exists() else None
    db.commit()
    db.refresh(db_score)
    return _score_to_out(db_score, song, instrument)


@router.delete("/scores/{score_id}")
def delete_score(score_id: int, db: Session = Depends(auth.get_db)):
    db_score = db.query(models.Score).filter(models.Score.id == score_id).first()
    if not db_score:
        raise HTTPException(status_code=404, detail="Score not found")

    if db_score.storage_path:
        file_path = _absolute_score_storage_path(db_score.storage_path)
        if file_path.exists():
            file_path.unlink()
    db.delete(db_score)
    db.commit()
    return {"message": "Score deleted"}


@router.put("/trigger_password_reset/{user_id}")
def trigger_password_reset(user_id: int, db: Session = Depends(auth.get_db)):
    user_db = db.query(models.User).filter(models.User.id == user_id).first()
    if not user_db:
        raise HTTPException(status_code=404, detail="User not found")
    if not user_db.email:
        raise HTTPException(status_code=400, detail="User has no email address")

    reset_token = auth.create_password_reset_token(user_db.user_name)
    reset_link = f"{os.getenv('FRONTEND_URL', 'http://localhost:5173')}/password_reset?token={reset_token}"
    send_email(
        user_db.email,
        "Password Reset Request",
        f"Ein Passwort-Reset wurde für dein Konto angefordert. Link: {reset_link}",
    )
    return {"message": "Password reset link sent", "reset_link": reset_link}
