"""
app/modules/countries/reader.py
===============================
The reading engine (v3).

For each programme's apply/portal link the reader:
  1. fetches the page (records reachability),
  2. extracts the admission CRITERIA from the page (AI when a key is configured,
     regex otherwise) — status, opening date, deadline, fee, CGPA, English,
  3. stores what it read as a LinkSnapshot (so the dashboard can show
     read-vs-stored side by side), and
  4. proposes a LinkChange for every criterion that differs from the stored
     value — nothing is applied until an admin approves it.

Reads run in the background so the HTTP request never blocks on the network.
"""
from __future__ import annotations

import asyncio
import hashlib
import html as _html
import logging
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlsplit

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.countries import ai_extract, keystore
from app.modules.countries.ai_extract import CRITERIA_KEYS
from app.modules.countries.models import (
    Country, LinkChange, LinkSnapshot, Program, ProgramLink, ReaderRun, University,
)

_logger = logging.getLogger("requirement_reader")

# A realistic browser signature — some university sites reject obvious bots.
_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
               "image/avif,image/webp,*/*;q=0.8"),
    "Accept-Language": "en-US,en;q=0.9",
}
_WS_RE = re.compile(r"\s+")
_TOKEN_RE = re.compile(r"[A-Za-z0-9]{24,}")
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_META_DESC_RE = re.compile(
    r'<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']', re.I | re.S)
# anchors that likely lead to admission / entry-requirement / fee pages
_SUBLINK_RE = re.compile(r'<a\b[^>]*href=["\']([^"\'#]+)["\'][^>]*>(.*?)</a>', re.I | re.S)
_SUB_HINT = re.compile(
    r"admission|entry\s*requirement|requirement|how\s*to\s*apply|apply|tuition|fees?|"
    r"deadline|eligibilit|english", re.I)


def _title_of(body: str) -> str:
    m = _TITLE_RE.search(body)
    if m:
        return _WS_RE.sub(" ", _html.unescape(re.sub(r"<[^>]+>", " ", m.group(1)))).strip()[:200]
    return ""


def _clean_text(body: str) -> str:
    body = re.sub(r"<script\b[^>]*>.*?</script>", " ", body, flags=re.I | re.S)
    body = re.sub(r"<style\b[^>]*>.*?</style>", " ", body, flags=re.I | re.S)
    body = re.sub(r"<[^>]+>", " ", body)
    body = _html.unescape(body)
    body = _TOKEN_RE.sub(" ", body)
    return _WS_RE.sub(" ", body).strip()


def _excerpt(body_html: str, text: str) -> str:
    """A short human-readable summary of the page (meta description, else first text)."""
    m = _META_DESC_RE.search(body_html)
    if m:
        desc = _WS_RE.sub(" ", _html.unescape(m.group(1))).strip()
        if desc:
            return desc[:300]
    return text[:300]


def _same_host(a: str, b: str) -> bool:
    try:
        return urlsplit(a).netloc.split(":")[0].lstrip("www.") == \
               urlsplit(b).netloc.split(":")[0].lstrip("www.")
    except Exception:
        return False


def _candidate_sublinks(body_html: str, base_url: str, limit: int = 4) -> list[str]:
    """Find a few on-site links that look like admission/requirement/fee pages."""
    seen, out = set(), []
    for href, label in _SUBLINK_RE.findall(body_html):
        anchor = _html.unescape(re.sub(r"<[^>]+>", " ", label))
        if not (_SUB_HINT.search(href) or _SUB_HINT.search(anchor)):
            continue
        full = urljoin(base_url, _html.unescape(href.strip()))
        if not full.lower().startswith("http") or not _same_host(full, base_url):
            continue
        if full.rstrip("/") == base_url.rstrip("/") or full in seen:
            continue
        seen.add(full)
        out.append(full)
        if len(out) >= limit:
            break
    return out


