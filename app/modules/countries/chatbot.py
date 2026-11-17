"""
app/modules/countries/chatbot.py
================================
Database-backed assistant. Answers ONLY from stored data — never invents.

Parses a question for a university (fuzzy), a country, and an intent
(criteria / English / CGPA / fee / deadline / status / links / programmes /
broken links / stats / list), then queries the DB and composes an answer.
Works offline — no API key required.
"""
from __future__ import annotations

import re
import unicodedata

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.modules.countries.models import Country, Program, ProgramLink, University

_STOP = {
    "a", "an", "the", "of", "for", "in", "at", "to", "and", "or", "is", "are",
    "what", "whats", "which", "who", "show", "me", "tell", "about", "give",
    "list", "all", "any", "do", "does", "need", "needed", "require", "required",
    "requirement", "requirements", "eligibility", "criteria", "admission",
    "admissions", "entry", "university", "universities", "uni", "college",
    "institute", "school", "please", "can", "you", "i", "want", "know",
    "programme", "programmes", "program", "programs", "course", "courses",
    "degree", "study", "studying", "apply", "application", "how", "many",
    "much", "cost", "fees", "fee", "deadline", "deadlines", "link", "links",
    "website", "site", "english", "language", "cgpa", "gpa", "there", "status",
    "open", "close", "master", "masters", "bachelor", "bachelors",
}


def _strip_accents(s: str) -> str:
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()


