"""
app/modules/countries/ai_extract.py
===================================
Extract admission CRITERIA from page text using an AI provider (Gemini / OpenAI),
with a regex fallback when no key is configured or the call fails.

The returned dict uses the same field names as the Program criteria columns:
    status, application_opening_date, deadline, apply_fee, cgpa_required, english_required
Missing values are returned as None.
"""
from __future__ import annotations

import json
import logging
import re

import httpx

_logger = logging.getLogger("requirement_reader")

CRITERIA_KEYS = ("status", "application_opening_date", "deadline",
                 "apply_fee", "cgpa_required", "english_required")

_MAX_CHARS = 12000     # keep prompts cheap/bounded

_PROMPT = (
    "You read a university admissions web page and extract admission criteria for a "
    "specific programme. Return ONLY a JSON object with these keys:\n"
    '  "status": "OPEN", "CLOSE" or "UNKNOWN" (are applications currently open?)\n'
    '  "application_opening_date": when applications open (string) or null\n'
    '  "deadline": application deadline (string) or null\n'
    '  "apply_fee": application fee incl. currency (string) or null\n'
    '  "cgpa_required": minimum CGPA/GPA requirement (string) or null\n'
    '  "english_required": English test requirement e.g. IELTS/TOEFL scores (string) or null\n'
    "Use null when the page does not clearly state a value. Do not invent values.\n\n"
    "Programme: {program}\nUniversity: {university}\n\nPAGE TEXT:\n{body}"
)


def _coerce(obj: dict) -> dict:
    out = {}
    for k in CRITERIA_KEYS:
        v = obj.get(k)
        if isinstance(v, (list, dict)):
            v = json.dumps(v, ensure_ascii=False)
        if v is not None:
            v = str(v).strip()
            if v.lower() in ("", "null", "none", "n/a", "unknown") and k != "status":
                v = None
        out[k] = v
    if out.get("status"):
        s = out["status"].upper()
        out["status"] = "OPEN" if "OPEN" in s else ("CLOSE" if "CLOS" in s else "UNKNOWN")
    return out


