#!/usr/bin/env python3
"""news_collect.py — EIN Entry-Point für die News-Pipeline (DE-Aktien).

Statt dpa_news.py / gnews_snapshot.py / de_news.py einzeln aufzurufen,
konsolidiert dieses Script die komplette Pipeline in einen Aufruf.
Die Quellen-Logik lebt in news_sources/ (Module: dpa, gnews, de_feeds).

Subcommands:
  fetch    Vollpipeline: dpa (collect+snapshot) + gnews + de_feeds/Foren
           → gemergtes News-Supplement (schema v1)
  merge    Snapshot-Dateien mergen (Passthrough, Dedupe by URL, newest wins)
  sources  Status der YAML-Quellen anzeigen (Feeds, Foren, Ticker)

Beispiele:
  news_collect.py fetch --ticker RHM.DE --date 2026-10-16 \
      --names "Rheinmetall" --isin DE0007030009 --max 30 \
      --out reports/.../news_supplement_RHM.DE_2026-10-16.json
  news_collect.py sources
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from news_sources import dpa, gnews, de_feeds  # noqa: E402


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def run_stage(name: str, module, argv: list[str]) -> bool:
    """Run one source module's main(); return True on success."""
    try:
        module.main(argv)
        return True
    except SystemExit as exc:
        code = exc.code
        log(f"   ({name}: exit {code})")
        return code in (0, None)
    except Exception as exc:  # noqa: BLE001 — stage isolation
        log(f"   ({name} fehlgeschlagen: {exc})")
        return False


def cmd_fetch(args: argparse.Namespace) -> int:
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    work = out.parent if out.parent != Path(".") else Path(".")
    dpa_snap = work / "dpa_snap.json"
    gnews_snap = work / "gnews_snap.json"
    de_snap = work / "de_snap.json"

    names = args.names or []
    log(f">> dpa collect (best-effort, Cache-Fallback)")
    run_stage("dpa collect", dpa, ["collect"])

    dpa_argv = ["snapshot", "--ticker", args.ticker, "--date", args.date,
                "--window-days", str(args.window_days), "--out", str(dpa_snap)]
    if names:
        dpa_argv += ["--names", *names]
    if args.isin:
        dpa_argv += ["--isin", args.isin]
    if not args.no_market:
        dpa_argv += ["--with-market"]
    log(f">> dpa snapshot")
    run_stage("dpa snapshot", dpa, dpa_argv)

    g_argv = ["fetch", "--ticker", args.ticker, "--date", args.date,
              "--window-days", str(args.window_days), "--max", str(args.max),
              "--out", str(gnews_snap)]
    if names:
        g_argv += ["--names", *names]
    if not args.no_market:
        g_argv += ["--with-market"]
    log(f">> gnews fetch")
    run_stage("gnews fetch", gnews, g_argv)

    de_argv = ["fetch", "--ticker", args.ticker, "--date", args.date,
               "--window-days", str(args.window_days), "--max", str(args.max),
               "--sector-max", str(args.sector_max),
               "--fach-max", str(args.fach_max),
               "--forum-max", str(args.forum_max),
               "--out", str(de_snap)]
    log(f">> de_feeds fetch (Portale + Foren)")
    run_stage("de_feeds fetch", de_feeds, de_argv)

    inputs = [p for p in (dpa_snap, gnews_snap, de_snap) if p.is_file() and p.stat().st_size > 2]
    if not inputs:
        log("   KEIN Snapshot erzeugt — keine Quelle lieferte Items")
        return 1

    log(f">> merge ({len(inputs)} Inputs → {args.out}, max {args.max})")
    ok = run_stage("merge", gnews, ["merge", "--ticker", args.ticker,
                                    "--date", args.date, "--in", *[str(p) for p in inputs],
                                    "--max", str(args.max), "--out", str(out)])
    if not ok or not out.is_file():
        log("   merge fehlgeschlagen")
        return 1

    try:
        n = len(json.loads(out.read_text(encoding="utf-8"))["items"])
    except (json.JSONDecodeError, KeyError) as exc:
        log(f"   Supplement ungültig: {exc}")
        return 1
    log(f"   Supplement: {out} ({n} Items)")
    print(str(out))
    return 0


