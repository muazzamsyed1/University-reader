"""
app/main.py
===========
FastAPI entry point — University Requirement Reader.

Serves the country / university / programme / requirement / link data and
the admin dashboard (frontend/index.html). The database itself is built by
`database/rebuild.py` (schema.sql + seed.sql) or re-synced from Excel via
`python -m app.modules.countries.excel_import`.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.core.config import settings
from app.core.init_db import init_db
from app.modules.auth.router import router as auth_router
from app.modules.countries import scheduler
from app.modules.countries.router import router as countries_router

logging.basicConfig(level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO))
logger = logging.getLogger("requirement_reader")

FRONTEND_DIR = Path(__file__).parent.parent / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting %s v%s (db_engine=%s)", settings.APP_NAME, settings.APP_VERSION, settings.DB_ENGINE)
    try:
        await init_db()
    except Exception as exc:  # pragma: no cover - startup resilience
        logger.error("DB init/check failed: %s", exc)
    try:
        await scheduler.start()
    except Exception as exc:  # pragma: no cover
        logger.error("Scheduler start failed: %s", exc)
    yield
    scheduler.shutdown()
    logger.info("Shutting down")


def create_application() -> FastAPI:
    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        description="University Requirement Reader — countries, universities, "
                    "programmes, eligibility requirements and source links for the "
                    "study-abroad processing team.",
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"], allow_credentials=True,
        allow_methods=["*"], allow_headers=["*"],
    )

    app.include_router(auth_router)
    app.include_router(countries_router)

    @app.get("/api/health", include_in_schema=False)
    async def health():
        return {"status": "ok", "app": settings.APP_NAME}

    if FRONTEND_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

    _NO_CACHE = {"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                 "Pragma": "no-cache", "Expires": "0"}

    async def _countries_json() -> str:
        """Full country list as JSON, injected into the dashboard so its dropdowns
        are always populated even if a client-side fetch later fails."""
        import json
        from sqlalchemy import func, select as _sel
        from app.core.database import AsyncSessionLocal
        from app.modules.countries.models import Country, Program, University
        try:
            async with AsyncSessionLocal() as db:
                unis = dict((await db.execute(_sel(
                    University.country_id, func.count(University.id)).group_by(University.country_id))).all())
                degs = {(cid, d): n for cid, d, n in (await db.execute(_sel(
                    Program.country_id, Program.degree_level, func.count(Program.id))
                    .group_by(Program.country_id, Program.degree_level))).all()}
                rows = (await db.execute(_sel(Country).order_by(Country.name))).scalars().all()
                out = [{"id": c.id, "name": c.name, "code": c.code, "slug": c.slug,
                        "university_count": unis.get(c.id, 0),
                        "master_count": degs.get((c.id, "MASTER"), 0),
                        "bachelor_count": degs.get((c.id, "BACHELOR"), 0),
                        "program_count": degs.get((c.id, "MASTER"), 0) + degs.get((c.id, "BACHELOR"), 0)}
                       for c in rows]
                return json.dumps(out)
        except Exception as exc:               # never let this break the page
            logger.warning("Could not inject countries into dashboard: %s", exc)
            return "[]"

    async def _render_dashboard() -> HTMLResponse:
        index = FRONTEND_DIR / "index.html"
        if index.exists():
            html = index.read_text(encoding="utf-8")
            html = html.replace("/*__COUNTRIES_JSON__*/[]", await _countries_json())
            # no-cache so browsers always load the latest dashboard, never a stale copy
            return HTMLResponse(html, headers=_NO_CACHE)
        return HTMLResponse("<h1>University Requirement Reader</h1>"
                            "<p><a href='/api/docs'>API docs</a></p>")

    # The dashboard is served at several paths. A brand-new path (e.g. /dashboard)
    # has no cache entry in the browser, so it is always fetched fresh — the reliable
    # way past an over-aggressive HTML cache.
    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def dashboard_root():
        return await _render_dashboard()

    @app.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
    async def dashboard_alt():
        return await _render_dashboard()

    @app.get("/home", response_class=HTMLResponse, include_in_schema=False)
    async def dashboard_home():
        return await _render_dashboard()

    return app


app = create_application()
