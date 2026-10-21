#!/usr/bin/env python3
"""German portal news + curated forum fetcher for TradingAgents runs.

Reads the human-editable source list (references/hardcoded_sources.yaml):
  * enabled RSS/Atom feeds (tagesschau, finanzmarktwelt, n-tv, ...)
  * hand-curated forum threads (e.g. BörsenNEWS RHM thread) parsed from HTML

Output is a schema-v1 snapshot compatible with gnews_snapshot.py merge:

    de_news.py fetch --ticker RHM.DE --date 2026-10-16 --out de_snap.json
    gnews_snapshot.py merge --ticker RHM.DE --date 2026-10-16 \
        --in dpa_snap.json gnews_snap.json de_snap.json --max 15 --out news_supplement_rhm_2026-10-16.json

No API keys. Degrades gracefully: a dead feed is reported in the summary,
never fatal.
"""

from __future__ import annotations

import argparse
import datetime as dt
import email.utils
import html as htmllib
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.request import Request, urlopen

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126 Safari/537.36")
_TIMEOUT = 15


def _ensure_yaml() -> None:
    """PyYAML is not on every system python; re-exec under the TradingAgents venv."""
    try:
        import yaml  # noqa: F401
        return
    except ImportError:
        pass
    candidates = [os.environ.get("TRADINGAGENTS_HOME", "").strip(),
                  str(Path.home() / "hermes/workspace/Home/TradingAgents")]
    for c in candidates:
        if not c:
            continue
        venv_py = Path(c) / ".venv" / "bin" / "python"
        if venv_py.is_file():
            os.execv(str(venv_py), [str(venv_py), *sys.argv])
    sys.exit("de_news.py needs PyYAML (not installed here, no TradingAgents venv found)")


def load_sources() -> dict:
    _ensure_yaml()
    import yaml  # noqa: F401  (ensured above)

    path = Path(os.environ.get("TRADINGAGENTS_SOURCES_YAML", "").strip()
                or Path(__file__).resolve().parent.parent.parent / "references" / "hardcoded_sources.yaml")
    if not path.is_file():
        sys.exit(f"sources file not found: {path}")
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _http_get(url: str) -> bytes | None:
    try:
        req = Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
        with urlopen(req, timeout=_TIMEOUT) as resp:
            data = resp.read(2 * 1024 * 1024)
        return data
    except Exception as exc:  # noqa: BLE001 - feed errors are non-fatal
        print(f"  ! {url}: {exc}", file=sys.stderr)
        return None


def _parse_date(raw: str | None) -> dt.date | None:
    if not raw:
        return None
    raw = raw.strip()
    try:
        return email.utils.parsedate_to_datetime(raw).date()
    except Exception:  # noqa: BLE001
        pass
    try:
        iso = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
        return dt.datetime.fromisoformat(iso).date()
    except ValueError:
        return None


def _text(el) -> str:
    return " ".join((el.text or "").split()) if el is not None else ""


def fetch_feed(feed_id: str, url: str, names: list[str], sector_kw: list[str],
               start: dt.date, as_of: dt.date) -> tuple[list[dict], list[dict]]:
    """Return (name_matches, sector_context_items)."""
    data = _http_get(url)
    if not data:
        return [], []
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        print(f"  ! {feed_id}: XML parse error: {exc}", file=sys.stderr)
        return [], []

    entries: list[tuple[str, str, str]] = []  # (title, link, date_raw+desc)
    if root.tag == "rss":
        for item in root.iter("item"):
            title = _text(item.find("title"))
            link = _text(item.find("link"))
            desc = re.sub(r"<[^>]+>", " ", htmllib.unescape(_text(item.find("description"))))
            entries.append((title, link, f"{_text(item.find('pubDate'))}\t{desc}"))
    else:  # Atom
        ns = {"a": "http://www.w3.org/2005/Atom"}
        for entry in root.findall("a:entry", ns):
            title = _text(entry.find("a:title", ns))
            link_el = entry.find("a:link[@rel='alternate']", ns) or entry.find("a:link", ns)
            link = (link_el.get("href") or "") if link_el is not None else ""
            desc = re.sub(r"<[^>]+>", " ", htmllib.unescape(_text(entry.find("a:summary", ns)) or _text(entry.find("a:content", ns))))
            date_raw = _text(entry.find("a:published", ns)) or _text(entry.find("a:updated", ns))
            entries.append((title, link, f"{date_raw}\t{desc}"))

    name_hits, sector_hits = [], []
    for title, link, blob in entries:
        if not (link.startswith("http") and title):
            continue
        date_part, _, desc = blob.partition("\t")
        pub = _parse_date(date_part)
        text = f"{title} {desc}".lower()
        if pub is None or not (start <= pub <= as_of):
            continue
        item_base = {"source": feed_id, "url": link.strip(),
                     "published_at": pub.isoformat()}
        if any(n.lower() in text for n in names):
            name_hits.append({**item_base, "title": title.strip(),
                              "excerpt": desc[:400]})
        elif sector_kw and any(k.lower() in text for k in sector_kw):
            sector_hits.append({**{**item_base, "source": f"{feed_id} [Sektor]"},
                                "title": title.strip(), "excerpt": desc[:400]})
    return name_hits, sector_hits