async def _fetch(client: httpx.AsyncClient, url: str):
    """Return (http_status, is_alive, content_hash, content_length, text, title, raw_html)."""
    try:
        r = await client.get(url, follow_redirects=True)
        alive = 200 <= r.status_code < 400
        raw = r.text if alive else ""
        text = _clean_text(raw) if alive else ""
        title = _title_of(raw) if alive else ""
        chash = hashlib.sha256(text.encode("utf-8", "ignore")).hexdigest() if text else None
        return r.status_code, alive, chash, len(text), text, title, raw
    except httpx.HTTPError:
        return None, False, None, None, "", "", ""


def _has_any_criteria(crit: dict | None) -> bool:
    if not crit:
        return False
    return any(v and str(v).strip() and not (k == "status" and str(v).upper() == "UNKNOWN")
               for k, v in crit.items() if not k.startswith("_"))


async def _read_page(client: httpx.AsyncClient, cfg, url: str,
                     program_name: str = "", university: str = "", deep: bool = False):
    """Fetch a URL, extract criteria, and (when `deep`) follow one admissions sub-page
    if the landing page yielded nothing. Returns (fetch_tuple, criteria_dict)."""
    status, ok, chash, clen, text, title, raw = await _fetch(client, url)
    criteria = None
    if ok and text:
        criteria = (await ai_extract.extract_criteria_ai(client, cfg, text, program_name, university)
                    if cfg else None)
        if criteria is None:
            criteria = ai_extract.extract_criteria_regex(text)
        criteria["_page_title"] = title
        criteria["_excerpt"] = _excerpt(raw, text)
        if deep and not _has_any_criteria(criteria):
            for sub in _candidate_sublinks(raw, url):
                s2, ok2, _h2, _l2, text2, _t2, _r2 = await _fetch(client, sub)
                if not (ok2 and text2):
                    continue
                more = (await ai_extract.extract_criteria_ai(client, cfg, text2, program_name, university)
                        if cfg else None) or ai_extract.extract_criteria_regex(text2)
                for k in ai_extract.CRITERIA_KEYS:
                    if more.get(k) and not criteria.get(k):
                        criteria[k] = more[k]
                criteria["_followed"] = sub
                if _has_any_criteria(criteria):
                    break
    return (status, ok, chash, clen, text, title, raw), criteria


def _norm(v) -> str:
    return _WS_RE.sub(" ", str(v or "").strip()).lower()


def _criteria_changes(stored: Program, read: dict):
    """Yield (field, old, new) for every criterion the reader read that differs."""
    for field in CRITERIA_KEYS:
        new = read.get(field)
        if new is None or not str(new).strip():
            continue
        if field == "status" and str(new).upper() == "UNKNOWN":
            continue
        old = getattr(stored, field, None)
        if _norm(old) == _norm(new):
            continue
        yield field, (old or ""), str(new)


# ── link discovery (used when a programme has no apply/portal link) ──
from urllib.parse import parse_qs, unquote, urlparse  # noqa: E402

_DDG_ENDPOINTS = ("https://lite.duckduckgo.com/lite/", "https://html.duckduckgo.com/html/")


async def _search_first_url(client: httpx.AsyncClient, query: str) -> str | None:
    """Best-effort keyless web search (DuckDuckGo). Returns the first off-site
    result URL, or None if the engine is unavailable/blocked."""
    for base in _DDG_ENDPOINTS:
        try:
            r = await client.post(base, data={"q": query},
                                  headers={"Content-Type": "application/x-www-form-urlencoded"})
            if r.status_code != 200:
                continue
            for h in re.findall(r'href="([^"]+)"', r.text):
                u = h
                if "uddg=" in h:
                    full = ("https:" + h) if h.startswith("//") else \
                           ("https://duckduckgo.com" + h) if h.startswith("/") else h
                    qs = parse_qs(urlparse(full).query)
                    if "uddg" in qs:
                        u = unquote(qs["uddg"][0])
                if u.startswith("http") and not any(x in u for x in (
                        "duckduckgo", "google.", "bing.", "w3.org")):
                    return u
        except httpx.HTTPError:
            continue
    return None


