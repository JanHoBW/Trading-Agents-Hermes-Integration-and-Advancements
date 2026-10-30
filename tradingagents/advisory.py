"""Deterministic position sizing and the pre-trade discipline gate.

Every figure here is arithmetic over inputs that already exist: the Trader's
structured proposal, the caller's book, the verified market levels and the
configured risk policy. No model estimates a number in this module, and an
input that is missing yields ``None`` plus the reason -- never a plausible
substitute.

Two states stay distinct on purpose: "no book was provided"
(``NOT_APPLICABLE``: the run never asked for sizing) and "a book was provided
but the number cannot be derived" (``BLOCKED``: e.g. no stop level, no equity).
Only the second is a defect worth surfacing.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

SCHEMA = "tradingagents.advisory.v1"
SIZING_FILENAME = "sizing.json"
DISCIPLINE_FILENAME = "discipline.json"

LONG_RATINGS = {"buy", "overweight"}
SHORT_RATINGS = {"sell", "underweight"}
HOLD_RATINGS = {"hold"}

#: Fallback policy. The per-account fields in the portfolio JSON win over the
#: config, which wins over these values.
DEFAULT_POLICY = {
    "risk_per_trade_pct": 1.0,
    "max_position_pct": 15.0,
    "min_reward_risk": 1.5,
    "stop_atr_multiple": 2.0,
    "min_stop_pct": 0.2,
    "atr_band": (0.5, 5.0),
    "max_adv_participation_pct": 1.0,
}

#: The policy knobs the run config supplies (flat keys, env-overridable).
POLICY_CONFIG_KEYS = (
    "risk_per_trade_pct",
    "max_position_pct",
    "min_reward_risk",
    "stop_atr_multiple",
    "min_stop_pct",
    "max_adv_participation_pct",
)


def direction_of(rating: str | None) -> str:
    """'long' / 'short' / 'none' / 'unknown' -- the side a rating implies."""
    r = (rating or "").strip().lower()
    if r in LONG_RATINGS:
        return "long"
    if r in SHORT_RATINGS:
        return "short"
    if r in HOLD_RATINGS:
        return "none"
    return "unknown"


def _num(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _positive(value) -> float | None:
    number = _num(value)
    return number if number is not None and number > 0 else None


def _floor(value: float) -> int:
    """Whole units, tolerant of float noise at an exact multiple."""
    return max(0, int(math.floor(value + 1e-9)))


def _held_units(account: Mapping[str, Any], ticker: str) -> float:
    """Signed units already held in ``ticker``; 0 when unknown."""
    symbol = (ticker or "").strip().upper()
    for position in account.get("positions") or []:
        if str(position.get("ticker", "")).strip().upper() == symbol:
            return _num(position.get("quantity")) or 0.0
    return 0.0


def market_levels(ticker: str, as_of_date: str, adv_window: int = 20) -> dict:
    """Latest verified close, ATR(14) and average daily traded value at the date.

    One cached OHLCV load, pinned to the analysis date (no look-ahead rows).
    Fail-open by design: sizing then falls back to the Trader's own levels, and
    a missing ATR is reported as missing rather than guessed.
    """
    try:
        from stockstats import wrap

        from tradingagents.dataflows.vendors.yahoo.ohlcv import load_ohlcv

        frame = load_ohlcv(ticker, as_of_date, fill_gaps=False)
        if frame is None or frame.empty:
            return {"available": False, "reason": "no price rows"}
        frame = frame.copy()
        close = _positive(frame["Close"].iloc[-1])
        stock = wrap(frame.copy())
        try:
            # stockstats computes a derived indicator on first access, so the
            # column does not exist until it is asked for.
            atr = _positive(stock["atr"].iloc[-1])
        except Exception:  # noqa: BLE001 -- too few rows for ATR(14)
            atr = None
        recent = frame.tail(max(1, int(adv_window)))
        traded = (recent["Close"] * recent["Volume"]).astype(float)
        adv_value = _positive(traded.mean())
        return {
            "available": True,
            "as_of": str(as_of_date),
            "last_close": close,
            "atr": atr,
            "adv_value": adv_value,
            "rows": int(len(frame)),
        }
    except Exception as exc:  # noqa: BLE001 -- a data miss must not fail the run
        return {"available": False, "reason": f"{type(exc).__name__}: {exc}"[:200]}


def build_advisory(
    *,
    ticker: str,
    as_of: str,
    rating: str | None,
    trader_action: str | None = None,
    proposal: Mapping[str, Any] | None = None,
    account: Mapping[str, Any] | None = None,
    levels: Mapping[str, Any] | None = None,
    policy: Mapping[str, Any] | None = None,
    target: float | None = None,
) -> dict:
    """The run's one arithmetic sizing plan plus the discipline checklist."""
    policy = {**DEFAULT_POLICY, **dict(policy or {})}
    proposal = dict(proposal or {})
    account = dict(account or {})
    levels = dict(levels or {})
    direction = direction_of(rating)
    checks: list[dict] = []
    missing: list[str] = []

    def check(name: str, passed: bool, detail: str, blocking: bool = False) -> None:
        checks.append({"name": name, "passed": bool(passed),
                       "blocking": bool(blocking), "detail": detail})

    def miss(name: str) -> None:
        missing.append(name)
        check(name, False, "not available", blocking=True)

    # --- the book ---------------------------------------------------------
    has_book = bool(account)
    equity = _positive(account.get("equity"))
    if not has_book:
        check("portfolio_context", False, "no portfolio was provided for this run")
    else:
        check("portfolio_context", True, "book provided")
        if equity is None:
            miss("equity")
        else:
            currency = str(account.get("currency") or "").strip()
            check("equity", True, f"{equity:,.2f}{' ' + currency if currency else ''}")

    # --- price levels -----------------------------------------------------
    entry = _positive(proposal.get("entry_price"))
    entry_source = "trader"
    if entry is None:
        entry = _positive(levels.get("last_close"))
        entry_source = "last_close" if entry is not None else "missing"
    if entry is None:
        miss("entry_price")
    else:
        check("entry_price", True, f"{entry:,.4f} ({entry_source})")

    stop = _positive(proposal.get("stop_loss"))
    stop_source = "trader"
    atr = _positive(levels.get("atr"))
    if stop is None and atr is not None and entry is not None and direction in {"long", "short"}:
        offset = float(policy["stop_atr_multiple"]) * atr
        stop = entry - offset if direction == "long" else entry + offset
        stop_source = "atr_derived"
    if stop is None:
        miss("stop_loss")
    else:
        check("stop_loss", True, f"{stop:,.4f} ({stop_source})")
        if stop_source == "atr_derived":
            check("stop_derived", True,
                  f"the proposal had no stop; {policy['stop_atr_multiple']}x ATR "
                  f"({atr:,.4f}) applied", blocking=False)

    # --- side and distance ------------------------------------------------
    risk_per_share = stop_pct = atr_multiple = None
    side_ok = True
    if entry is not None and stop is not None:
        risk_per_share = abs(entry - stop)
        stop_pct = risk_per_share / entry * 100
        if direction in {"long", "short"}:
            side_ok = stop < entry if direction == "long" else stop > entry
            check("levels_match_direction", side_ok,
                  f"{direction}: entry {entry:,.4f}, stop {stop:,.4f}" if side_ok else
                  f"{direction}: stop {stop:,.4f} sits on the wrong side of entry {entry:,.4f}",
                  blocking=True)
        if risk_per_share > 0:
            check("stop_distance", stop_pct >= float(policy["min_stop_pct"]),
                  f"{stop_pct:.2f} % of entry (minimum {policy['min_stop_pct']} %)",
                  blocking=True)
            if atr is not None:
                atr_multiple = risk_per_share / atr
                low, high = policy["atr_band"]
                check("atr_band", low <= atr_multiple <= high,
                      f"{atr_multiple:.2f}x ATR {atr:,.4f} (band {low}-{high}x)",
                      blocking=False)

    # --- sizing -----------------------------------------------------------
    cash = _positive(account.get("cash"))
    risk_pct = _positive(account.get("risk_per_trade_pct")) or float(policy["risk_per_trade_pct"])
    max_pos_pct = _positive(account.get("max_position_pct")) or float(policy["max_position_pct"])
    caps: dict[str, int | None] = {}
    position: dict[str, Any] = {}
    if equity and entry and risk_per_share and side_ok and direction in {"long", "short"}:
        budget = equity * risk_pct / 100
        caps["by_risk"] = _floor(budget / risk_per_share)
        total_units = _floor(equity * max_pos_pct / 100 / entry)
        held = _held_units(account, ticker)
        held_same_side = max(0.0, held) if direction == "long" else max(0.0, -held)
        caps["by_position"] = max(0, total_units - _floor(held_same_side))
        caps["by_cash"] = _floor(cash / entry) if (cash is not None and direction == "long") else None
        binding = {name: cap for name, cap in caps.items() if cap is not None}
        shares = min(binding.values()) if binding else 0
        position = {
            "shares": shares,
            "notional": round(shares * entry, 2),
            "risk": round(shares * risk_per_share, 2),
            "position_pct": round(shares * entry / equity * 100, 2),
            "risk_pct": round(shares * risk_per_share / equity * 100, 2),
            "binding_constraint": min(binding.items(), key=lambda kv: kv[1])[0] if binding else None,
            "currency": account.get("currency"),
            "executable": False,
        }

    # --- target / reward-risk --------------------------------------------
    target = _positive(target)
    reward_risk = None
    if entry is not None and target is not None and risk_per_share:
        if direction == "long" and target > entry:
            reward_risk = (target - entry) / risk_per_share
        elif direction == "short" and target < entry:
            reward_risk = (entry - target) / risk_per_share
        if reward_risk is not None:
            check("reward_risk", reward_risk >= float(policy["min_reward_risk"]),
                  f"{reward_risk:.2f} (minimum {policy['min_reward_risk']})", blocking=False)

    # --- liquidity --------------------------------------------------------
    adv_value = _positive(levels.get("adv_value"))
    if adv_value and position.get("notional"):
        share_of_adv = position["notional"] / adv_value * 100
        check("liquidity", share_of_adv <= float(policy["max_adv_participation_pct"]),
              f"{share_of_adv:.2f} % of the 20-day average traded value "
              f"(limit {policy['max_adv_participation_pct']} %)", blocking=False)

    failed = [c for c in checks if c["blocking"] and not c["passed"]]
    warned = [c for c in checks if not c["blocking"] and not c["passed"]]
    if not has_book:
        status, verdict = "not_assessable", "NOT_APPLICABLE"
    elif failed:
        status, verdict = "fail", "BLOCKED"
    else:
        status = "warn" if warned else "pass"
        verdict = "NO_TRADE" if direction == "none" or not position.get("shares") else "EXECUTE"
    if position:
        position["executable"] = verdict == "EXECUTE"

    return _plan(ticker, as_of, rating, direction, trader_action, account, levels,
                 {"risk_per_trade_pct": risk_pct, "max_position_pct": max_pos_pct,
                  "min_reward_risk": policy["min_reward_risk"],
                  "stop_atr_multiple": policy["stop_atr_multiple"],
                  "min_stop_pct": policy["min_stop_pct"]},
                 {"entry": entry, "entry_source": entry_source, "stop": stop,
                  "stop_source": stop_source,
                  "stop_pct": round(stop_pct, 2) if stop_pct is not None else None,
                  "atr": atr, "atr_multiple": round(atr_multiple, 2) if atr_multiple else None,
                  "last_close": _positive(levels.get("last_close")),
                  "target": target,
                  "reward_risk": round(reward_risk, 2) if reward_risk else None,
                  "adv_value": adv_value},
                 position, caps, checks, missing, verdict=verdict, status=status)