def _extract_json(text: str) -> dict | None:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*", "", text).strip().rstrip("`").strip()
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                return None
    return None


async def _call_gemini(client, cfg, prompt) -> str | None:
    base = "https://generativelanguage.googleapis.com/v1beta"
    url = f"{base}/models/{cfg['model']}:generateContent"
    body = {"contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0, "responseMimeType": "application/json"}}
    r = await client.post(url, params={"key": cfg["key"]}, json=body)
    if r.status_code != 200:
        _logger.warning("Gemini extract failed: %s %s", r.status_code, r.text[:200])
        return None
    data = r.json()
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError):
        return None


async def _call_openai_compatible(client, cfg, prompt, url, json_mode=True) -> str | None:
    """OpenAI-style /chat/completions — used by OpenAI (ChatGPT) and Llama hosts."""
    body = {"model": cfg["model"], "temperature": 0,
            "messages": [{"role": "user", "content": prompt}]}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    r = await client.post(url, headers={"Authorization": f"Bearer {cfg['key']}"}, json=body)
    if r.status_code != 200:
        _logger.warning("LLM (%s) failed: %s %s", cfg.get("provider"), r.status_code, r.text[:200])
        return None
    try:
        return r.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError):
        return None


async def _call_openai(client, cfg, prompt) -> str | None:
    return await _call_openai_compatible(
        client, cfg, prompt, "https://api.openai.com/v1/chat/completions")


async def _call_llama(client, cfg, prompt) -> str | None:
    # Llama models are served through OpenAI-compatible hosts (Groq by default).
    base = cfg.get("base_url") or "https://api.groq.com/openai/v1"
    return await _call_openai_compatible(
        client, cfg, prompt, base.rstrip("/") + "/chat/completions")


async def _call_anthropic(client, cfg, prompt) -> str | None:
    """Anthropic (Claude) Messages API."""
    url = "https://api.anthropic.com/v1/messages"
    headers = {"x-api-key": cfg["key"], "anthropic-version": "2023-06-01",
               "content-type": "application/json"}
    body = {"model": cfg["model"], "max_tokens": 1024, "temperature": 0,
            "messages": [{"role": "user", "content": prompt}]}
    r = await client.post(url, headers=headers, json=body)
    if r.status_code != 200:
        _logger.warning("Claude extract failed: %s %s", r.status_code, r.text[:200])
        return None
    try:
        parts = r.json()["content"]
        return "".join(p.get("text", "") for p in parts if p.get("type") == "text")
    except (KeyError, IndexError, TypeError):
        return None


async def _call_llm(client, cfg, prompt) -> str | None:
    """Route a prompt to whichever provider is configured."""
    p = (cfg.get("provider") or "").lower()
    if p == "gemini":
        return await _call_gemini(client, cfg, prompt)
    if p in ("claude", "anthropic"):
        return await _call_anthropic(client, cfg, prompt)
    if p in ("llama", "groq", "together", "openrouter"):
        return await _call_llama(client, cfg, prompt)
    # openai / chatgpt / anything else OpenAI-compatible
    return await _call_openai(client, cfg, prompt)


_URL_PROMPT = (
    "You are helping locate the official university admissions/application page.\n"
    "Programme: {program}\nUniversity: {university}\nCountry: {country}\n\n"
    "Reply with ONLY the single best official URL (the university's own domain, the "
    "programme or admissions page), nothing else. If unsure, give the university's "
    "official homepage. No prose, no markdown — just the URL."
)
_URL_RE = re.compile(r"https?://[^\s\"'<>)\]]+", re.I)


async def discover_url_ai(client: httpx.AsyncClient, cfg: dict, program: str = "",
                          university: str = "", country: str = "") -> str | None:
    """Ask the configured AI for the official admissions URL. None on failure."""
    if not cfg:
        return None
    prompt = _URL_PROMPT.format(program=program[:200], university=university[:200],
                                country=country[:100])
    try:
        raw = await _call_llm(client, cfg, prompt)
    except httpx.HTTPError:
        return None
    if not raw:
        return None
    m = _URL_RE.search(raw)
    return m.group(0).rstrip(".,);]") if m else None


async def extract_criteria_ai(client: httpx.AsyncClient, cfg: dict, page_text: str,
                              program: str = "", university: str = "") -> dict | None:
    """Return a criteria dict via AI, or None on failure (caller falls back to regex)."""
    if not page_text or not cfg:
        return None
    prompt = _PROMPT.format(program=program[:200], university=university[:200],
                            body=page_text[:_MAX_CHARS])
    try:
        raw = await _call_llm(client, cfg, prompt)
    except httpx.HTTPError as exc:
        _logger.warning("AI extract transport error: %s", exc)
        return None
    if not raw:
        return None
    obj = _extract_json(raw)
    return _coerce(obj) if isinstance(obj, dict) else None


# ── regex fallback ────────────────────────────────────────────────
# English-test scores (several providers). Window kept tight to avoid grabbing
# an unrelated number further down the sentence.
_RE_IELTS = re.compile(r"ielts\D{0,45}?(\d(?:\.\d)?)", re.I)
_RE_TOEFL = re.compile(r"toefl\D{0,45}?(\d{2,3})", re.I)
_RE_DUOLINGO = re.compile(r"duolingo\D{0,45}?(\d{2,3})", re.I)
_RE_PTE = re.compile(r"\bpte\b\D{0,45}?(\d{2,3})", re.I)
_RE_CAE = re.compile(r"(?:cambridge|c1\s+advanced|cae|cpe)\D{0,45}?(\d{2,3})", re.I)
_RE_ENG_GENERIC = re.compile(
    r"english\s+(?:language\s+)?(?:proficiency|requirement|test|certificate)", re.I)

_RE_CGPA = re.compile(
    r"(?:minimum\s+)?(?:c?gpa|grade\s+point\s+average)\D{0,25}?(\d(?:\.\d{1,2})?)", re.I)
_RE_PERCENT = re.compile(
    r"(?:minimum|at\s+least|above|overall)\D{0,25}?(\d{2,3})\s?%", re.I)
_RE_CLASS = re.compile(r"(?:second|first|upper[-\s]?second|2:1|2\.1)\s+class(?:\s+honours)?", re.I)

# currencies, either "€1,500" or "1500 EUR"
_CUR = r"(?:€|£|\$|₹|¥|eur|usd|gbp|chf|sek|pln|aud|cad|inr|rs\.?)"
_RE_FEE = re.compile(
    r"(?:application|registration|admission|enrol(?:l)?ment)\s+fee\D{0,40}?"
    r"(" + _CUR + r"\s?\d[\d.,]*|\d[\d.,]*\s?" + _CUR + r")", re.I)
_RE_FEE_NONE = re.compile(
    r"(?:no|free|without|waived|zero)\s+(?:application|registration|admission)\s+fee"
    r"|(?:application|registration)\s+fee[^.\n]{0,20}?(?:free|waived|none|€?\s?0\b)", re.I)

_MONTHS = (r"january|february|march|april|may|june|july|august|september|october|"
           r"november|december|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec")
_RE_DATE = re.compile(
    r"(?:\d{1,2}(?:st|nd|rd|th)?\s+(?:of\s+)?(?:" + _MONTHS + r")\w*\.?(?:\s+\d{4})?"
    r"|(?:" + _MONTHS + r")\w*\.?\s+\d{1,2}(?:st|nd|rd|th)?(?:,?\s+\d{4})?"
    r"|\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4})", re.I)
_RE_DEADLINE = re.compile(
    r"(?:application\s+)?(?:deadline|closing\s+date|last\s+date(?:\s+to\s+apply)?|"
    r"apply\s+by|applications?\s+close|must\s+be\s+(?:submitted|received)\s+by)"
    r"[^.\n\r]{0,80}", re.I)
_RE_OPENDATE = re.compile(
    r"(?:applications?\s+open|application\s+period|opens?\s+on|open\s+from|"
    r"registration\s+opens?)[^.\n\r]{0,80}", re.I)

_RE_OPEN = re.compile(
    r"(applications?\s+(?:are\s+)?(?:now\s+)?open|admissions?\s+(?:are\s+)?open|"
    r"intake\s+open|enrol(?:l)?ment\s+open|now\s+open|apply\s+now|open\s+for\s+applications?)",
    re.I)
_RE_CLOSED = re.compile(
    r"(applications?\s+(?:are\s+)?closed|admissions?\s+closed|registration\s+closed|"
    r"no\s+longer\s+accepting|applications?\s+have\s+closed|intake\s+closed)", re.I)


def _dsnippet(m) -> str | None:
    """A deadline/open snippet is only accepted when it carries a real date."""
    snippet = re.sub(r"\s+", " ", m.group(0)).strip()
    if _RE_DATE.search(snippet):
        return snippet[:140]
    return None


def extract_criteria_regex(page_text: str) -> dict:
    """Best-effort criteria extraction with no AI key (broad heuristics)."""
    out = {k: None for k in CRITERIA_KEYS}
    if not page_text:
        return out
    t = page_text

    # status
    if _RE_CLOSED.search(t):
        out["status"] = "CLOSE"
    elif _RE_OPEN.search(t):
        out["status"] = "OPEN"

    # english tests (collect every provider mentioned)
    eng = []
    for rx, name in ((_RE_IELTS, "IELTS"), (_RE_TOEFL, "TOEFL"),
                     (_RE_DUOLINGO, "Duolingo"), (_RE_PTE, "PTE"), (_RE_CAE, "Cambridge")):
        m = rx.search(t)
        if m:
            eng.append(f"{name} {m.group(1)}")
    if not eng and _RE_ENG_GENERIC.search(t):
        eng.append("English proficiency required")
    if eng:
        out["english_required"] = ", ".join(eng)

    # CGPA / percentage / class
    m = _RE_CGPA.search(t)
    if m:
        out["cgpa_required"] = m.group(1)
    else:
        m = _RE_PERCENT.search(t)
        if m:
            out["cgpa_required"] = f"{m.group(1)}%"
        elif _RE_CLASS.search(t):
            out["cgpa_required"] = re.sub(r"\s+", " ", _RE_CLASS.search(t).group(0)).strip()

    # fee
    m = _RE_FEE.search(t)
    if m:
        out["apply_fee"] = m.group(1).strip()
    elif _RE_FEE_NONE.search(t):
        out["apply_fee"] = "No application fee"

    # deadline (must carry a date)
    for m in _RE_DEADLINE.finditer(t):
        snip = _dsnippet(m)
        if snip:
            out["deadline"] = snip
            break

    # opening date (must carry a date)
    for m in _RE_OPENDATE.finditer(t):
        snip = _dsnippet(m)
        if snip:
            out["application_opening_date"] = snip
            break

    return out
