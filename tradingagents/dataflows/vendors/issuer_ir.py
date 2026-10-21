"""First-party news from an issuer's own website, discovered via its sitemap.

Why a sitemap and not a crawl: German issuer sites are increasingly JavaScript
apps whose news list is rendered client-side, so the HTML holds no article links
(Rheinmetall's site is a Nuxt app — verified 2026-10-12). Their *sitemaps*,
however, list every release with a dated slug
(``/de/media/news-watch/news/2026/10/2026-10-08-…``) and the article pages
themselves are server-rendered with readable text (verified 2026-10-21: 3.4 kB of
clean text per release). That makes discovery cheap, deterministic and polite:
one sitemap request instead of walking a site.

Configuration is per issuer in the issuer registry (``ir`` block), never inferred:

    "ir": {
      "enabled": true,
      "terms_checked": "2026-10-21: robots.txt allows crawling; internal analysis only",
      "sitemap": "https://www.rheinmetall.com/sitemap.site_1.xml",
      "url_patterns": ["/media/news-watch/news/\\d{4}/\\d{2}/\\d{4}-\\d{2}-\\d{2}-"]
    }

Gate rules: no ``ir`` block, ``enabled`` false, or an empty ``terms_checked``
raises ``VendorNotConfiguredError`` so the routing chain falls through — a source
is only used after someone decided the site may be read. ``robots.txt`` is
honoured per URL (``urllib.robotparser``); disallowed entries are skipped and
counted in the output, never silently dropped.

The output is the issuer's own wording, so the header says so: issuer claims are
not independent corroboration.
"""
from __future__ import annotations

import gzip
import hashlib
import re
import urllib.robotparser as robotparser
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests

from tradingagents.dataflows.config import get_config
from tradingagents.dataflows.errors import VendorNotConfiguredError, VendorUnavailableError
from tradingagents.dataflows.issuer_registry import load_instrument
from tradingagents.dataflows.net import get_scrubbed

REQUEST_TIMEOUT = 25
MAX_SITEMAP_BYTES = 8_000_000
SITEMAP_CACHE_SECONDS = 24 * 3600
MAX_ARTICLE_FETCHES = 8
ARTICLE_TEXT_CHARS = 1200
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/155.0.0.0 Safari/537.36"
)
_DATE_IN_SLUG = re.compile(r"(\d{4})-(\d{2})-(\d{2})")


# ---------------------------------------------------------------------------
# Pure helpers (testable without network)
# ---------------------------------------------------------------------------

def parse_sitemap(xml_text: str) -> list[str]:
    """Every URL in a ``urlset``, or in a ``sitemapindex`` (indexes are flattened later)."""
    if len(xml_text.encode("utf-8")) > MAX_SITEMAP_BYTES:
        raise ValueError("sitemap exceeds the 8 MB safety limit")
    root = ET.fromstring(xml_text)
    return [loc.text.strip() for loc in root.iter()
            if loc.tag.rsplit("}", 1)[-1].lower() == "loc" and loc.text and loc.text.strip()]


def is_sitemap_index(xml_text: str) -> bool:
    return "<sitemapindex" in xml_text[:2000]


def dated_entries(urls: list[str], patterns: list[str], start: date, end: date) -> list[dict]:
    """URLs whose path matches a configured pattern and whose slug date is in window.

    The date comes from the ``YYYY-MM-DD`` slug; a URL without one is skipped
    rather than guessed (an undated release cannot be placed point-in-time).
    """
    compiled = [re.compile(p) for p in patterns]
    out: list[dict] = []
    for url in urls:
        path = urlparse(url).path
        if not any(rx.search(path) for rx in compiled):
            continue
        m = _DATE_IN_SLUG.search(path)
        if not m:
            continue
        try:
            when = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            continue
        if start <= when <= end:
            out.append({"url": url, "date": when.isoformat()})
    out.sort(key=lambda item: (item["date"], item["url"]), reverse=True)
    return out


def clean_article(html: str) -> tuple[str, str]:
    """(title, text) from an article page: drop scripts/styles, collapse the rest."""
    title = ""
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
    if m:
        title = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", m.group(1))).strip()
    body = re.sub(r"<script.*?</script>|<style.*?</style>|<noscript.*?</noscript>",
                  " ", html, flags=re.S | re.I)
    body = re.sub(r"<[^>]+>", " ", body)
    body = (body.replace("&nbsp;", " ").replace("&amp;", "&")
                .replace("&quot;", '"').replace("&#39;", "'")
                .replace("&auml;", "ä").replace("&ouml;", "ö").replace("&uuml;", "ü")
                .replace("&Auml;", "Ä").replace("&Ouml;", "Ö").replace("&Uuml;", "Ü")
                .replace("&szlig;", "ß"))
    body = re.sub(r"\s+", " ", body).strip()
    # Cut the site chrome (nav, contact block, share buttons) that precedes the
    # release itself: RHM marks the start with "| Pressemitteilung"/"| Press release".
    for marker in ("| Pressemitteilung", "| Press release"):
        if marker in body:
            body = body.split(marker, 1)[1].strip()
            break
    return title, body


