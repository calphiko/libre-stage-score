import asyncio
import logging
import os
import secrets
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from starlette.types import ASGIApp, Message, Receive, Scope, Send
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from backend import auth, models, schemas
from backend.bootstrap import initialize_missing_database
from backend.database import engine
from backend.paths import data_root
from backend.routers import admin, password_reset
from backend.sync.router import router as sync_router
from backend.sync import worker as sync_worker
from backend.sync import service as sync_service
from backend.utils.password_validator import validate_password


def _sqlite_column_names(table_name: str) -> set[str]:
    with engine.connect() as connection:
        rows = connection.execute(text(f"PRAGMA table_info({table_name})"))
        return {str(row[1]) for row in rows}


def _ensure_backward_compatible_schema() -> None:
    models.Base.metadata.create_all(bind=engine)
    if engine.dialect.name != "sqlite":
        return

    score_columns = _sqlite_column_names("scores")
    if score_columns and "file_hash" not in score_columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE scores ADD COLUMN file_hash VARCHAR(64)"))

    user_columns = _sqlite_column_names("users")
    if user_columns:
        for column_name, column_def in {
            "musician": "BOOLEAN NOT NULL DEFAULT 0",
            "is_singer": "BOOLEAN NOT NULL DEFAULT 0",
            "mm_username": "VARCHAR(128)",
        }.items():
            if column_name not in user_columns:
                with engine.begin() as connection:
                    connection.execute(text(f"ALTER TABLE users ADD COLUMN {column_name} {column_def}"))

    existing_tables = set()
    with engine.connect() as connection:
        rows = connection.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
        existing_tables = {str(row[0]) for row in rows}

    if "groups" not in existing_tables:
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE groups (id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT, name VARCHAR(128) NOT NULL UNIQUE, notes TEXT)"))

    if "user_groups" not in existing_tables:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TABLE user_groups (id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, group_id INTEGER NOT NULL, UNIQUE(user_id, group_id), FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE, FOREIGN KEY(group_id) REFERENCES groups(id) ON DELETE CASCADE)"
                )
            )

    if "document_access" not in existing_tables:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TABLE document_access (id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT, document_type VARCHAR(32) NOT NULL, document_id INTEGER NOT NULL, user_id INTEGER, group_id INTEGER, UNIQUE(document_type, document_id, user_id, group_id), FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE, FOREIGN KEY(group_id) REFERENCES groups(id) ON DELETE CASCADE)"
                )
            )


initialize_missing_database()
_ensure_backward_compatible_schema()
PROJECT_ROOT = data_root()
SCORES_STORAGE_ROOT = PROJECT_ROOT / "backend" / "storage" / "scores"

logger = logging.getLogger(__name__)


class SyncRequestSizeLimitMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app
        try:
            self.max_bytes = int(os.getenv("SYNC_MAX_REQUEST_BYTES", str(17 * 1024 * 1024)))
        except ValueError as exc:
            raise RuntimeError("SYNC_MAX_REQUEST_BYTES must be a positive integer") from exc
        if self.max_bytes <= 0:
            raise RuntimeError("SYNC_MAX_REQUEST_BYTES must be a positive integer")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith("/sync/"):
            await self.app(scope, receive, send)
            return
        content_length = next(
            (value for name, value in scope["headers"] if name == b"content-length"),
            None,
        )
        if content_length is not None:
            try:
                declared_size = int(content_length)
            except ValueError:
                await self._reject(send, 400, "Invalid Content-Length")
                return
            if declared_size < 0:
                await self._reject(send, 400, "Invalid Content-Length")
                return
            if declared_size > self.max_bytes:
                await self._reject(send, 413, "Sync request exceeds configured size limit")
                return

        messages: deque[Message] = deque()
        total_size = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            total_size += len(message.get("body", b""))
            if total_size > self.max_bytes:
                await self._reject(send, 413, "Sync request exceeds configured size limit")
                return
            messages.append(message)
            if not message.get("more_body", False):
                break

        async def replay_body() -> Message:
            if messages:
                return messages.popleft()
            return await receive()

        await self.app(scope, replay_body, send)

    @staticmethod
    async def _reject(send: Send, status_code: int, detail: str) -> None:
        body = ('{"detail":"' + detail + '"}').encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": status_code,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
            }
        )
        await send({"type": "http.response.body", "body": body})


