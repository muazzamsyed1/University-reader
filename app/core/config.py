"""
app/core/config.py
==================
Platform configuration. Values are read from .env automatically.
"""
from __future__ import annotations

import re
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8",
        case_sensitive=False, extra="ignore",
    )

    # ── Application ──────────────────────────────────────────────
    APP_NAME: str = "University Requirement Reader"
    APP_VERSION: str = "3.0.0"
    DEBUG: bool = False
    API_V1_PREFIX: str = "/api/v1"
    LOG_LEVEL: str = "INFO"

    # ── Database ─────────────────────────────────────────────────
    # A full connection string (e.g. Railway's auto-provided DATABASE_URL) takes
    # precedence over the individual DB_* parts below when set.
    DB_URL: str = Field(default="", validation_alias="DATABASE_URL")
    DB_ENGINE: str = "postgresql"          # "postgresql" or "sqlite"
    DB_HOST: str = "localhost"
    DB_PORT: int = 5432
    DB_NAME: str = "study_abroad_db"
    DB_USER: str = "postgres"
    DB_PASSWORD: str = "postgres"
    SQLITE_DB_PATH: str = "study_abroad.db"

    # ── Excel source workbooks (for re-sync) ─────────────────────
    EXCEL_DIR: str = str(_PROJECT_ROOT / "countriesEXCELsheetsData")
    # On a fresh/empty database (e.g. first cloud deploy), auto-load from EXCEL_DIR.
    AUTO_IMPORT_ON_EMPTY: bool = True

    # ── Security / authentication ────────────────────────────────
    # SECRET_KEY signs login tokens AND derives the key that encrypts stored
    # API keys. Keep it secret and stable — changing it invalidates all tokens
    # and makes previously-stored API keys unreadable.
    SECRET_KEY: str = "CHANGE-ME-dev-only-secret-key-please-override-in-env"
    FERNET_KEY: str = ""                    # optional explicit key; else derived from SECRET_KEY
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 12     # 12 h sessions

    # Optional allow-list of e-mail addresses for admin accounts (comma list).
    # Only enforced when RESTRICT_ADMIN_EMAILS is true; by default the super-admin
    # may add any gmail / e-mail address.
    RESTRICT_ADMIN_EMAILS: bool = False
    ADMIN_WHITELIST: str = ""               # set in .env (comma-separated) if restricting
    ADMIN_SUPERUSER_EMAIL: str = ""         # set in .env; if empty, no auto-seed
    ADMIN_SUPERUSER_PASSWORD: str = ""      # set in .env; if empty, no auto-seed

    # ── AI extraction (reader) ───────────────────────────────────
    # Provider key may also be stored (encrypted) in the dashboard, which
    # overrides this env value at runtime.
    AI_PROVIDER: str = "gemini"            # "gemini" or "openai"
    GEMINI_API_KEY: str = ""
    GEMINI_BASE_URL: str = "https://generativelanguage.googleapis.com/v1beta"
    GEMINI_MODEL: str = "gemini-3-flash-preview"
    OPENAI_API_KEY: str = ""
    OPENAI_BASE_URL: str = "https://api.openai.com/v1"
    OPENAI_MODEL: str = "gpt-4o-mini"

    @property
    def allowed_admin_emails(self) -> set[str]:
        return {e.strip().lower() for e in self.ADMIN_WHITELIST.split(",") if e.strip()}

    def _url_with_driver(self, driver: str) -> str | None:
        """Normalise a provided DATABASE_URL to use the given driver (asyncpg /
        psycopg2) and drop query params that those drivers don't accept."""
        raw = (self.DB_URL or "").strip()
        if not raw:
            return None
        raw = raw.split("?", 1)[0]                       # drop ?sslmode=... etc.
        if raw.startswith("postgres://"):
            raw = "postgresql://" + raw[len("postgres://"):]
        if raw.startswith("postgresql+"):
            raw = re.sub(r"^postgresql\+[a-z0-9]+://", "postgresql://", raw)
        if raw.startswith("postgresql://"):
            return "postgresql+" + driver + "://" + raw[len("postgresql://"):]
        return raw                                       # some other scheme — use as-is

    @property
    def DATABASE_URL(self) -> str:
        override = self._url_with_driver("asyncpg")
        if override:
            return override
        engine = self.DB_ENGINE.lower()
        if engine in ("postgresql", "postgres"):
            return (f"postgresql+asyncpg://{self.DB_USER}:{self.DB_PASSWORD}"
                    f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}")
        db_file = Path(self.SQLITE_DB_PATH).resolve()
        return f"sqlite+aiosqlite:///{db_file}"

    @property
    def DATABASE_URL_SYNC(self) -> str:
        override = self._url_with_driver("psycopg2")
        if override:
            return override
        engine = self.DB_ENGINE.lower()
        if engine in ("postgresql", "postgres"):
            return (f"postgresql+psycopg2://{self.DB_USER}:{self.DB_PASSWORD}"
                    f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}")
        db_file = Path(self.SQLITE_DB_PATH).resolve()
        return f"sqlite:///{db_file}"


settings = Settings()
