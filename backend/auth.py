import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from jwt.exceptions import InvalidTokenError as JWTError
from sqlalchemy.orm import Session

from backend import database, models


SECRET_KEY = os.getenv("SECRET_KEY", "change-me-in-production-please-use-env-secret")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 15
REFRESH_TOKEN_EXPIRE_DAYS = 90
RESET_PASSWORD_TOKEN_EXPIRE_MINUTES = 15
MAX_ACTIVE_SESSIONS = 5

oauth2_password_reset_scheme = OAuth2PasswordBearer(tokenUrl="password_reset/new_password", auto_error=False)


def get_token_from_cookie_or_header(request: Request) -> str | None:
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        return auth_header[7:]
    cookie_token = request.cookies.get("access_token")
    if cookie_token:
        return cookie_token.removeprefix("Bearer ")
    return None


def hash_pw(plain_pw: str) -> str:
    return bcrypt.hashpw(plain_pw.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    if not hashed.startswith(("$2b$", "$2a$", "$2y$")):
        return False
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        return False


def authenticate_user(db: Session, username: str, password: str) -> models.User | None:
    user = db.query(models.User).filter(models.User.user_name == username).first()
    if not user:
        return None
    if not verify_password(password, user.user_pw):
        return None
    if user.status == "deactivated":
        return None
    return user


def create_access_token(data: dict) -> str:
    payload = data.copy()
    payload["exp"] = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def _create_refresh_token_record(user_id: int, db: Session) -> str:
    now = datetime.now(timezone.utc)
    active_tokens = (
        db.query(models.RefreshToken)
        .filter(
            models.RefreshToken.user_id == user_id,
            models.RefreshToken.revoked == False,
            models.RefreshToken.expires_at > now,
        )
        .order_by(models.RefreshToken.created_at.asc())
        .all()
    )

    if len(active_tokens) >= MAX_ACTIVE_SESSIONS:
        tokens_to_revoke = active_tokens[: len(active_tokens) - MAX_ACTIVE_SESSIONS + 1]
        for old in tokens_to_revoke:
            old.revoked = True

    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    db.add(
        models.RefreshToken(
            token_hash=token_hash,
            user_id=user_id,
            expires_at=now + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS),
        )
    )
    return token


def create_refresh_token(user_id: int, db: Session) -> str:
    token = _create_refresh_token_record(user_id, db)
    db.commit()
    return token


def revoke_all_refresh_tokens_for_user(user_id: int, db: Session) -> None:
    db.query(models.RefreshToken).filter(
        models.RefreshToken.user_id == user_id,
        models.RefreshToken.revoked == False,
    ).update({models.RefreshToken.revoked: True}, synchronize_session=False)
    db.commit()


def rotate_refresh_token(token: str, db: Session) -> tuple[models.User, str]:
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    db_token = db.query(models.RefreshToken).filter(models.RefreshToken.token_hash == token_hash).first()
    if not db_token:
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    user = db.query(models.User).filter(models.User.id == db_token.user_id).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    if db_token.revoked:
        revoke_all_refresh_tokens_for_user(user.id, db)
        raise HTTPException(status_code=401, detail="Refresh token reuse detected")

    if db_token.expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
        db_token.revoked = True
        db.commit()
        raise HTTPException(status_code=401, detail="Refresh token has expired")

    if user.status == "deactivated":
        raise HTTPException(status_code=401, detail="Account is deactivated")

    db_token.revoked = True
    new_refresh_token = _create_refresh_token_record(user.id, db)
    db.commit()
    return user, new_refresh_token


def revoke_refresh_token(token: str, db: Session) -> None:
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    db_token = db.query(models.RefreshToken).filter(models.RefreshToken.token_hash == token_hash).first()
    if db_token:
        db_token.revoked = True
        db.commit()


def blacklist_access_token(token: str, db: Session) -> None:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        return

    exp = payload.get("exp")
    if not exp:
        return

    token_hash = hashlib.sha256(token.encode()).hexdigest()
    expires_at = datetime.fromtimestamp(exp, tz=timezone.utc)
    if expires_at <= datetime.now(timezone.utc):
        return

    db.add(models.TokenBlacklist(token_hash=token_hash, expires_at=expires_at))
    db.commit()


def create_password_reset_token(user_name: str) -> str:
    exp = datetime.now(timezone.utc) + timedelta(minutes=RESET_PASSWORD_TOKEN_EXPIRE_MINUTES)
    return jwt.encode({"sub": user_name, "scope": "password_reset_token", "exp": exp}, SECRET_KEY, algorithm=ALGORITHM)


def get_db():
    db = database.SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_user(request: Request, db: Session = Depends(get_db)) -> dict:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
    )
    token = get_token_from_cookie_or_header(request)
    if not token:
        raise credentials_exception

    token_hash = hashlib.sha256(token.encode()).hexdigest()
    if db.query(models.TokenBlacklist).filter(models.TokenBlacklist.token_hash == token_hash).first():
        raise HTTPException(status_code=401, detail="Token has been revoked")

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError as exc:
        raise credentials_exception from exc

    username = payload.get("sub")
    user_group = payload.get("role")
    if username is None or user_group is None:
        raise credentials_exception

    user_db = db.query(models.User).filter(models.User.user_name == username).first()
    if user_db and user_db.status == "deactivated":
        raise HTTPException(status_code=401, detail="Account is deactivated")

    return {"user_name": username, "user_group": user_group}


def verify_password_reset_token(
    token: str = Depends(oauth2_password_reset_scheme), db: Session = Depends(get_db)
) -> tuple[str, str]:
    if not token:
        raise HTTPException(status_code=400, detail="Missing reset token")
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError as exc:
        raise HTTPException(status_code=400, detail="Invalid or expired token") from exc

    if payload.get("scope") != "password_reset_token":
        raise HTTPException(status_code=400, detail="Invalid token scope")
    username = payload.get("sub")
    if not username:
        raise HTTPException(status_code=400, detail="Invalid token subject")

    token_hash = hashlib.sha256(token.encode()).hexdigest()
    if db.query(models.UsedPasswordResetToken).filter(models.UsedPasswordResetToken.token_hash == token_hash).first():
        raise HTTPException(status_code=400, detail="Token has already been used")

    return username, token
