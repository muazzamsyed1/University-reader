"""
app/modules/countries/excel_import.py
=====================================
Load the admission workbooks into the database (fresh rebuild / re-sync).

`import_all()` wipes the DATA tables (countries → universities → programs →
links, plus reader snapshots/changes) and reloads them from the Excel records.
Admin accounts, API keys and the reader schedule are left untouched.
"""
from __future__ import annotations

import re
import unicodedata

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.countries import excel_extract as ex
from app.modules.countries.models import (
    Country, LinkChange, LinkSnapshot, Program, ProgramLink, ReaderRun, University,
)

_CUR_MAP = {"EUR": "EUR", "EURO": "EUR", "€": "EUR", "USD": "USD", "$": "USD",
            "GBP": "GBP", "£": "GBP", "CZK": "CZK", "KČ": "CZK"}
_FEE_RE = re.compile(r"(€|£|\$|EUR|EURO|USD|GBP|CZK|K[Čc])?\s*([\d][\d.,\s]*\d|\d)\s*"
                     r"(€|£|\$|EUR|EURO|USD|GBP|CZK|K[Čc])?", re.IGNORECASE)


def _norm_status(raw: str) -> str | None:
    """Map a free-text status from a sheet onto OPEN / CLOSE (column is short).
    Anything unclear returns None; the original wording is kept in notes."""
    s = (raw or "").strip().lower()
    if not s:
        return None
    if "not open" in s or "not yet" in s or "opens " in s:
        return "CLOSE"                      # announced but not open yet
    if "clos" in s:
        return "CLOSE"
    if "open" in s:
        return "OPEN"
    return None


def _cut(value: str | None, limit: int) -> str | None:
    """Defensively trim a value to its column size."""
    if value is None:
        return None
    v = str(value).strip()
    return (v[:limit] or None) if v else None