async def _discover_link(client: httpx.AsyncClient, cfg, program: str,
                         university: str, country: str) -> str | None:
    """Find an official link for a programme that has none: AI first (accurate
    when a key is set), then a best-effort web search. The URL is verified
    reachable before being returned."""
    candidate = None
    if cfg:
        candidate = await ai_extract.discover_url_ai(client, cfg, program, university, country)
    if not candidate:
        q = " ".join(x for x in (university, program, country, "admission apply") if x)
        candidate = await _search_first_url(client, q)
    if not candidate or not candidate.lower().startswith("http"):
        return None
    status, ok, *_ = await _fetch(client, candidate)
    return candidate if ok else None


async def run_reader(db: AsyncSession, countries: list[str] | None = None,
                     concurrency: int = 6, timeout: float = 25.0,
                     run_id: str | None = None, use_ai: bool | None = None,
                     max_pages: int | None = None, discover: bool = False,
                     university_ids: list[str] | None = None, deep: bool = False) -> dict:
    codes = [c.strip().upper() for c in (countries or []) if c.strip()]
    label = ",".join(codes) if codes else "ALL"

    if run_id:
        run = (await db.execute(select(ReaderRun).where(ReaderRun.id == run_id))).scalar_one()
    else:
        run = ReaderRun(countries=label, status="RUNNING")
        db.add(run)
        await db.flush()

    # AI config (None -> regex fallback)
    cfg = await keystore.get_active_config(db)
    if use_ai is False:
        cfg = None
    run.used_ai = bool(cfg)

    def _rows_stmt():
        s = (select(ProgramLink, Program, University)
             .join(University, University.id == ProgramLink.university_id)
             .outerjoin(Program, Program.id == ProgramLink.program_id))
        if codes:
            s = s.join(Country, Country.id == University.country_id).where(Country.code.in_(codes))
        if university_ids:
            s = s.where(ProgramLink.university_id.in_(university_ids))
        return s

    # ── optionally discover a link for programmes that have none ──
    discovered = 0
    if discover:
        nlstmt = (select(Program, University, Country)
                  .join(University, University.id == Program.university_id)
                  .join(Country, Country.id == Program.country_id)
                  .outerjoin(ProgramLink, ProgramLink.program_id == Program.id)
                  .where(ProgramLink.id.is_(None)))
        if codes:
            nlstmt = nlstmt.where(Country.code.in_(codes))
        if university_ids:
            nlstmt = nlstmt.where(Program.university_id.in_(university_ids))
        nolink = (await db.execute(nlstmt)).all()
        if max_pages:
            nolink = nolink[:max_pages]
        async with httpx.AsyncClient(timeout=timeout, headers=_HEADERS, verify=False,
                                     follow_redirects=True) as dclient:
            for prog, uni, ctry in nolink:
                url = await _discover_link(dclient, cfg, prog.name, uni.name, ctry.name)
                if url:
                    lk = ProgramLink(program_id=prog.id, university_id=uni.id, url=url,
                                     link_type="APPLY", label="Auto-discovered",
                                     is_auto_discovered=True)
                    db.add(lk)
                    await db.flush()
                    db.add(LinkChange(run_id=run.id, link_id=lk.id, university_id=uni.id,
                                      program_id=prog.id, country_id=prog.country_id,
                                      change_type="LINK_FOUND", field="apply_link",
                                      old_value=prog.apply_link or "", new_value=url))
                    discovered += 1
                else:
                    db.add(LinkChange(run_id=run.id, university_id=uni.id, program_id=prog.id,
                                      country_id=prog.country_id, change_type="LINK_NEEDED",
                                      field="apply_link", old_value=prog.apply_link or "",
                                      new_value="(no link found automatically — please add one)"))
        await db.flush()

    # links to read (with programme + university context)
    rows = (await db.execute(_rows_stmt())).all()
    if max_pages:
        rows = rows[:max_pages]
    run.links_checked = len(rows)
    await db.flush()

    # previous snapshot per link (for LINK_DOWN / LINK_RESTORED)
    prev: dict[str, LinkSnapshot] = {}
    if rows:
        link_ids = [lk.id for lk, _p, _u in rows]
        snaps = (await db.execute(
            select(LinkSnapshot).where(LinkSnapshot.link_id.in_(link_ids))
            .order_by(LinkSnapshot.link_id, LinkSnapshot.fetched_at.desc()))).scalars().all()
        for sn in snaps:
            prev.setdefault(sn.link_id, sn)

    # existing PENDING criteria changes — so a re-run doesn't duplicate them
    pending_pairs: set[tuple[str, str]] = set()
    prog_ids = [p.id for _lk, p, _u in rows if p is not None]
    if prog_ids:
        pend = (await db.execute(
            select(LinkChange.program_id, LinkChange.field)
            .where(LinkChange.change_type == "CRITERIA_CHANGED",
                   LinkChange.status == "PENDING",
                   LinkChange.program_id.in_(prog_ids)))).all()
        pending_pairs = {(pid, fld) for pid, fld in pend}

    sem = asyncio.Semaphore(concurrency)
    now = datetime.now(timezone.utc)
    counters = {"alive": 0, "broken": 0, "read": 0, "changes": 0, "pages": 0}

    # One programme context per distinct URL (so a shared university/portal page
    # is fetched only ONCE even when many programmes point to it).
    unique: dict[str, tuple] = {}
    for lk, p, u in rows:
        unique.setdefault(lk.url, (p, u))

    cache: dict[str, tuple] = {}
    async with httpx.AsyncClient(timeout=timeout, headers=_HEADERS, verify=False,
                                 follow_redirects=True) as client:
        async def fetch_one(url, program, uni):
            async with sem:
                cache[url] = await _read_page(
                    client, cfg, url,
                    program_name=program.name if program else "",
                    university=uni.name, deep=deep)
        await asyncio.gather(*(fetch_one(url, p, u) for url, (p, u) in unique.items()))
    counters["pages"] = sum(1 for v in cache.values() if v[0][1] and v[0][4])

    # Apply each fetched page to every programme/link that shares its URL.
    for link, program, uni in rows:
        (status, ok, chash, clen, text, _title, _raw), criteria = cache.get(
            link.url, ((None, False, None, None, "", "", ""), None))

        link.http_status = status
        link.is_alive = ok
        link.last_checked_at = now
        if ok:
            link.broken_since = None
            counters["alive"] += 1
        else:
            if link.broken_since is None:
                link.broken_since = now
            counters["broken"] += 1
        if ok and text:
            counters["read"] += 1

        db.add(LinkSnapshot(link_id=link.id, run_id=run.id,
                            program_id=program.id if program else None,
                            http_status=status, is_alive=ok,
                            content_hash=chash, content_length=clen,
                            criteria_found=criteria))

        # propose criteria changes
        if ok and program is not None and criteria:
            for field, old, new in _criteria_changes(program, criteria):
                if (program.id, field) in pending_pairs:
                    continue                      # already awaiting review
                pending_pairs.add((program.id, field))
                db.add(LinkChange(run_id=run.id, link_id=link.id,
                                  university_id=uni.id, program_id=program.id,
                                  country_id=program.country_id,
                                  change_type="CRITERIA_CHANGED", field=field,
                                  old_value=str(old), new_value=str(new)))
                counters["changes"] += 1

        # link up/down vs previous snapshot
        pv = prev.get(link.id)
        if pv is not None:
            if pv.is_alive and not ok:
                db.add(LinkChange(run_id=run.id, link_id=link.id, university_id=uni.id,
                                  program_id=program.id if program else None,
                                  country_id=program.country_id if program else None,
                                  change_type="LINK_DOWN", field="reachable",
                                  old_value="alive", new_value="unreachable"))
                counters["changes"] += 1
            elif (pv.is_alive is False) and ok:
                db.add(LinkChange(run_id=run.id, link_id=link.id, university_id=uni.id,
                                  program_id=program.id if program else None,
                                  country_id=program.country_id if program else None,
                                  change_type="LINK_RESTORED", field="reachable",
                                  old_value="unreachable", new_value="alive"))
                counters["changes"] += 1

    run.links_alive = counters["alive"]
    run.links_broken = counters["broken"]
    run.programs_read = counters["read"]
    run.changes_found = counters["changes"]
    run.status = "DONE"
    run.finished_at = datetime.now(timezone.utc)
    engine = "AI" if cfg else "regex"
    run.message = (
        f"Read {len(rows)} link(s) for {label} from {len(unique)} unique page(s): "
        f"{counters['alive']} reachable, {counters['broken']} broken, "
        f"{counters['pages']} page(s) parsed ({engine}), "
        + (f"{discovered} link(s) auto-found, " if discover else "")
        + f"{counters['changes']} change(s) proposed.")
    await db.commit()
    return {"run_id": run.id, "countries": label, "checked": len(rows),
            "alive": counters["alive"], "broken": counters["broken"],
            "programs_read": counters["read"], "changes": counters["changes"],
            "used_ai": bool(cfg), "discovered": discovered, "message": run.message}


