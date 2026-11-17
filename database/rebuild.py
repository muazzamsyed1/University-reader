"""
database/rebuild.py
===================
Fresh rebuild of the PostgreSQL database from the Excel workbooks.

  * DROPS and recreates the public schema (destroys ALL existing tables),
  * creates the v3 tables,
  * imports the admission workbooks (France Masters + Italy MS/BS),
  * seeds the super-admin from .env.

Run:  venv\\Scripts\\python.exe -m database.rebuild
"""
from __future__ import annotations

import asyncio
import sys

from sqlalchemy import text

from app.core.config import settings
from app.core.database import AsyncSessionLocal, engine
from app.core.init_db import _seed_super_admin
from app.modules.countries.excel_import import import_all
from app.modules.countries.models import Base


async def rebuild() -> None:
    print(f"Target DB: {settings.DB_NAME} @ {settings.DB_HOST}:{settings.DB_PORT}")
    is_pg = settings.DB_ENGINE.lower() in ("postgresql", "postgres")

    print("Dropping and recreating schema…")
    async with engine.begin() as conn:
        if is_pg:
            await conn.execute(text("DROP SCHEMA public CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))
        else:
            await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    print("Importing Excel workbooks…")
    async with AsyncSessionLocal() as db:
        res = await import_all(db, wipe=False)   # schema is already empty
    print(f"  countries={res['countries']}  universities={res['universities']}  "
          f"programs={res['programs']}  links={res['links']}")
    for code, s in res["summary"].items():
        print(f"    {code}: {s['country']} — {s['universities']} unis, "
              f"{s['master']} master, {s['bachelor']} bachelor")

    print("Seeding super-admin…")
    await _seed_super_admin()

    await engine.dispose()
    print("Done.")


if __name__ == "__main__":
    try:
        asyncio.run(rebuild())
    except Exception as exc:
        print("REBUILD FAILED:", exc)
        sys.exit(1)