def parse_alternates(html: str) -> list[str]:
    """URLs this page declares as its own translations (``rel=alternate hreflang``).

    Issuers publish each release in German and English under *different* slugs
    ("…uebernimmt-mangalia-werft…" vs "…acquires-the-mangalia-shipyard…"), so the
    URL cannot be used to pair them. The article page's hreflang block names the
    twin exactly (verified on rheinmetall.com 2026-10-21); without it both
    languages would be reported as separate releases.
    """
    alternates = []
    for tag in re.findall(r"<link[^>]*>", html, re.I):
        if "alternate" not in tag.lower() or "hreflang" not in tag.lower():
            continue
        m = re.search(r'href="([^"]+)"', tag)
        if m and m.group(1).startswith("http"):
            alternates.append(m.group(1).strip())
    return alternates


def excerpt(text: str, limit: int = ARTICLE_TEXT_CHARS) -> str:
    """A readable excerpt, cut on a word boundary and marked when truncated."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    return (cut[: cut.rfind(" ")] if " " in cut else cut).rstrip() + " …"


# ---------------------------------------------------------------------------
# Network helpers
# ---------------------------------------------------------------------------

def _cache_dir() -> Path:
    base = Path(get_config().get("data_cache_dir", "~/.tradingagents/cache")).expanduser()
    path = base / "sitemaps"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _cached_sitemap(url: str) -> str:
    """The sitemap body, cached for a day; gzip is detected by magic bytes."""
    cache = _cache_dir() / (hashlib.sha256(url.encode()).hexdigest()[:16] + ".xml")
    try:
        if cache.is_file() and (datetime.now(timezone.utc).timestamp() - cache.stat().st_mtime) < SITEMAP_CACHE_SECONDS:
            return cache.read_text(encoding="utf-8", errors="replace")
    except OSError:
        pass
    try:
        resp = get_scrubbed(url, params={}, timeout=REQUEST_TIMEOUT, secret="")
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise VendorUnavailableError(f"issuer sitemap request failed: {exc}") from None
    raw = resp.content
    if raw[:2] == b"\x1f\x8b":
        try:
            raw = gzip.decompress(raw)
        except OSError as exc:
            raise VendorUnavailableError(f"issuer sitemap is not valid gzip: {exc}") from None
    text = raw.decode("utf-8", errors="replace")
    try:
        cache.write_text(text, encoding="utf-8")
    except OSError:
        pass  # a cache miss must never fail the call
    return text


_robots_cache: dict[str, robotparser.RobotFileParser | None] = {}


def _robots_for(url: str) -> robotparser.RobotFileParser | None:
    """Parsed robots.txt for the URL's host (None when it could not be read)."""
    parts = urlparse(url)
    host = f"{parts.scheme}://{parts.netloc}"
    if host in _robots_cache:
        return _robots_cache[host]
    parser = robotparser.RobotFileParser()
    parser.set_url(f"{host}/robots.txt")
    try:
        parser.read()
    except Exception:  # noqa: BLE001 — unreachable robots.txt is not a blanket denial
        parser = None
    _robots_cache[host] = parser
    return parser


def _allowed(url: str) -> bool:
    parser = _robots_for(url)
    if parser is None:
        return True
    try:
        return parser.can_fetch(USER_AGENT, url)
    except Exception:  # noqa: BLE001
        return True


def fetch_article(url: str) -> tuple[str, str, list[str]]:
    """(title, text, translation URLs) of one release page."""
    try:
        resp = get_scrubbed(url, params={}, timeout=REQUEST_TIMEOUT, secret="")
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise VendorUnavailableError(f"issuer article request failed: {exc}") from None
    title, text = clean_article(resp.text)
    return title, text, parse_alternates(resp.text)


def _sitemap_urls(sitemap: str, depth: int = 0) -> list[str]:
    """All page URLs from a sitemap, one level of index nesting."""
    text = _cached_sitemap(sitemap)
    try:
        urls = parse_sitemap(text)
    except (ET.ParseError, ValueError) as exc:
        raise VendorUnavailableError(f"issuer sitemap is invalid: {exc}") from None
    if not is_sitemap_index(text) or depth >= 2:
        return urls
    pages: list[str] = []
    for child in urls:
        if child.endswith((".xml", ".xml.gz")):
            pages.extend(_sitemap_urls(child, depth + 1))
    return pages or urls


# ---------------------------------------------------------------------------
# Vendor entry point
# ---------------------------------------------------------------------------

