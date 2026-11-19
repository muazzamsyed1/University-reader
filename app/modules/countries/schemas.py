"""Pydantic schemas for the University Requirement Reader API (v3, criteria model)."""
from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict


class _ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ── links ────────────────────────────────────────────────────────
class LinkOut(_ORM):
    id: str
    url: str
    link_type: str
    label: str | None = None
    http_status: int | None = None
    is_alive: bool | None = None
    broken_since: datetime | None = None
    is_auto_discovered: bool = False
    last_checked_at: datetime | None = None


class LinkIn(BaseModel):
    url: str
    link_type: str = "APPLY"
    label: str | None = None


class LinkUpdate(BaseModel):
    url: str | None = None
    link_type: str | None = None
    label: str | None = None


# ── programmes (criteria inline) ─────────────────────────────────
class ProgramOut(_ORM):
    id: str
    name: str
    degree_level: str = "MASTER"
    field: str | None = None
    language: str | None = None
    application_opening_date: str | None = None
    deadline: str | None = None
    apply_link: str | None = None
    portal_name: str | None = None
    apply_fee: str | None = None
    fee_amount: float | None = None
    fee_currency: str | None = None
    cgpa_required: str | None = None
    status: str | None = None
    english_required: str | None = None
    requirements_text: str | None = None
    notes: str | None = None
    source_sheet: str | None = None
    origin: str = "excel"
    links: list[LinkOut] = []


class ProgramWithUniversity(ProgramOut):
    university_id: str
    university_name: str
    country_code: str
    country_name: str | None = None


class ProgramIn(BaseModel):
    name: str
    degree_level: str = "MASTER"
    field: str | None = None
    language: str | None = None
    application_opening_date: str | None = None
    deadline: str | None = None
    apply_link: str | None = None
    portal_name: str | None = None
    apply_fee: str | None = None
    cgpa_required: str | None = None
    status: str | None = None
    english_required: str | None = None
    requirements_text: str | None = None
    notes: str | None = None


class ProgramUpdate(BaseModel):
    name: str | None = None
    degree_level: str | None = None
    field: str | None = None
    language: str | None = None
    application_opening_date: str | None = None
    deadline: str | None = None
    apply_link: str | None = None
    portal_name: str | None = None
    apply_fee: str | None = None
    cgpa_required: str | None = None
    status: str | None = None
    english_required: str | None = None
    requirements_text: str | None = None
    notes: str | None = None


# ── universities / countries ─────────────────────────────────────
class UniversityOut(_ORM):
    id: str
    name: str
    slug: str
    city: str | None = None
    type: str | None = None
    website: str | None = None
    contact: str | None = None
    origin: str = "excel"
    program_count: int = 0
    master_count: int = 0
    bachelor_count: int = 0
    country_code: str | None = None
    country_name: str | None = None


class UniversityDetail(UniversityOut):
    programs: list[ProgramOut] = []


class UniversityIn(BaseModel):
    country_code: str
    country_name: str | None = None
    name: str
    city: str | None = None
    type: str | None = None
    website: str | None = None
    contact: str | None = None
    programs: list[ProgramIn] = []


class UniversityUpdate(BaseModel):
    name: str | None = None
    city: str | None = None
    type: str | None = None
    website: str | None = None
    contact: str | None = None


class CountryOut(_ORM):
    id: str
    name: str
    code: str
    slug: str
    university_count: int = 0
    program_count: int = 0
    master_count: int = 0
    bachelor_count: int = 0


# ── stats ────────────────────────────────────────────────────────
class StatsOut(BaseModel):
    countries: int
    universities: int
    programs: int
    master_programs: int
    bachelor_programs: int
    links: int
    links_alive: int
    links_broken: int
    pending_changes: int


# ── reader ───────────────────────────────────────────────────────
class ReaderRunResult(BaseModel):
    run_id: str
    countries: str
    checked: int
    alive: int
    broken: int
    programs_read: int = 0
    changes: int
    used_ai: bool = False
    discovered: int = 0
    message: str


class ReaderRunOut(_ORM):
    id: str
    countries: str | None = None
    status: str
    links_checked: int
    links_alive: int
    links_broken: int
    programs_read: int = 0
    changes_found: int
    used_ai: bool = False
    message: str | None = None
    started_at: datetime
    finished_at: datetime | None = None


# ── side-by-side read vs stored ──────────────────────────────────
class CompareField(BaseModel):
    field: str
    stored: str | None = None
    read: str | None = None
    changed: bool = False


class CompareRow(BaseModel):
    program_id: str
    university: str
    program: str
    degree_level: str
    country_code: str | None = None
    link_alive: bool | None = None
    http_status: int | None = None
    page_title: str | None = None
    read_chars: int = 0
    source_url: str | None = None
    followed_url: str | None = None
    fields: list[CompareField] = []
    changed_count: int = 0


