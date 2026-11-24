"""Data-access layer for the University Requirement Reader (v3, criteria model).

All list/search helpers accept `scope`: None  = all countries (super-admin),
or a list of country codes a country-admin is restricted to.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import delete as sa_delete, func, or_ as sa_or, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.modules.countries import reader as reader_engine
from app.modules.countries.excel_import import _parse_fee
from app.modules.countries.models import (
    Country, LinkChange, LinkSnapshot, Program, ProgramLink, ReaderRun,
    ReaderSchedule, University,
)

CRITERIA_FIELDS = ("status", "application_opening_date", "deadline",
                   "apply_fee", "cgpa_required", "english_required", "apply_link")


def _slug(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return (re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower() or "x")[:180]


def _scoped_codes(scope: list[str] | None) -> list[str] | None:
    if scope is None:
        return None
    return [c.upper() for c in scope]


async def _country_ids_for_scope(db: AsyncSession, scope: list[str] | None) -> list[str] | None:
    codes = _scoped_codes(scope)
    if codes is None:
        return None
    rows = (await db.execute(select(Country.id).where(Country.code.in_(codes)))).scalars().all()
    return list(rows)


# =====================================================================
#  STATS / COUNTRIES
# =====================================================================
async def stats(db: AsyncSession, scope: list[str] | None = None) -> dict:
    ids = await _country_ids_for_scope(db, scope)

    def _wc(stmt, col):
        return stmt.where(col.in_(ids)) if ids is not None else stmt

    countries = len(ids) if ids is not None else \
        (await db.execute(select(func.count(Country.id)))).scalar()
    universities = (await db.execute(_wc(select(func.count(University.id)), University.country_id))).scalar()
    master = (await db.execute(_wc(
        select(func.count(Program.id)).where(Program.degree_level == "MASTER"), Program.country_id))).scalar()
    bachelor = (await db.execute(_wc(
        select(func.count(Program.id)).where(Program.degree_level == "BACHELOR"), Program.country_id))).scalar()
    programs = (await db.execute(_wc(select(func.count(Program.id)), Program.country_id))).scalar()

    link_q = select(ProgramLink.is_alive, func.count(ProgramLink.id))
    if ids is not None:
        link_q = (link_q.join(University, University.id == ProgramLink.university_id)
                  .where(University.country_id.in_(ids)))
    link_rows = (await db.execute(link_q.group_by(ProgramLink.is_alive))).all()
    links = sum(n for _a, n in link_rows)
    alive = sum(n for a, n in link_rows if a is True)
    broken = sum(n for a, n in link_rows if a is False)

    chg_q = select(func.count(LinkChange.id)).where(LinkChange.status == "PENDING")
    if ids is not None:
        chg_q = chg_q.where(LinkChange.country_id.in_(ids))
    pending = (await db.execute(chg_q)).scalar()

    return {"countries": countries, "universities": universities, "programs": programs,
            "master_programs": master, "bachelor_programs": bachelor,
            "links": links, "links_alive": alive, "links_broken": broken,
            "pending_changes": pending}


async def list_countries(db: AsyncSession, scope: list[str] | None = None) -> list[dict]:
    codes = _scoped_codes(scope)
    uni_counts = dict((await db.execute(
        select(University.country_id, func.count(University.id)).group_by(University.country_id))).all())
    deg_counts = dict(((cid, deg), n) for cid, deg, n in (await db.execute(
        select(Program.country_id, Program.degree_level, func.count(Program.id))
        .group_by(Program.country_id, Program.degree_level))).all())
    stmt = select(Country).order_by(Country.name)
    if codes is not None:
        stmt = stmt.where(Country.code.in_(codes))
    rows = (await db.execute(stmt)).scalars().all()
    out = []
    for c in rows:
        m = deg_counts.get((c.id, "MASTER"), 0)
        b = deg_counts.get((c.id, "BACHELOR"), 0)
        out.append({"id": c.id, "name": c.name, "code": c.code, "slug": c.slug,
                    "university_count": uni_counts.get(c.id, 0),
                    "program_count": m + b, "master_count": m, "bachelor_count": b})
    return out


async def get_country(db: AsyncSession, code: str) -> Country | None:
    return (await db.execute(
        select(Country).where(Country.code == code.upper()))).scalar_one_or_none()


# =====================================================================
#  UNIVERSITIES / PROGRAMS
# =====================================================================
async def list_universities(db: AsyncSession, country_code: str | None = None,
                            q: str | None = None, scope: list[str] | None = None) -> list[dict]:
    stmt = (select(University, Country).join(Country, Country.id == University.country_id))
    codes = _scoped_codes(scope)
    if codes is not None:
        stmt = stmt.where(Country.code.in_(codes))
    if country_code:
        stmt = stmt.where(Country.code == country_code.upper())
    if q:
        like = f"%{q.strip()}%"
        slug_like = f"%{_slug(q)}%"
        stmt = stmt.where(sa_or(University.name.ilike(like), University.city.ilike(like),
                                University.slug.ilike(slug_like),
                                Country.name.ilike(like), Country.code.ilike(like)))
    stmt = stmt.order_by(University.name)
    rows = (await db.execute(stmt)).all()

    deg_counts = dict(((uid, deg), n) for uid, deg, n in (await db.execute(
        select(Program.university_id, Program.degree_level, func.count(Program.id))
        .group_by(Program.university_id, Program.degree_level))).all())
    out = []
    for u, c in rows:
        m = deg_counts.get((u.id, "MASTER"), 0)
        b = deg_counts.get((u.id, "BACHELOR"), 0)
        out.append({"id": u.id, "name": u.name, "slug": u.slug, "city": u.city, "type": u.type,
                    "website": u.website, "contact": u.contact, "origin": u.origin,
                    "program_count": m + b, "master_count": m, "bachelor_count": b,
                    "country_code": c.code, "country_name": c.name})
    return out


async def get_university(db: AsyncSession, university_id: str) -> University | None:
    return (await db.execute(
        select(University)
        .options(selectinload(University.programs).selectinload(Program.links),
                 selectinload(University.country))
        .where(University.id == university_id))).scalar_one_or_none()


async def get_program(db: AsyncSession, program_id: str) -> Program | None:
    return (await db.execute(
        select(Program).options(selectinload(Program.links))
        .where(Program.id == program_id))).scalar_one_or_none()


async def search_programs(db: AsyncSession, country_code: str | None = None,
                          degree: str | None = None, q: str | None = None,
                          has_link: bool | None = None, status: str | None = None,
                          limit: int = 200, offset: int = 0,
                          scope: list[str] | None = None) -> list[dict]:
    stmt = (select(Program, University, Country)
            .join(University, University.id == Program.university_id)
            .join(Country, Country.id == Program.country_id)
            .options(selectinload(Program.links)))
    codes = _scoped_codes(scope)
    if codes is not None:
        stmt = stmt.where(Country.code.in_(codes))
    if country_code:
        stmt = stmt.where(Country.code == country_code.upper())
    if degree:
        stmt = stmt.where(Program.degree_level == degree.upper())
    if status:
        stmt = stmt.where(Program.status == status.upper())
    if q:
        like = f"%{q.strip()}%"
        slug_like = f"%{_slug(q)}%"
        stmt = stmt.where(sa_or(Program.name.ilike(like), University.name.ilike(like),
                                Program.field.ilike(like), University.slug.ilike(slug_like),
                                Country.name.ilike(like), Country.code.ilike(like)))
    if has_link is True:
        stmt = stmt.where(Program.apply_link.isnot(None))
    elif has_link is False:
        stmt = stmt.where(Program.apply_link.is_(None))
    stmt = stmt.order_by(University.name, Program.name).limit(limit).offset(offset)
    rows = (await db.execute(stmt)).all()
    out = []
    for p, u, c in rows:
        d = {col: getattr(p, col) for col in (
            "id", "name", "degree_level", "field", "language", "application_opening_date",
            "deadline", "apply_link", "portal_name", "apply_fee", "fee_amount", "fee_currency",
            "cgpa_required", "status", "english_required", "requirements_text", "notes",
            "source_sheet", "origin")}
        d["fee_amount"] = float(p.fee_amount) if p.fee_amount is not None else None
        d["links"] = p.links
        d["university_id"] = u.id
        d["university_name"] = u.name
        d["university_city"] = u.city
        d["university_type"] = u.type
        d["country_code"] = c.code
        d["country_name"] = c.name
        out.append(d)
    return out


# =====================================================================
#  LINKS
# =====================================================================
async def list_links(db: AsyncSession, country_code: str | None = None,
                     only_broken: bool = False, scope: list[str] | None = None) -> list:
    stmt = (select(ProgramLink).join(University, University.id == ProgramLink.university_id)
            .join(Country, Country.id == University.country_id))
    codes = _scoped_codes(scope)
    if codes is not None:
        stmt = stmt.where(Country.code.in_(codes))
    if country_code:
        stmt = stmt.where(Country.code == country_code.upper())
    if only_broken:
        stmt = stmt.where(ProgramLink.is_alive.is_(False))
    return (await db.execute(stmt.order_by(ProgramLink.created_at))).scalars().all()


async def broken_links(db: AsyncSession, country_code: str | None = None,
                       scope: list[str] | None = None) -> list[dict]:
    stmt = (select(ProgramLink, University, Country, Program)
            .join(University, University.id == ProgramLink.university_id)
            .join(Country, Country.id == University.country_id)
            .outerjoin(Program, Program.id == ProgramLink.program_id)
            .where(ProgramLink.is_alive.is_(False)))
    codes = _scoped_codes(scope)
    if codes is not None:
        stmt = stmt.where(Country.code.in_(codes))
    if country_code:
        stmt = stmt.where(Country.code == country_code.upper())
    rows = (await db.execute(stmt.order_by(Country.name, University.name))).all()
    out = []
    for lk, u, c, p in rows:
        out.append({"link_id": lk.id, "url": lk.url, "link_type": lk.link_type,
                    "http_status": lk.http_status, "broken_since": lk.broken_since,
                    "last_checked_at": lk.last_checked_at, "country": c.name,
                    "country_code": c.code, "university_id": u.id, "university": u.name,
                    "program": p.name if p else None, "program_id": p.id if p else None,
                    "degree_level": p.degree_level if p else None})
    return out


# =====================================================================
#  READER RUNS / SIDE-BY-SIDE COMPARE
# =====================================================================
async def list_runs(db: AsyncSession, limit: int = 20) -> list[ReaderRun]:
    return (await db.execute(
        select(ReaderRun).order_by(ReaderRun.started_at.desc()).limit(limit))).scalars().all()


async def reader_estimate(db: AsyncSession, country_code: str | None = None,
                          university_id: str | None = None,
                          scope: list[str] | None = None) -> dict:
    """Estimate how long a read will take: counts links + distinct pages and
    projects seconds (pages are fetched ~6 at a time; shared pages fetched once)."""
    from sqlalchemy import distinct, func as _f
    stmt = (select(ProgramLink.url)
            .join(University, University.id == ProgramLink.university_id)
            .join(Country, Country.id == University.country_id))
    codes = _scoped_codes(scope)
    if codes is not None:
        stmt = stmt.where(Country.code.in_(codes))
    if country_code:
        stmt = stmt.where(Country.code == country_code.upper())
    if university_id:
        stmt = stmt.where(ProgramLink.university_id == university_id)
    urls = (await db.execute(stmt)).scalars().all()
    links = len(urls)
    unique = len(set(urls))
    cfg = await reader_engine.keystore.get_active_config(db)
    per_page = 3.0 if cfg else 1.6           # AI calls are slower than regex
    concurrency = 6
    import math
    est = int(math.ceil(unique / concurrency) * per_page) + 3
    def _human(s):
        if s < 60:
            return f"about {max(s, 3)} seconds"
        m = s / 60.0
        return f"about {m:.0f} minute" + ("s" if round(m) != 1 else "")
    return {"links": links, "unique_pages": unique, "engine": "AI" if cfg else "regex",
            "estimated_seconds": est, "estimated_human": _human(est),
            "message": f"{links} link(s), {unique} unique page(s) to read — {_human(est)} "
                       f"({'AI' if cfg else 'regex'})."}


async def run_compare(db: AsyncSession, run_id: str, only_changed: bool = False,
                      scope: list[str] | None = None) -> dict:
    """Side-by-side: for each programme read in this run, stored value vs read value."""
    run = (await db.execute(select(ReaderRun).where(ReaderRun.id == run_id))).scalar_one_or_none()
    if not run:
        raise HTTPException(404, "Run not found.")
    # latest snapshot per program in this run
    snaps = (await db.execute(
        select(LinkSnapshot).where(LinkSnapshot.run_id == run_id,
                                   LinkSnapshot.program_id.isnot(None))
        .order_by(LinkSnapshot.program_id, LinkSnapshot.fetched_at.desc()))).scalars().all()
    latest: dict[str, LinkSnapshot] = {}
    for s in snaps:
        latest.setdefault(s.program_id, s)
    if not latest:
        return {"run_id": run_id, "status": run.status, "used_ai": run.used_ai,
                "total": 0, "changed": 0, "rows": [],
                "message": "No programme pages were parsed in this run yet."}

    pids = list(latest.keys())
    prog_rows = (await db.execute(
        select(Program, University, Country)
        .join(University, University.id == Program.university_id)
        .join(Country, Country.id == Program.country_id)
        .where(Program.id.in_(pids)))).all()
    codes = _scoped_codes(scope)

    # map each snapshot's link_id -> the URL it was read from ("where from")
    link_ids = [s.link_id for s in latest.values() if s.link_id]
    url_by_link = dict((await db.execute(
        select(ProgramLink.id, ProgramLink.url).where(ProgramLink.id.in_(link_ids)))).all()) \
        if link_ids else {}

    def _norm(v):
        return re.sub(r"\s+", " ", str(v or "").strip()).lower()

    rows = []
    changed_total = 0
    for p, u, c in prog_rows:
        if codes is not None and c.code not in codes:
            continue
        snap = latest[p.id]
        found = snap.criteria_found or {}
        fields = []
        changed_here = 0
        for f in CRITERIA_FIELDS:
            if f == "apply_link":
                continue   # link isn't read off the page
            stored = getattr(p, f, None)
            read = found.get(f)
            is_changed = bool(read) and _norm(read) != _norm(stored) and \
                not (f == "status" and str(read).upper() == "UNKNOWN")
            if is_changed:
                changed_here += 1
            fields.append({"field": f, "stored": stored, "read": read, "changed": is_changed})
        if only_changed and changed_here == 0:
            continue
        changed_total += 1 if changed_here else 0
        rows.append({"program_id": p.id, "university": u.name, "program": p.name,
                     "degree_level": p.degree_level, "country_code": c.code,
                     "link_alive": snap.is_alive, "http_status": snap.http_status,
                     "page_title": found.get("_page_title") or "",
                     "read_chars": snap.content_length or 0,
                     "source_url": url_by_link.get(snap.link_id) or "",
                     "followed_url": (found.get("_followed") if isinstance(found, dict) else None),
                     "fields": fields, "changed_count": changed_here})
    rows.sort(key=lambda r: (-r["changed_count"], r["university"]))
    return {"run_id": run_id, "status": run.status, "used_ai": run.used_ai,
            "total": len(rows), "changed": changed_total, "rows": rows,
            "message": run.message or ""}


# =====================================================================
#  CHANGES / REVIEW
# =====================================================================
async def list_changes(db: AsyncSession, run_id: str | None = None,
                       country_code: str | None = None, status: str = "PENDING",
                       scope: list[str] | None = None) -> list[dict]:
    stmt = (select(LinkChange, University, Program, Country, ProgramLink)
            .outerjoin(University, University.id == LinkChange.university_id)
            .outerjoin(Program, Program.id == LinkChange.program_id)
            .outerjoin(Country, Country.id == LinkChange.country_id)
            .outerjoin(ProgramLink, ProgramLink.id == LinkChange.link_id))
    if status and status.upper() != "ALL":
        stmt = stmt.where(LinkChange.status == status.upper())
    if run_id:
        stmt = stmt.where(LinkChange.run_id == run_id)
    codes = _scoped_codes(scope)
    if codes is not None:
        stmt = stmt.where(Country.code.in_(codes))
    if country_code:
        stmt = stmt.where(Country.code == country_code.upper())
    rows = (await db.execute(stmt.order_by(LinkChange.detected_at.desc()).limit(1000))).all()
    out = []
    for ch, u, p, c, lk in rows:
        out.append({"id": ch.id, "change_type": ch.change_type, "field": ch.field,
                    "old_value": ch.old_value, "new_value": ch.new_value, "status": ch.status,
                    "reviewed_at": ch.reviewed_at, "detected_at": ch.detected_at,
                    "country": c.name if c else None, "country_code": c.code if c else None,
                    "university": u.name if u else None, "program": p.name if p else None,
                    "program_id": p.id if p else None,
                    "degree_level": p.degree_level if p else None,
                    "url": lk.url if lk else None})
    return out


async def review_change(db: AsyncSession, change_id: str, decision: str) -> dict:
    ch = (await db.execute(select(LinkChange).where(LinkChange.id == change_id))).scalar_one_or_none()
    if not ch:
        raise HTTPException(404, "Change not found.")
    if ch.status != "PENDING":
        return {"ok": True, "message": f"Already {ch.status.lower()}.", "status": ch.status}
    decision = decision.lower().strip()
    if decision == "approve":
        if ch.change_type in ("CRITERIA_CHANGED", "LINK_FOUND") and ch.program_id \
                and ch.field in CRITERIA_FIELDS:
            prog = (await db.execute(
                select(Program).where(Program.id == ch.program_id))).scalar_one_or_none()
            if prog is not None:
                setattr(prog, ch.field, ch.new_value)
                if ch.field == "apply_fee":
                    amt, cur = _parse_fee(ch.new_value or "")
                    prog.fee_amount, prog.fee_currency = amt, cur
        ch.status = "APPROVED"
    elif decision == "decline":
        # a declined auto-discovered link is removed so the reader won't reuse it
        if ch.change_type == "LINK_FOUND" and ch.link_id:
            lk = (await db.execute(
                select(ProgramLink).where(ProgramLink.id == ch.link_id))).scalar_one_or_none()
            if lk is not None and getattr(lk, "is_auto_discovered", False):
                await db.delete(lk)
        ch.status = "DECLINED"
    else:
        raise HTTPException(422, "decision must be 'approve' or 'decline'.")
    ch.reviewed_at = datetime.now(timezone.utc)
    await db.commit()
    return {"ok": True, "message": f"Change {ch.status.lower()}.", "status": ch.status}


async def delete_change(db: AsyncSession, change_id: str) -> dict:
    ch = (await db.execute(select(LinkChange).where(LinkChange.id == change_id))).scalar_one_or_none()
    if not ch:
        raise HTTPException(404, "Change not found.")
    await db.delete(ch)
    await db.commit()
    return {"ok": True, "message": "Change removed."}


# =====================================================================
#  SCHEDULE
# =====================================================================
async def get_schedule(db: AsyncSession) -> ReaderSchedule:
    sched = (await db.execute(
        select(ReaderSchedule).where(ReaderSchedule.id == "default"))).scalar_one_or_none()
    if sched is None:
        sched = ReaderSchedule(id="default")
        db.add(sched)
        await db.commit()
        await db.refresh(sched)
    return sched


async def update_schedule(db: AsyncSession, data: dict) -> ReaderSchedule:
    sched = await get_schedule(db)
    for k, v in data.items():
        if hasattr(sched, k):
            setattr(sched, k, v)
    await db.commit()
    await db.refresh(sched)
    return sched


# =====================================================================
#  DATA ENTRY / EDITING
# =====================================================================
async def create_university(db: AsyncSession, data: dict) -> dict:
    code = (data.get("country_code") or "").upper()
    country = await get_country(db, code)
    if country is None:
        if not data.get("country_name"):
            raise HTTPException(422, "Unknown country; provide country_name to create it.")
        country = Country(name=data["country_name"], code=code, slug=_slug(data["country_name"]))
        db.add(country)
        await db.flush()
    uni = University(country_id=country.id, name=data["name"], slug=_slug(data["name"]),
                     city=data.get("city"), type=data.get("type"), website=data.get("website"),
                     contact=data.get("contact"), origin="manual")
    db.add(uni)
    await db.flush()
    for prog in data.get("programs", []):
        await _add_program(db, uni, country.id, prog)
    await db.commit()
    return {"ok": True, "message": "University created.", "university_id": uni.id}


async def _add_program(db: AsyncSession, uni: University, country_id: str, prog: dict) -> Program:
    amt, cur = _parse_fee(prog.get("apply_fee") or "")
    p = Program(
        university_id=uni.id, country_id=country_id, name=prog["name"],
        degree_level=(prog.get("degree_level") or "MASTER").upper(),
        field=prog.get("field"), language=prog.get("language"),
        application_opening_date=prog.get("application_opening_date"),
        deadline=prog.get("deadline"), apply_link=prog.get("apply_link"),
        portal_name=prog.get("portal_name"), apply_fee=prog.get("apply_fee"),
        fee_amount=amt, fee_currency=cur, cgpa_required=prog.get("cgpa_required"),
        status=(prog.get("status") or "").upper() or None,
        english_required=prog.get("english_required"),
        requirements_text=prog.get("requirements_text"), notes=prog.get("notes"),
        origin="manual")
    db.add(p)
    await db.flush()
    al = (prog.get("apply_link") or "").strip()
    if al.lower().startswith("http"):
        db.add(ProgramLink(program_id=p.id, university_id=uni.id, url=al,
                           link_type="APPLY", label="Apply / portal link"))
    return p


async def update_university(db: AsyncSession, university_id: str, data: dict) -> dict:
    uni = (await db.execute(
        select(University).where(University.id == university_id))).scalar_one_or_none()
    if not uni:
        raise HTTPException(404, "University not found.")
    for k, v in data.items():
        if hasattr(uni, k):
            setattr(uni, k, v)
    await db.commit()
    return {"ok": True, "message": "University updated.", "university_id": uni.id}


async def delete_university(db: AsyncSession, university_id: str) -> dict:
    uni = (await db.execute(
        select(University).where(University.id == university_id))).scalar_one_or_none()
    if not uni:
        raise HTTPException(404, "University not found.")
    await db.delete(uni)
    await db.commit()
    return {"ok": True, "message": "University deleted."}


async def add_program_to_university(db: AsyncSession, university_id: str, prog: dict) -> dict:
    uni = (await db.execute(
        select(University).where(University.id == university_id))).scalar_one_or_none()
    if not uni:
        raise HTTPException(404, "University not found.")
    p = await _add_program(db, uni, uni.country_id, prog)
    await db.commit()
    return {"ok": True, "message": "Programme added.", "program_id": p.id}


async def update_program(db: AsyncSession, program_id: str, data: dict) -> dict:
    p = (await db.execute(select(Program).where(Program.id == program_id))).scalar_one_or_none()
    if not p:
        raise HTTPException(404, "Programme not found.")
    for k, v in data.items():
        if hasattr(p, k):
            setattr(p, k, v)
    if "degree_level" in data and data["degree_level"]:
        p.degree_level = data["degree_level"].upper()
    if "status" in data and data["status"]:
        p.status = data["status"].upper()
    if "apply_fee" in data:
        p.fee_amount, p.fee_currency = _parse_fee(data.get("apply_fee") or "")
    await db.commit()
    return {"ok": True, "message": "Programme updated.", "program_id": p.id}


async def delete_program(db: AsyncSession, program_id: str) -> dict:
    p = (await db.execute(select(Program).where(Program.id == program_id))).scalar_one_or_none()
    if not p:
        raise HTTPException(404, "Programme not found.")
    await db.delete(p)
    await db.commit()
    return {"ok": True, "message": "Programme deleted."}


async def add_link(db: AsyncSession, data: dict) -> dict:
    program_id = data.get("program_id")
    university_id = data.get("university_id")
    if not university_id and program_id:
        p = (await db.execute(select(Program).where(Program.id == program_id))).scalar_one_or_none()
        if not p:
            raise HTTPException(404, "Programme not found.")
        university_id = p.university_id
    if not university_id:
        raise HTTPException(422, "Provide program_id or university_id.")
    link = ProgramLink(program_id=program_id, university_id=university_id, url=data["url"],
                       link_type=data.get("link_type", "APPLY"), label=data.get("label"))
    db.add(link)
    if program_id:
        p = (await db.execute(select(Program).where(Program.id == program_id))).scalar_one_or_none()
        if p and not p.apply_link:
            p.apply_link = data["url"]
    await db.commit()
    return {"ok": True, "message": "Link added.", "link_id": link.id}


async def update_link(db: AsyncSession, link_id: str, data: dict) -> dict:
    link = (await db.execute(select(ProgramLink).where(ProgramLink.id == link_id))).scalar_one_or_none()
    if not link:
        raise HTTPException(404, "Link not found.")
    if "url" in data and data["url"] and data["url"] != link.url:
        link.url = data["url"]
        link.is_alive = None
        link.http_status = None
        link.broken_since = None
    for k in ("link_type", "label"):
        if data.get(k) is not None:
            setattr(link, k, data[k])
    await db.commit()
    return {"ok": True, "message": "Link updated.", "link_id": link.id}


async def delete_link(db: AsyncSession, link_id: str) -> dict:
    link = (await db.execute(select(ProgramLink).where(ProgramLink.id == link_id))).scalar_one_or_none()
    if not link:
        raise HTTPException(404, "Link not found.")
    await db.delete(link)
    await db.commit()
    return {"ok": True, "message": "Link deleted."}


async def resolve_link(db: AsyncSession, link_id: str) -> dict:
    link = (await db.execute(select(ProgramLink).where(ProgramLink.id == link_id))).scalar_one_or_none()
    if not link:
        raise HTTPException(404, "Link not found.")
    link.is_alive = True
    link.broken_since = None
    await db.commit()
    return {"ok": True, "message": "Broken-link notification cleared.", "link_id": link.id}


# =====================================================================
#  READ A LINK NOW & COMPARE WITH STORED DATA
# =====================================================================
async def read_and_compare(db: AsyncSession, url: str, program_id: str | None = None) -> dict:
    prog = uni = None
    if program_id:
        prog = (await db.execute(
            select(Program).where(Program.id == program_id))).scalar_one_or_none()
    if prog is None and url:
        # auto-match a stored programme by this URL (apply_link or a saved link)
        prog = (await db.execute(
            select(Program).where(Program.apply_link == url).limit(1))).scalar_one_or_none()
        if prog is None:
            lk = (await db.execute(
                select(ProgramLink).where(ProgramLink.url == url,
                                          ProgramLink.program_id.isnot(None)).limit(1))).scalar_one_or_none()
            if lk:
                prog = (await db.execute(
                    select(Program).where(Program.id == lk.program_id))).scalar_one_or_none()
    if prog:
        program_id = prog.id
        uni = (await db.execute(
            select(University).where(University.id == prog.university_id))).scalar_one_or_none()
    res = await reader_engine.read_criteria_from_url(
        db, url, program_name=prog.name if prog else "", university=uni.name if uni else "")
    stored = {}
    if prog:
        stored = {f: getattr(prog, f, None) for f in CRITERIA_FIELDS if f != "apply_link"}
    return {"ok": res["ok"], "http_status": res["http_status"], "text_chars": res["text_chars"],
            "used_ai": res["used_ai"], "read_criteria": res.get("criteria", {}),
            "stored_criteria": stored, "program_id": program_id,
            "page_title": res.get("page_title"), "followed_url": res.get("followed_url"),
            "program_name": prog.name if prog else None,
            "university_name": uni.name if uni else None, "message": res["message"]}


async def explore_university(db: AsyncSession, university: str, country_code: str = "",
                             program: str = "", degree_level: str = "MASTER",
                             url: str | None = None,
                             scope: list[str] | None = None) -> dict:
    """Read a university/programme that may not be stored yet, and report what
    was found. Nothing is written — the admin decides whether to save it."""
    university = (university or "").strip()
    if not university:
        raise HTTPException(422, "Provide the university name.")
    code = (country_code or "").strip().upper()
    codes = _scoped_codes(scope)
    if code and codes is not None and code not in codes:
        raise HTTPException(403, "You are not assigned to this country.")

    country = await get_country(db, code) if code else None
    country_name = country.name if country else code

    # is it already stored?
    existing = None
    if country is not None:
        existing = (await db.execute(
            select(University).where(University.country_id == country.id,
                                     University.slug == _slug(university)))).scalar_one_or_none()
    if existing is None:
        existing = (await db.execute(
            select(University).where(University.slug == _slug(university)))).scalar_one_or_none()

    res = await reader_engine.discover_and_read(
        db, university=university, program=program, country=country_name, url=url)
    res["university"] = university
    res["program"] = program
    res["degree_level"] = (degree_level or "MASTER").upper()
    res["country_code"] = code
    res["already_exists"] = existing is not None
    res["existing_university_id"] = existing.id if existing else None
    if existing is not None:
        res["message"] += f" (Note: '{existing.name}' is already in the database.)"
    return res


async def save_explored(db: AsyncSession, data: dict,
                        scope: list[str] | None = None) -> dict:
    """Store a university + programme that was just read (criteria included)."""
    name = (data.get("university") or "").strip()
    code = (data.get("country_code") or "").strip().upper()
    if not name:
        raise HTTPException(422, "Provide the university name.")
    if not code:
        raise HTTPException(422, "Provide the country.")
    codes = _scoped_codes(scope)
    if codes is not None and code not in codes:
        raise HTTPException(403, "You are not assigned to this country.")

    country = await get_country(db, code)
    if country is None:
        cname = (data.get("country_name") or "").strip() or code
        country = Country(name=cname, code=code, slug=_slug(cname))
        db.add(country)
        await db.flush()

    uslug = _slug(name)
    uni = (await db.execute(
        select(University).where(University.country_id == country.id,
                                 University.slug == uslug))).scalar_one_or_none()
    created_uni = False
    if uni is None:
        uni = University(country_id=country.id, name=name, slug=uslug,
                         city=(data.get("city") or None),
                         type=(data.get("university_type") or None),
                         website=(data.get("website") or None), origin="manual")
        db.add(uni)
        await db.flush()
        created_uni = True
    else:
        if data.get("city") and not uni.city:
            uni.city = data["city"]
        if data.get("university_type") and not uni.type:
            uni.type = data["university_type"]

    crit = data.get("criteria") or {}
    prog_payload = {
        "name": (data.get("program") or "").strip() or "(unnamed programme)",
        "degree_level": (data.get("degree_level") or "MASTER").upper(),
        "apply_link": data.get("url") or None,
        "status": crit.get("status") if crit.get("status") != "UNKNOWN" else None,
        "application_opening_date": crit.get("application_opening_date"),
        "deadline": crit.get("deadline"),
        "apply_fee": crit.get("apply_fee"),
        "cgpa_required": crit.get("cgpa_required"),
        "english_required": crit.get("english_required"),
        "requirements_text": data.get("requirements_text") or None,
        "notes": f"Read from {data.get('url') or 'the web'}" if data.get("url") else None,
    }
    prog = await _add_program(db, uni, country.id, prog_payload)
    await db.commit()
    return {"ok": True, "university_id": uni.id, "program_id": prog.id,
            "message": (("University created" if created_uni else "Added to existing university")
                        + f" — '{prog.name}' saved with the criteria that were read.")}


async def read_university_now(db: AsyncSession, university_id: str,
                              scope: list[str] | None = None) -> dict:
    """Read every link of ONE university right now (fast) and return the
    stored-vs-read comparison immediately. Proves the reader works on a
    freshly-added university without running a whole-country read."""
    uni = (await db.execute(
        select(University).options(selectinload(University.country))
        .where(University.id == university_id))).scalar_one_or_none()
    if not uni:
        raise HTTPException(404, "University not found.")
    codes = _scoped_codes(scope)
    if codes is not None and uni.country and uni.country.code not in codes:
        raise HTTPException(403, "You are not assigned to this country.")
    result = await reader_engine.run_reader(
        db, countries=None, university_ids=[university_id], deep=True)
    run_id = result.get("run_id")
    compare = await run_compare(db, run_id, scope=scope) if run_id else {"rows": []}
    return {"ok": True, "university_id": university_id, "university_name": uni.name,
            "run_id": run_id, "checked": result.get("checked", 0),
            "alive": result.get("alive", 0), "broken": result.get("broken", 0),
            "programs_read": result.get("programs_read", 0),
            "changes": result.get("changes", 0), "used_ai": result.get("used_ai", False),
            "message": result.get("message", ""), "compare": compare}