def _ir_config(instrument: dict, ticker: str) -> dict:
    """The gated ``ir`` block: enabled, terms checked, sitemap, URL patterns."""
    ir = instrument.get("ir") or {}
    if not isinstance(ir, dict) or not ir.get("enabled"):
        raise VendorNotConfiguredError(f"no issuer-site news source enabled for {ticker}")
    if not str(ir.get("terms_checked") or "").strip():
        raise VendorNotConfiguredError(
            f"issuer-site news for {ticker} has no recorded terms check; "
            f"set ir.terms_checked before enabling it"
        )
    sitemap = str(ir.get("sitemap") or "").strip()
    if urlparse(sitemap).scheme != "https" or not urlparse(sitemap).hostname:
        raise VendorNotConfiguredError(f"issuer sitemap for {ticker} must be an HTTPS URL")
    patterns = ir.get("url_patterns")
    if not isinstance(patterns, list) or not patterns:
        raise VendorNotConfiguredError(f"issuer-site news for {ticker} has no url_patterns")
    return {"sitemap": sitemap, "url_patterns": [str(p) for p in patterns],
            "terms_checked": str(ir["terms_checked"]).strip()}


def get_news_issuer_ir(
    ticker: str,
    start_date: str,
    end_date: str,
    *,
    limit: int | None = None,
    excerpt_chars: int | None = None,
) -> str:
    """Dated releases from the issuer's own site within [start_date, end_date].

    Terminal for the chain on success (including a verified empty window), like
    the RSS vendor: an issuer site that answered with nothing in the window is a
    real absence *in that source*, and saying so is more useful than falling
    through to general news that is not first-party.
    """
    start, end = date.fromisoformat(start_date), date.fromisoformat(end_date)
    if start > end:
        raise ValueError("start_date must not be after end_date")

    instrument = load_instrument(ticker)
    cfg = _ir_config(instrument, ticker)

    urls = _sitemap_urls(cfg["sitemap"])
    matched = dated_entries(urls, cfg["url_patterns"], start, end)
    allowed: list[dict] = []
    blocked: list[dict] = []
    for entry in matched:
        (allowed if _allowed(entry["url"]) else blocked).append(entry)
    if matched and not allowed:
        raise VendorUnavailableError(
            f"robots.txt of {urlparse(cfg['sitemap']).hostname} disallows every matching "
            f"release path for this user agent"
        )

    if limit is None:
        limit = min(MAX_ARTICLE_FETCHES, int(get_config().get("news_article_limit", 20)))
    limit = max(1, int(limit))
    width = ARTICLE_TEXT_CHARS if excerpt_chars is None else max(80, int(excerpt_chars))
    header = [
        f"## Official issuer site: {instrument.get('issuer', ticker)} ({ticker})",
        f"- Source: issuer's own sitemap and release pages — {cfg['sitemap']}",
        f"- Terms check on record: {cfg['terms_checked']}",
        f"- Window: {start} to {end}; releases are dated from their URL slug",
        "- First-party wording: these are the issuer's own claims, not independent corroboration.",
    ]
    if not matched:
        header.append(
            "- Result: no dated release matched in this window; this is an absence in this "
            "source, not evidence that the issuer published nothing elsewhere."
        )
    if blocked:
        header.append(f"- Skipped {len(blocked)} release(s) disallowed by robots.txt.")
    lines = header + [""]

    # German releases first, so the twin of a pairing is normally already known
    # (and skipped) by the time its English version comes up.
    allowed.sort(key=lambda e: (e["date"], "/de/" in e["url"], e["url"]), reverse=True)
    twins: set[str] = set()
    shown = 0
    skipped_twins = 0
    for entry in allowed:
        if shown >= limit:
            break
        if entry["url"] in twins:
            skipped_twins += 1
            continue
        try:
            title, text, alternates = fetch_article(entry["url"])
        except VendorUnavailableError as exc:
            lines.append(f"### {entry['date']} — (page unavailable)")
            lines.append(f"- URL: {entry['url']}")
            lines.append(f"- Fetch failed: {exc}")
            lines.append("")
            continue
        twins.update(url for url in alternates if url != entry["url"])
        slug_title = entry["url"].rstrip("/").rsplit("/", 1)[-1]
        slug_title = re.sub(r"^\d{4}-\d{2}-\d{2}-", "", slug_title).replace("-", " ").strip()
        heading = title or slug_title or "(untitled release)"
        lines.append(f"### {entry['date']} — {heading}")
        lines.append(f"- URL: {entry['url']}")
        body = excerpt(text, width)
        if body:
            lines.append(body)
        lines.append("")
        shown += 1
    if skipped_twins:
        lines.append(f"_(skipped {skipped_twins} translation twin(s) of already-shown releases)_")
    if len(allowed) > limit:
        lines.append(f"_(showing {limit} of {len(allowed)} matching releases; newest first)_")
    if not allowed and blocked:
        lines.append("_(all matching releases were disallowed by robots.txt)_")
    return "\n".join(lines).strip()
