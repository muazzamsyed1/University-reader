"""
app/modules/countries/excel_extract.py
=======================================
Parse the admission workbooks into normalised programme records.

Supported workbooks (auto-detected by sheet layout, not file name):
  • France_Masters_*.xlsx   → sheet "France Masters"  (all Master's)
  • Italy_*_Admissions_*.xlsx → sheets "MS Programs" (Master's) + "BS Programs" (Bachelor's)

Parsing is header-driven: each column is located by matching its header text,
so column re-ordering or small header edits do not break the import.
"""
from __future__ import annotations

import datetime as _dt
import glob
import os
import re
import unicodedata
from dataclasses import dataclass

import openpyxl

from app.core.config import settings

# ── country inference from file / sheet ──────────────────────────
_COUNTRY_BY_HINT = [
    ("france", ("France", "FR")),
    ("italy", ("Italy", "IT")),
    ("german", ("Germany", "DE")),
    ("czech", ("Czech Republic", "CZ")),
    ("austri", ("Austria", "AT")),
]


def _clean(v) -> str:
    if v is None:
        return ""
    if isinstance(v, _dt.datetime):
        # drop a midnight time component; keep real times if present
        v = v.date() if (v.hour == v.minute == v.second == 0) else v
    if isinstance(v, _dt.date):
        return v.isoformat()
    s = str(v).strip()
    # repair mojibake smart-quotes that show up as the replacement char
    s = s.replace("�", "'").replace("’", "'").replace("‘", "'")
    return re.sub(r"\s+", " ", s).strip()


def _norm_header(v) -> str:
    return re.sub(r"[^a-z0-9]+", " ", _clean(v).lower()).strip()


@dataclass
class ProgramRecord:
    country_name: str
    country_code: str
    university: str
    name: str
    degree_level: str                 # MASTER / BACHELOR
    language: str = ""
    field: str = ""
    cgpa_required: str = ""
    english_required: str = ""
    portal_name: str = ""
    requirements_text: str = ""
    apply_link: str = ""
    additional_link: str = ""
    notes: str = ""
    application_opening_date: str = ""
    deadline: str = ""
    apply_fee: str = ""
    status: str = ""
    contact: str = ""
    university_city: str = ""
    university_type: str = ""
    university_website: str = ""
    source_sheet: str = ""
    source_row: str = ""


def _country_for(path: str, sheet: str) -> tuple[str, str]:
    """Identify the country. The SHEET name wins over the file name, so one
    workbook may hold sheets for several countries."""
    for hay in (sheet.lower(), os.path.basename(path).lower()):
        for hint, (name, code) in _COUNTRY_BY_HINT:
            if hint in hay:
                return name, code
    return "Unknown", "XX"


# Sheets holding personal/student records are never imported (privacy) and are
# not programme catalogues anyway.
_PII_HEADERS = ("student name", "password", "passowrd", "email id", "contact no",
                "counselor", "processor assigned", "refund status")


def _is_personal_data_sheet(header_keys: list[str]) -> bool:
    joined = " | ".join(header_keys)
    return any(p in joined for p in _PII_HEADERS)


class _Row:
    """Header-indexed access to a worksheet row."""

    def __init__(self, header: list[str]):
        self.index: dict[str, int] = {}
        for i, h in enumerate(header):
            key = _norm_header(h)
            if key and key not in self.index:
                self.index[key] = i

    def find(self, *patterns: str) -> int | None:
        for pat in patterns:
            p = _norm_header(pat)
            for key, i in self.index.items():
                if p and p in key:
                    return i
        return None


def _get(row: tuple, idx: int | None) -> str:
    if idx is None or idx >= len(row):
        return ""
    return _clean(row[idx])


def _degree_from(text: str, default: str) -> str:
    t = text.lower()
    if "bachelor" in t or t.strip() in ("bs", "bsc", "ba"):
        return "BACHELOR"
    if "master" in t or "phd" in t or "doctor" in t or t.strip() in ("ms", "msc", "ma"):
        return "MASTER"
    return default


