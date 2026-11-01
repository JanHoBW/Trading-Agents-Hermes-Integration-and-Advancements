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
    if isinstance(raw.get("facts"), dict) or any(key in raw for key in ("release_facts", "release_fact_evidence")):
        # vendor output: {"facts": {label: value}, "reference_date": ...}
        evidence = {entry["label"]: entry for entry in raw.get("release_fact_evidence") or []}
        figures = [
            dict(evidence.get(label) or {"label": label, "value": value,
             "source_url": raw.get("source") or raw.get("overview_url") or "",
             "reference_date": raw.get("reference_date") or ""})
            for label, value in (raw.get("facts") or {}).items()
        ]
        for label, value in (raw.get("release_facts") or {}).items():
            if label in (raw.get("facts") or {}):
                continue
            figures.append(dict(evidence.get(label) or {
                "label": label, "value": value,
                "source_url": raw.get("release_facts_source") or "",
                "reference_date": raw.get("release_facts_date") or "",
                "publication_date": raw.get("release_facts_date") or "",
                "perimeter_note": "not stated; see release",
            }))
        figures.extend(dict(entry) for label, entry in evidence.items()
                       if label not in (raw.get("release_facts") or {}) and label not in (raw.get("facts") or {}))
        # Collector display prose may be longer than schema-1 ordinary text.
        # Keep the independent raw quotation untouched; never coerce numeric fields.
        for figure in figures:
            for key in ("label", "value", "perimeter_note"):
                if isinstance(figure.get(key), str):
                    figure[key] = figure[key][:200]
        guidance = raw.get("guidance") or ""
        if isinstance(guidance, dict):
            guidance = {key: value[:200] if isinstance(value, str) and key != "guidance_raw_sentence" else value
                        for key, value in guidance.items()}
        return {
            "issuer": raw.get("issuer") or ticker,
            "reference_date": raw.get("reference_date") or "",
            **({"publication_date": raw["publication_date"]} if "publication_date" in raw else {}),
            "perimeter": raw.get("perimeter_note") or raw.get("perimeter") or "",
            "figures": figures[:MAX_FACTS],
            "guidance": guidance,
            "guidance_date": raw.get("guidance_date") or "",
            "release_facts_date": raw.get("release_facts_date") or "",
            "guidance_source": raw.get("guidance_source") or "",
            "guidance_perimeter": raw.get("guidance_perimeter") or "not stated; see release",
            "calendar": raw.get("calendar") or [],
        }
    # Imported typed evidence must survive the same validator as vendor data.
    if "figures" not in raw and raw.get("guidance"):
        raw = {**raw, "figures": []}
    if not isinstance(raw.get("figures"), list):
        raise SystemExit("facts JSON needs either 'facts' (label->value) or 'figures' (list)")
    return {**raw, "issuer": raw.get("issuer") or ticker,
            "reference_date": raw.get("reference_date") or "",
            "perimeter": raw.get("perimeter") or "",
            "figures": raw["figures"][:MAX_FACTS]}


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
            collected = issuer_facts.collect_ir_facts(args.ticker, as_of=args.date)
        except Exception as exc:  # noqa: BLE001 — CLI reports, never tracebacks
            print(f"IR-Fakten nicht abrufbar: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 2
        if not any(collected.get(key) for key in ("facts", "guidance", "release_facts", "release_fact_evidence")):
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

    try:
        from tradingagents.dataflows.vendors.news_supplement import _validate_facts
        facts = _validate_facts(facts, as_of=args.date)
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        print(f"IR-Fakten ungueltig: {exc}", file=sys.stderr)
        return 2
    if not facts["figures"] and not facts["guidance"]:
        print("Snapshot ohne Kennzahlen und ohne Guidance — nicht geschrieben", file=sys.stderr)
        return 2
    _write(args.ticker, args.date, facts, Path(args.out).expanduser())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