async def discover_and_read(db: AsyncSession, university: str, program: str = "",
                            country: str = "", url: str | None = None,
                            timeout: float = 30.0) -> dict:
    """Read a university/programme that may not be in the database yet.

    When no URL is given, the official page is located from the university name
    (AI first, then a web search), then its admission criteria are read.
    """
    cfg = await keystore.get_active_config(db)
    async with httpx.AsyncClient(timeout=timeout, headers=_HEADERS, verify=False,
                                 follow_redirects=True) as client:
        used = (url or "").strip()
        discovered = False
        if not used:
            used = await _discover_link(client, cfg, program, university, country) or ""
            discovered = bool(used)
        if not used:
            return {"ok": False, "url": "", "discovered": False, "criteria": {},
                    "page_title": "", "used_ai": bool(cfg), "http_status": None,
                    "text_chars": 0, "followed_url": None,
                    "message": "Could not find a web page for that university. "
                               "Please paste the university/programme link and try again."}
        (status, ok, _ch, clen, text, title, _raw), criteria = await _read_page(
            client, cfg, used, program_name=program, university=university, deep=True)

    if not ok:
        return {"ok": False, "url": used, "discovered": discovered, "criteria": {},
                "page_title": "", "used_ai": bool(cfg), "http_status": status,
                "text_chars": 0, "followed_url": None,
                "message": f"Could not open {used} (status {status or 'unreachable'})."}
    criteria = criteria or {}
    followed = criteria.pop("_followed", None)
    page_title = criteria.pop("_page_title", title)
    criteria.pop("_excerpt", None)
    found = sum(1 for k in ai_extract.CRITERIA_KEYS if criteria.get(k))
    engine = "AI" if cfg else "regex"
    return {"ok": True, "url": used, "discovered": discovered, "criteria": criteria,
            "page_title": page_title, "used_ai": bool(cfg), "http_status": status,
            "text_chars": clen or len(text), "followed_url": followed,
            "message": (f"Read '{page_title or used}' ({engine})"
                        + (" — link found automatically" if discovered else "")
                        + f"; {found} criterion/criteria found.")}


