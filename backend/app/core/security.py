"""Password hashing and JWT tokens (carried over from the old CRM, moved to PyJWT)."""

from datetime import UTC, datetime, timedelta
from typing import Any

import bcrypt
import jwt

from app.config import get_settings


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except ValueError:
        return False


def _create_token(claims: dict[str, Any], token_type: str, expires: timedelta) -> str:
    s = get_settings()
    payload = {**claims, "type": token_type, "exp": datetime.now(UTC) + expires}
    return jwt.encode(payload, s.jwt_secret_key, algorithm=s.jwt_algorithm)


def create_access_token(user_id: str, tenant_id: str) -> str:
    minutes = get_settings().access_token_minutes
    return _create_token({"sub": user_id, "tid": tenant_id}, "access", timedelta(minutes=minutes))


def create_refresh_token(user_id: str, tenant_id: str) -> str:
    days = get_settings().refresh_token_days
    return _create_token({"sub": user_id, "tid": tenant_id}, "refresh", timedelta(days=days))


def verify_token(token: str, token_type: str) -> dict[str, Any] | None:
    s = get_settings()
    try:
        payload = jwt.decode(token, s.jwt_secret_key, algorithms=[s.jwt_algorithm])
    except jwt.PyJWTError:
        return None
    if payload.get("type") != token_type or not payload.get("sub") or not payload.get("tid"):
        return None
    return payload
