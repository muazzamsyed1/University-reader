"""Auth / admin REST endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.modules.auth import service
from app.modules.auth.deps import CurrentAdmin, get_current_admin, require_super_admin
from app.modules.auth.schemas import (
    AdminCreateIn, AdminOut, AdminUpdateIn, LoginIn, MeOut, TokenOut,
)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post("/login", response_model=TokenOut)
async def login(payload: LoginIn, db: AsyncSession = Depends(get_db)):
    return await service.authenticate(db, payload.email, payload.password)


@router.get("/me", response_model=MeOut)
async def me(admin: CurrentAdmin = Depends(get_current_admin)):
    return MeOut(email=admin.email, role=admin.role, full_name=admin.full_name,
                 countries=admin.country_codes, is_super=admin.is_super)


@router.get("/admins", response_model=list[AdminOut])
async def list_admins(_: CurrentAdmin = Depends(require_super_admin),
                      db: AsyncSession = Depends(get_db)):
    return await service.list_admins(db)


@router.post("/admins", response_model=AdminOut)
async def create_admin(payload: AdminCreateIn,
                       admin: CurrentAdmin = Depends(require_super_admin),
                       db: AsyncSession = Depends(get_db)):
    created = await service.create_admin(db, payload.model_dump(), created_by=admin.email)
    return created


@router.put("/admins/{admin_id}", response_model=AdminOut)
async def update_admin(admin_id: str, payload: AdminUpdateIn,
                       _: CurrentAdmin = Depends(require_super_admin),
                       db: AsyncSession = Depends(get_db)):
    return await service.update_admin(db, admin_id, payload.model_dump(exclude_unset=True))


@router.delete("/admins/{admin_id}")
async def delete_admin(admin_id: str,
                       admin: CurrentAdmin = Depends(require_super_admin),
                       db: AsyncSession = Depends(get_db)):
    await service.delete_admin(db, admin_id, acting_email=admin.email)
    return {"ok": True, "message": "Admin deleted."}