async def read_criteria_from_url(db: AsyncSession, url: str, program_name: str = "",
                                 university: str = "", timeout: float = 25.0) -> dict:
    """Read one URL now and return the criteria found (used by manual verify).

    Follows one admissions/requirements sub-page when the landing page itself
    carries no criteria — so a bare university link still yields data.
    """
    cfg = await keystore.get_active_config(db)
    async with httpx.AsyncClient(timeout=timeout, headers=_HEADERS, verify=False) as client:
        (status, ok, _chash, clen, text, title, _raw), criteria = await _read_page(
            client, cfg, url, program_name, university, deep=True)
    if not ok:
        return {"ok": False, "http_status": status, "criteria": {}, "used_ai": False,
                "text_chars": 0, "page_title": "", "followed_url": None,
                "message": f"Could not read {url} (status {status or 'unreachable'})."}
    criteria = criteria or {}
    followed = criteria.pop("_followed", None)
    page_title = criteria.pop("_page_title", title)
    criteria.pop("_excerpt", None)
    engine = "AI" if cfg else "regex"
    note = f" (followed {followed})" if followed else ""
    return {"ok": True, "http_status": status, "criteria": criteria, "used_ai": bool(cfg),
            "text_chars": clen or len(text), "page_title": page_title, "followed_url": followed,
            "message": f"Read '{page_title or url}' ({engine}){note}; "
                       f"{sum(1 for k in ai_extract.CRITERIA_KEYS if criteria.get(k))} criterion/criteria found."}


