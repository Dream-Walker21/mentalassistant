"""JWT access token issuance and verification for data_service.

HS256 + JWT_SECRET.  Access tokens are stateless (not stored in DB);
refresh tokens are stored in ``auth_sessions`` via :class:`DataStore`.

This module is pure computation: it depends only on PyJWT and environment
variables, not on Flask or the data layer, so it can be unit-tested in
isolation.
"""

from __future__ import annotations

import os
import time
from uuid import uuid4

import jwt

JWT_ALGORITHM = "HS256"


def _secret() -> str:
    return os.getenv("JWT_SECRET", "").strip()


def _expire_minutes() -> int:
    return int(os.getenv("JWT_ACCESS_EXPIRE_MINUTES", "30"))


def create_access_token(user_id: str) -> str:
    secret = _secret()
    if not secret:
        raise RuntimeError("JWT_SECRET is not configured")
    now = int(time.time())
    payload = {
        "sub": user_id,
        "iat": now,
        "exp": now + _expire_minutes() * 60,
        "jti": uuid4().hex,
    }
    return jwt.encode(payload, secret, algorithm=JWT_ALGORITHM)


def verify_access_token(token: str) -> str | None:
    secret = _secret()
    if not secret or not token:
        return None
    try:
        payload = jwt.decode(token, secret, algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError:
        return None
    user_id = payload.get("sub")
    if not isinstance(user_id, str):
        return None
    return user_id
