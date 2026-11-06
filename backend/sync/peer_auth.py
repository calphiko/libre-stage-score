from __future__ import annotations

import ipaddress
import os
import re
import secrets
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import HTTPException, Request
from jwt.exceptions import InvalidTokenError
from sqlalchemy.orm import Session

from backend import models
from backend.sync import service


_KEY_REFERENCE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_TOKEN_LIFETIME_SECONDS = 300
_RATE_WINDOW_SECONDS = 60
_rate_lock = threading.Lock()
_request_times: dict[str, deque[float]] = {}


def key_references(reference: str | None) -> tuple[str, ...]:
    if not reference:
        return ()
    references = tuple(item.strip() for item in reference.split(","))
    if (
        any(not _KEY_REFERENCE_RE.fullmatch(item) for item in references)
        or len(set(references)) != len(references)
    ):
        raise RuntimeError("Peer credentials_reference must contain unique environment variable names")
    return references


def _key(reference: str) -> str:
    value = os.getenv(reference)
    if value is None or len(value.encode("utf-8")) < 32:
        raise RuntimeError(f"Peer signing key {reference!r} must contain at least 32 bytes")
    return value


def signing_key(reference: str | None) -> tuple[str, str]:
    references = key_references(reference)
    if not references:
        raise RuntimeError("Peer credentials_reference is not configured")
    return references[0], _key(references[0])


def validation_keys(reference: str | None) -> list[str]:
    references = key_references(reference)
    if not references:
        raise RuntimeError("Peer credentials_reference is not configured")
    return [_key(name) for name in references]


def create_token(peer_id: str, node_id: str, credentials_reference: str | None) -> str:
    _key_id, secret = signing_key(credentials_reference)
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "iss": node_id,
            "sub": node_id,
            "aud": peer_id,
            "iat": now,
            "nbf": now,
            "exp": now + timedelta(seconds=_TOKEN_LIFETIME_SECONDS),
            "jti": secrets.token_urlsafe(18),
            "typ": "sync-peer",
        },
        secret,
        algorithm="HS256",
    )


def _is_secure_transport(request: Request) -> bool:
    if request.url.scheme == "https":
        return True
    host = request.client.host if request.client else ""
    if host.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _enforce_rate_limit(peer_id: str) -> None:
    try:
        limit = int(os.getenv("SYNC_RATE_LIMIT_PER_MINUTE", "120"))
    except ValueError as exc:
        raise RuntimeError("SYNC_RATE_LIMIT_PER_MINUTE must be a positive integer") from exc
    if limit <= 0:
        raise RuntimeError("SYNC_RATE_LIMIT_PER_MINUTE must be a positive integer")
    now = time.monotonic()
    with _rate_lock:
        times = _request_times.setdefault(peer_id, deque())
        while times and times[0] <= now - _RATE_WINDOW_SECONDS:
            times.popleft()
        if len(times) >= limit:
            retry_after = max(1, int(_RATE_WINDOW_SECONDS - (now - times[0])))
            raise HTTPException(
                status_code=429,
                detail="Peer sync rate limit exceeded",
                headers={"Retry-After": str(retry_after)},
            )
        times.append(now)


def authenticate_peer(
    request: Request,
    authorization: str | None,
    x_sync_node_id: str | None,
    db: Session,
) -> models.SyncPeer:
    if not _is_secure_transport(request):
        raise HTTPException(status_code=400, detail="Sync endpoints require HTTPS")
    if not authorization or not x_sync_node_id:
        raise HTTPException(status_code=401, detail="Peer authentication required")
    scheme, separator, supplied_token = authorization.partition(" ")
    if not separator or scheme.casefold() != "bearer" or not supplied_token:
        raise HTTPException(status_code=401, detail="Peer authentication required")
    try:
        peer_id = str(uuid.UUID(x_sync_node_id))
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Peer authentication failed") from exc
    peer = db.get(models.SyncPeer, peer_id)
    if peer is None or not peer.active or not peer.credentials_reference:
        raise HTTPException(status_code=401, detail="Peer authentication failed")
    try:
        local_node_id = service.get_local_node_id(db)
        if local_node_id == "unconfigured":
            raise RuntimeError("Local node identity is not configured")
        keys = validation_keys(peer.credentials_reference)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=503, detail="Peer authentication is not configured") from exc
    claims = None
    for key in keys:
        try:
            claims = jwt.decode(
                supplied_token,
                key,
                algorithms=["HS256"],
                audience=local_node_id,
                issuer=peer.peer_id,
                leeway=30,
                options={"require": ["exp", "iat", "nbf", "iss", "aud", "sub", "jti"]},
            )
            break
        except InvalidTokenError:
            continue
    if (
        claims is None
        or claims.get("sub") != peer.peer_id
        or claims.get("typ") != "sync-peer"
        or claims.get("iss") != peer.peer_id
    ):
        raise HTTPException(status_code=401, detail="Peer authentication failed")
    _enforce_rate_limit(peer.peer_id)
    return peer