def _tokens(s: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", _strip_accents(s).lower())


def _sig_tokens(s: str) -> set[str]:
    return {t for t in _tokens(s) if t not in _STOP and len(t) >= 2}


async def _match_university(db: AsyncSession, question: str):
    q_norm = _strip_accents(question).lower()
    q_tokens = set(_tokens(q_norm))
    rows = (await db.execute(
        select(University.id, University.name).order_by(University.name))).all()
    crows = (await db.execute(select(Country.name, Country.code))).all()
    country_tokens: set[str] = set()
    for cn, cc in crows:
        country_tokens |= set(_tokens(cn))
        country_tokens.add(cc.lower())

    best, best_score = None, 0.0
    for uid, name in rows:
        name_l = _strip_accents(name).lower()
        if name_l and name_l in q_norm:
            score = 100 + len(name_l)
        else:
            u_sig = _sig_tokens(name) - country_tokens
            if not u_sig:
                continue
            matched = u_sig & q_tokens
            if not matched:
                continue
            coverage = len(matched) / len(u_sig)
            score = len(matched) + 2 * coverage
            if len(matched) == 1 and max(len(t) for t in matched) < 4 and coverage < 1:
                continue
        if score > best_score:
            best_score, best = score, uid
    if best is None or best_score < 1:
        return None
    return (await db.execute(
        select(University).where(University.id == best).options(
            selectinload(University.programs).selectinload(Program.links),
            selectinload(University.links),
        ))).scalar_one_or_none()


async def _match_country(db: AsyncSession, question: str):
    q_tokens = set(_tokens(question))
    rows = (await db.execute(select(Country))).scalars().all()
    for c in rows:
        if c.code.lower() in q_tokens:
            return c
    for c in rows:
        cname = set(_tokens(c.name))
        if cname and cname <= q_tokens:
            return c
    return None


def _fmt_header(uni: University, country_name: str | None) -> str:
    bits = [uni.name]
    sub = ", ".join(x for x in (uni.city, country_name) if x)
    if sub:
        bits.append(f"({sub})")
    return " ".join(bits)


_CRIT_LABEL = [
    ("status", "Status"), ("application_opening_date", "Opens"),
    ("deadline", "Deadline"), ("apply_fee", "Application fee"),
    ("cgpa_required", "Min CGPA"), ("english_required", "English"),
    ("apply_link", "Apply link"),
]


def _criteria_answer(uni: University, country_name: str | None,
                     only: str | None = None) -> str:
    header = _fmt_header(uni, country_name)
    if not uni.programs:
        return f"I have **{header}** on record, but no programmes are stored for it yet."
    lines = [f"**{header}** — admission criteria:"]
    found = False
    for p in uni.programs:
        fields = [(lbl, getattr(p, key)) for key, lbl in _CRIT_LABEL
                  if (only is None or key == only) and getattr(p, key, None)]
        if only is None and p.requirements_text:
            fields.append(("Requirements", p.requirements_text))
        if not fields:
            continue
        found = True
        lines.append(f"\n• {p.name} ({p.degree_level.title()})")
        for lbl, val in fields:
            lines.append(f"   – {lbl}: {val}")
    if not found:
        what = dict(_CRIT_LABEL).get(only or "", "")
        return (f"**{header}** is on record, but I don't have "
                f"{('a ' + what) if what else 'that'} value stored yet.")
    return "\n".join(lines)


def _links_answer(uni: University, country_name: str | None) -> str:
    header = _fmt_header(uni, country_name)
    seen = {}
    for p in uni.programs:
        for l in p.links:
            seen.setdefault(l.url, l)
    for l in uni.links:
        seen.setdefault(l.url, l)
    if uni.website:
        seen.setdefault(uni.website, None)
    if not seen:
        return f"I don't have any links stored for **{header}** yet."
    lines = [f"**{header}** — links:"]
    for url, l in seen.items():
        if l is None:
            lines.append(f"• {url}  (website)")
        else:
            status = ("live" if l.is_alive is True else
                      "broken" if l.is_alive is False else "unchecked")
            lines.append(f"• {url}  [{l.link_type} · {status}]")
    return "\n".join(lines)


def _programmes_answer(uni: University, country_name: str | None) -> str:
    header = _fmt_header(uni, country_name)
    if not uni.programs:
        return f"**{header}** has no programmes stored yet."
    masters = [p for p in uni.programs if p.degree_level == "MASTER"]
    bachelors = [p for p in uni.programs if p.degree_level == "BACHELOR"]
    lines = [f"**{header}** — {len(uni.programs)} programme(s) "
             f"({len(masters)} Master's, {len(bachelors)} Bachelor's):"]
    for p in uni.programs[:80]:
        lines.append(f"• {p.name} ({p.degree_level.title()})")
    return "\n".join(lines)


async def _country_list_answer(db: AsyncSession, country: Country) -> str:
    rows = (await db.execute(
        select(University.name, University.city)
        .where(University.country_id == country.id).order_by(University.name))).all()
    if not rows:
        return f"I don't have any universities stored for {country.name} yet."
    lines = [f"**{country.name}** — {len(rows)} universit{'y' if len(rows) == 1 else 'ies'}:"]
    for name, city in rows[:60]:
        lines.append(f"• {name}" + (f" — {city}" if city else ""))
    if len(rows) > 60:
        lines.append(f"…and {len(rows) - 60} more.")
    return "\n".join(lines)


async def _stats_answer(db: AsyncSession) -> str:
    async def _c(model, *where):
        stmt = select(func.count()).select_from(model)
        for w in where:
            stmt = stmt.where(w)
        return (await db.execute(stmt)).scalar_one()
    countries = await _c(Country)
    unis = await _c(University)
    masters = await _c(Program, Program.degree_level == "MASTER")
    bachelors = await _c(Program, Program.degree_level == "BACHELOR")
    links = await _c(ProgramLink)
    broken = await _c(ProgramLink, ProgramLink.is_alive.is_(False))
    return ("Here's what's in the database right now:\n"
            f"• {countries} countries\n• {unis} universities\n"
            f"• {masters} Master's + {bachelors} Bachelor's programmes\n"
            f"• {links} links ({broken} currently broken)")


async def _broken_answer(db: AsyncSession, country: Country | None) -> str:
    stmt = (select(University.name, ProgramLink.url, ProgramLink.http_status)
            .join(ProgramLink, ProgramLink.university_id == University.id)
            .where(ProgramLink.is_alive.is_(False)))
    if country:
        stmt = stmt.where(University.country_id == country.id)
    rows = (await db.execute(stmt.limit(40))).all()
    if not rows:
        scope = f" for {country.name}" if country else ""
        return f"Good news — there are no broken links{scope} right now."
    lines = [f"{len(rows)} broken link(s)" + (f" in {country.name}" if country else "") + ":"]
    for name, url, status in rows:
        code = f" (HTTP {status})" if status else ""
        lines.append(f"• {name}: {url}{code}")
    return "\n".join(lines)


_HELP = (
    "I'm the admissions assistant. I answer from the database. Try:\n"
    "• “Admission criteria for <university>”\n"
    "• “English / CGPA / fee / deadline / status for <university>”\n"
    "• “Apply link for <university>”\n"
    "• “List universities in Italy”\n"
    "• “How many programmes are in the database?”\n"
    "• “Show broken links”"
)


def _detect_intent(q: str) -> str:
    ql = f" {q.lower()} "

    def has(*words):
        return any(f" {w} " in ql or ql.strip().startswith(w) or ql.strip().endswith(w)
                   for w in words)
    if has("broken", "down", "dead link", "not working", "unreachable"):
        return "broken"
    if (has("how many", "count", "total", "number of", "stats", "statistics",
            "overview", "summary") and not has("requirement", "requirements")):
        return "stats"
    if has("ielts", "toefl", "english", "cefr", "language proficiency"):
        return "english_required"
    if has("cgpa", "gpa", "grade point", "percentage", "marks"):
        return "cgpa_required"
    if has("fee", "fees", "cost", "price", "how much"):
        return "apply_fee"
    if has("deadline", "last date", "closing date", "apply by", "due date"):
        return "deadline"
    if has("status", "open", "closed", "close", "accepting"):
        return "status"
    if has("link", "links", "website", "url", "portal", "apply link"):
        return "links"
    if has("programme", "programmes", "program", "programs", "courses", "which course"):
        return "programmes"
    if has("list", "show all", "universities in", "unis in", "which universities"):
        return "list"
    return "criteria"


async def answer(db: AsyncSession, question: str) -> dict:
    q = (question or "").strip()
    if not q:
        return {"answer": _HELP, "matched_university": None}
    if re.fullmatch(r"(hi|hello|hey|help|start|menu|yo|sup)[.! ]*", q.lower()):
        return {"answer": _HELP, "matched_university": None}

    intent = _detect_intent(q)
    country = await _match_country(db, q)

    if intent == "stats":
        return {"answer": await _stats_answer(db), "matched_university": None}
    if intent == "broken":
        return {"answer": await _broken_answer(db, country), "matched_university": None}
    if intent == "list":
        if country:
            return {"answer": await _country_list_answer(db, country), "matched_university": None}
        return {"answer": "Which country? e.g. “list universities in Italy”.",
                "matched_university": None}

    uni = await _match_university(db, q)
    if uni is None:
        if country:
            return {"answer": await _country_list_answer(db, country), "matched_university": None}
        return {"answer": "I couldn't find a university by that name. "
                          "Try the exact name, or ask “list universities in <country>”.\n\n" + _HELP,
                "matched_university": None}

    cname = (await db.execute(
        select(Country.name).where(Country.id == uni.country_id))).scalar_one_or_none()

    if intent == "links":
        text = _links_answer(uni, cname)
    elif intent == "programmes":
        text = _programmes_answer(uni, cname)
    elif intent in ("english_required", "cgpa_required", "apply_fee", "deadline", "status"):
        text = _criteria_answer(uni, cname, only=intent)
    else:
        text = _criteria_answer(uni, cname, only=None)

    return {"answer": text,
            "matched_university": {"id": uni.id, "name": uni.name, "country": cname}}