def _parse_sheet(path: str, ws, default_degree: str) -> list[ProgramRecord]:
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []
    hdr = _Row([_clean(c) for c in rows[0]])
    cname, ccode = _country_for(path, ws.title)

    # never import sheets of personal/student records
    if _is_personal_data_sheet(list(hdr.index.keys())):
        return []

    c_uni = hdr.find("university")
    c_prog = hdr.find("program track", "master s program", "programme", "program")
    c_degree = hdr.find("degree level", "degree")
    c_lang = hdr.find("teaching language", "language")
    c_faculty = hdr.find("faculty")
    c_city = hdr.find("city")
    c_utype = hdr.find("type")
    c_subject = hdr.find("field")
    c_start = hdr.find("study start")
    c_cgpa = hdr.find("required cgpa", "cgpa requirement", "cgpa")
    c_eng = hdr.find("english requirement", "english certificate", "english")
    c_portal = hdr.find("portal name", "portal")
    c_reqs = hdr.find("program specific admission criteria", "program requirements",
                      "group requirements", "admission criteria")
    c_link = hdr.find("official link", "university application link", "application link from",
                      "application link", "apply link", "links", "link")
    c_link2 = hdr.find("additional official link", "additional application link")
    c_notes = hdr.find("program notes", "other application information", "notes")
    c_open = hdr.find("application opening date", "opening date", "admission opening date",
                      "application opens", "opens")
    c_dead = hdr.find("application deadline", "deadline")
    # "application fee" must win over "tuition fee"; find the application-fee column first
    c_fee = hdr.find("application fee")
    if c_fee is None:
        c_fee = hdr.find("fee")
    c_status = hdr.find("program status", "status")
    c_contact = hdr.find("contact")
    c_srow = hdr.find("ms source row", "bs source row", "bachelor s source form",
                      "source rows", "source row", "source form")
    # Czech-specific extra columns — folded into requirements/notes so no data is lost
    c_prevdeg = hdr.find("previous degree requirement")
    c_exam = hdr.find("online exam selection details", "online test selection details",
                      "selection details", "exam selection")
    c_tuition = hdr.find("tuition")
    c_deadnotes = hdr.find("deadline notes", "deadline note")
    # the sheet states its own currency, e.g. "Tuition (EUR per year)"
    sheet_currency = ""
    for key in hdr.index:
        m = re.search(r"\b(eur|usd|gbp|czk|chf|pln)\b", key)
        if m:
            sheet_currency = m.group(1).upper()
            break

    out: list[ProgramRecord] = []
    cur_uni = cur_site = ""          # carried from "group header" rows
    for r in rows[1:]:
        if not r:
            continue
        uni = _get(r, c_uni)
        prog = _get(r, c_prog)
        faculty = _get(r, c_faculty)
        # Grouped layout: a row that names a university but has no programme is a
        # header for the rows beneath it (university name may sit in Faculty).
        if not prog:
            header_name = uni or faculty
            if header_name:
                cur_uni = header_name
                cur_site = _get(r, c_link)
            continue
        uni = uni or cur_uni
        if not uni:
            continue
        degree = _degree_from(_get(r, c_degree), default_degree)

        # requirements_text = base criteria + previous-degree + exam/selection details
        reqs = [x for x in (_get(r, c_reqs),
                            ("Previous degree: " + _get(r, c_prevdeg)) if _get(r, c_prevdeg) else "",
                            ("Selection / exam: " + _get(r, c_exam)) if _get(r, c_exam) else "")
                if x]
        # notes = base notes/deadline-notes + study start + faculty + tuition
        base_notes = _get(r, c_notes) or (_get(r, c_deadnotes) if c_deadnotes != c_notes else "")
        tuition = _get(r, c_tuition)
        cur = sheet_currency or "EUR"
        notes = [x for x in (
            base_notes,
            ("Faculty: " + faculty) if (faculty and faculty != uni) else "",
            ("Study start: " + _get(r, c_start)) if _get(r, c_start) else "",
            (f"Tuition: {tuition} {cur}/year") if tuition else "") if x]

        # application fee — always carry the currency when the sheet states one
        fee = _get(r, c_fee)
        if fee and sheet_currency and not re.search(r"[€£$]|[A-Za-z]{3}", fee):
            fee = f"{fee} {sheet_currency}"

        out.append(ProgramRecord(
            country_name=cname, country_code=ccode,
            university=uni or "Unknown University",
            name=prog or "(unnamed programme)",
            degree_level=degree,
            language=_get(r, c_lang),
            field=_get(r, c_subject),
            cgpa_required=_get(r, c_cgpa),
            english_required=_get(r, c_eng),
            portal_name=_get(r, c_portal),
            requirements_text=" | ".join(reqs),
            apply_link=_get(r, c_link),
            additional_link=_get(r, c_link2),
            notes=" | ".join(notes),
            application_opening_date=_get(r, c_open),
            deadline=_get(r, c_dead),
            apply_fee=fee,
            status=_get(r, c_status),
            contact=_get(r, c_contact),
            university_city=_get(r, c_city),
            university_type=_get(r, c_utype),
            university_website=cur_site,
            source_sheet=f"{os.path.basename(path)} :: {ws.title}",
            source_row=_get(r, c_srow),
        ))
    return out


def extract(folder: str | None = None) -> list[ProgramRecord]:
    """Read every .xlsx in the folder and return all programme records."""
    folder = folder or settings.EXCEL_DIR
    records: list[ProgramRecord] = []
    for path in sorted(glob.glob(os.path.join(folder, "*.xlsx"))):
        if os.path.basename(path).startswith("~$"):
            continue
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        for ws in wb.worksheets:
            title = ws.title.lower()
            default = "BACHELOR" if ("bs" in title or "bachelor" in title) else "MASTER"
            records.extend(_parse_sheet(path, ws, default))
        wb.close()
    return records


def _ukey(name: str) -> str:
    """Normalise a university name the same way the importer slugs it, so the
    Excel-side unique count matches the de-duplicated DB rows."""
    n = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", n.lower()).strip("-")[:180]


def summarise(records: list[ProgramRecord]) -> dict:
    by_country: dict[str, dict] = {}
    for r in records:
        c = by_country.setdefault(r.country_code, {"country": r.country_name,
                                                   "universities": set(), "MASTER": 0, "BACHELOR": 0})
        c["universities"].add(_ukey(r.university))
        c[r.degree_level] = c.get(r.degree_level, 0) + 1
    return {code: {"country": v["country"], "universities": len(v["universities"]),
                   "master": v["MASTER"], "bachelor": v["BACHELOR"]}
            for code, v in by_country.items()}
