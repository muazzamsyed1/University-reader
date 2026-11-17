"""
app/modules/countries/keystore.py
=================================
Secure storage and retrieval of provider API keys for AI extraction.

Keys are encrypted at rest (Fernet, see app.core.security) and never returned
in plaintext over the API — only a masked form (last 4 chars) is shown.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import decrypt_secret, encrypt_secret, mask_secret
from app.modules.countries.models import ApiKey

_DEFAULT_MODEL = {
    "gemini": "gemini-3-flash-preview",
    "openai": "gpt-4o-mini",            # ChatGPT
    "claude": "claude-haiku-4-5",       # Anthropic
    "llama": "llama-3.3-70b-versatile", # served via Groq by default
}
_PROVIDERS = tuple(_DEFAULT_MODEL.keys())


async def list_keys(db: AsyncSession) -> list[dict]:
    rows = (await db.execute(select(ApiKey).order_by(ApiKey.created_at.desc()))).scalars().all()
    return [{"id": k.id, "provider": k.provider, "label": k.label, "masked": k.masked,
             "model": k.model, "is_active": k.is_active, "created_by": k.created_by,
             "created_at": k.created_at, "updated_at": k.updated_at} for k in rows]


async def add_key(db: AsyncSession, provider: str, key: str, label: str | None,
                  model: str | None, created_by: str) -> dict:
    provider = (provider or "").lower().strip()
    if provider == "chatgpt":
        provider = "openai"
    if provider in ("anthropic",):
        provider = "claude"
    if provider not in _PROVIDERS:
        raise HTTPException(422, f"provider must be one of: {', '.join(_PROVIDERS)}.")
    key = (key or "").strip()
    if len(key) < 8:
        raise HTTPException(422, "That API key looks too short.")
    # new key becomes the active one for its provider; deactivate the others
    for other in (await db.execute(
            select(ApiKey).where(ApiKey.provider == provider, ApiKey.is_active.is_(True)))).scalars():
        other.is_active = False
    row = ApiKey(provider=provider, label=label or f"{provider} key",
                 encrypted_key=encrypt_secret(key), masked=mask_secret(key),
                 model=model or _DEFAULT_MODEL[provider], is_active=True, created_by=created_by)
    db.add(row)
    await db.commit()
    return {"ok": True, "id": row.id, "message": f"{provider} key saved and set active."}


async def set_active(db: AsyncSession, key_id: str) -> dict:
    row = (await db.execute(select(ApiKey).where(ApiKey.id == key_id))).scalar_one_or_none()
    if not row:
        raise HTTPException(404, "API key not found.")
    for other in (await db.execute(
            select(ApiKey).where(ApiKey.provider == row.provider))).scalars():
        other.is_active = (other.id == key_id)
    row.updated_at = datetime.now(timezone.utc)
    await db.commit()
    return {"ok": True, "message": "Active key updated."}


async def delete_key(db: AsyncSession, key_id: str) -> dict:
    row = (await db.execute(select(ApiKey).where(ApiKey.id == key_id))).scalar_one_or_none()
    if not row:
        raise HTTPException(404, "API key not found.")
    await db.delete(row)
    await db.commit()
    return {"ok": True, "message": "API key deleted."}


def _looks_real(key: str | None) -> bool:
    """Reject empty / placeholder keys (e.g. 'you gemini api key', 'change-me')."""
    if not key:
        return False
    k = key.strip()
    if len(k) < 20 or " " in k:
        return False
    low = k.lower()
    return not any(bad in low for bad in ("your", "change", "placeholder", "xxxx", "<"))


async def get_active_config(db: AsyncSession) -> dict | None:
    """Resolve the active AI config. Prefers the configured default provider, but
    will use ANY active stored key (so adding a Claude/Llama/ChatGPT key just works).
    Falls back to .env. Returns None if nothing usable is configured."""
    preferred = settings.AI_PROVIDER.lower().strip() or "gemini"
    actives = (await db.execute(
        select(ApiKey).where(ApiKey.is_active.is_(True))
        .order_by(ApiKey.updated_at.desc()))).scalars().all()
    # order candidates: preferred provider first, then most-recent others
    actives.sort(key=lambda r: (r.provider != preferred,))
    for row in actives:
        key = decrypt_secret(row.encrypted_key)
        if key and key.strip():
            return {"provider": row.provider, "key": key,
                    "model": row.model or _DEFAULT_MODEL.get(row.provider, "")}
    # fall back to .env (ignoring obvious placeholders)
    for prov, envkey, model in (
        ("gemini", settings.GEMINI_API_KEY, settings.GEMINI_MODEL),
        ("openai", settings.OPENAI_API_KEY, settings.OPENAI_MODEL),
    ):
        if _looks_real(envkey):
            return {"provider": prov, "key": envkey.strip(), "model": model}
    return None
