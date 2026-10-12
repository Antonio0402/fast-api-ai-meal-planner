from __future__ import annotations

import threading
import time
from typing import Any
from uuid import UUID

import httpx
import jwt
from fastapi import Depends, Header
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import APIError
from app.db.session import get_db

JWKS_CACHE_SECONDS = 300
_jwks_lock = threading.Lock()
_jwks_cache: dict[str, Any] | None = None
_jwks_cache_expires_at = 0.0


def get_jwks(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    global _jwks_cache, _jwks_cache_expires_at
    if not settings.supabase_url:
        return {"keys": []}
    if authorization and settings.supabase_jwt_secret:
        try:
            if jwt.get_unverified_header(
                authorization.removeprefix("Bearer ").strip()
            ).get("alg") == "HS256":
                return {"keys": []}
        except jwt.PyJWTError:
            return {"keys": []}
    now = time.monotonic()
    if _jwks_cache is not None and now < _jwks_cache_expires_at:
        return _jwks_cache
    with _jwks_lock:
        now = time.monotonic()
        if _jwks_cache is not None and now < _jwks_cache_expires_at:
            return _jwks_cache
        url = (
            f"{settings.supabase_url.rstrip('/')}"
            "/auth/v1/.well-known/jwks.json"
        )
        try:
            response = httpx.get(url, timeout=5.0)
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise APIError(401, "UNAUTHORIZED", "Không thể xác minh token.") from exc
        if not isinstance(data, dict) or not isinstance(data.get("keys"), list):
            raise APIError(401, "UNAUTHORIZED", "JWKS không hợp lệ.")
        _jwks_cache = data
        _jwks_cache_expires_at = now + JWKS_CACHE_SECONDS
        return data


def get_current_user(
    authorization: str | None = Header(default=None),
    jwks: dict[str, Any] = Depends(get_jwks),
    db: Session = Depends(get_db),
) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise APIError(401, "UNAUTHORIZED", "Bearer token là bắt buộc.")
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise APIError(401, "UNAUTHORIZED", "Bearer token là bắt buộc.")
    issuer = f"{settings.supabase_url.rstrip('/')}/auth/v1"
    try:
        header = jwt.get_unverified_header(token)
        algorithm = header.get("alg")
        if algorithm == "HS256" and settings.supabase_jwt_secret:
            key: Any = settings.supabase_jwt_secret
            algorithms = ["HS256"]
        else:
            key_id = header.get("kid")
            jwk = next(
                (
                    item
                    for item in jwks.get("keys", [])
                    if isinstance(item, dict) and item.get("kid") == key_id
                ),
                None,
            )
            if jwk is None or algorithm not in {"RS256", "ES256"}:
                raise jwt.InvalidTokenError("No matching signing key.")
            key = jwt.PyJWK.from_dict(jwk).key
            algorithms = [algorithm]
        claims = jwt.decode(
            token,
            key=key,
            algorithms=algorithms,
            issuer=issuer,
            audience="authenticated",
            options={"require": ["exp", "iat", "sub", "iss", "aud"]},
        )
        user_id = str(UUID(claims["sub"]))
    except (jwt.PyJWTError, KeyError, TypeError, ValueError) as exc:
        raise APIError(401, "UNAUTHORIZED", "Token không hợp lệ hoặc đã hết hạn.") from exc
    with db.begin():
        db.execute(
            text(
                """
                INSERT INTO user_profiles (user_id, default_portions)
                VALUES (:user_id, 2)
                ON CONFLICT (user_id) DO NOTHING
                """
            ),
            {"user_id": user_id},
        )
    return user_id
