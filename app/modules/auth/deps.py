"""FastAPI dependencies: current admin + country scoping."""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.core.security import decode_access_token
from app.modules.countries.models import AdminUser, Country

_bearer = HTTPBearer(auto_error=False)


@dataclass
class CurrentAdmin:
    id: str
    email: str
    role: str
    full_name: str | None
    country_codes: list[str]          # codes this admin may manage ([] with is_super=True = all)
    is_super: bool

    def may_access(self, code: str | None) -> bool:
        if self.is_super:
            return True
        return bool(code) and code.upper() in self.country_codes


async def get_current_admin(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: AsyncSession = Depends(get_db),
) -> CurrentAdmin:
    if creds is None or not creds.credentials:
        raise HTTPException(401, "Not authenticated.")
    payload = decode_access_token(creds.credentials)
    if not payload or "sub" not in payload:
        raise HTTPException(401, "Session expired — please sign in again.")
    admin = (await db.execute(
        select(AdminUser).options(selectinload(AdminUser.countries))
        .where(AdminUser.email == payload["sub"]))).scalar_one_or_none()
    if not admin or not admin.is_active:
        raise HTTPException(401, "Account not found or disabled.")

    is_super = admin.role == "SUPER_ADMIN"
    if is_super:
        codes: list[str] = []
    else:
        ids = [ac.country_id for ac in admin.countries]
        codes = [c.code.upper() for c in (await db.execute(
            select(Country).where(Country.id.in_(ids)))).scalars().all()] if ids else []
    return CurrentAdmin(id=admin.id, email=admin.email, role=admin.role,
                        full_name=admin.full_name, country_codes=codes, is_super=is_super)


async def require_super_admin(admin: CurrentAdmin = Depends(get_current_admin)) -> CurrentAdmin:
    if not admin.is_super:
        raise HTTPException(403, "Super-admin access required.")
    return admin
