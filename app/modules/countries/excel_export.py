"""
app/modules/countries/excel_export.py
=====================================
Build polished .xlsx workbooks straight from the database.

Two exports are offered:
  * programmes_workbook() - a country's universities/programmes and their criteria
  * run_workbook()        - what a reader run READ vs what was STORED, side by side

Styling is deliberately plain-professional: a branded header row, frozen panes,
auto-filter, sensible column widths, wrapped text and subtle banding.
"""
from __future__ import annotations

import io
from datetime import datetime, timezone

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

BRAND = "2F6BFF"
HEAD_FILL = PatternFill("solid", fgColor=BRAND)
HEAD_FONT = Font(color="FFFFFF", bold=True, size=11)
TITLE_FONT = Font(bold=True, size=14, color="1F2937")
MUTED_FONT = Font(size=9, color="6B7280")
CHANGED_FILL = PatternFill("solid", fgColor="FFF4CC")     # soft amber
BAND_FILL = PatternFill("solid", fgColor="F7F9FC")
THIN = Side(style="thin", color="E3E8EF")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)

MASTERS = "Master's"
BACHELORS = "Bachelor's"


def _degree_label(value: str | None) -> str:
    return BACHELORS if (value or "").upper() == "BACHELOR" else MASTERS


def _style_sheet(ws, headers: list[str], n_rows: int, start_row: int = 1) -> None:
    """Apply the house style to a header row + its data block."""
    for col, name in enumerate(headers, start=1):
        c = ws.cell(row=start_row, column=col, value=name)
        c.fill, c.font = HEAD_FILL, HEAD_FONT
        c.alignment = Alignment(vertical="center", horizontal="left", wrap_text=True)
        c.border = BORDER
    ws.row_dimensions[start_row].height = 26
    ws.freeze_panes = ws.cell(row=start_row + 1, column=1)
    if n_rows:
        last = get_column_letter(len(headers))
        ws.auto_filter.ref = f"A{start_row}:{last}{start_row + n_rows}"

    # widths from content, with sane caps
    for col in range(1, len(headers) + 1):
        longest = len(str(headers[col - 1]))
        for row in range(start_row + 1, start_row + n_rows + 1):
            v = ws.cell(row=row, column=col).value
            if v is not None:
                longest = max(longest, min(len(str(v)), 70))
        ws.column_dimensions[get_column_letter(col)].width = min(max(longest + 2, 10), 48)

    # borders + banding + wrapping for the data block
    for i, row in enumerate(range(start_row + 1, start_row + n_rows + 1)):
        for col in range(1, len(headers) + 1):
            c = ws.cell(row=row, column=col)
            c.border = BORDER
            c.alignment = Alignment(vertical="top", wrap_text=True)
            if i % 2:
                c.fill = BAND_FILL


def _meta(ws, title: str, subtitle: str) -> None:
    """A small title block placed above the table (rows 1-2)."""
    ws.cell(row=1, column=1, value=title).font = TITLE_FONT
    ws.cell(row=2, column=1, value=subtitle).font = MUTED_FONT


def _save(wb: Workbook) -> bytes:
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC")


# =====================================================================
#  PROGRAMMES EXPORT
# =====================================================================
PROGRAM_HEADERS = [
    "Country", "University", "City", "Institution type", "Programme", "Degree",
    "Field", "Language", "Status", "Application opens", "Deadline",
    "Application fee", "Fee amount", "Currency", "CGPA required",
    "English required", "Apply / portal link", "Requirements", "Notes",
]


