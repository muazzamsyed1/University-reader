"""Auth / admin business logic — login, admin CRUD, country assignment."""
from __future__ import annotations

import re
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.security import create_access_token, hash_password, verify_password
from app.modules.countries.models import AdminCountry, AdminUser, Country

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _norm_email(email: str) -> str:
    e = (email or "").strip().lower()
    if not _EMAIL_RE.match(e):
        raise HTTPException(422, "Please enter a valid e-mail address.")
    return e


def _ensure_whitelisted(email: str) -> None:
    # The super-admin may add any gmail / e-mail address. A whitelist is only
    # enforced when ADMIN_WHITELIST is explicitly set AND restriction is on.
    wl = settings.allowed_admin_emails
    if settings.RESTRICT_ADMIN_EMAILS and wl and email not in wl:
        raise HTTPException(
            403, "This e-mail is not on the allowed admin list. "
                 "Add it to ADMIN_WHITELIST or turn off RESTRICT_ADMIN_EMAILS.")


async def _codes_to_countries(db: AsyncSession, codes: list[str]) -> list[Country]:
    codes = [c.strip().upper() for c in codes if c.strip()]
    if not codes:
        return []
    rows = (await db.execute(select(Country).where(Country.code.in_(codes)))).scalars().all()
    found = {c.code for c in rows}
    missing = [c for c in codes if c not in found]
    if missing:
        raise HTTPException(422, f"Unknown country code(s): {', '.join(missing)}")
    return rows


async def authenticate(db: AsyncSession, email: str, password: str) -> dict:
    email = _norm_email(email)
    admin = (await db.execute(
        select(AdminUser).options(selectinload(AdminUser.countries))
        .where(AdminUser.email == email))).scalar_one_or_none()
    if not admin or not admin.is_active or not verify_password(password, admin.password_hash):
        raise HTTPException(401, "Invalid e-mail or password.")
    admin.last_login_at = datetime.now(timezone.utc)
    await db.commit()

    codes = await _country_codes_for(db, admin)
    token = create_access_token(admin.email, {"role": admin.role})
    return {"access_token": token, "email": admin.email, "role": admin.role,
            "full_name": admin.full_name, "countries": codes}


async def _country_codes_for(db: AsyncSession, admin: AdminUser) -> list[str]:
    if admin.role == "SUPER_ADMIN":
        return [c.code for c in (await db.execute(
            select(Country).order_by(Country.name))).scalars().all()]
    ids = [ac.country_id for ac in admin.countries]
    if not ids:
        return []
    return [c.code for c in (await db.execute(
        select(Country).where(Country.id.in_(ids)).order_by(Country.name))).scalars().all()]


async def admin_detail(db: AsyncSession, admin_id: str) -> dict:
    admin = (await db.execute(
        select(AdminUser).options(selectinload(AdminUser.countries))
        .where(AdminUser.id == admin_id))).scalar_one_or_none()
    if not admin:
        raise HTTPException(404, "Admin not found.")
    all_countries = {c.id: c for c in (await db.execute(select(Country))).scalars().all()}
    countries = [all_countries[ac.country_id] for ac in admin.countries
                 if ac.country_id in all_countries]
    return {"id": admin.id, "email": admin.email, "full_name": admin.full_name,
            "role": admin.role, "is_active": admin.is_active,
            "last_login_at": admin.last_login_at, "created_at": admin.created_at,
            "countries": countries}


async def create_admin(db: AsyncSession, data: dict, created_by: str) -> dict:
    email = _norm_email(data["email"])
    _ensure_whitelisted(email)
    if (await db.execute(select(AdminUser).where(AdminUser.email == email))).scalar_one_or_none():
        raise HTTPException(409, "An admin with that e-mail already exists.")
    role = data.get("role", "COUNTRY_ADMIN").upper()
    if role not in ("SUPER_ADMIN", "COUNTRY_ADMIN"):
        raise HTTPException(422, "role must be SUPER_ADMIN or COUNTRY_ADMIN.")
    admin = AdminUser(email=email, full_name=data.get("full_name"), role=role,
                      password_hash=hash_password(data["password"]), is_active=True)
    db.add(admin)
    await db.flush()
    if role == "COUNTRY_ADMIN":
        for c in await _codes_to_countries(db, data.get("country_codes", [])):
            db.add(AdminCountry(admin_id=admin.id, country_id=c.id))
    await db.commit()
    return await admin_detail(db, admin.id)


async def update_admin(db: AsyncSession, admin_id: str, data: dict) -> dict:
    admin = (await db.execute(
        select(AdminUser).options(selectinload(AdminUser.countries))
        .where(AdminUser.id == admin_id))).scalar_one_or_none()
    if not admin:
        raise HTTPException(404, "Admin not found.")
    if data.get("full_name") is not None:
        admin.full_name = data["full_name"]
    if data.get("password"):
        admin.password_hash = hash_password(data["password"])
    if data.get("is_active") is not None:
        admin.is_active = data["is_active"]
    if data.get("country_codes") is not None and admin.role != "SUPER_ADMIN":
        for ac in list(admin.countries):
            await db.delete(ac)
        await db.flush()
        for c in await _codes_to_countries(db, data["country_codes"]):
            db.add(AdminCountry(admin_id=admin.id, country_id=c.id))
    await db.commit()
    return await admin_detail(db, admin_id)


async def delete_admin(db: AsyncSession, admin_id: str, acting_email: str) -> None:
    admin = (await db.execute(
        select(AdminUser).where(AdminUser.id == admin_id))).scalar_one_or_none()
    if not admin:
        raise HTTPException(404, "Admin not found.")
    if admin.email == acting_email:
        raise HTTPException(400, "You cannot delete your own account.")
    if admin.role == "SUPER_ADMIN":
        supers = (await db.execute(
            select(AdminUser).where(AdminUser.role == "SUPER_ADMIN"))).scalars().all()
        if len(supers) <= 1:
            raise HTTPException(400, "Cannot delete the only super-admin.")
    await db.delete(admin)
    await db.commit()


async def list_admins(db: AsyncSession) -> list[dict]:
    admins = (await db.execute(
        select(AdminUser).options(selectinload(AdminUser.countries))
        .order_by(AdminUser.created_at))).scalars().all()
    # resolve assigned countries for each admin
    all_countries = {c.id: c for c in (await db.execute(select(Country))).scalars().all()}
    out = []
    for a in admins:
        countries = [all_countries[ac.country_id] for ac in a.countries
                     if ac.country_id in all_countries]
        out.append({"id": a.id, "email": a.email, "full_name": a.full_name, "role": a.role,
                    "is_active": a.is_active, "last_login_at": a.last_login_at,
                    "created_at": a.created_at, "countries": countries})
    return out
