"""
app/modules/countries/verify.py
===============================
Verify the database matches the source Excel sheets (counts per country,
split by degree level). Only Excel-sourced rows are compared.
"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.countries import excel_extract as ex
from app.modules.countries.models import Country, Program, University


async def verify_excel(db: AsyncSession) -> dict:
    records = ex.extract()
    summary = ex.summarise(records)   # {code: {country, universities, master, bachelor}}

    db_unis = dict((code, n) for code, n in (await db.execute(
        select(Country.code, func.count(University.id))
        .join(University, University.country_id == Country.id)
        .where(University.origin == "excel").group_by(Country.code))).all())
    db_deg = {}
    for code, deg, n in (await db.execute(
            select(Country.code, Program.degree_level, func.count(Program.id))
            .join(Program, Program.country_id == Country.id)
            .where(Program.origin == "excel")
            .group_by(Country.code, Program.degree_level))).all():
        db_deg[(code, deg)] = n

    rows = []
    all_match = True
    for code, exp in sorted(summary.items()):
        db_m = db_deg.get((code, "MASTER"), 0)
        db_b = db_deg.get((code, "BACHELOR"), 0)
        match = (db_unis.get(code, 0) == exp["universities"]
                 and db_m == exp["master"] and db_b == exp["bachelor"])
        all_match = all_match and match
        rows.append({"country": exp["country"],
                     "db_universities": db_unis.get(code, 0),
                     "excel_universities": exp["universities"],
                     "db_master": db_m, "excel_master": exp["master"],
                     "db_bachelor": db_b, "excel_bachelor": exp["bachelor"],
                     "match": match})
    msg = ("Database matches the Excel sheets." if all_match
           else "Some counts differ — see the rows below.")
    return {"rows": rows, "all_match": all_match, "message": msg}