def _plan(ticker, as_of, rating, direction, trader_action, account, levels,
          policy, plan_levels, position, caps, checks, missing, *, verdict, status) -> dict:
    """Assemble the serialisable advisory record."""
    return {
        "schema": SCHEMA,
        "ticker": ticker,
        "as_of": str(as_of),
        "rating": rating,
        "trader_action": trader_action,
        "direction": direction,
        "verdict": verdict,
        "status": status,
        "account": {key: account.get(key) for key in
                    ("equity", "cash", "currency", "risk_per_trade_pct", "max_position_pct")
                    if account.get(key) is not None},
        "policy": policy,
        "levels": plan_levels,
        "position": position,
        "caps": caps,
        "checks": checks,
        "missing": missing,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def _fmt(value, digits: int = 2, suffix: str = "") -> str:
    number = _num(value)
    return "not available" if number is None else f"{number:,.{digits}f}{suffix}"


def render_markdown(plan: Mapping[str, Any]) -> str:
    """The report section: the plan, then every check with its result."""
    lines = [
        "Deterministic sizing of the decision against the caller's book. Every figure "
        "below is computed in code from the Trader's structured proposal, the verified "
        "market levels and the configured risk policy; no model estimated it.",
        "",
        f"**Verdict**: {plan['verdict']}",
        "",
        "| Field | Value |",
        "|---|---:|",
        f"| Rating / direction | {plan.get('rating') or 'not available'} / {plan['direction']} |",
        f"| Entry | {_fmt((plan.get('levels') or {}).get('entry'), 4)} "
        f"({(plan.get('levels') or {}).get('entry_source', 'not available')}) |",
        f"| Stop | {_fmt((plan.get('levels') or {}).get('stop'), 4)} "
        f"({(plan.get('levels') or {}).get('stop_source', 'not available')}) |",
        f"| Stop distance | {_fmt((plan.get('levels') or {}).get('stop_pct'), 2, ' %')} |",
        f"| ATR / multiple | {_fmt((plan.get('levels') or {}).get('atr'), 4)} / "
        f"{_fmt((plan.get('levels') or {}).get('atr_multiple'), 2, 'x')} |",
        f"| Target / reward-risk | {_fmt((plan.get('levels') or {}).get('target'), 4)} / "
        f"{_fmt((plan.get('levels') or {}).get('reward_risk'), 2)} |",
        f"| Equity | {_fmt((plan.get('account') or {}).get('equity'), 2)} |",
        "| Position | "
        + (f"{plan['position'].get('shares')} units, "
           f"{_fmt(plan['position'].get('notional'), 2)} "
           f"({_fmt(plan['position'].get('position_pct'), 2, ' % of equity')}), "
           f"risk {_fmt(plan['position'].get('risk'), 2)} on the stop"
           if plan.get('position') else "not available") + " |",
        f"| Binding constraint | {(plan.get('position') or {}).get('binding_constraint') or 'not available'} |",
    ]
    missing = plan.get("missing") or []
    if missing:
        lines += ["", f"**Not available**: {', '.join(missing)}"]
    if plan.get("checks"):
        lines += ["", "| Check | Result | Detail |", "|---|---|---|"]
        for item in plan["checks"]:
            result = "pass" if item["passed"] else ("FAIL" if item["blocking"] else "warning")
            lines.append(f"| {item['name']} | {result} | {item['detail']} |")
    return "\n".join(lines)


def render_context(plan: Mapping[str, Any]) -> str:
    """The compact block injected into the risk and Portfolio-Manager prompts."""
    if plan.get("verdict") == "NOT_APPLICABLE":
        return ("Deterministic sizing: not available (no portfolio book was provided). "
                "Do not state a share count; give sizing as a percentage the caller can "
                "apply to their own book.")
    if plan.get("verdict") == "BLOCKED":
        reasons = "; ".join(c["name"] for c in plan["checks"] if c["blocking"] and not c["passed"])
        return (f"Deterministic sizing is blocked ({reasons or 'missing inputs'}). "
                "Do not invent a share count; say that a size cannot be derived and why.")
    if plan.get("verdict") == "NO_TRADE":
        if plan.get("direction") == "none":
            return ("Deterministic sizing: no trade — the rating is Hold, so there is no "
                    "position to size. Do not propose a share count.")
        if plan.get("direction") == "unknown":
            return ("Deterministic sizing: no executable size — the direction could not be "
                    "established (no structured Trader proposal). Do not propose a share count.")
        position = plan.get("position") or {}
        return (f"Deterministic sizing: no executable size (the "
                f"{position.get('binding_constraint') or 'position caps'} limited it to 0 units). "
                "Do not propose a share count; say why no size is available.")
    levels = plan.get("levels") or {}
    position = plan.get("position") or {}
    account = plan.get("account") or {}
    risk_pct = account.get("risk_per_trade_pct") or (plan.get("policy") or {}).get("risk_per_trade_pct")
    return "\n".join([
        "Deterministic sizing of the Trader's proposal (computed in code, not estimated):",
        f"- Direction: {plan['direction']}; risk budget {_fmt(risk_pct)} % "
        f"of equity {_fmt(account.get('equity'))} {(account.get('currency') or '')}".rstrip(),
        f"- Entry {_fmt(levels.get('entry'), 4)} ({levels.get('entry_source')}), "
        f"stop {_fmt(levels.get('stop'), 4)} ({levels.get('stop_source')}) = "
        f"{_fmt(levels.get('stop_pct'), 2, ' %')} = "
        f"{_fmt(levels.get('atr_multiple'), 2, 'x')} ATR",
        f"- Position: {position.get('shares')} units, notional "
        f"{_fmt(position.get('notional'))} ({_fmt(position.get('position_pct'), 2, ' % of equity')}), "
        f"risk {_fmt(position.get('risk'))} at the stop",
        f"- Verdict: {plan['verdict']}; binding constraint: "
        f"{position.get('binding_constraint') or 'none'}",
        "Use these figures verbatim for sizing. If you disagree with a level, say which "
        "and why -- do not silently substitute another share count.",
    ])


def policy_from_config(config: Mapping[str, Any] | None) -> dict:
    """The run config's flat sizing keys, ignoring absent ones."""
    config = config or {}
    return {key: config[key] for key in POLICY_CONFIG_KEYS if config.get(key) is not None}


def build_from_state(state: Mapping[str, Any], *, rating: str | None = None,
                     config: Mapping[str, Any] | None = None) -> dict:
    """Compute the plan from what a run carries; never touches the network."""
    proposal = dict(state.get("trader_proposal") or {})
    return build_advisory(
        ticker=str(state.get("company_of_interest") or ""),
        as_of=str(state.get("trade_date") or ""),
        rating=rating or proposal.get("action") or state.get("final_rating"),
        trader_action=proposal.get("action"),
        proposal=proposal,
        account=state.get("portfolio_account") or {},
        levels=state.get("market_levels") or {},
        policy=policy_from_config(config),
        target=state.get("final_price_target"),
    )


def write_artifacts(report_dir, plan: Mapping[str, Any]) -> None:
    """Write ``sizing.json`` and ``discipline.json`` next to the report."""
    report_dir = Path(report_dir)
    discipline = {
        "schema": "tradingagents.discipline.v1",
        "ticker": plan.get("ticker"),
        "as_of": plan.get("as_of"),
        "rating": plan.get("rating"),
        "status": plan.get("status"),
        "verdict": plan.get("verdict"),
        "checks": plan.get("checks") or [],
        "missing": plan.get("missing") or [],
        "generated_at": plan.get("generated_at"),
    }
    try:
        (report_dir / SIZING_FILENAME).write_text(
            json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
        (report_dir / DISCIPLINE_FILENAME).write_text(
            json.dumps(discipline, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError:
        # A read-only results dir must not turn a finished run into a crash.
        pass
