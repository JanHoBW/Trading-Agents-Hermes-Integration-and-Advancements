#!/usr/bin/env python3
"""gnews.io snapshot builder + refine-from-cache + multi-source snapshot merger.

gnews.io = Google News API with date window (from/to) — closes the 7-day
on-demand gap left by the ~80-min finanznachrichten RSS (see dpa_news.py).

Language case split (--lang):
  auto (default): ticker suffix decides — .XNAS/.XNYS/.XNGS/.XBOU -> en,
  .DE/.XETRA -> de, unknown suffix -> de (with a printed hint). For en,
  --names must be the ENGLISH company name; the market-query default also
  switches (de: "Dax", en: "S&P 500").

Key handling: GNEWS_API_KEY is read from the active Hermes profile .env.
It is never printed, logged, or echoed in output.

Free tier: 100 requests/day + burst protection (back-to-back requests get
HTTP 429). This script paces requests (6 s apart) and retries 429s with a
Retry-After (or 30 s) backoff, max 2 attempts per query.

Response cache (request-load reduction):
  Each fetched query response is appended to
  ~/.hermes/profiles/trading-agents/gnews/cache.jsonl
  (one JSON line: fetched_at, q, q_norm, lang, from, to, total, articles).
  A refetch of the same (normalized q, lang, to) inside --cache-ttl
  (default 1800 s) is served from cache with ZERO API requests. This makes
  repeated runs the same day and dpa+gnews re-merges cost-free.
  `refine` builds snapshots from cached articles with ZERO requests —
  refined queries (keyword re-filtering) never burn quota.

Usage:
  gnews_snapshot.py fetch --ticker IFX.DE --names "Infineon" --date 2026-10-14
                        [--lang auto|de|en] [--window-days 7] [--max 10]
                        [--with-market] [--market-query "..."] [--cache-ttl 1800]
                        [--out snap.json]
  gnews_snapshot.py refine --ticker NVDA.XNAS --date 2026-10-14 --q "NVDA"
                        --keywords "earnings, guidance" --out snap.json
  gnews_snapshot.py merge --ticker ASML --date 2026-10-14 \
                        --in gnews_snap.json dpa_snap.json --out merged.json

Output: news-supplement snapshot, schema v1 (see references/research-subagent.md)
-> hand to the wrapper via --news-supplement.
"""
import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

PROFILE_ENV = os.path.expanduser("~/.hermes/profiles/trading-agents/.env")
CACHE_PATH = os.path.expanduser("~/.hermes/profiles/trading-agents/gnews/cache.jsonl")
ENDPOINT = "https://gnews.io/api/v4/search"
PACING_S = 6.0          # min gap between API requests (burst protection)
REQUEST_TIMEOUT = 25
MAX_EXCERPT = 6000
EN_SUFFIXES = (".XNAS", ".XNYS", ".XNGS", ".XBOU")
DE_SUFFIXES = (".DE", ".XETRA")