def _slug(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text[:180] or "x"


def _parse_fee(raw: str) -> tuple[float | None, str | None]:
    if not raw:
        return None, None
    m = _FEE_RE.search(raw)
    if not m:
        return None, None
    cur = (m.group(1) or m.group(3) or "").upper()
    currency = _CUR_MAP.get(cur)
    num = m.group(2).replace(" ", "").replace(",", "")
    try:
        if num.count(".") > 1:
            num = num.replace(".", "")
        return float(num), currency
    except ValueError:
        return None, currency


async def _wipe_data(db: AsyncSession) -> None:
    for model in (LinkChange, LinkSnapshot, ReaderRun, ProgramLink, Program, University, Country):
        await db.execute(delete(model))
    await db.flush()


async def _wipe_country(db: AsyncSession, code: str) -> None:
    """Delete one country's data (universities, programmes, links, reader rows)
    without touching any other country."""
    country = (await db.execute(select(Country).where(Country.code == code))).scalar_one_or_none()
    if country is None:
        return
    uni_ids = (await db.execute(
        select(University.id).where(University.country_id == country.id))).scalars().all()
    prog_ids = (await db.execute(
        select(Program.id).where(Program.country_id == country.id))).scalars().all()
    link_ids = (await db.execute(
        select(ProgramLink.id).where(ProgramLink.university_id.in_(uni_ids)))).scalars().all() \
        if uni_ids else []
    if link_ids:
        await db.execute(delete(LinkSnapshot).where(LinkSnapshot.link_id.in_(link_ids)))
    if prog_ids:
        await db.execute(delete(LinkSnapshot).where(LinkSnapshot.program_id.in_(prog_ids)))
    await db.execute(delete(LinkChange).where(LinkChange.country_id == country.id))
    if prog_ids:
        await db.execute(delete(LinkChange).where(LinkChange.program_id.in_(prog_ids)))
    if uni_ids:
        await db.execute(delete(ProgramLink).where(ProgramLink.university_id.in_(uni_ids)))
    await db.execute(delete(Program).where(Program.country_id == country.id))
    await db.execute(delete(University).where(University.country_id == country.id))
    await db.execute(delete(Country).where(Country.id == country.id))
    await db.flush()


async def import_country(db: AsyncSession, code: str, folder: str | None = None,
                         replace: bool = True) -> dict:
    """Import a SINGLE country additively (default: replace just that country's
    data). France/Italy/Pakistan and everything else are left untouched."""
    code = code.upper()
    records = [r for r in ex.extract(folder) if r.country_code == code]
    if not records:
        return {"country": code, "universities": 0, "programs": 0, "links": 0,
                "message": f"No rows found for {code} in the Excel folder."}
    if replace:
        await _wipe_country(db, code)
    res = await _insert_records(db, records)
    res["country"] = code
    res["message"] = (f"Imported {code}: {res['universities']} universities, "
                      f"{res['programs']} programmes, {res['links']} links.")
    return res


async def _insert_records(db: AsyncSession, records: list) -> dict:
    """Insert programme records (countries/universities deduped). Caller commits."""
    countries: dict[str, Country] = {}
    universities: dict[tuple[str, str], University] = {}
    n_programs = n_links = 0

    for rec in records:
        # ── country (reuse an existing row if present, e.g. additive import) ──
        country = countries.get(rec.country_code)
        if country is None:
            country = (await db.execute(
                select(Country).where(Country.code == rec.country_code))).scalar_one_or_none()
            if country is None:
                country = Country(name=rec.country_name, code=rec.country_code,
                                  slug=_slug(rec.country_name), is_active=True)
                db.add(country)
                await db.flush()
            countries[rec.country_code] = country

        # ── university (dedupe by country + normalised name) ──
        uslug = _slug(rec.university)
        ukey = (rec.country_code, uslug)
        uni = universities.get(ukey)
        if uni is None:
            uni = University(country_id=country.id, name=rec.university, slug=uslug,
                             city=rec.university_city or None,
                             type=rec.university_type or None,
                             website=rec.university_website or None,
                             contact=rec.contact or None, origin="excel")
            db.add(uni)
            await db.flush()
            universities[ukey] = uni
        else:
            # fill in any detail that was blank on the first row of this university
            if rec.contact and not uni.contact:
                uni.contact = rec.contact
            if rec.university_city and not uni.city:
                uni.city = rec.university_city
            if rec.university_type and not uni.type:
                uni.type = rec.university_type
            if rec.university_website and not uni.website:
                uni.website = rec.university_website

        # ── programme (criteria inline) ──
        fee_amt, fee_cur = _parse_fee(rec.apply_fee)
        # always show the currency alongside the amount
        fee_text = (rec.apply_fee or "").strip()
        if fee_text and fee_cur and not re.search(r"[€£$₹]|[A-Za-z]{3}", fee_text):
            fee_text = f"{fee_text} {fee_cur}"
        status = _norm_status(rec.status)
        # keep the sheet's exact wording when it says more than OPEN/CLOSE
        notes_text = rec.notes or ""
        raw_status = (rec.status or "").strip()
        if raw_status and raw_status.upper() not in ("OPEN", "CLOSE", "CLOSED"):
            notes_text = (notes_text + " | " if notes_text else "") + f"Status (as listed): {raw_status}"
        prog = Program(
            university_id=uni.id, country_id=country.id,
            name=_cut(rec.name, 600), degree_level=rec.degree_level,
            language=_cut(rec.language, 200),
            field=_cut(rec.field, 200),
            application_opening_date=_cut(rec.application_opening_date, 400),
            deadline=_cut(rec.deadline, 400),
            apply_link=rec.apply_link or None,
            portal_name=_cut(rec.portal_name, 400),
            apply_fee=_cut(fee_text, 400),
            fee_amount=fee_amt, fee_currency=_cut(fee_cur, 10),
            cgpa_required=rec.cgpa_required or None,
            status=_cut(status, 40),
            english_required=rec.english_required or None,
            requirements_text=rec.requirements_text or None,
            notes=notes_text or None,
            source_sheet=_cut(rec.source_sheet, 120), source_row=_cut(rec.source_row, 40),
            origin="excel",
        )
        db.add(prog)
        await db.flush()
        n_programs += 1

        # ── apply/portal links for the reader ──
        for url, label in ((rec.apply_link, "Apply / portal link"),
                           (rec.additional_link, "Additional link")):
            if url and url.lower().startswith("http"):
                db.add(ProgramLink(program_id=prog.id, university_id=uni.id, url=url,
                                   link_type="APPLY", label=label))
                n_links += 1

    await db.commit()
    return {"countries": len(countries), "universities": len(universities),
            "programs": n_programs, "links": n_links,
            "summary": ex.summarise(records)}


async def import_all(db: AsyncSession, folder: str | None = None, wipe: bool = True) -> dict:
    records = ex.extract(folder)
    if wipe:
        await _wipe_data(db)
    return await _insert_records(db, records)