# ---------------------------------------------------------------- forums ---

_WEEKDAYS = {"montag": 0, "dienstag": 1, "mittwoch": 2, "donnerstag": 3,
             "freitag": 4, "samstag": 5, "sonntag": 6}


def _forum_date(raw: str | None, as_of: dt.date) -> dt.date | None:
    """German relative timestamps: '12:55 Uhr', 'Gestern 9:14 Uhr', 'Mittwoch 17:39 Uhr'."""
    if not raw:
        return None
    m = re.search(r"(\d{1,2})\.(\d{1,2})(?:\.(\d{4}))?", raw)
    if m:
        try:
            year = int(m.group(3)) if m.group(3) else as_of.year
            return dt.date(year, int(m.group(2)), int(m.group(1)))
        except ValueError:
            pass
    time_m = re.search(r"(\d{1,2}):(\d{2})\s*Uhr", raw)
    if not time_m:
        return None
    low = raw.lower()
    if "gestern" in low:
        return as_of - dt.timedelta(days=1)
    for name, wd in _WEEKDAYS.items():
        if name in low:
            delta = (as_of.weekday() - wd) % 7
            return as_of - dt.timedelta(days=delta)
    return as_of  # bare "HH:MM Uhr" => today


def fetch_forum(label: str, url: str, start: dt.date, as_of: dt.date) -> list[dict]:
    """Parse a boersennews discussion thread.

    Each post lives in a wrapper `<div class="userPosting" id="userPostingId_<id>">`
    carrying author (`<strong data-quotation="username">`), posted time
    (`<span data-quotation="created">`) and body
    (`<div class="text-break overflow-auto ...">`). The server renders A/B markup
    variants, so every field has a plain fallback.
    """
    data = _http_get(url.split("#")[0])
    if not data:
        return []
    page = data.decode("utf-8", errors="replace")
    body = page[page.find("<body"):]
    thread_m = re.match(r"(https?://[^/]+/community/diskussion/[^/]+/\d+)", url)
    thread_base = thread_m.group(1) if thread_m else None

    items: list[dict] = []
    wraps = list(re.finditer(r'id="userPostingId_(\d+)"', body))
    if not wraps:  # markup variant without wrapper ids: use content divs
        wraps = list(re.finditer(r'class="userPostingContent', body))
    for k, wm in enumerate(wraps):
        end = wraps[k + 1].start() if k + 1 < len(wraps) else wm.start() + 9000
        seg = body[wm.start():end]
        post_id = wm.group(1)  # None in the content-div fallback variant
        if not post_id:
            id_m = re.search(r'id="(\d{9})"', seg)
            post_id = id_m.group(1) if id_m else None

        author = ""
        author_m = re.search(r'<strong data-quotation="username">\s*([^<]+?)\s*</strong>', seg)
        if author_m:
            author = author_m.group(1).strip()
        else:
            cands = re.findall(r"<strong[^>]*>([^<]{2,40})</strong>", seg[:1200])
            author = cands[-1].strip() if cands else ""
        time_m = re.search(r'<span data-quotation="created">([^<]+)</span>', seg)
        body_m = re.search(r'text-break overflow-auto[^"]*"[^>]*>(.*?)</div>', seg, re.S)
        if not body_m:
            continue
        post_body = " ".join(htmllib.unescape(re.sub(r"<[^>]+>", " ", body_m.group(1))).split())
        if len(post_body) < 20:
            continue
        pub = _forum_date(time_m.group(1).strip() if time_m else None, as_of)
        if pub is None or pub < start:
            continue
        items.append({
            "source": f"boersennews-forum:{label[:40]}",
            "url": f"{thread_base}/{post_id}/" if thread_base and post_id else url.split("#")[0],
            "published_at": pub.isoformat(),
            "title": f"[Forum] {author or '?'}: {post_body[:90]}{'…' if len(post_body) > 90 else ''}",
            "excerpt": post_body[:400],
        })
    return items