class RunCompareOut(BaseModel):
    run_id: str
    status: str
    used_ai: bool = False
    total: int = 0
    changed: int = 0
    rows: list[CompareRow] = []
    message: str = ""


class ReadUniversityOut(BaseModel):
    ok: bool = True
    university_id: str
    university_name: str
    run_id: str | None = None
    checked: int = 0
    alive: int = 0
    broken: int = 0
    programs_read: int = 0
    changes: int = 0
    used_ai: bool = False
    message: str = ""
    compare: RunCompareOut | None = None


# ── changes / review ─────────────────────────────────────────────
class ChangeOut(BaseModel):
    id: str
    change_type: str
    field: str | None = None
    old_value: str | None = None
    new_value: str | None = None
    status: str = "PENDING"
    reviewed_at: datetime | None = None
    detected_at: datetime | None = None
    country: str | None = None
    country_code: str | None = None
    university: str | None = None
    program: str | None = None
    program_id: str | None = None
    degree_level: str | None = None
    url: str | None = None


class ChangeReview(BaseModel):
    decision: str   # 'approve' or 'decline'


class BrokenLinkOut(BaseModel):
    link_id: str
    url: str
    link_type: str
    http_status: int | None = None
    broken_since: datetime | None = None
    last_checked_at: datetime | None = None
    country: str
    country_code: str
    university_id: str | None = None
    university: str
    program: str | None = None
    program_id: str | None = None
    degree_level: str | None = None


# ── schedule ─────────────────────────────────────────────────────
class ScheduleOut(_ORM):
    enabled: bool
    frequency: str
    at_time: str | None = None
    run_date: date | None = None
    interval_hours: int | None = None
    day_of_week: int | None = None
    countries: str
    timezone: str
    last_run_at: datetime | None = None
    next_run_at: datetime | None = None


class ScheduleIn(BaseModel):
    enabled: bool | None = None
    frequency: str | None = None
    at_time: str | None = None
    run_date: date | None = None
    interval_hours: int | None = None
    day_of_week: int | None = None
    countries: str | None = None
    timezone: str | None = None


# ── API keys ─────────────────────────────────────────────────────
class ApiKeyIn(BaseModel):
    provider: str                 # gemini / openai
    key: str
    label: str | None = None
    model: str | None = None


class ApiKeyOut(BaseModel):
    id: str
    provider: str
    label: str | None = None
    masked: str
    model: str | None = None
    is_active: bool
    created_by: str | None = None
    created_at: datetime
    updated_at: datetime


# ── verification ─────────────────────────────────────────────────
class VerifyExcelRow(BaseModel):
    country: str
    db_universities: int
    excel_universities: int
    db_master: int
    excel_master: int
    db_bachelor: int
    excel_bachelor: int
    match: bool


class VerifyExcelOut(BaseModel):
    rows: list[VerifyExcelRow]
    all_match: bool
    message: str


class ExploreIn(BaseModel):
    university: str
    country_code: str = ""
    program: str = ""
    degree_level: str = "MASTER"
    url: str | None = None


class ExploreOut(BaseModel):
    ok: bool = False
    university: str = ""
    program: str = ""
    degree_level: str = "MASTER"
    country_code: str = ""
    url: str = ""
    discovered: bool = False
    page_title: str | None = None
    followed_url: str | None = None
    http_status: int | None = None
    text_chars: int = 0
    used_ai: bool = False
    criteria: dict = {}
    already_exists: bool = False
    existing_university_id: str | None = None
    message: str = ""


class ExploreSaveIn(BaseModel):
    university: str
    country_code: str
    country_name: str | None = None
    program: str = ""
    degree_level: str = "MASTER"
    university_type: str | None = None
    city: str | None = None
    website: str | None = None
    url: str | None = None
    criteria: dict = {}
    requirements_text: str | None = None


class VerifyReadUrlIn(BaseModel):
    url: str
    program_id: str | None = None


class VerifyReadUrlOut(BaseModel):
    ok: bool
    http_status: int | None = None
    text_chars: int = 0
    used_ai: bool = False
    read_criteria: dict = {}
    stored_criteria: dict = {}
    program_id: str | None = None
    program_name: str | None = None
    university_name: str | None = None
    page_title: str | None = None
    followed_url: str | None = None
    message: str


# ── generic ──────────────────────────────────────────────────────
class OpResult(BaseModel):
    ok: bool = True
    message: str
    university_id: str | None = None
    program_id: str | None = None
    link_id: str | None = None
    status: str | None = None


# ── chatbot ──────────────────────────────────────────────────────
class ChatIn(BaseModel):
    message: str


class ChatMatchedUniversity(BaseModel):
    id: str
    name: str
    country: str | None = None


class ChatOut(BaseModel):
    answer: str
    matched_university: ChatMatchedUniversity | None = None