def programmes_workbook(rows: list[dict], scope_label: str) -> bytes:
    """rows: dicts as returned by repository.search_programs()."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Programmes"
    _meta(ws, f"Admissions programmes - {scope_label}",
          f"Exported {_stamp()} | {len(rows)} programme(s)")

    start = 4
    for i, p in enumerate(rows):
        r = start + 1 + i
        vals = [
            p.get("country_name") or p.get("country_code"),
            p.get("university_name"), p.get("university_city"), p.get("university_type"),
            p.get("name"), _degree_label(p.get("degree_level")),
            p.get("field"), p.get("language"), p.get("status"),
            p.get("application_opening_date"), p.get("deadline"),
            p.get("apply_fee"), p.get("fee_amount"), p.get("fee_currency"),
            p.get("cgpa_required"), p.get("english_required"), p.get("apply_link"),
            p.get("requirements_text"), p.get("notes"),
        ]
        for col, v in enumerate(vals, start=1):
            ws.cell(row=r, column=col, value=v)
    _style_sheet(ws, PROGRAM_HEADERS, len(rows), start_row=start)

    # summary tab
    s = wb.create_sheet("Summary")
    masters = sum(1 for p in rows if (p.get("degree_level") or "").upper() == "MASTER")
    unis = len({p.get("university_name") for p in rows})
    open_now = sum(1 for p in rows if (p.get("status") or "").upper() == "OPEN")
    with_link = sum(1 for p in rows if p.get("apply_link"))
    _meta(s, "Summary", f"Exported {_stamp()}")
    pairs = [("Scope", scope_label), ("Universities", unis), ("Programmes", len(rows)),
             (MASTERS, masters), (BACHELORS, len(rows) - masters),
             ("Currently open", open_now), ("With an apply link", with_link)]
    for i, (k, v) in enumerate(pairs):
        s.cell(row=5 + i, column=1, value=k).font = Font(bold=True)
        s.cell(row=5 + i, column=2, value=v)
    s.column_dimensions["A"].width = 24
    s.column_dimensions["B"].width = 40
    return _save(wb)


# =====================================================================
#  READER-RUN EXPORT (read vs stored)
# =====================================================================
_CRIT = [("status", "Status"), ("application_opening_date", "Application opens"),
         ("deadline", "Deadline"), ("apply_fee", "Application fee"),
         ("cgpa_required", "CGPA required"), ("english_required", "English required")]


def run_workbook(compare: dict, scope_label: str) -> bytes:
    """compare: dict as returned by repository.run_compare()."""
    rows = compare.get("rows") or []
    headers = ["University", "Programme", "Degree", "Country", "Reachable", "HTTP",
               "Chars read", "Changes", "Page title", "Source URL"]
    for _key, label in _CRIT:
        headers += [f"{label} - stored", f"{label} - read"]

    wb = Workbook()
    ws = wb.active
    ws.title = "Read vs stored"
    engine = "AI" if compare.get("used_ai") else "regex"
    _meta(ws, f"Reader results - {scope_label}",
          f"Exported {_stamp()} | {len(rows)} programme(s) | engine: {engine}")

    start = 4
    changed_cells = []
    for i, r in enumerate(rows):
        row_i = start + 1 + i
        base = [r.get("university"), r.get("program"), _degree_label(r.get("degree_level")),
                r.get("country_code"),
                "Yes" if r.get("link_alive") else "No", r.get("http_status"),
                r.get("read_chars"), r.get("changed_count"),
                r.get("page_title"), r.get("source_url")]
        for col, v in enumerate(base, start=1):
            ws.cell(row=row_i, column=col, value=v)
        by_field = {f.get("field"): f for f in (r.get("fields") or [])}
        col = len(base) + 1
        for key, _label in _CRIT:
            f = by_field.get(key) or {}
            ws.cell(row=row_i, column=col, value=f.get("stored"))
            ws.cell(row=row_i, column=col + 1, value=f.get("read"))
            if f.get("changed"):
                changed_cells.append((row_i, col + 1))
            col += 2
    _style_sheet(ws, headers, len(rows), start_row=start)
    for row_i, col in changed_cells:          # highlight differences after banding
        ws.cell(row=row_i, column=col).fill = CHANGED_FILL

    legend = wb.create_sheet("Legend")
    _meta(legend, "How to read this export", f"Exported {_stamp()}")
    notes = [
        ("Stored", "The value currently saved in the database."),
        ("Read", "The value the reader found on the live page."),
        ("Amber cell", "The read value differs from the stored value (awaiting approval)."),
        ("Changes", "How many criteria differ for that programme."),
        ("Source URL", "The exact page the reader opened."),
    ]
    for i, (k, v) in enumerate(notes):
        legend.cell(row=5 + i, column=1, value=k).font = Font(bold=True)
        legend.cell(row=5 + i, column=2, value=v)
    legend.cell(row=7, column=1).fill = CHANGED_FILL
    legend.column_dimensions["A"].width = 16
    legend.column_dimensions["B"].width = 70
    return _save(wb)
