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
                             website=None, contact=rec.contact or None, origin="excel")
            db.add(uni)
            await db.flush()
            universities[ukey] = uni
        elif rec.contact and not uni.contact:
            uni.contact = rec.contact

        # ── programme (criteria inline) ──
        fee_amt, fee_cur = _parse_fee(rec.apply_fee)
        status = (rec.status or "").upper().strip()
        if status in ("CLOSE", "CLOSED", "CLOSED NOW"):
            status = "CLOSE"
        elif status.startswith("OPEN"):
            status = "OPEN"
        prog = Program(
            university_id=uni.id, country_id=country.id,
            name=rec.name, degree_level=rec.degree_level,
            language=rec.language or None,
            application_opening_date=rec.application_opening_date or None,
            deadline=rec.deadline or None,
            apply_link=rec.apply_link or None,
            portal_name=rec.portal_name or None,
            apply_fee=rec.apply_fee or None,
            fee_amount=fee_amt, fee_currency=fee_cur,
            cgpa_required=rec.cgpa_required or None,
            status=status or None,
            english_required=rec.english_required or None,
            requirements_text=rec.requirements_text or None,
            notes=rec.notes or None,
            source_sheet=rec.source_sheet, source_row=rec.source_row or None,
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
