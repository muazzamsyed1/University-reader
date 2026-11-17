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

    # Any run still marked RUNNING at startup is an orphan (its task died with the
    # previous process) — mark it FAILED so it doesn't show 'running' forever.
    try:
        from app.modules.countries.reader import fail_stale_runs
        async with AsyncSessionLocal() as db:
            n = await fail_stale_runs(db, older_than_minutes=0)
        if n:
            logger.info("Cleared %s orphaned reader run(s) from a previous restart.", n)
    except Exception as exc:
        logger.warning("Could not clear orphaned runs: %s", exc)

    async with AsyncSessionLocal() as session:
        count = (await session.execute(select(func.count()).select_from(Country))).scalar_one()

    if count == 0:
        # Empty DB (e.g. a fresh cloud deploy): auto-load from the Excel sheets if present.
        await _auto_import()
    else:
        logger.info("Database ready: %s countries present.", count)


async def _auto_import() -> None:
    """Populate an empty database from the Excel sheets in EXCEL_DIR, once.
    Safe no-op when the folder has no sheets or import is disabled."""
    import glob
    import os

    if not getattr(settings, "AUTO_IMPORT_ON_EMPTY", True):
        logger.warning("Database empty and AUTO_IMPORT_ON_EMPTY is off — "
                       "build it with: python database/rebuild.py")
        return
    folder = settings.EXCEL_DIR
    sheets = glob.glob(os.path.join(folder, "*.xlsx")) if folder and os.path.isdir(folder) else []
    if not sheets:
        logger.warning("Database empty and no Excel sheets found in %s — "
                       "add universities via the dashboard or run the rebuild.", folder)
        return
    try:
        from app.modules.countries.excel_import import import_all
        async with AsyncSessionLocal() as db:
            res = await import_all(db, folder=folder, wipe=False)
        logger.info("Auto-imported data: %s countries, %s universities, %s programmes.",
                    res.get("countries"), res.get("universities"), res.get("programs"))
    except Exception as exc:                       # never block startup
        logger.error("Auto-import failed (%s); start with an empty DB and import later.", exc)
