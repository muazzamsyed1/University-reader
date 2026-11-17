"""
app/core/init_db.py
===================
Ensure tables exist and seed the super-admin on startup.

create_all is a no-op when tables already exist. The super-admin is seeded from
ADMIN_SUPERUSER_EMAIL / ADMIN_SUPERUSER_PASSWORD only when that e-mail is in the
ADMIN_WHITELIST and a password is set — otherwise seeding is skipped (safe).
"""
from __future__ import annotations

import logging

from sqlalchemy import func, select

from app.core.config import settings
from app.core.database import AsyncSessionLocal, engine
from app.core.security import hash_password
from app.modules.countries.models import AdminUser, Base, Country

logger = logging.getLogger("requirement_reader")


async def _seed_super_admin() -> None:
    email = settings.ADMIN_SUPERUSER_EMAIL.strip().lower()
    password = settings.ADMIN_SUPERUSER_PASSWORD
    if not email or not password:
        logger.info("Super-admin not seeded (set ADMIN_SUPERUSER_EMAIL + "
                    "ADMIN_SUPERUSER_PASSWORD in .env to enable login).")
        return
    async with AsyncSessionLocal() as db:
        existing = (await db.execute(
            select(AdminUser).where(AdminUser.email == email))).scalar_one_or_none()
        if existing:
            return
        db.add(AdminUser(email=email, full_name="Super Admin", role="SUPER_ADMIN",
                         password_hash=hash_password(password), is_active=True))
        await db.commit()
        logger.info("Seeded super-admin: %s", email)


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    await _seed_super_admin()

    async with AsyncSessionLocal() as session:
        count = (await session.execute(select(func.count()).select_from(Country))).scalar_one()
        if count == 0:
            logger.warning(
                "Database has no countries. Build it with:  python database/rebuild.py")
        else:
            logger.info("Database ready: %s countries present.", count)
