"""Deterministic fundamental arithmetic on explicitly dated, unit-bearing facts.

No network, model calls, cause inference, or fiscal-year guessing. Values in the
JSON block retain source units; the surrounding statement CSV may be scaled.
"""

from __future__ import annotations

import json
import math
from datetime import date, timedelta


def validated_records(records: list[dict]) -> list[dict]:
    """Reject malformed numbers/dates and remove arbitrary metadata instructions."""
    fields = {
        "metric",
        "value",
        "start",
        "end",
        "unit",
        "filed",
        "accession",
        "form",
        "source",
        "tag",
        "perimeter",
        "basis",
        "tax_basis",
        "capex_sign",
        "filing_fiscal_year",
        "filing_fiscal_period",
        "duration_days",
        "period_type",
    }
    valid = []
    for record in records:
        if not isinstance(record, dict):
            continue
        value = record.get("value")
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            continue
        if not all(
            isinstance(record.get(k), str) and 0 < len(record[k]) <= 160
            for k in ("metric", "unit", "end")
        ):
            continue
        try:
            for key in ("start", "end", "filed"):
                if record.get(key) is not None:
                    date.fromisoformat(record[key])
            if record.get("start") and record["start"] > record["end"]:
                continue
        except (ValueError, TypeError):
            continue
        clean = {
            k: v
            for k, v in record.items()
            if k in fields
            and (v is None or isinstance(v, (str, int, float)) and not isinstance(v, bool))
        }
        if any(isinstance(v, str) and len(v) > 160 for v in clean.values()):
            continue
        valid.append(clean)
    # Preserve each period's latest known vintage, deduping repeated tool calls.
    chosen = {}
    conflicts = set()
    for r in valid:
        key = (r["metric"], r.get("start"), r["end"], r["unit"])
        if key not in chosen or (r.get("filed") or "") > (chosen[key].get("filed") or ""):
            chosen[key] = r
            conflicts.discard(key)
        elif r.get("filed") == chosen[key].get("filed") and any(
            r.get(k) != chosen[key].get(k)
            for k in ("value", "perimeter", "basis", "tax_basis", "source")
        ):
            conflicts.add(key)
    return [r for key, r in chosen.items() if key not in conflicts]


def comparable_quarters(records: list[dict]) -> list[dict]:
    """Discrete quarters, or auditable cumulative differences; never sum YTDs."""
    result = []
    for record in records:
        if not record.get("start"):
            continue
        days = (date.fromisoformat(record["end"]) - date.fromisoformat(record["start"])).days
        if 60 <= days <= 115:
            result.append({**record, "period_type": "quarter", "derivation": "reported"})
            continue
        if not 150 <= days <= 400:
            continue
        if days >= 300 and not str(record.get("form", "")).startswith(("10-K", "20-F", "40-F")):
            continue  # a rolling 12-month 10-Q total is not a fiscal year
        previous = [
            r
            for r in records
            if r["metric"] == record["metric"]
            and r.get("start") == record["start"]
            and r["end"] < record["end"]
            and r.get("unit") == record.get("unit")
            and r.get("tag") == record.get("tag")
            and all(r.get(k) == record.get(k) for k in ("perimeter", "basis", "tax_basis"))
        ]
        if not previous:
            continue
        prior = max(previous, key=lambda r: r["end"])
        same_vintage = (
            record.get("accession") and record.get("accession") == prior.get("accession")
        ) or record.get("filed") == prior.get("filed")
        if not same_vintage and (
            "/A" in str(record.get("form"))
            or "/A" in str(prior.get("form"))
            or prior.get("filed", "") > record.get("filed", "")
        ):
            continue
        start = (date.fromisoformat(prior["end"]) + timedelta(days=1)).isoformat()
        duration = (date.fromisoformat(record["end"]) - date.fromisoformat(start)).days
        if 60 <= duration <= 115:
            result.append(
                {
                    **record,
                    "start": start,
                    "duration_days": duration,
                    "value": record["value"] - prior["value"],
                    "period_type": "quarter",
                    "derivation": "cumulative_difference",
                    "inputs": [prior, record],
                }
            )
    # A reported discrete value wins over a derived value for the same period.
    chosen = {}
    for r in sorted(result, key=lambda r: r["derivation"] == "reported"):
        chosen[(r["metric"], r["start"], r["end"], r["unit"])] = r
    return sorted(chosen.values(), key=lambda r: (r["end"], r["metric"]))


def trailing_four_quarters(records: list[dict]) -> dict:
    """Sum only a metric's latest four non-overlapping contiguous quarters."""
    metrics = {}
    for metric in sorted({r["metric"] for r in records}):
        if "EPS" in metric:
            continue  # quarterly per-share denominators need not be identical
        rows = sorted([r for r in records if r["metric"] == metric], key=lambda r: r["end"])[-4:]
        if len(rows) != 4 or len({r["unit"] for r in rows}) != 1:
            continue
        if any(len({r.get(k) for r in rows}) != 1 for k in ("perimeter", "basis", "tax_basis")):
            continue
        if any(
            date.fromisoformat(b["start"]) != date.fromisoformat(a["end"]) + timedelta(days=1)
            for a, b in zip(rows, rows[1:], strict=False)
        ):
            continue
        if (
            not 300
            <= (date.fromisoformat(rows[-1]["end"]) - date.fromisoformat(rows[0]["start"])).days
            <= 400
        ):
            continue
        metrics[metric] = {
            "value": sum(r["value"] for r in rows),
            "unit": rows[0]["unit"],
            "start": rows[0]["start"],
            "end": rows[-1]["end"],
        }
    if not metrics:
        return {"status": "unavailable", "reason": "four consecutive quarters required"}
    latest = max(m["end"] for m in metrics.values())
    metrics = {k: v for k, v in metrics.items() if v["end"] == latest}
    starts = {m["start"] for m in metrics.values()}
    return {
        "status": "ok",
        "start": next(iter(starts)) if len(starts) == 1 else None,
        "end": latest,
        "metrics": metrics,
    }


