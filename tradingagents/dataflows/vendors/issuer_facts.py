"""Structured IR facts from the issuer's own investor-relations page.

Why this vendor exists
----------------------
``issuer_ir`` answers "what did the company publish" — dated release pages. It
cannot answer "what are the company's own key figures, guidance and calendar",
because those live in a *facts block* on the IR overview page whose numbers are
animated client-side: reading them out of the DOM returns scrambled digits
(``0009``, ``69889``, ``6655928`` on successive loads of one page), and the
figures carry a reference date and a reporting perimeter that ordinary news
scraping drops.

The block is small and highly structured, so it is served deterministically:

* ``facts_json`` — the CMS snippet behind the key-figures block. Any JSON that
  contains ``{"title": ..., "value": ...}`` pairs works; the walker is generic,
  so a new issuer needs configuration, not code.
* ``quote_json`` — a share-price endpoint returning
  ``{price, currency, changeRelative, date, exchange}``.
* ``calendar`` — the financial calendar, kept as a *verified* static list (see
  ``calendar_verified``): IR calendars are HTML/JS and guessing at them is worse
  than a dated statement someone checked.
* ``guidance`` — when no facts endpoint exists, the newest release from the
  issuer sitemap is scanned for guidance sentences (forward range and margin).
  Coarser than a facts block, and labelled as such.

Every rendered block carries its reference date, its perimeter (continued vs
total-group figures), the source URL and — when a previous run's state exists —
a delta against it, so a guidance cut surfaces mechanically instead of waiting
for an analyst to notice.

Configuration (per issuer, in the issuer registry; never inferred)::

    "ir_facts": {
      "enabled": true,
      "terms_checked": "2026-10-29: robots.txt allows the IR pages + APIs; internal analysis only",
      "facts_json": "https://www.rheinmetall.com/api/de/meta/content-snippets/investor-relations/kennzahlen",
      "quote_json": "https://www.rheinmetall.com/api/stock-teaser",
      "overview_url": "https://www.rheinmetall.com/de/investor-relations/ueberblick",
      "reference_date": "31.12.2025",
      "perimeter_note": "fortgeführte Aktivitäten",
      "calendar": [{"date": "2026-11-05", "event": "Bericht 3. Quartal 2026", "place": "Online"}],
      "calendar_verified": "2026-10-29"
    }

Gate rules mirror ``issuer_ir``: no block, ``enabled`` false, or an empty
``terms_checked`` raises ``VendorNotConfiguredError`` so the routing chain and
the event-context builder fall through to an explicit gap — a source is only
read after someone recorded that it may be.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests

from tradingagents.dataflows.config import get_config
from tradingagents.dataflows.errors import VendorNotConfiguredError, VendorUnavailableError
from tradingagents.dataflows.issuer_registry import load_instrument
from tradingagents.dataflows.net import get_scrubbed
from tradingagents.dataflows.vendors import issuer_ir

REQUEST_TIMEOUT = 25
CACHE_SECONDS = 6 * 3600
MAX_JSON_BYTES = 4_000_000
CALENDAR_STALE_DAYS = 120
GUIDANCE_EXCERPT_CHARS = 1800
USER_AGENT = issuer_ir.USER_AGENT

# A label/value pair in a CMS facts array. The walker accepts any nesting, so
# the same code serves a flat list and a deeply framed page tree.
_FACT_KEYS = {"title", "value"}

# Guidance sentences in German issuer wording. Deliberately narrow: a missed
# sentence costs one line, a wrong number costs a decision.
_RANGE_RE = re.compile(
    r"(?:Spanne von|Prognose|erwartet|rechnet)[^.]{0,160}?"
    r"(\d[\d.,]*)\s*(?:Mio|Mrd)?\s*(?:EUR|Euro)?\s*(?:bis|-)\s*(\d[\d.,]*)\s*(Mio|Mrd)?",
    re.IGNORECASE,
)
_MARGIN_RE = re.compile(
    r"(?:operative[n]?\s+)?(?:Ergebnis|EBIT|Umsatz)?marge[^.\d]{0,60}?(\d{1,3}(?:[.,]\d)?)\s*%",
    re.IGNORECASE,
)
# Slugs of results publications, where guidance is stated. Language-agnostic
# enough for DE/EN issuers; a miss only costs one more fetch.
_RESULTS_SLUG_RE = re.compile(
    r"(ergebnis|halbjahr|quartal|geschaeftsjahr|geschäftsjahr|bilanz|finanzbericht|"
    r"jahresabschluss|prognose|ausblick|rekord|zahlen|results|half-year|quarter|annual|outlook)",
    re.IGNORECASE,
)
# Sentences that state a *forward* expectation. Achieved figures and order news
# must not match, or the extraction reports last period's actuals as guidance.
_GUIDANCE_CONTEXT_RE = re.compile(
    r"(prognose|rechnet|erwartet|erwartete|ausblick|guidance|outlook|anvisiert|"
    r"projektiert|zielt|plant|strebt)",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Pure helpers (testable without network)
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _guess_reference_date(text: str) -> str:
    """First ``Stand: TT.MM.JJJJ`` / ``as of YYYY-MM-DD`` in a JSON body."""
    for pattern in (r"Stand:\s*(\d{2}\.\d{2}\.\d{4})", r"as of\s+(\d{4}-\d{2}-\d{2})",
                    r"Stand\s+(\d{2}\.\d{2}\.\d{4})"):
        found = re.search(pattern, text)
        if found:
            return found.group(1)
    return ""


def walk_facts(node, out: list[dict] | None = None) -> list[dict]:
    """Every ``{"title": str, "value": str}`` pair in a JSON document.

    Generic on purpose: the CMS of a German issuer nests its facts block under
    frame/areablock/element layers that differ per site release. Matching shapes
    instead of paths keeps the adapter free of per-issuer code, and the caller
    points ``facts_json`` at the *snippet* endpoint, not the whole page, so the
    only pairs walked are the ones the page displays as figures.
    """
    if out is None:
        out = []
    if isinstance(node, dict):
        title, value = node.get("title"), node.get("value")
        if isinstance(title, str) and isinstance(value, str) and title.strip() and value.strip() \
                and set(node) >= _FACT_KEYS:
            out.append({"label": title.strip(), "value": value.strip()})
        for child in node.values():
            walk_facts(child, out)
    elif isinstance(node, list):
        for child in node:
            walk_facts(child, out)
    return out


# A leading unit in a CMS label ("MrdEUR Umsatz", "EUR Dividende") belongs to the
# value, not the label: keeping it in the label would make the same fact arrive as
# a different key once an issuer switches from Mio to Mrd, and it reads badly in
# the prompt. Footnote asterisks are dropped for the same reason.
_LEADING_UNIT_RE = re.compile(
    r"^(Mrd\.?\s*EUR|Mio\.?\s*EUR|Mrd\.?|Mio\.?|EUR|USD|Tsd\.?)\s+(.+)$", re.IGNORECASE
)
_FOOTNOTE_RE = re.compile(r"\*+\s*$")


def normalize_fact(title: str, value: str) -> tuple[str, str]:
    """``(label, value)`` with the unit moved to the value and footnotes stripped."""
    label = _FOOTNOTE_RE.sub("", (title or "").strip()).strip()
    val = (value or "").strip()
    match = _LEADING_UNIT_RE.match(label)
    if match and not val.endswith("%"):
        unit, rest = match.group(1).replace(".", "").replace(" ", ""), match.group(2)
        if rest and not rest[0].isdigit():
            label = rest.strip()
            if unit.upper() in {"EUR", "USD"}:
                val = f"{val} {unit.upper()}"
            else:
                val = f"{val} {unit.replace('Mrd', 'Mrd ').replace('Mio', 'Mio ').strip()}"
    return label, val


def extract_quote(node) -> dict:
    """The share-price record in a JSON document, or ``{}``.

    Matches on shape (``price`` plus at least one of currency/change/exchange),
    so any quote endpoint that follows the usual naming works unchanged.
    """
    if isinstance(node, dict):
        price = node.get("price")
        if isinstance(price, (int, float)) and {"price"} < set(node) and (
            "currency" in node or "changeRelative" in node or "exchange" in node
        ):
            return {
                "price": float(price),
                "currency": str(node.get("currency") or ""),
                "change": str(node.get("changeAbsolute") or ""),
                "change_relative": str(node.get("changeRelative") or ""),
                "date": str(node.get("date") or ""),
                "exchange": str(node.get("exchange") or ""),
            }
        for child in node.values():
            found = extract_quote(child)
            if found:
                return found
    elif isinstance(node, list):
        for child in node:
            found = extract_quote(child)
            if found:
                return found
    return {}


def extract_guidance(text: str) -> dict:
    """Guidance sentences from release prose: forward range and margin only.

    Two deliberate narrowings, both learned from a real H1 release:

    * Only **sentences with guidance context** are scanned. A results release
      states the achieved margin ("Ergebnismarge 15,0 %") before it states the
      guided one ("Ergebnismarge rund 19 % erwartet"); reading the text in order
      picked the achieved figure.
    * **Backlog is not extracted from prose at all.** The same release quotes a
      group backlog and one per segment; a regex cannot tell them apart, and the
      authoritative number is in the facts block anyway.

    Only what the text states is returned; a missing pattern stays missing.
    """
    guidance: dict[str, str] = {}
    flat = re.sub(r"\s+", " ", text or "")
    sentences = [s for s in re.split(r"(?<=[.!?;])\s+", flat) if s.strip()]
    candidates = [s for s in sentences if _GUIDANCE_CONTEXT_RE.search(s)] or [flat]

    def follows_context(sentence: str, position: int, window: int = 200) -> bool:
        """Is a guidance keyword close *before* ``position`` in this sentence?

        Bullet-style headers ("Operative Marge 15,0 % … Angepasste Jahresprognose")
        contain no full stop, so sentence splitting does not separate the achieved
        figure from the word that made the sentence a candidate. Requiring the
        keyword to precede the number is what rejects the achieved figure.
        """
        for found in _GUIDANCE_CONTEXT_RE.finditer(sentence):
            if 0 <= position - found.start() <= window:
                return True
        return False

    for sentence in candidates:
        if "guidance_range" not in guidance:
            span = _RANGE_RE.search(sentence)
            if span:
                low, high, unit = span.group(1), span.group(2), (span.group(3) or "").strip()
                guidance["guidance_range"] = f"{low} bis {high}{(' ' + unit) if unit else ''}".strip()
                guidance["guidance_sentence"] = sentence.strip(" .;-")[:400]
        if "guidance_margin" not in guidance:
            margin = _MARGIN_RE.search(sentence)
            if margin and follows_context(sentence, margin.start()):
                guidance["guidance_margin"] = f"{margin.group(1)} %"
        if "guidance_range" in guidance and "guidance_margin" in guidance:
            break
    return guidance


def diff_facts(old: dict | None, new: dict) -> list[str]:
    """What changed between two collected states, as agent-readable lines.

    Only differences are listed; an unchanged fact set produces no lines at all
    (the caller then says "no change since <date>"), which keeps the block short
    enough to be read on every run instead of skipped.
    """
    if not old:
        return []
    changes: list[str] = []
    old_facts = old.get("facts") or {}
    new_facts = new.get("facts") or {}
    for label in sorted(set(old_facts) | set(new_facts)):
        before, after = old_facts.get(label), new_facts.get(label)
        if before == after:
            continue
        if before is None:
            changes.append(f"NEU: {label} = {after}")
        elif after is None:
            changes.append(f"ENTFALLEN: {label} (war {before})")
        else:
            changes.append(f"GEAENDERT: {label}: {before} -> {after}")

    old_g, new_g = old.get("guidance") or {}, new.get("guidance") or {}
    for key in ("guidance_range", "guidance_margin"):
        before, after = old_g.get(key), new_g.get(key)
        if before and after and before != after:
            changes.append(f"GEAENDERT: {key}: {before} -> {after}")
        elif after and not before:
            changes.append(f"NEU: {key} = {after}")

    if (old.get("reference_date") or "") != (new.get("reference_date") or ""):
        changes.append(
            f"REFERENZDATUM: {old.get('reference_date') or '—'} -> {new.get('reference_date') or '—'}"
        )

    old_cal = {f"{c.get('date')}|{c.get('event')}" for c in (old.get("calendar") or [])}
    new_cal = {f"{c.get('date')}|{c.get('event')}" for c in (new.get("calendar") or [])}
    for entry in sorted(new_cal - old_cal):
        changes.append(f"KALENDER NEU: {entry.replace('|', ' — ')}")
    for entry in sorted(old_cal - new_cal):
        changes.append(f"KALENDER ENTFERNT: {entry.replace('|', ' — ')}")

    old_q, new_q = old.get("quote") or {}, new.get("quote") or {}
    if old_q.get("price") and new_q.get("price") and old_q["price"] != new_q["price"]:
        changes.append(
            f"KURS: {old_q['price']:,.2f} -> {new_q['price']:,.2f} {new_q.get('currency') or ''}".strip()
        )
    return changes


def render_ir_facts(facts: dict, *, delta: list[str] | None = None) -> str:
    """The prompt block: figures with reference date, then quote, calendar, delta."""
    issuer = facts.get("issuer") or facts.get("ticker") or "issuer"
    lines = [
        f"## IR facts: {issuer} ({facts.get('ticker', '?')}) — issuer's own figures",
    ]
    source = facts.get("source") or ""
    reference = facts.get("reference_date") or "not stated"
    perimeter = facts.get("perimeter_note") or ""
    lines.append(
        f"- Reference date: {reference}" + (f" · perimeter: {perimeter}" if perimeter else "")
    )
    if source:
        lines.append(f"- Source: {source}")
    if facts.get("terms_checked"):
        lines.append(f"- Terms check on record: {facts['terms_checked']}")
    lines.append(
        "- First-party figures: the issuer's own claims, not independent corroboration. "
        "Do not treat the reference date as the current quarter."
    )
    lines.append("")

    figures = facts.get("facts") or {}
    if figures:
        for label, value in figures.items():
            lines.append(f"- {label}: {value}")
    elif facts.get("facts_error"):
        lines.append(f"- GAP: {facts['facts_error']}")
    else:
        lines.append("- (no key figures could be read from this issuer's facts source)")

    quote = facts.get("quote") or {}
    if quote.get("price"):
        delta_txt = ""
        if quote.get("change_relative"):
            delta_txt = f" ({quote.get('change') or ''} {quote['change_relative']})".replace("  ", " ")
        lines.append(
            f"- Share price: {quote['price']:,.2f} {quote.get('currency') or ''}{delta_txt} "
            f"· {quote.get('exchange') or 'exchange n/a'} · {quote.get('date') or 'timestamp n/a'}"
        )

    guidance = facts.get("guidance") or {}
    if guidance:
        lines.append("")
        origin = facts.get("guidance_source") or ""
        lines.append(
            f"- Guidance (extracted from release text dated {facts.get('guidance_date') or 'n/a'}"
            + (f", {origin}" if origin else "")
            + "):"
        )
        for key in ("guidance_range", "guidance_margin"):
            if guidance.get(key):
                lines.append(f"  - {key}: {guidance[key]}")
        if guidance.get("guidance_sentence"):
            lines.append(f"  - wording: \"{guidance['guidance_sentence']}\"")
        lines.append(
            "  - Extract by pattern match on prose; quote the wording, not just the numbers."
        )

    calendar = facts.get("calendar") or []
    if calendar:
        lines.append("")
        verified = facts.get("calendar_verified") or "date not recorded"
        stale = facts.get("calendar_stale")
        lines.append(f"- Financial calendar (verified {verified}{'; STALE, re-verify' if stale else ''}):")
        for entry in calendar[:12]:
            place = f" ({entry['place']})" if entry.get("place") else ""
            lines.append(f"  - {entry.get('date')}: {entry.get('event')}{place}")

    if delta:
        lines.append("")
        lines.append(f"- CHANGES since the last stored state ({facts.get('previous_seen') or 'unknown'}):")
        for line in delta[:20]:
            lines.append(f"  - {line}")
    elif facts.get("previous_seen"):
        lines.append("")
        lines.append(f"- No change since the last stored state ({facts['previous_seen']}).")
    return "\n".join(lines).strip()


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

def _state_dir() -> Path:
    base = Path(get_config().get("data_cache_dir", "~/.tradingagents/cache")).expanduser()
    path = base / "ir_facts"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _state_path(ticker: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", ticker.upper())
    return _state_dir() / f"{safe}.json"


def load_state(ticker: str) -> dict | None:
    """The previously stored facts for ``ticker`` (``None`` when there is none)."""
    path = _state_path(ticker)
    try:
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def save_state(ticker: str, facts: dict) -> None:
    """Persist the collected facts; a failed write never fails the analysis."""
    path = _state_path(ticker)
    try:
        path.write_text(json.dumps(facts, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass


def _cache_get(url: str) -> str | None:
    cache = _state_dir() / "_http" / (hashlib.sha256(url.encode()).hexdigest()[:16] + ".json")
    try:
        if cache.is_file() and (_now().timestamp() - cache.stat().st_mtime) < CACHE_SECONDS:
            return cache.read_text(encoding="utf-8", errors="replace")
    except OSError:
        pass
    return None


def _cache_put(url: str, text: str) -> None:
    cache = _state_dir() / "_http" / (hashlib.sha256(url.encode()).hexdigest()[:16] + ".json")
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(text, encoding="utf-8")
    except OSError:
        pass


def _fetch_json(url: str) -> tuple[object, str]:
    """``(parsed, raw_text)`` for an HTTPS JSON endpoint, cached for six hours."""
    if urlparse(url).scheme != "https":
        raise VendorNotConfiguredError(f"IR facts endpoint must be HTTPS, got {url!r}")
    raw = _cache_get(url)
    if raw is None:
        try:
            response = get_scrubbed(url, params={}, timeout=REQUEST_TIMEOUT, secret="")
            response.raise_for_status()
        except requests.RequestException as exc:
            raise VendorUnavailableError(f"IR facts request failed: {exc}") from None
        raw = response.text
        if len(raw.encode("utf-8")) > MAX_JSON_BYTES:
            raise VendorUnavailableError("IR facts response exceeds the size limit")
        _cache_put(url, raw)
    try:
        return json.loads(raw), raw
    except json.JSONDecodeError as exc:
        raise VendorUnavailableError(f"IR facts endpoint did not return JSON: {exc}") from None


# ---------------------------------------------------------------------------
# Collection
# ---------------------------------------------------------------------------

def _facts_config(instrument: dict, ticker: str) -> dict:
    block = instrument.get("ir_facts") or {}
    if not isinstance(block, dict) or not block.get("enabled"):
        raise VendorNotConfiguredError(f"no IR facts source enabled for {ticker}")
    if not str(block.get("terms_checked") or "").strip():
        raise VendorNotConfiguredError(
            f"IR facts source for {ticker} has no recorded terms check; "
            f"set ir_facts.terms_checked before enabling it"
        )
    config = {
        "facts_json": str(block.get("facts_json") or "").strip(),
        "quote_json": str(block.get("quote_json") or "").strip(),
        "overview_url": str(block.get("overview_url") or "").strip(),
        "reference_date": str(block.get("reference_date") or "").strip(),
        "perimeter_note": str(block.get("perimeter_note") or "").strip(),
        "calendar": [c for c in (block.get("calendar") or []) if isinstance(c, dict)],
        "calendar_verified": str(block.get("calendar_verified") or "").strip(),
        "facts_include": [str(p) for p in (block.get("facts_include") or [])],
        "terms_checked": str(block["terms_checked"]).strip(),
    }
    if not config["facts_json"] and not _fallback_source(instrument):
        raise VendorNotConfiguredError(
            f"IR facts source for {ticker} has neither facts_json nor a usable ir "
            f"sitemap for the release-text fallback"
        )
    return config


def _fallback_source(instrument: dict) -> dict | None:
    """The ``ir`` block, when it can drive the release-text fallback."""
    ir = instrument.get("ir") or {}
    if not isinstance(ir, dict) or not ir.get("enabled"):
        return None
    sitemap, patterns = str(ir.get("sitemap") or "").strip(), ir.get("url_patterns")
    if not sitemap or not isinstance(patterns, list) or not patterns:
        return None
    return {"sitemap": sitemap, "url_patterns": [str(p) for p in patterns]}


def _latest_release_text(instrument: dict) -> tuple[str, str, str]:
    """``(date, url, text)`` of the newest release reachable via the sitemap."""
    source = _fallback_source(instrument)
    if not source:
        raise VendorNotConfiguredError("no sitemap configured for the release-text fallback")
    urls = issuer_ir._sitemap_urls(source["sitemap"])
    # A window wide enough to find the newest release, narrow enough to stay cheap.
    entries = issuer_ir.dated_entries(
        urls, source["url_patterns"], date(2000, 1, 1), date.today()
    )
    entries = [e for e in entries if "/de/" in e["url"]] or entries
    for entry in sorted(entries, key=lambda e: (e["date"], e["url"]), reverse=True)[:4]:
        if not issuer_ir._allowed(entry["url"]):
            continue
        try:
            _title, text, _alts = issuer_ir.fetch_article(entry["url"])
        except VendorUnavailableError:
            continue
        if text.strip():
            return entry["date"], entry["url"], text
    raise VendorUnavailableError("no readable issuer release found for the guidance fallback")


def _guidance_from_releases(instrument: dict, limit: int = 6) -> tuple[dict, str, str]:
    """``(guidance, date, url)`` from the newest release that states guidance.

    Company news and order announcements carry no guidance sentence, so the
    newest release is usually the wrong one; a results publication a few weeks
    back is the right one. Scanning a bounded list is cheaper than guessing.
    """
    source = _fallback_source(instrument)
    if not source:
        raise VendorNotConfiguredError("no sitemap configured for the release-text fallback")
    urls = issuer_ir._sitemap_urls(source["sitemap"])
    entries = issuer_ir.dated_entries(urls, source["url_patterns"], date(2000, 1, 1), date.today())
    entries = [e for e in entries if "/de/" in e["url"]] or entries
    # Guidance lives in results publications, not in the newest order news: sort
    # results-like slugs to the front, newest first inside each group, and scan a
    # bounded prefix. Without this the October news items crowd out the H1 report.
    def _rank(entry: dict) -> tuple:
        slug = entry["url"].rstrip("/").rsplit("/", 1)[-1].lower()
        return (0 if _RESULTS_SLUG_RE.search(slug) else 1, -date.fromisoformat(entry["date"]).toordinal())

    for entry in sorted(entries, key=_rank)[:limit]:
        if not issuer_ir._allowed(entry["url"]):
            continue
        try:
            _title, text, _alts = issuer_ir.fetch_article(entry["url"])
        except VendorUnavailableError:
            continue
        found = extract_guidance(text)
        if found:
            return found, entry["date"], entry["url"]
    return {}, "", ""


def collect_ir_facts(ticker: str, config: dict | None = None) -> dict:
    """Every fact this issuer's configuration can serve, as a plain dict.

    Deterministic and network-only-on-miss: the facts endpoint is cached for six
    hours, and a missing quote never costs the key figures.
    """
    instrument = load_instrument(ticker, config)
    cfg = _facts_config(instrument, ticker)

    facts: dict[str, str] = {}
    reference = cfg["reference_date"]
    source = cfg["facts_json"] or ""
    facts_error = ""

    if cfg["facts_json"]:
        document, raw = _fetch_json(cfg["facts_json"])
        pairs = walk_facts(document)
        if cfg["facts_include"]:
            patterns = [re.compile(p, re.IGNORECASE) for p in cfg["facts_include"]]
            pairs = [p for p in pairs if any(r.search(p["label"]) for r in patterns)]
        for pair in pairs:
            label, value = normalize_fact(pair["label"], pair["value"])
            facts.setdefault(label, value)
        if not reference:
            reference = _guess_reference_date(raw)

    guidance: dict[str, str] = {}
    guidance_date = ""
    guidance_source: list[str] = [""]
    if facts and _fallback_source(instrument):
        # The facts block states the last completed year's figures; the current
        # guidance lives in the newest release. Both belong in the prompt — a
        # prior-year backlog next to a cut guidance range is the whole point.
        # A missing sitemap or an unreadable release is not a gap here: the
        # figures above are already the answer.
        try:
            guidance, guidance_date, release_url = _guidance_from_releases(instrument)
            if guidance:
                guidance_source[0] = release_url
        except (VendorNotConfiguredError, VendorUnavailableError):
            guidance = {}
            guidance_date = ""
    if not facts:
        # No facts endpoint: fall back to the newest release's wording. Coarser,
        # and the block says so, but it keeps every sitemap-configured issuer
        # covered without a bespoke adapter.
        release_date, release_url, text = _latest_release_text(instrument)
        guidance = extract_guidance(text)
        guidance_date = release_date
        source = release_url
        if not guidance:
            facts_error = (
                "the release text carried no recognisable guidance sentence "
                "(forward range/margin); report the guidance as unavailable"
            )

    quote: dict = {}
    if cfg["quote_json"]:
        try:
            quote = extract_quote(_fetch_json(cfg["quote_json"])[0])
        except (VendorNotConfiguredError, VendorUnavailableError):
            quote = {}

    calendar = cfg["calendar"]
    verified = cfg["calendar_verified"]
    stale = False
    if verified and calendar:
        try:
            age = (date.today() - date.fromisoformat(verified)).days
            stale = age > CALENDAR_STALE_DAYS
        except ValueError:
            stale = False

    result = {
        "ticker": ticker.upper(),
        "issuer": instrument.get("issuer") or ticker,
        "as_of": date.today().isoformat(),
        "reference_date": reference,
        "perimeter_note": cfg["perimeter_note"],
        "terms_checked": cfg["terms_checked"],
        "source": source,
        "overview_url": cfg["overview_url"],
        "facts": facts,
        "quote": quote,
        "guidance": guidance,
        "guidance_date": guidance_date,
        "guidance_source": guidance_source[0],
        "calendar": calendar,
        "calendar_verified": verified,
        "calendar_stale": stale,
        "facts_error": facts_error,
    }
    return result


def get_ir_facts(ticker: str, as_of: str | None = None, *, include_delta: bool = True) -> str:
    """The IR-facts block for ``ticker``, with a delta against the stored state.

    ``as_of`` is the run's trade date. State is only written for a
    current-date run: a historical backtest must not overwrite the delta basis
    of today's analysis with numbers it read from an old report.
    """
    facts = collect_ir_facts(ticker)
    previous = load_state(ticker) if include_delta else None
    delta = diff_facts(previous, facts) if previous else []
    if previous:
        facts["previous_seen"] = str(previous.get("as_of") or previous.get("stored_at") or "earlier run")
    facts["stored_at"] = _now().isoformat(timespec="seconds")

    today = date.today().isoformat()
    if as_of is None or str(as_of)[:10] >= today:
        save_state(ticker, facts)
    return render_ir_facts(facts, delta=delta if include_delta else None)


def is_configured(ticker: str, config: dict | None = None) -> tuple[bool, str]:
    """``(configured, reason)`` for pre-flight checks — never raises.

    Used by the wrapper's ``doctor`` so an un-onboarded ticker is reported as
    "no IR facts adapter" (a scope statement, with the onboarding hint) instead
    of failing a run whose other sources are fine.
    """
    try:
        instrument = load_instrument(ticker, config)
    except Exception as exc:  # noqa: BLE001 — a probe must not raise
        return False, f"issuer registry: {exc}"
    block = instrument.get("ir_facts") or {}
    if not isinstance(block, dict) or not block.get("enabled"):
        return False, "no ir_facts block in the issuer registry"
    if not str(block.get("terms_checked") or "").strip():
        return False, "ir_facts.terms_checked missing"
    verified = str(block.get("calendar_verified") or "").strip()
    if verified:
        try:
            if (date.today() - date.fromisoformat(verified)).days > CALENDAR_STALE_DAYS:
                return True, f"calendar verified {verified} is older than {CALENDAR_STALE_DAYS} days"
        except ValueError:
            pass
    return True, "ok"
