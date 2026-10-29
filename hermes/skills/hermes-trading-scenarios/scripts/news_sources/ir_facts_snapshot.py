#!/usr/bin/env python3
"""ir_facts_snapshot.py — IR-Fakten als Snapshot für den News-Supplement-Kanal.

Zwei Wege, ein Format:

  build   liest die Fakten über den deterministischen Vendor (issuer_facts)
          und schreibt einen Snapshot für ``--supplement=``.
  import  nimmt eine vom Recherche-Subagenten erstellte Fakten-JSON, prüft und
          normalisiert sie und schreibt denselben Snapshot.

Der Snapshot ist Schema v1 mit leerer ``items``-Liste und einem ``facts``-Block:
Fakten sind *Zustand* mit eigenem Referenzdatum, keine Schlagzeile — deshalb
werden sie nicht auf das 7-Tage-Fenster gefiltert (siehe news_supplement.py).

Beispiele:
  ir_facts_snapshot.py build  --ticker RHM.DE --date 2026-11-03 --out facts.json
  ir_facts_snapshot.py import --ticker XYZ.DE --date 2026-11-03 \\
      --in raw_facts.json --out facts.json
  ir_facts_snapshot.py check  --ticker RHM.DE
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

MAX_FACTS = 40


def _repo_import():
    """Import the fork's vendor, with a clear error when the repo is missing."""
    try:
        from tradingagents.dataflows.vendors import issuer_facts  # noqa: F401
        return issuer_facts
    except ImportError:
        pass
    home = os.environ.get("TRADINGAGENTS_HOME", "").strip() or str(
        Path.home() / "hermes/workspace/Home/TradingAgents"
    )
    if (Path(home) / "tradingagents").is_dir():
        sys.path.insert(0, home)
    from tradingagents.dataflows.vendors import issuer_facts
    return issuer_facts


def _normalise_facts(raw: dict, ticker: str) -> dict:
    """Accept a bare facts object or a vendor dict; return the snapshot block."""
    if "facts" in raw and isinstance(raw["facts"], dict):
        # vendor output: {"facts": {label: value}, "reference_date": ...}
        figures = [
            {"label": label, "value": value,
             "source_url": raw.get("source") or raw.get("overview_url") or ""}
            for label, value in raw["facts"].items()
        ]
        return {
            "issuer": raw.get("issuer") or ticker,
            "reference_date": raw.get("reference_date") or "",
            "perimeter": raw.get("perimeter_note") or raw.get("perimeter") or "",
            "figures": figures[:MAX_FACTS],
            "guidance": raw.get("guidance") or "",
            "calendar": raw.get("calendar") or [],
        }
    # sub-agent output: {"figures": [{"label","value","source_url","reference_date"}], ...}
    figures = raw.get("figures")
    if not isinstance(figures, list):
        raise SystemExit("facts JSON needs either 'facts' (label->value) or 'figures' (list)")
    clean = []
    for figure in figures[:MAX_FACTS]:
        if not isinstance(figure, dict) or not figure.get("label") or not figure.get("value"):
            raise SystemExit("every figure needs 'label' and 'value'")
        if not str(figure.get("source_url") or "").startswith("https://"):
            raise SystemExit(f"figure {figure['label']!r} needs an https source_url")
        clean.append({
            "label": str(figure["label"]).strip(),
            "value": str(figure["value"]).strip(),
            "source_url": str(figure["source_url"]).strip(),
            "reference_date": str(figure.get("reference_date") or raw.get("reference_date") or "").strip(),
        })
    reference = str(raw.get("reference_date") or "").strip()
    if not reference:
        raise SystemExit("facts JSON needs a reference_date (the figures' own as-of date)")
    return {
        "issuer": str(raw.get("issuer") or ticker).strip(),
        "reference_date": reference,
        "perimeter": str(raw.get("perimeter") or "").strip(),
        "figures": clean,
        "guidance": raw.get("guidance") or "",
        "calendar": raw.get("calendar") or [],
    }


def _write(ticker: str, as_of: str, facts: dict, out: Path) -> None:
    snapshot = {
        "schema_version": 1,
        "ticker": ticker.upper(),
        "as_of": as_of,
        "items": [],
        "facts": facts,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(snapshot, ensure_ascii=False, indent=1), encoding="utf-8")
    size = out.stat().st_size
    print(f"fakten-snapshot: {out} ({size} B, {len(facts['figures'])} Kennzahlen, "
          f"Referenz {facts['reference_date']})", file=sys.stderr)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    build = sub.add_parser("build", help="facts via the deterministic issuer_facts vendor")
    build.add_argument("--ticker", required=True)
    build.add_argument("--date", default=date.today().isoformat())
    build.add_argument("--out", required=True)

    imp = sub.add_parser("import", help="normalise a researcher-authored facts JSON")
    imp.add_argument("--ticker", required=True)
    imp.add_argument("--date", default=date.today().isoformat())
    imp.add_argument("--in", dest="infile", required=True)
    imp.add_argument("--out", required=True)

    check = sub.add_parser("check", help="is an IR facts adapter configured for this ticker?")
    check.add_argument("--ticker", required=True)

    args = ap.parse_args(argv)

    if args.cmd == "check":
        issuer_facts = _repo_import()
        configured, reason = issuer_facts.is_configured(args.ticker)
        print(json.dumps({"ticker": args.ticker.upper(), "configured": configured, "reason": reason}))
        return 0 if configured else 3

    if args.cmd == "build":
        issuer_facts = _repo_import()
        try:
            collected = issuer_facts.collect_ir_facts(args.ticker)
        except Exception as exc:  # noqa: BLE001 — CLI reports, never tracebacks
            print(f"IR-Fakten nicht abrufbar: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 2
        if not collected.get("facts") and not collected.get("guidance"):
            print("keine Kennzahlen und keine Guidance gefunden", file=sys.stderr)
            return 2
        facts = _normalise_facts(collected, args.ticker)
    else:
        try:
            raw = json.loads(Path(args.infile).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"Fakten-JSON unlesbar: {exc}", file=sys.stderr)
            return 2
        facts = _normalise_facts(raw, args.ticker)

    if not facts["figures"] and not facts["guidance"]:
        print("Snapshot ohne Kennzahlen und ohne Guidance — nicht geschrieben", file=sys.stderr)
        return 2
    _write(args.ticker, args.date, facts, Path(args.out).expanduser())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
