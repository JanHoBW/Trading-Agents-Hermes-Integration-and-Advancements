"""Per-run record of what the data layer actually delivered, and what it skipped.

The routing layer is the only place that knows which vendor served a call and
which vendors had to be passed over. This module keeps that knowledge for the
duration of one run so it can be shown where it matters: in the Portfolio
Manager's prompt (so a decision cannot silently rest on missing inputs) and in
the saved report (so a reader sees the gaps next to the conclusion).

Design constraints:

* One run per process. ``reset()`` is called at the start of a run and the
  registry is process-wide; the analysts fan out across threads, which is why
  the store is lock-protected rather than thread-local.
* Nothing is inferred. A method appears here because ``route_to_vendor``
  handled it. A method that was never called is reported as "not requested",
  never as a gap — an agent that did not ask for insider data is not a data
  failure.
* Degradation is recorded, not judged. The registry does not decide whether a
  gap is tolerable; it states the vendor, the state and the vendor's own reason.
"""

from __future__ import annotations

import threading

# Routing outcomes a record can carry. "ok" is the only non-skip state.
OK = "ok"
NOT_CONFIGURED = "not_configured"
UNAVAILABLE = "unavailable"
FAILED = "failed"
NO_DATA = "no_data"

_SKIP_STATES = (NOT_CONFIGURED, UNAVAILABLE, FAILED, NO_DATA)

# Inputs a complete analysis of each asset type would normally draw on. Used
# only to separate "the run never asked" from "the run asked and was refused" —
# the second is a gap, the first is a scope statement.
EXPECTED_METHODS = {
    "stock": (
        "get_stock_data",
        "get_indicators",
        "get_fundamentals",
        "get_balance_sheet",
        "get_cashflow",
        "get_income_statement",
        "get_news",
        "get_global_news",
        "get_insider_transactions",
        "get_macro_indicators",
    ),
    "crypto": (
        "get_stock_data",
        "get_indicators",
        "get_news",
        "get_global_news",
        "get_macro_indicators",
    ),
}

_lock = threading.RLock()
_methods: dict[str, dict] = {}
_calls = 0


def reset() -> None:
    """Forget every record; called once at the start of a run."""
    global _calls
    with _lock:
        _methods.clear()
        _calls = 0


def record(method: str, vendor: str, state: str = OK, reason: str = "") -> None:
    """Record that ``vendor`` answered ``method`` with ``state``.

    Repeated identical skips collapse (a vendor tried on every bar of a window
    is one gap, not fifty); successful calls only increment the call counter.
    """
    global _calls
    with _lock:
        _calls += 1
        rec = _methods.setdefault(method, {"served_by": [], "skips": []})
        if state == OK:
            if vendor not in rec["served_by"]:
                rec["served_by"].append(vendor)
            return
        if state not in _SKIP_STATES:  # defensive: never invent a state
            state = FAILED
        entry = {"vendor": vendor, "state": state, "reason": (reason or "").strip()}
        if entry not in rec["skips"]:
            rec["skips"].append(entry)


def snapshot() -> dict:
    """The run's coverage as plain data, safe to put in the graph state/JSON."""
    with _lock:
        methods = {
            method: {
                "status": _status(rec),
                "served_by": list(rec["served_by"]),
                "skips": [dict(s) for s in rec["skips"]],
            }
            for method, rec in sorted(_methods.items())
        }
        calls = _calls
    gaps = [
        {"method": m, **skip}
        for m, rec in methods.items()
        for skip in rec["skips"]
    ]
    return {
        "methods": methods,
        "gaps": gaps,
        "totals": {
            "calls": calls,
            "methods": len(methods),
            "degraded": sum(1 for m in methods.values() if m["status"] != OK),
            "gaps": len(gaps),
        },
    }


def _status(rec: dict) -> str:
    if rec["served_by"] and rec["skips"]:
        return "degraded"   # another vendor covered the call
    if rec["served_by"]:
        return OK
    if rec["skips"]:
        return "unavailable"  # nothing served the call at all
    return OK


def _expected(asset_type: str | None) -> tuple[str, ...]:
    return EXPECTED_METHODS.get((asset_type or "stock").lower(), EXPECTED_METHODS["stock"])


def render_context(snap: dict, asset_type: str | None = None, max_lines: int = 14) -> str:
    """A compact block for the Portfolio Manager; empty when nothing was routed.

    Deliberately imperative about the consequence: an unavailable input is not a
    licence to assume a value, it is a reason to say so and size the call down.
    """
    methods = (snap or {}).get("methods") or {}
    if not methods:
        return ""
    expected = _expected(asset_type)
    delivered = [m for m in expected if m in methods and methods[m]["status"] == OK]
    degraded = [m for m in expected if m in methods and methods[m]["status"] != OK]
    gaps = (snap or {}).get("gaps") or []

    lines = [
        "DATA COVERAGE — which inputs this run actually received.",
        f"- Expected inputs delivered cleanly: {len(delivered)}/{len(expected)}",
    ]
    if degraded:
        lines.append("- Degraded or unavailable: " + ", ".join(degraded))
    missing = [m for m in expected if m not in methods]
    if missing:
        lines.append("- Not requested by any analyst this run: " + ", ".join(missing))
    for gap in gaps[:max_lines]:
        reason = f": {gap['reason']}" if gap.get("reason") else ""
        lines.append(f"- GAP {gap['method']} via {gap['vendor']} ({gap['state']}){reason}")
    if len(gaps) > max_lines:
        lines.append(f"- ... and {len(gaps) - max_lines} further vendor skip(s)")
    lines.append(
        "Rule: an input listed as degraded, unavailable or missing was NOT seen. "
        "Do not assume its value, do not describe it as absent in the world, and "
        "state the gap where it weakens the call. Size conviction to what you "
        "actually received."
    )
    return "\n".join(lines)


def render_markdown(
    snap: dict,
    asset_type: str | None = None,
    window_lines: list[str] | None = None,
) -> str:
    """The report section: input windows, a per-method table, the concrete gaps."""
    methods = (snap or {}).get("methods") or {}
    if not methods:
        return ""
    expected = _expected(asset_type)
    rows = [
        "| Input | Status | Served by | Skipped |",
        "| --- | --- | --- | --- |",
    ]
    for method in sorted(methods):
        rec = methods[method]
        skipped = ", ".join(
            f"{s['vendor']} ({s['state']})" for s in rec["skips"]
        ) or "—"
        served = ", ".join(rec["served_by"]) or "—"
        rows.append(f"| `{method}` | {rec['status']} | {served} | {skipped} |")
    not_requested = [m for m in expected if m not in methods]

    out = [
        "Which inputs this run requested, who served them, and which vendors were "
        "skipped. Derived from the routing layer's own records — a method that is "
        "absent was not requested by an analyst this run, and is not reported as "
        "a data failure.",
        "",
    ]
    if window_lines:
        out += ["**Analysis windows**", ""] + [f"- {line}" for line in window_lines] + [""]
    out += ["\n".join(rows), ""]
    gaps = (snap or {}).get("gaps") or []
    if gaps:
        out.append("**Gaps**")
        out.append("")
        for gap in gaps:
            reason = f" — {gap['reason']}" if gap.get("reason") else ""
            out.append(f"- `{gap['method']}`: {gap['vendor']} {gap['state']}{reason}")
        out.append("")
    if not_requested:
        out.append("**Not requested this run:** " + ", ".join(f"`{m}`" for m in not_requested))
        out.append("")
    return "\n".join(out)