# ----------------------------------------------------------------- main ---

def cmd_fetch(args) -> None:
    src = load_sources()
    spec = (src.get("tickers") or {}).get(args.ticker)
    if not spec:
        sys.exit(f"no entry for ticker {args.ticker} in sources file "
                 f"(add it under 'tickers:')")
    names = [str(n) for n in spec.get("names", [])] + [args.ticker.split(".")[0]]
    sector = (src.get("sectors") or {}).get(spec.get("sector", ""), {})
    sector_kw = [str(k) for k in sector.get("keywords", [])]

    as_of = dt.date.fromisoformat(args.date)
    start = as_of - dt.timedelta(days=args.window_days)
    retrieved = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")

    items: list[dict] = []
    seen_urls: set[str] = set()
    summary = {"ticker": args.ticker, "as_of": as_of.isoformat(),
               "feeds": {}, "forums": {}}
    sector_pool: list[dict] = []

    for feed in src.get("feeds", []):
        if not feed.get("enabled"):
            continue
        hits, sector_hits = fetch_feed(feed["id"], feed["url"], names,
                                       sector_kw, start, as_of)
        summary["feeds"][feed["id"]] = {"name_matches": len(hits),
                                        "sector_context": len(sector_hits)}
        sector_pool.extend(sector_hits)
        for it in hits:
            if it["url"] not in seen_urls:
                seen_urls.add(it["url"])
                items.append({**it, "retrieved_at": retrieved})

    for forum in spec.get("forums", []):
        posts = fetch_forum(forum.get("label", "forum"), forum["url"], start, as_of)
        summary["forums"][forum.get("label", "?")] = len(posts)
        for it in posts:
            if it["url"] not in seen_urls:
                seen_urls.add(it["url"])
                items.append({**it, "retrieved_at": retrieved})

    # Sector context fills remaining slots after name matches.
    room = max(0, args.max - len(items))
    for it in sector_pool[:room]:
        if it["url"] not in seen_urls:
            seen_urls.add(it["url"])
            items.append({**it, "retrieved_at": retrieved})

    snapshot = {"schema_version": 1, "ticker": args.ticker, "as_of": as_of.isoformat(),
                "source": "de_news", "items": items[:args.max]}
    out = json.dumps(snapshot, ensure_ascii=False, indent=2)
    if len(out.encode()) > 100_000:
        sys.exit("snapshot exceeds 100 KB; raise --max or narrow the window")
    if args.out:
        Path(args.out).write_text(out + "\n", encoding="utf-8")
        print(json.dumps({"ok": True, "out": str(args.out), "items": len(items[:args.max]),
                          **summary}, ensure_ascii=False))
    else:
        print(out)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    pf = sub.add_parser("fetch", help="fetch feeds + forums for one ticker")
    pf.add_argument("--ticker", required=True, help="key under 'tickers:' in the sources file")
    pf.add_argument("--date", required=True, help="as_of, YYYY-MM-DD")
    pf.add_argument("--window-days", type=int, default=7)
    pf.add_argument("--max", type=int, default=10)
    pf.add_argument("--sector-max", type=int, default=3,
                    help="(informational; sector items fill free slots up to --max)")
    pf.add_argument("--out")
    pf.set_defaults(func=cmd_fetch)
    args = ap.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