def analyze_fundamentals(
    records: list[dict], as_of: str, *, frequency="quarterly", asset_type="equity"
) -> dict:
    """A bounded, source-unit CFO/capex/FCF block consumed by vendor tool output."""
    if asset_type.lower() == "etf":
        return {
            "schema_version": 1,
            "as_of": as_of,
            "status": "not_applicable",
            "periods": [],
            "ttm": {"status": "not_applicable"},
            "causes": "not inferred",
        }
    records = validated_records(
        [
            r
            for r in records
            if isinstance(r, dict)
            and isinstance(r.get("filed"), str)
            and r["filed"] <= as_of
            and isinstance(r.get("end"), str)
            and r["end"] <= as_of
            and isinstance(r.get("unit"), str)
            and r["unit"].upper() not in {"UNKNOWN", "NOT_SPECIFIED"}
        ]
    )
    selected = comparable_quarters(records) if frequency == "quarterly" else records
    periods = []
    for start, end, unit in sorted(
        {(r["start"], r["end"], r["unit"]) for r in selected if r.get("start")}
    ):
        rows = {
            r["metric"]: r
            for r in selected
            if (r["start"], r["end"], r["unit"]) == (start, end, unit)
        }
        cfo_row, capex_row, denominator = (
            rows.get(k, {}) for k in ("Operating Cash Flow", "Capital Expenditure", "Net Income")
        )
        cfo, capex = cfo_row.get("value"), capex_row.get("value")
        if cfo is None and capex is None:
            continue
        fcf_status = "unavailable: matching CFO and capex required"
        fcf = None
        if cfo is not None and capex is not None:
            if (
                not cfo_row.get("perimeter")
                or cfo_row.get("perimeter") in {"unknown", "not_specified"}
                or cfo_row.get("perimeter") != capex_row.get("perimeter")
            ):
                fcf_status = "unreconciled: reporting perimeter"
            elif capex_row.get("capex_sign") != "outflow_positive" or capex < 0:
                fcf_status = "unavailable: capex outflow convention required"
            else:
                fcf, fcf_status = cfo - capex, "ok"
        conversion_status = "unavailable: matching positive Net Income denominator required"
        cfo_conversion = fcf_conversion = None
        if cfo is not None and denominator.get("value", 0) > 0:
            if any(
                not cfo_row.get(k)
                or cfo_row.get(k) in {"unknown", "not_specified"}
                or cfo_row.get(k) != denominator.get(k)
                for k in ("perimeter", "tax_basis", "basis")
            ):
                conversion_status = "unreconciled: tax basis, reporting basis or perimeter"
            else:
                conversion_status = "ok"
                cfo_conversion = cfo / denominator["value"]
                fcf_conversion = fcf / denominator["value"] if fcf is not None else None
        periods.append(
            {
                "start": start,
                "end": end,
                "unit": unit,
                "cfo": cfo,
                "capex": capex,
                "fcf": fcf,
                "fcf_status": fcf_status,
                "cfo_conversion": cfo_conversion,
                "fcf_conversion": fcf_conversion,
                "conversion_denominator": "Net Income",
                "conversion_status": conversion_status,
                "provenance": rows,
            }
        )
    ttm = (
        trailing_four_quarters(selected)
        if frequency == "quarterly"
        else {"status": "unavailable", "reason": "annual periods are not quarters"}
    )
    return {
        "schema_version": 1,
        "as_of": as_of,
        "status": "ok" if periods or ttm["status"] == "ok" else "unavailable",
        "limitations": "unreconciled"
        if frequency == "quarterly"
        and any(
            r.get("start")
            and (date.fromisoformat(r["end"]) - date.fromisoformat(r["start"])).days > 115
            and not any(s["metric"] == r["metric"] and s["end"] == r["end"] for s in selected)
            for r in records
        )
        else "none",
        "periods": periods[-4:],
        "ttm": ttm,
        "causes": "not inferred",
    }


def records_from_messages(messages: list, *, as_of: str | None = None) -> list[dict]:
    """Read only structured period payloads from statement tool responses."""
    records = []
    for message in messages:
        if getattr(message, "type", None) != "tool" or getattr(message, "name", None) not in {
            "get_cashflow",
            "get_income_statement",
            "get_balance_sheet",
        }:
            continue
        text = getattr(message, "content", "")
        if not isinstance(text, str):
            continue
        for line in text.splitlines():
            if line.startswith("# PERIOD_FACTS: "):
                try:
                    payload = json.loads(line[len("# PERIOD_FACTS: ") :])
                except ValueError:
                    continue
                if isinstance(payload, list):
                    records.extend(r for r in payload if isinstance(r, dict))
    if as_of is not None:
        records = [r for r in records if isinstance(r.get("filed"), str) and r["filed"] <= as_of]
    return validated_records(records)


def render_analytics(
    records: list[dict], as_of: str, *, frequency="quarterly", asset_type="equity"
) -> str:
    """CSV-safe comments carrying raw period metadata and deterministic output."""
    records = validated_records(records)
    ends = sorted({r["end"] for r in records})[-8:]
    records = [r for r in records if r["end"] in ends]
    return (
        "# PERIOD_FACTS: "
        + json.dumps(records, sort_keys=True, allow_nan=False)
        + "\n"
        + "# FUNDAMENTAL_ANALYTICS: "
        + json.dumps(
            analyze_fundamentals(records, as_of, frequency=frequency, asset_type=asset_type),
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    )