def cmd_merge(args: argparse.Namespace) -> int:
    ok = run_stage("merge", gnews, ["merge", "--ticker", args.ticker,
                                    "--date", args.date, "--in", *args.inputs,
                                    "--max", str(args.max), "--out", args.out])
    return 0 if ok else 1


def cmd_sources(_args: argparse.Namespace) -> int:
    import os
    path = Path(os.environ.get("TRADINGAGENTS_SOURCES_YAML", "").strip()
                or HERE.parent / "references" / "hardcoded_sources.yaml")
    if not path.is_file():
        log(f"sources file not found: {path}")
        return 1
    import yaml
    d = yaml.safe_load(path.read_text(encoding="utf-8"))
    print(f"Sources: {path}")
    feeds = (d.get("feeds") or [])
    for f in feeds:
        if isinstance(f, dict):
            mark = "ON " if f.get("enabled") else "OFF"
            print(f"  [feed]  {mark} {f.get('id', '?'):<20} {f.get('url', '?')}")
    sectors = (d.get("sectors") or {})
    for name, s in sectors.items():
        kws = s.get("keywords", []) if isinstance(s, dict) else []
        print(f"  [sector] {name:<20} {len(kws)} Keywords: {', '.join(kws[:6])}{'…' if len(kws) > 6 else ''}")
    tickers = (d.get("tickers") or {})
    for t, meta in tickers.items():
        forums = meta.get("forums") or []
        print(f"  [ticker] {t:<10} ISIN={meta.get('isin', '?')}  {len(forums)} Foren")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    pf = sub.add_parser("fetch", help="vollständige News-Pipeline → Supplement")
    pf.add_argument("--ticker", required=True)
    pf.add_argument("--date", required=True, help="as_of, YYYY-MM-DD")
    pf.add_argument("--names", nargs="*", help="Firmennamen (dpa + gnews Matching)")
    pf.add_argument("--isin", help="exakte ISIN (dpa fn:isin Tag)")
    pf.add_argument("--window-days", type=int, default=7)
    pf.add_argument("--max", type=int, default=30,
                    help="max Items im Supplement (Framework-/Vendor-Limit: 30)")
    pf.add_argument("--sector-max", type=int, default=5,
                    help="max Sektor-Kontext-Items ([Sektor]-Tag) im de-Snapshot")
    pf.add_argument("--fach-max", type=int, default=10,
                    help="max Fachquellen-Items ([Fachquelle]-Tag, hartpunkt/esut)")
    pf.add_argument("--forum-max", type=int, default=3,
                    help="max Forum-Posts (werden zuletzt aufgefüllt)")
    pf.add_argument("--no-market", action="store_true", help="Makro-Flash-Items deaktivieren")
    pf.add_argument("--out", default=None,
                    help="Supplement-Ziel (Default: news_supplement_<TICKER>_<DATE>.json in cwd)")
    pf.set_defaults(fn=cmd_fetch)

    pm = sub.add_parser("merge", help="Snapshot-Dateien mergen")
    pm.add_argument("--ticker", required=True)
    pm.add_argument("--date", required=True)
    pm.add_argument("--in", dest="inputs", nargs="+", required=True)
    pm.add_argument("--max", type=int, default=10)
    pm.add_argument("--out", required=True)
    pm.set_defaults(fn=cmd_merge)

    ps = sub.add_parser("sources", help="YAML-Quellenstatus anzeigen")
    ps.set_defaults(fn=cmd_sources)

    args = ap.parse_args(argv)
    if args.cmd == "fetch" and not args.out:
        args.out = f"news_supplement_{args.ticker}_{args.date}.json"
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