@asynccontextmanager
async def lifespan(_app: FastAPI):
    worker_task = None
    if sync_worker.sync_enabled():
        configuration = sync_worker.load_worker_configuration()
        worker_task = asyncio.create_task(sync_worker.run_sync_worker(*configuration), name="sync-worker")
        logger.info("Background synchronization is enabled")
    try:
        yield
    finally:
        if worker_task is not None:
            worker_task.cancel()
            try:
                await worker_task
            except asyncio.CancelledError:
                pass


app = FastAPI(title="libre-stage user service", version="0.1.0", lifespan=lifespan)
app.add_middleware(SyncRequestSizeLimitMiddleware)

origins_env = os.getenv("CORS_ORIGINS", "http://localhost:5173")
origins = [origin.strip() for origin in origins_env.split(",") if origin.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

AUTH_COOKIE_SECURE = os.getenv("AUTH_COOKIE_SECURE", "false").lower() in {"1", "true", "yes", "on"}
AUTH_COOKIE_SAMESITE = os.getenv("AUTH_COOKIE_SAMESITE", "lax")
AUTH_COOKIE_DOMAIN = os.getenv("AUTH_COOKIE_DOMAIN")
CSRF_COOKIE_NAME = "csrf_token"
CSRF_HEADER_NAME = "X-CSRF-Token"
CSRF_EXEMPT_PATHS = {"/login", "/refresh", "/health", "/version", "/csrf"}


def _normalize_origin(origin: str | None) -> str | None:
    if not origin:
        return None
    return origin.rstrip("/").lower()


def _origin_from_referer(referer: str | None) -> str | None:
    if not referer:
        return None
    parsed = urlparse(referer)
    if not parsed.scheme or not parsed.netloc:
        return None
    return _normalize_origin(f"{parsed.scheme}://{parsed.netloc}")


def _is_trusted_origin(request: Request) -> bool:
    origin = _normalize_origin(request.headers.get("origin"))
    referer_origin = _origin_from_referer(request.headers.get("referer"))
    candidate = origin or referer_origin
    if candidate is None:
        return True

    trusted = set()
    for configured_origin in origins:
        normalized = _normalize_origin(configured_origin)
        parsed = urlparse(normalized or "")
        if parsed.scheme and parsed.netloc:
            trusted.add(normalized)

    host = request.headers.get("host")
    if host:
        trusted.add(_normalize_origin(f"{request.url.scheme}://{host}"))

    return candidate in trusted


@app.middleware("http")
async def csrf_cookie_guard(request: Request, call_next):
    method = request.method.upper()
    if method in {"GET", "HEAD", "OPTIONS"}:
        return await call_next(request)
    if request.url.path in CSRF_EXEMPT_PATHS:
        return await call_next(request)
    if request.headers.get("Authorization"):
        return await call_next(request)
    if not request.cookies.get("access_token"):
        return await call_next(request)
    if not _is_trusted_origin(request):
        raise HTTPException(status_code=403, detail="Origin validation failed")

    csrf_cookie = request.cookies.get(CSRF_COOKIE_NAME)
    csrf_header = request.headers.get(CSRF_HEADER_NAME)
    if not csrf_cookie or not csrf_header or csrf_cookie != csrf_header:
        raise HTTPException(status_code=403, detail="CSRF validation failed")
    return await call_next(request)


def _set_auth_cookie(response: Response, key: str, value: str, max_age_seconds: int) -> None:
    response.set_cookie(
        key=key,
        value=value,
        max_age=max_age_seconds,
        httponly=True,
        secure=AUTH_COOKIE_SECURE,
        samesite=AUTH_COOKIE_SAMESITE,
        domain=AUTH_COOKIE_DOMAIN,
        path="/",
    )


def _set_csrf_cookie(response: Response, value: str, max_age_seconds: int) -> None:
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=value,
        max_age=max_age_seconds,
        httponly=False,
        secure=AUTH_COOKIE_SECURE,
        samesite=AUTH_COOKIE_SAMESITE,
        domain=AUTH_COOKIE_DOMAIN,
        path="/",
    )


def _clear_auth_cookie(response: Response, key: str) -> None:
    response.delete_cookie(
        key=key,
        domain=AUTH_COOKIE_DOMAIN,
        path="/",
        secure=AUTH_COOKIE_SECURE,
        samesite=AUTH_COOKIE_SAMESITE,
    )


def _resolve_pdf_path(storage_path: str) -> Path:
    candidate = Path(storage_path)
    if candidate.suffix.lower() != ".pdf":
        raise HTTPException(status_code=400, detail="Only PDF score files are allowed")

    if candidate.is_absolute():
        resolved = candidate.resolve()
    elif candidate.parts and candidate.parts[0] in {"backend", "scores"}:
        resolved = (PROJECT_ROOT / candidate).resolve()
    else:
        resolved = (SCORES_STORAGE_ROOT / candidate).resolve()
    try:
        resolved.relative_to(PROJECT_ROOT)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid storage path") from exc

    if not resolved.is_file():
        raise HTTPException(status_code=404, detail="Score file not found")
    return resolved


