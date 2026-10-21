#!/usr/bin/env python3
"""dpa-AFX news collector + TradingAgents news-supplement builder (prototype).

Source : public RSS feeds of finanznachrichten.de (dpa / dpa-AFX syndication)
Cache  : persistent JSONL (one line per unique article, deduped by link)
Output : per-run news-supplement snapshot, schema v1 (see research-subagent.md)

Usage:
  dpa_news.py collect [--cache PATH]
  dpa_news.py snapshot --ticker ASML --date 2026-10-14 [--names "ASML"] [--isin NL0010273215]
                      [--window-days 7] [--max-items 10] [--with-market] [--out snap.json]
"""
import argparse
import email.utils
import hashlib
import json
import os
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

DEFAULT_FEEDS = {
    "dpa-afx": "https://www.finanznachrichten.de/rss-nachrichten-medien-dpa-afx",
    "adhoc": "https://www.finanznachrichten.de/rss-aktien-adhoc/",
    "marktberichte": "https://www.finanznachrichten.de/rss-marktberichte",
}
FN_NS = "{http://www.finanznachrichten.de/service/rss}"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"

MARKET_RE = re.compile(r"DAX-FLASH|Börsentag auf einen Blick|Marktüberblick|Börse heute", re.I)


def http_get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def parse_date(s):
    """ISO 8601 or RFC 822 -> aware datetime (UTC)."""
    if not s:
        return None
    s = s.strip()
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        pass
    try:
        d = email.utils.parsedate_to_datetime(s)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d
    except Exception:
        return None


def fetch_feed(name, url):
    root = ET.fromstring(http_get(url))
    out = []
    for item in root.iter("item"):
        pub = parse_date(item.findtext("pubDate") or item.findtext("date"))
        isin_el = item.find(FN_NS + "isin")
        desc = (item.findtext("description") or "").strip()
        rec = {
            "link": (item.findtext("link") or "").strip(),
            "title": (item.findtext("title") or "").strip(),
            "published_at": pub.astimezone(timezone.utc).isoformat() if pub else None,
            "source": "finanznachrichten.de " + ("(dpa-AFX)" if "(dpa-AFX)" in desc else "(dpa)" if "(dpa)" in desc else "(other)"),
            "feed": name,
            "isin": (isin_el.text.strip() if isin_el is not None and isin_el.text else None),
            "excerpt": desc[:1500],
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }
        if rec["link"] and rec["published_at"]:
            out.append(rec)
    return out


def load_cache(path):
    if not os.path.exists(path):
        return set()
    seen = set()
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    seen.add(json.loads(line)["link"])
                except Exception:
                    pass
    return seen


def cmd_collect(args):
    cache = args.cache
    os.makedirs(os.path.dirname(cache), exist_ok=True)
    seen = load_cache(cache)
    new = 0
    with open(cache, "a", encoding="utf-8") as f:
        for name, url in DEFAULT_FEEDS.items():
            try:
                recs = fetch_feed(name, url)
            except Exception as e:
                print(f"[warn] {name}: {e}", file=sys.stderr)
                continue
            for r in recs:
                if r["link"] in seen:
                    continue
                seen.add(r["link"])
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
                new += 1
            print(f"[ok] {name}: {len(recs)} items in feed")
    print(f"[ok] +{new} new articles, cache total {len(seen)} -> {cache}")


def text_match(needle, *texts):
    """Case-insensitive word-boundary match; handles 'SAP' vs 'SAP-AG'."""
    pat = re.compile(r"(?<![a-zäöüß0-9])" + re.escape(needle.lower()) + r"(?![a-zäöüß0-9])")
    return any(pat.search(t.lower()) for t in texts if t)


def cmd_snapshot(args):
    cache = args.cache
    if not os.path.exists(cache):
        sys.exit(f"cache not found: {cache} (run 'collect' first)")
    as_of = datetime.strptime(args.date, "%Y-%m-%d").date()
    start = as_of - timedelta(days=args.window_days)
    needles = [args.ticker] + list(args.names or []) + ([args.isin] if args.isin else [])
    needles = [n.strip() for n in needles if n and n.strip()]

    hits, market = [], []
    with open(cache, encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
            except Exception:
                continue
            # date-level window (schema v1 uses YYYY-MM-DD published_at),
            # so same-day articles count even though as_of is a date, not a time
            day = (r.get("published_at") or "")[:10]
            if not day or not (start.isoformat() <= day <= as_of.isoformat()):
                continue
            text = r.get("title") + " " + (r.get("excerpt") or "")
            if args.isin and r.get("isin") and r["isin"].lower() == args.isin.lower():
                hits.append(r)
                continue
            if any(text_match(n, r.get("title", ""), r.get("excerpt", "")) for n in needles):
                hits.append(r)
            elif MARKET_RE.search(r.get("title", "")):
                market.append(r)
    # dedupe by link, newest first
    by_link = {}
    for r in hits:
        by_link.setdefault(r["link"], r)
    hits = sorted(by_link.values(), key=lambda r: r["published_at"], reverse=True)
    if args.with_market:
        m_by_link = {}
        for r in market:
            m_by_link.setdefault(r["link"], r)
        for r in sorted(m_by_link.values(), key=lambda r: r["published_at"], reverse=True)[:2]:
            if r["link"] not in {h["link"] for h in hits}:
                hits.append(r)
    hits = hits[: args.max_items]

    now = datetime.now(timezone.utc).isoformat()
    snap = {
        "schema_version": 1,
        "ticker": args.ticker,
        "as_of": args.date,
        "items": [
            {
                "title": r["title"],
                "source": r["source"],
                "url": r["link"],
                "published_at": r["published_at"][:10],
                "retrieved_at": now,
                "excerpt": r["excerpt"],
            }
            for r in hits
        ],
    }
    out = args.out or f"dpa_snapshot_{args.ticker.replace('.', '_')}_{args.date}.json"
    data = json.dumps(snap, ensure_ascii=False, indent=2)
    with open(out, "w", encoding="utf-8") as f:
        f.write(data)
    print(f"[ok] {len(snap['items'])} items for {args.ticker} -> {out}")
    print(f"     sha256 {hashlib.sha256(data.encode()).hexdigest()[:16]}  size {len(data)} B")
    for r in hits:
        print(f"     {r['published_at'][:16]} | {r['title'][:78]}")


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--cache", default=os.path.expanduser("~/.hermes/profiles/trading-agents/dpa-news/dpaafx.jsonl"))
    sub = p.add_subparsers(dest="cmd", required=True)
    pc = sub.add_parser("collect", help="fetch feeds, append new articles to cache")
    pc.set_defaults(fn=cmd_collect)
    ps = sub.add_parser("snapshot", help="build news-supplement snapshot from cache")
    ps.add_argument("--ticker", required=True)
    ps.add_argument("--date", required=True, help="as_of, YYYY-MM-DD")
    ps.add_argument("--names", nargs="*", help="company names to match (e.g. 'Rheinmetall')")
    ps.add_argument("--isin", help="exact ISIN match (fn:isin tag in feed)")
    ps.add_argument("--window-days", type=int, default=7)
    ps.add_argument("--max-items", type=int, default=10)
    ps.add_argument("--with-market", action="store_true", help="add up to 2 market-flash items")
    ps.add_argument("--out", help="output path (default: dpa_snapshot_<ticker>_<date>.json)")
    ps.set_defaults(fn=cmd_snapshot)
    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
