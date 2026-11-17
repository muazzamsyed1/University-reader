"""
app/core/security.py
====================
Password hashing, login tokens (JWT) and at-rest encryption for stored API keys.

Nothing here talks to the database — these are pure crypto helpers used by the
auth module and the API-key store.
"""
from __future__ import annotations

import base64
import hashlib
from datetime import datetime, timedelta, timezone

import bcrypt
from cryptography.fernet import Fernet, InvalidToken
from jose import JWTError, jwt

from app.core.config import settings


# ── Passwords ────────────────────────────────────────────────────
# Use the bcrypt library directly (avoids passlib's broken version detection
# with bcrypt >= 4.1). bcrypt only considers the first 72 bytes of a password.
def _pw_bytes(plain: str) -> bytes:
    return plain.encode("utf-8")[:72]


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(_pw_bytes(plain), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(_pw_bytes(plain), hashed.encode("utf-8"))
    except Exception:
        return False


# ── Login tokens (JWT) ───────────────────────────────────────────
def create_access_token(subject: str, extra: dict | None = None) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": subject,
        "iat": now,
        "exp": now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    except JWTError:
        return None


# ── API-key encryption at rest (Fernet / AES) ────────────────────
def _fernet() -> Fernet:
    """A stable Fernet key, from FERNET_KEY if given, else derived from SECRET_KEY."""
    raw = settings.FERNET_KEY.strip()
    if raw:
        key = raw.encode()
    else:
        digest = hashlib.sha256(settings.SECRET_KEY.encode("utf-8")).digest()
        key = base64.urlsafe_b64encode(digest)
    return Fernet(key)


def encrypt_secret(plain: str) -> str:
    return _fernet().encrypt(plain.encode("utf-8")).decode("utf-8")


def decrypt_secret(token: str) -> str | None:
    try:
        return _fernet().decrypt(token.encode("utf-8")).decode("utf-8")
    except (InvalidToken, Exception):
        return None


def mask_secret(plain: str) -> str:
    """Show only the last 4 characters of a key, never the whole thing."""
    if not plain:
        return ""
    tail = plain[-4:]
    return f"••••••••{tail}"