@app.post("/login", response_model=schemas.TokenResponse)
def login(data: schemas.LoginRequest, response: Response, db: Session = Depends(auth.get_db)):
    user = auth.authenticate_user(db, data.username, data.password)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    access_token = auth.create_access_token({"sub": user.user_name, "role": user.user_group})
    refresh_token = auth.create_refresh_token(user.id, db)
    csrf_token = secrets.token_urlsafe(32)

    _set_auth_cookie(response, "access_token", access_token, auth.ACCESS_TOKEN_EXPIRE_MINUTES * 60)
    _set_auth_cookie(response, "refresh_token", refresh_token, auth.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60)
    _set_csrf_cookie(response, csrf_token, auth.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60)

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "csrf_token": csrf_token,
        "token_type": "bearer",
        "expires_in": auth.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        "message": "Login successful",
    }


@app.post("/refresh", response_model=schemas.TokenResponse)
def refresh_token(
    request: Request,
    response: Response,
    refresh_data: schemas.RefreshRequest | None = None,
    db: Session = Depends(auth.get_db),
):
    refresh_token_value = request.cookies.get("refresh_token")
    if not refresh_token_value and refresh_data:
        refresh_token_value = refresh_data.refresh_token
    if not refresh_token_value:
        raise HTTPException(status_code=401, detail="No refresh token provided")

    user, new_refresh_token = auth.rotate_refresh_token(refresh_token_value, db)
    new_access_token = auth.create_access_token({"sub": user.user_name, "role": user.user_group})
    csrf_token = request.cookies.get(CSRF_COOKIE_NAME) or secrets.token_urlsafe(32)

    _set_auth_cookie(response, "access_token", new_access_token, auth.ACCESS_TOKEN_EXPIRE_MINUTES * 60)
    _set_auth_cookie(response, "refresh_token", new_refresh_token, auth.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60)
    _set_csrf_cookie(response, csrf_token, auth.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60)

    return {
        "access_token": new_access_token,
        "refresh_token": new_refresh_token,
        "csrf_token": csrf_token,
        "token_type": "bearer",
        "expires_in": auth.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        "message": "Token refreshed",
    }


@app.post("/logout")
def logout(
    request: Request,
    response: Response,
    logout_data: schemas.LogoutRequest | None = None,
    db: Session = Depends(auth.get_db),
):
    token = auth.get_token_from_cookie_or_header(request)
    if token:
        auth.blacklist_access_token(token, db)

    refresh_token_value = request.cookies.get("refresh_token")
    if not refresh_token_value and logout_data and logout_data.refresh_token:
        refresh_token_value = logout_data.refresh_token
    if refresh_token_value:
        auth.revoke_refresh_token(refresh_token_value, db)

    _clear_auth_cookie(response, "access_token")
    _clear_auth_cookie(response, "refresh_token")
    _clear_auth_cookie(response, CSRF_COOKIE_NAME)
    return {"message": "Logged out successfully"}


@app.get("/csrf")
def get_csrf_token(request: Request, response: Response, _=Depends(auth.get_current_user)):
    csrf_token = request.cookies.get(CSRF_COOKIE_NAME) or secrets.token_urlsafe(32)
    _set_csrf_cookie(response, csrf_token, auth.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60)
    return {"csrf_token": csrf_token}


@app.get("/me", response_model=schemas.UserOut)
def get_me(current=Depends(auth.get_current_user), db: Session = Depends(auth.get_db)):
    user = db.query(models.User).filter(models.User.user_name == current["user_name"]).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@app.get("/groups", response_model=list[schemas.GroupOut])
def list_groups(current=Depends(auth.get_current_user), db: Session = Depends(auth.get_db)):
    auth.require_editor_or_admin(current)
    return db.query(models.Group).order_by(models.Group.name.asc()).all()


@app.put("/update_user", response_model=schemas.UserOut)
def update_user(
    payload: schemas.UserSelfUpdate,
    current=Depends(auth.get_current_user),
    db: Session = Depends(auth.get_db),
):
    user_db = db.query(models.User).filter(models.User.user_name == current["user_name"]).first()
    if not user_db:
        raise HTTPException(status_code=404, detail="User not found")

    update_data = payload.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(user_db, key, value)
    db.commit()
    db.refresh(user_db)
    return user_db


