"""
app/modules/countries/models.py
================================
Canonical ORM models for the University Requirement Reader (v3).

Data model:
    Country ──< University ──< Program
                                   └──< ProgramLink
    AdminUser ──< AdminCountry        (which countries an admin may manage)
    ApiKey                            (encrypted provider keys for AI extraction)
    ReaderRun ──< LinkSnapshot / LinkChange

Each Program now carries its ADMISSION CRITERIA inline (the fields the team
actually tracks): application opening date, deadline, apply/portal link,
application fee, required CGPA, status (admission OPEN/CLOSE) and English
requirement. Master's and Bachelor's are separated by `degree_level`.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


# =====================================================================
#  CORE DATA
# =====================================================================
class Country(Base):
    __tablename__ = "countries"

    id: Mapped[str] = mapped_column(sa.String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(sa.String(100), nullable=False, unique=True)
    code: Mapped[str] = mapped_column(sa.String(5), nullable=False, unique=True)
    slug: Mapped[str] = mapped_column(sa.String(120), nullable=False, unique=True)
    is_active: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=_now, onupdate=_now)

    universities: Mapped[list["University"]] = relationship(
        back_populates="country", cascade="all, delete-orphan")


class University(Base):
    __tablename__ = "universities"

    id: Mapped[str] = mapped_column(sa.String(36), primary_key=True, default=_uuid)
    country_id: Mapped[str] = mapped_column(
        sa.String(36), sa.ForeignKey("countries.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(sa.String(500), nullable=False)
    slug: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    city: Mapped[str | None] = mapped_column(sa.String(200))
    type: Mapped[str | None] = mapped_column(sa.String(100))
    website: Mapped[str | None] = mapped_column(sa.Text)
    contact: Mapped[str | None] = mapped_column(sa.Text)
    origin: Mapped[str] = mapped_column(sa.String(20), nullable=False, default="excel")
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=_now, onupdate=_now)

    __table_args__ = (sa.UniqueConstraint("country_id", "slug", name="uq_univ_country_slug"),)

    country: Mapped["Country"] = relationship(back_populates="universities")
    programs: Mapped[list["Program"]] = relationship(
        back_populates="university", cascade="all, delete-orphan")
    links: Mapped[list["ProgramLink"]] = relationship(
        back_populates="university", cascade="all, delete-orphan")


class Program(Base):
    """A programme/track with its admission criteria inline."""
    __tablename__ = "programs"

    id: Mapped[str] = mapped_column(sa.String(36), primary_key=True, default=_uuid)
    university_id: Mapped[str] = mapped_column(
        sa.String(36), sa.ForeignKey("universities.id", ondelete="CASCADE"), nullable=False, index=True)
    country_id: Mapped[str] = mapped_column(
        sa.String(36), sa.ForeignKey("countries.id", ondelete="CASCADE"), nullable=False, index=True)

    name: Mapped[str] = mapped_column(sa.String(600), nullable=False)
    degree_level: Mapped[str] = mapped_column(sa.String(20), nullable=False, default="MASTER", index=True)
    field: Mapped[str | None] = mapped_column(sa.String(200))
    language: Mapped[str | None] = mapped_column(sa.String(200))

    # ── the tracked admission CRITERIA ───────────────────────────
    application_opening_date: Mapped[str | None] = mapped_column(sa.String(400))
    deadline: Mapped[str | None] = mapped_column(sa.String(400))
    apply_link: Mapped[str | None] = mapped_column(sa.Text)        # portal / apply URL
    portal_name: Mapped[str | None] = mapped_column(sa.String(400))
    apply_fee: Mapped[str | None] = mapped_column(sa.String(400))  # raw, e.g. "30 EURO"
    fee_amount: Mapped[float | None] = mapped_column(sa.Numeric(12, 2))
    fee_currency: Mapped[str | None] = mapped_column(sa.String(10))
    cgpa_required: Mapped[str | None] = mapped_column(sa.Text)
    status: Mapped[str | None] = mapped_column(sa.String(40), index=True)   # OPEN / CLOSE / UNKNOWN
    english_required: Mapped[str | None] = mapped_column(sa.Text)
    requirements_text: Mapped[str | None] = mapped_column(sa.Text)          # free-form criteria

    notes: Mapped[str | None] = mapped_column(sa.Text)
    source_sheet: Mapped[str | None] = mapped_column(sa.String(120))
    source_row: Mapped[str | None] = mapped_column(sa.String(40))
    origin: Mapped[str] = mapped_column(sa.String(20), nullable=False, default="excel")
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=_now, onupdate=_now)

    university: Mapped["University"] = relationship(back_populates="programs")
    links: Mapped[list["ProgramLink"]] = relationship(
        back_populates="program", cascade="all, delete-orphan")

    # Criteria that the reader reads/compares, in display order.
    CRITERIA_FIELDS = (
        "status", "application_opening_date", "deadline",
        "apply_fee", "cgpa_required", "english_required", "apply_link",
    )


class ProgramLink(Base):
    __tablename__ = "program_links"

    id: Mapped[str] = mapped_column(sa.String(36), primary_key=True, default=_uuid)
    program_id: Mapped[str | None] = mapped_column(
        sa.String(36), sa.ForeignKey("programs.id", ondelete="CASCADE"), index=True)
    university_id: Mapped[str] = mapped_column(
        sa.String(36), sa.ForeignKey("universities.id", ondelete="CASCADE"), nullable=False, index=True)
    url: Mapped[str] = mapped_column(sa.Text, nullable=False)
    link_type: Mapped[str] = mapped_column(sa.String(20), nullable=False, default="APPLY")
    label: Mapped[str | None] = mapped_column(sa.String(300))
    http_status: Mapped[int | None] = mapped_column(sa.Integer)
    is_alive: Mapped[bool | None] = mapped_column(sa.Boolean, index=True)
    broken_since: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    is_auto_discovered: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)
    last_checked_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=_now, onupdate=_now)

    program: Mapped["Program | None"] = relationship(back_populates="links")
    university: Mapped["University"] = relationship(back_populates="links")


# =====================================================================
#  ADMIN / AUTH
# =====================================================================
class AdminUser(Base):
    __tablename__ = "admin_users"

    id: Mapped[str] = mapped_column(sa.String(36), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(sa.String(255), nullable=False, unique=True, index=True)
    full_name: Mapped[str | None] = mapped_column(sa.String(200))
    password_hash: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    role: Mapped[str] = mapped_column(sa.String(20), nullable=False, default="COUNTRY_ADMIN")  # SUPER_ADMIN / COUNTRY_ADMIN
    is_active: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=_now, onupdate=_now)

    countries: Mapped[list["AdminCountry"]] = relationship(
        back_populates="admin", cascade="all, delete-orphan")


class AdminCountry(Base):
    """Which countries a COUNTRY_ADMIN is allowed to manage (super-admin = all)."""
    __tablename__ = "admin_countries"

    id: Mapped[str] = mapped_column(sa.String(36), primary_key=True, default=_uuid)
    admin_id: Mapped[str] = mapped_column(
        sa.String(36), sa.ForeignKey("admin_users.id", ondelete="CASCADE"), nullable=False, index=True)
    country_id: Mapped[str] = mapped_column(
        sa.String(36), sa.ForeignKey("countries.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=_now)

    __table_args__ = (sa.UniqueConstraint("admin_id", "country_id", name="uq_admin_country"),)

    admin: Mapped["AdminUser"] = relationship(back_populates="countries")


class ApiKey(Base):
    """A provider API key for AI extraction, stored ENCRYPTED at rest."""
    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(sa.String(36), primary_key=True, default=_uuid)
    provider: Mapped[str] = mapped_column(sa.String(40), nullable=False, index=True)  # gemini / openai
    label: Mapped[str | None] = mapped_column(sa.String(200))
    encrypted_key: Mapped[str] = mapped_column(sa.Text, nullable=False)
    masked: Mapped[str] = mapped_column(sa.String(60), nullable=False)
    model: Mapped[str | None] = mapped_column(sa.String(100))
    is_active: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=True, index=True)
    created_by: Mapped[str | None] = mapped_column(sa.String(255))
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=_now, onupdate=_now)


# =====================================================================
#  READER BOOKKEEPING
# =====================================================================
class ReaderRun(Base):
    __tablename__ = "reader_runs"

    id: Mapped[str] = mapped_column(sa.String(36), primary_key=True, default=_uuid)
    countries: Mapped[str | None] = mapped_column(sa.String(120))  # 'ALL' or 'FR,IT'
    status: Mapped[str] = mapped_column(sa.String(20), nullable=False, default="RUNNING")
    links_checked: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    links_alive: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    links_broken: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    programs_read: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    changes_found: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    used_ai: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)
    message: Mapped[str | None] = mapped_column(sa.Text)
    started_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))


class LinkSnapshot(Base):
    """What the reader saw for one link during one run (incl. criteria it read)."""
    __tablename__ = "link_snapshots"

    id: Mapped[str] = mapped_column(sa.String(36), primary_key=True, default=_uuid)
    link_id: Mapped[str] = mapped_column(
        sa.String(36), sa.ForeignKey("program_links.id", ondelete="CASCADE"), nullable=False, index=True)
    run_id: Mapped[str] = mapped_column(
        sa.String(36), sa.ForeignKey("reader_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    program_id: Mapped[str | None] = mapped_column(
        sa.String(36), sa.ForeignKey("programs.id", ondelete="SET NULL"), index=True)
    http_status: Mapped[int | None] = mapped_column(sa.Integer)
    is_alive: Mapped[bool | None] = mapped_column(sa.Boolean)
    content_hash: Mapped[str | None] = mapped_column(sa.String(64))
    content_length: Mapped[int | None] = mapped_column(sa.Integer)
    criteria_found: Mapped[dict | None] = mapped_column(sa.JSON)   # read criteria {field: value}
    fetched_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=_now)


class LinkChange(Base):
    """A detected difference between what the reader read and the stored value."""
    __tablename__ = "link_changes"

    id: Mapped[str] = mapped_column(sa.String(36), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(
        sa.String(36), sa.ForeignKey("reader_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    link_id: Mapped[str | None] = mapped_column(
        sa.String(36), sa.ForeignKey("program_links.id", ondelete="SET NULL"), index=True)
    university_id: Mapped[str | None] = mapped_column(
        sa.String(36), sa.ForeignKey("universities.id", ondelete="SET NULL"))
    program_id: Mapped[str | None] = mapped_column(
        sa.String(36), sa.ForeignKey("programs.id", ondelete="SET NULL"))
    country_id: Mapped[str | None] = mapped_column(
        sa.String(36), sa.ForeignKey("countries.id", ondelete="SET NULL"), index=True)
    change_type: Mapped[str] = mapped_column(sa.String(30), nullable=False, index=True)
    field: Mapped[str | None] = mapped_column(sa.String(60))
    old_value: Mapped[str | None] = mapped_column(sa.Text)
    new_value: Mapped[str | None] = mapped_column(sa.Text)
    status: Mapped[str] = mapped_column(sa.String(20), nullable=False, default="PENDING", index=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    detected_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=_now)


class ReaderSchedule(Base):
    """Single-row config for automatic (timed) reading."""
    __tablename__ = "reader_schedule"

    id: Mapped[str] = mapped_column(sa.String(36), primary_key=True, default="default")
    enabled: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)
    frequency: Mapped[str] = mapped_column(sa.String(20), nullable=False, default="daily")
    at_time: Mapped[str | None] = mapped_column(sa.String(5), default="02:00")
    run_date: Mapped[object | None] = mapped_column(sa.Date)
    interval_hours: Mapped[int | None] = mapped_column(sa.Integer, default=24)
    day_of_week: Mapped[int | None] = mapped_column(sa.Integer, default=0)
    countries: Mapped[str] = mapped_column(sa.String(120), nullable=False, default="ALL")
    timezone: Mapped[str] = mapped_column(sa.String(60), nullable=False, default="Asia/Karachi")
    last_run_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    next_run_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), default=_now, onupdate=_now)