# =====================================================================
#  BACKGROUND READING
# =====================================================================
_run_active = False


async def _execute_run(run_id: str, countries: list[str] | None, use_ai: bool | None,
                       max_pages: int | None, discover: bool = False) -> None:
    global _run_active
    from app.core.database import AsyncSessionLocal
    try:
        async with AsyncSessionLocal() as db:
            await run_reader(db, countries=countries, run_id=run_id,
                             use_ai=use_ai, max_pages=max_pages, discover=discover)
    except Exception as exc:
        _logger.error("Background read failed: %s", exc, exc_info=True)
        try:
            async with AsyncSessionLocal() as db2:
                run = (await db2.execute(
                    select(ReaderRun).where(ReaderRun.id == run_id))).scalar_one_or_none()
                if run is not None:
                    run.status = "FAILED"
                    run.message = f"Read failed: {exc}"
                    run.finished_at = datetime.now(timezone.utc)
                    await db2.commit()
        except Exception:
            pass
    finally:
        _run_active = False


async def fail_stale_runs(db: AsyncSession, older_than_minutes: int = 15) -> int:
    """Mark RUNNING runs that never finished (e.g. the server restarted mid-read)
    as FAILED, so they stop showing 'running' forever and stop blocking new reads."""
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=older_than_minutes)
    stale = (await db.execute(select(ReaderRun).where(
        ReaderRun.status == "RUNNING", ReaderRun.started_at < cutoff))).scalars().all()
    for r in stale:
        r.status = "FAILED"
        r.finished_at = datetime.now(timezone.utc)
        r.message = (r.message or "") + " (stopped: interrupted or timed out)"
    if stale:
        await db.commit()
    return len(stale)


async def start_background_read(db: AsyncSession, countries: list[str] | None = None,
                                use_ai: bool | None = None, max_pages: int | None = None,
                                discover: bool = False) -> dict:
    """Create the run row, launch the read in the background, and return at once."""
    global _run_active
    label = ",".join(c.strip().upper() for c in countries if c.strip()) if countries else "ALL"
    # clear out orphaned runs from earlier crashes/restarts first
    await fail_stale_runs(db)
    # a genuinely-active run started recently still blocks a second one
    recent = (await db.execute(select(func.count(ReaderRun.id)).where(
        ReaderRun.status == "RUNNING"))).scalar()
    if _run_active and recent:
        return {"run_id": "", "countries": label, "checked": 0, "alive": 0, "broken": 0,
                "programs_read": 0, "changes": 0, "used_ai": False,
                "message": "A read is already running — please wait for it to finish."}
    # guard stuck on but nothing actually running (e.g. after a restart) -> clear it
    if _run_active and not recent:
        _run_active = False
    run = ReaderRun(countries=label, status="RUNNING")
    db.add(run)
    await db.commit()
    run_id = run.id
    _run_active = True
    asyncio.create_task(_execute_run(run_id, countries, use_ai, max_pages, discover))
    return {"run_id": run_id, "countries": label, "checked": 0, "alive": 0, "broken": 0,
            "programs_read": 0, "changes": 0, "used_ai": False,
            "message": f"Reading started for {label} in the background. "
                       "The page stays responsive and will update when it finishes."}