@app.put("/change_password")
def change_password(
    data: schemas.PasswordUpdateRequest,
    current=Depends(auth.get_current_user),
    db: Session = Depends(auth.get_db),
):
    user_db = db.query(models.User).filter(models.User.user_name == current["user_name"]).first()
    if not user_db:
        raise HTTPException(status_code=404, detail="User not found")
    if user_db.id != data.user_id:
        raise HTTPException(status_code=403, detail="You are not allowed to update this user")
    if not auth.verify_password(data.old_password, user_db.user_pw):
        raise HTTPException(status_code=403, detail="Wrong password")
    is_valid, error_msg = validate_password(data.new_password)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error_msg)
    if auth.verify_password(data.new_password, user_db.user_pw):
        raise HTTPException(status_code=403, detail="Old and new passwords are identical")

    user_db.user_pw = auth.hash_pw(data.new_password)
    db.commit()
    return {"message": "Password updated successfully"}


@app.get("/user_list", response_model=list[schemas.UserListElem])
def get_users_list(_=Depends(auth.get_current_user), db: Session = Depends(auth.get_db)):
    return (
        db.query(models.User)
        .filter(models.User.musician == True, models.User.status == "active")
        .order_by(models.User.clear_name.asc())
        .all()
    )


@app.get("/scores/{score_id}/document")
def get_score_document(score_id: int, current=Depends(auth.get_current_user), db: Session = Depends(auth.get_db)):
    score = db.query(models.Score).filter(models.Score.id == score_id).first()
    if not score:
        raise HTTPException(status_code=404, detail="Score not found")
    if not auth.user_can_access_document(db, current, "score", score_id):
        raise HTTPException(status_code=403, detail="You do not have access to this document")

    file_path = None
    if score.file_hash:
        try:
            digest = sync_service.validate_sha256(score.file_hash)
        except sync_service.SyncImportError as exc:
            raise HTTPException(status_code=503, detail="Score file metadata is invalid") from exc
        file_path = sync_service.local_score_blob(db, digest)
        if file_path is None and sync_service.pdf_sync_strategy() != "metadata":
            try:
                if sync_worker.fetch_blob_from_peers(digest):
                    file_path = sync_service.local_score_blob(db, digest)
            except RuntimeError as exc:
                logger.warning("Could not retrieve score %s from peers: %s", score_id, exc)
    elif score.storage_path:
        try:
            file_path = _resolve_pdf_path(score.storage_path)
        except HTTPException as exc:
            if exc.status_code != 404:
                raise

    if file_path is None:
        raise HTTPException(status_code=503, detail="Score PDF is not available on this instance")
    return FileResponse(
        path=file_path,
        media_type="application/pdf",
        filename=file_path.name,
        content_disposition_type="inline",
    )


@app.get("/version", response_model=dict)
def get_version():
    return {"release": "0.1.0", "title": "libre-stage user service"}


@app.get("/health")
def health_check(db: Session = Depends(auth.get_db)):
    db.execute(text("SELECT 1"))
    active_peers = db.scalars(
        select(models.SyncPeer).where(models.SyncPeer.active.is_(True))
    ).all()
    cursors = {
        cursor.peer_id: cursor
        for cursor in db.scalars(select(models.SyncCursor)).all()
    }
    now = datetime.now(timezone.utc)
    pending_changes = 0
    last_successful_sync = None
    peers_without_success = 0
    for peer in active_peers:
        cursor = cursors.get(peer.peer_id)
        pending_changes += db.scalar(
            select(func.count())
            .select_from(models.ChangeLog)
            .where(models.ChangeLog.seq > (cursor.last_sent if cursor else 0))
        ) or 0
        last_success = cursor.last_success_at if cursor else None
        if last_success is None:
            peers_without_success += 1
            continue
        if last_success.tzinfo is None:
            last_success = last_success.replace(tzinfo=timezone.utc)
        if last_successful_sync is None or last_success < last_successful_sync:
            last_successful_sync = last_success

    sync_age_seconds = (
        max(0, int((now - last_successful_sync).total_seconds()))
        if last_successful_sync is not None
        else None
    )
    return {
        "status": "ok",
        "database": "connected",
        "sync": {
            "enabled": sync_worker.sync_enabled(),
            "active_peers": len(active_peers),
            "pending_changes": pending_changes,
            "last_successful_sync": last_successful_sync,
            "last_sync_age_seconds": sync_age_seconds,
            "peers_without_success": peers_without_success,
        },
    }


app.include_router(admin.router)
app.include_router(password_reset.router)
app.include_router(sync_router)