def read_key():
    try:
        with open(PROFILE_ENV, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("GNEWS_API_KEY="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    sys.exit(f"GNEWS_API_KEY not found in {PROFILE_ENV}")


def resolve_lang(ticker, lang):
    """Case split: US-suffixed tickers -> en, DE/EU (or unknown) -> de."""
    if lang != "auto":
        return lang
    t = ticker.upper()
    if t.endswith(EN_SUFFIXES):
        return "en"
    if t.endswith(DE_SUFFIXES):
        return "de"
    print(f"[hint] no DE/US suffix on {ticker} — defaulting to lang=de "
          f"(override: --lang en + English --names)", file=sys.stderr)
    return "de"


def market_query_default(lang):
    return "Dax" if lang == "de" else "S&P 500"


def _norm_q(q):
    return re.sub(r"\s+", " ", q.strip().lower())


def _read_cache():
    try:
        with open(CACHE_PATH, encoding="utf-8") as f:
            return [json.loads(l) for l in f if l.strip()]
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def cache_lookup(q, lang, as_of, ttl):
    """Fresh cache entry for (norm_q, lang, to=as_of) fetched within ttl, else None."""
    nk = _norm_q(q)
    cands = [e for e in _read_cache()
             if e.get("q_norm") == nk and e.get("lang") == lang
             and e.get("to") == as_of.isoformat()]
    if not cands:
        return None
    best = max(cands, key=lambda e: e.get("fetched_at", ""))
    age = (datetime.now(timezone.utc)
           - datetime.fromisoformat(best["fetched_at"])).total_seconds()
    if age > ttl:
        return None
    print(f"[cache] q={q!r} age={age:.0f}s (ttl {ttl:.0f}s) — no API request", file=sys.stderr)
    return best


def cache_append(entry):
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    with open(CACHE_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


_last_req = [0.0]


def gnews_query(query, api_key, window_days, as_of, lang="de", **kw):
    """One gnews.io search with pacing + 429 backoff (max 2 attempts)."""
    params = {
        "q": query,
        "apikey": api_key,
        "lang": lang,
        "max": 20,
        "from": (as_of - timedelta(days=window_days)).isoformat(),
        "to": as_of.isoformat(),
        **kw,
    }
    url = ENDPOINT + "?" + urllib.parse.urlencode(params)
    for attempt in (1, 2):
        gap = PACING_S - (time.time() - _last_req[0])
        if gap > 0:
            time.sleep(gap)
        _last_req[0] = time.time()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "hermes-trading/1.0"})
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt == 1:
                wait = e.headers.get("Retry-After")
                wait = float(wait) if wait and wait.isdigit() else 30.0
                print(f"[warn] 429 (rate limit) — backing off {wait:.0f}s", file=sys.stderr)
                time.sleep(wait)
                continue
            raise
    sys.exit("gnews.io still rate-limited after 2 attempts")


def excerpt_of(a):
    for field in ("content", "description", "title"):
        v = (a.get(field) or "").strip()
        if v:
            return v[:MAX_EXCERPT]
    return ""


def title_key(it):
    """Normalized title + date: syndicated reposts share titles, not URLs."""
    norm = re.sub(r"[^a-z0-9]+", "", it["title"].lower())
    return (norm, it["published_at"])


def to_item(a, now_iso):
    return {
        "title": (a.get("title") or "").strip(),
        "source": ((a.get("source") or {}).get("name") or "unknown").strip(),
        "url": (a.get("url") or "").strip(),
        "published_at": (a.get("publishedAt") or "")[:10],
        "retrieved_at": now_iso,
        "excerpt": excerpt_of(a),
    }


def validate_item(it):
    """Schema v1 hard rules: https URL, date shape, non-empty title/url."""
    if not it["url"].startswith("https://") or not it["title"]:
        return False
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", it["published_at"]):
        return False
    return True


def write_snapshot(snap, out):
    data = json.dumps(snap, ensure_ascii=False, indent=2)
    if len(data.encode()) > 100 * 1024:
        sys.exit("snapshot exceeds 100 KB schema limit")
    with open(out, "w", encoding="utf-8") as f:
        f.write(data)
    print(f"[ok] {len(snap['items'])} items for {snap['ticker']} -> {out}")
    print(f"     sha256 {hashlib.sha256(data.encode()).hexdigest()[:16]}  size {len(data)} B")
    for it in snap["items"]:
        print(f"     {it['published_at']} | {it['source'][:24]:24} | {it['title'][:66]}")


def collect_items(arts, now_iso):
    """Validate + dedupe raw gnews articles into snapshot items (newest first)."""
    items, seen_urls, seen_titles = [], set(), set()
    for a in arts:
        it = to_item(a, now_iso)
        if not validate_item(it) or it["url"] in seen_urls:
            continue
        tk = title_key(it)
        if tk in seen_titles:
            continue
        seen_urls.add(it["url"])
        seen_titles.add(tk)
        items.append(it)
    items.sort(key=lambda x: x["published_at"], reverse=True)
    return items


def cmd_fetch(args):
    key = read_key()
    as_of = datetime.strptime(args.date, "%Y-%m-%d")
    lang = resolve_lang(args.ticker, args.lang)
    query = (args.names[0] if args.names else args.ticker).strip()
    now_iso = datetime.now(timezone.utc).isoformat()
    market_query = args.market_query or market_query_default(lang)
    queries = [query] + ([market_query] if args.with_market else [])

    items = []
    for q in queries:
        cached = cache_lookup(q, lang, as_of, args.cache_ttl)
        if cached:
            d = {"totalArticles": cached.get("total"), "articles": cached["articles"]}
        else:
            d = gnews_query(q, key, args.window_days, as_of, lang=lang)
            cache_append({
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "q": q, "q_norm": _norm_q(q), "lang": lang,
                "from": (as_of - timedelta(days=args.window_days)).isoformat(),
                "to": as_of.isoformat(),
                "total": d.get("totalArticles"),
                "articles": d.get("articles") or [],
            })
        arts = d.get("articles") or []
        print(f"[ok] q={q!r} (lang={lang}): {d.get('totalArticles')} total in window, {len(arts)} returned")
        items.extend(collect_items(arts, now_iso))
    items = items[: args.max]

    write_snapshot(
        {"schema_version": 1, "ticker": args.ticker, "as_of": args.date, "items": items},
        args.out or f"gnews_snapshot_{args.ticker.replace('.', '_')}_{args.date}.json",
    )


def cmd_refine(args):
    """Build a snapshot from cached gnews articles — ZERO API requests."""
    as_of = datetime.strptime(args.date, "%Y-%m-%d")
    kws = [k.strip().lower() for k in args.keywords.split(",") if k.strip()]
    entries = _read_cache()
    if not entries:
        sys.exit(f"no gnews cache at {CACHE_PATH} — run fetch first")
    nk = _norm_q(args.q)
    cands = [e for e in entries if e.get("q_norm") == nk and e.get("to") == as_of.isoformat()]
    if not cands:
        sys.exit(f"no cached response for q={args.q!r} as of {args.date} — run fetch first")
    best = max(cands, key=lambda e: e.get("fetched_at", ""))
    print(f"[cache] q={args.q!r} ({best['fetched_at'][:16]}) — no API request", file=sys.stderr)

    now_iso = datetime.now(timezone.utc).isoformat()
    items, seen_urls, seen_titles = [], set(), set()
    for a in best.get("articles") or []:
        it = to_item(a, now_iso)
        if not validate_item(it) or it["url"] in seen_urls:
            continue
        blob = (it["title"] + " " + it["excerpt"]).lower()
        if not any(k in blob for k in kws):
            continue
        tk = title_key(it)
        if tk in seen_titles:
            continue
        seen_urls.add(it["url"])
        seen_titles.add(tk)
        items.append(it)
    items.sort(key=lambda x: x["published_at"], reverse=True)
    items = items[: args.max]
    if not items:
        sys.exit(f"no cached articles for q={args.q!r} match keywords {kws}")
    write_snapshot(
        {"schema_version": 1, "ticker": args.ticker, "as_of": args.date, "items": items},
        args.out or f"gnews_refined_{args.ticker.replace('.', '_')}_{args.date}.json",
    )


def cmd_merge(args):
    """Merge multiple schema-v1 snapshots (same ticker/as_of) into one ≤10-item file."""
    pools = []
    for path in args.inputs:
        with open(path, encoding="utf-8") as f:
            snap = json.load(f)
        if snap.get("ticker") != args.ticker or snap.get("as_of") != args.date:
            sys.exit(f"{path}: ticker/as_of mismatch ({snap.get('ticker')}/{snap.get('as_of')})")
        pools.extend(snap.get("items") or [])
    pools.sort(key=lambda x: x["published_at"], reverse=True)
    kept, seen_urls, seen_titles = [], set(), set()
    for it in pools:
        if not validate_item(it) or it["url"] in seen_urls:
            continue
        tk = title_key(it)
        if tk in seen_titles:
            continue
        seen_urls.add(it["url"])
        seen_titles.add(tk)
        kept.append(it)
    items = kept[: args.max]
    write_snapshot(
        {"schema_version": 1, "ticker": args.ticker, "as_of": args.date, "items": items},
        args.out,
    )


def main(argv=None):
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    pf = sub.add_parser("fetch", help="build a snapshot from gnews.io queries (cached)")
    pf.add_argument("--ticker", required=True)
    pf.add_argument("--names", nargs="*", help="company names; first = most specific (query term)")
    pf.add_argument("--date", required=True, help="as_of, YYYY-MM-DD")
    pf.add_argument("--lang", choices=["auto", "de", "en"], default="auto",
                    help="search language: auto = ticker suffix (.XNAS/.XNYS/.XNGS/.XBOU -> en, "
                         ".DE/.XETRA -> de, else de + hint)")
    pf.add_argument("--window-days", type=int, default=7)
    pf.add_argument("--max", type=int, default=10)
    pf.add_argument("--with-market", action="store_true")
    pf.add_argument("--market-query", default=None,
                    help="second query when --with-market (default: Dax / S&P 500 by lang)")
    pf.add_argument("--cache-ttl", type=int, default=1800,
                    help="serve same (q,lang,as_of) from cache within this many seconds (default 1800)")
    pf.add_argument("--out")
    pf.set_defaults(fn=cmd_fetch)
    pr = sub.add_parser("refine", help="snapshot from cached articles by keywords (zero API requests)")
    pr.add_argument("--ticker", required=True)
    pr.add_argument("--date", required=True, help="as_of, YYYY-MM-DD")
    pr.add_argument("--q", required=True, help="query term that was fetched earlier")
    pr.add_argument("--keywords", required=True, help="comma list; substring match on title+excerpt")
    pr.add_argument("--max", type=int, default=10)
    pr.add_argument("--out")
    pr.set_defaults(fn=cmd_refine)
    pm = sub.add_parser("merge", help="merge snapshot files (dedupe by URL, newest wins)")
    pm.add_argument("--ticker", required=True)
    pm.add_argument("--date", required=True)
    pm.add_argument("--in", dest="inputs", nargs="+", required=True)
    pm.add_argument("--max", type=int, default=10)
    pm.add_argument("--out", required=True)
    pm.set_defaults(fn=cmd_merge)
    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
