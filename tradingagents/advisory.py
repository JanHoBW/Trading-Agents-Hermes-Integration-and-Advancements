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
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from tradingagents import fx

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


def _book_currencies(account: Mapping[str, Any] | None,
                     instruments: Mapping[str, Mapping[str, Any]] | None) -> list[str]:
    """Currencies a book's instruments are quoted in, for the FX cache lookup."""
    out: list[str] = []
    for position in (account or {}).get("positions") or []:
        meta = (instruments or {}).get(str(position.get("ticker") or "").strip().upper()) or {}
        currency = str(meta.get("underlying_currency") or "").strip().upper()
        if currency and currency not in out:
            out.append(currency)
    return out


def _days_between(start: Any, end: Any) -> int | None:
    """Calendar days between two ISO dates; ``None`` when either is unreadable."""
    try:
        return max(0, (date.fromisoformat(str(end)[:10]) - date.fromisoformat(str(start)[:10])).days)
    except (TypeError, ValueError):
        return None


def book_exposure(
    account: Mapping[str, Any] | None,
    instruments: Mapping[str, Mapping[str, Any]] | None,
    account_currency: str | None = None,
    fx_rates: Mapping[str, Mapping] | None = None,
    as_of: str | None = None,
) -> dict:
    """Exposure of every booked position, per instrument and per underlying.

    A knock-out is exposure to its underlying through the contractual ratio; an
    ETF or an ETC is its market value (times the leverage, when it resets daily).
    When the ratio or the price is not on record the row is marked ``unknown``
    with the reason -- the gate may not treat it as one share per unit.
    """
    account = dict(account or {})
    instruments = dict(instruments or {})
    account_currency = (account_currency or account.get("currency") or "").upper() or None
    rows: list[dict] = []
    for position in account.get("positions") or []:
        key = str(position.get("ticker") or "").strip().upper()
        if not key:
            continue
        meta = dict(instruments.get(key) or {})
        kind = str(meta.get("kind") or "unknown")
        units = _num(position.get("quantity")) or 0.0
        market_value = _num(position.get("market_value"))
        row = {
            "key": key,
            "kind": kind,
            "name": meta.get("name") or meta.get("underlying_name"),
            "units": units,
            "market_value": market_value,
            "underlying": (meta.get("underlying") or "").upper() or None,
            "underlying_name": meta.get("underlying_name"),
            "ratio": _positive(meta.get("ratio")),
            "leverage": _positive(meta.get("leverage")),
            "reset": meta.get("reset"),
            "currency": None,
            "exposure": None,
            "status": "ok",
            "reason": None,
            "source": meta.get("source"),
            "as_of": meta.get("as_of"),
            "acquired_on": position.get("acquired_on"),
            "held_days": _days_between(position.get("acquired_on"), as_of),
        }
        if kind == "option":
            price = _positive(meta.get("underlying_price"))
            delta = _num(meta.get("delta"))
            multiplier = _positive(meta.get("multiplier")) or 100.0
            if price is None or delta is None:
                row.update(status="unknown", reason="no delta or underlying price on record")
            else:
                row["delta"] = delta
                row["multiplier"] = multiplier
                row["currency"] = (meta.get("underlying_currency") or "").upper() or None
                # Delta-adjusted: a put carries a negative exposure, and the
                # premium is not the exposure.
                row["exposure"] = round(units * multiplier * delta * price, 2)
        elif kind == "knock_out":
            ratio, price = row["ratio"], _positive(meta.get("underlying_price"))
            if ratio is None or price is None:
                row.update(status="unknown", reason="no ratio or underlying price on record")
            else:
                sign = -1.0 if meta.get("direction") == "short" else 1.0
                row["currency"] = (meta.get("underlying_currency") or "").upper() or None
                row["exposure"] = round(units * ratio * price * sign, 2)
        elif kind in {"etf", "etc", "fund", "share"}:
            leverage = row["leverage"] or 1.0
            if market_value is None:
                row.update(status="unknown", reason="no market value in the book")
            else:
                sign = -1.0 if units < 0 else 1.0
                row["currency"] = account_currency
                row["exposure"] = round(market_value * leverage * sign, 2)
                # A fund is a basket: its stated top holdings are split out so a
                # constituent held inside it counts against that constituent.
                holdings = meta.get("holdings") or []
                if holdings and kind in {"etf", "fund"}:
                    row["coverage"] = _num(meta.get("holdings_coverage"))
                    row["holdings_as_of"] = meta.get("holdings_as_of")
                    row["lookthrough"] = [
                        {"symbol": str(item.get("symbol") or "").strip().upper(),
                         "name": item.get("name"),
                         "weight": round(_num(item.get("weight")) or 0.0, 6),
                         "exposure": round(row["exposure"] * (_num(item.get("weight")) or 0.0), 2)}
                        for item in holdings
                        if item.get("symbol") and (_num(item.get("weight")) or 0.0) > 0
                    ]
        else:
            row.update(status="unknown", reason="instrument not resolved")
        if row["exposure"] is not None and row["currency"] and account_currency \
                and row["currency"] != account_currency:
            rate = fx.rate_for(row["currency"], account_currency, fx_rates)
            if rate is None:
                # Comparable only after conversion; without a stamped rate the
                # figure stays in its own currency and is named as such.
                row["status"] = "fx_missing"
                row["reason"] = f"{row['currency']} exposure, no FX rate to {account_currency}"
            else:
                row["exposure_native"] = row["exposure"]
                row["fx"] = {"pair": rate.get("pair"), "rate": round(float(rate["rate"]), 6),
                             "as_of": rate.get("as_of"), "source": rate.get("source")}
                row["exposure"] = round(row["exposure"] * float(rate["rate"]), 2)
                row["currency"] = account_currency
        rows.append(row)

    groups: dict[str, list[dict]] = {}
    for row in rows:
        name = row["underlying"] or row["key"]
        groups.setdefault(name, []).append(row)

    # Baskets contribute to their constituents' groups. Both views are kept:
    # the fund as an instrument, and the constituent exposure inside it.
    contributions: dict[str, dict] = {}
    for row in rows:
        for item in row.get("lookthrough") or []:
            slot = contributions.setdefault(
                item["symbol"], {"net": 0.0, "gross": 0.0, "via": []})
            slot["net"] += item["exposure"]
            slot["gross"] += abs(item["exposure"])
            slot["via"].append(row["key"])
    by_underlying: dict[str, dict] = {}
    for name, items in groups.items():
        known = [r for r in items if r["exposure"] is not None and r["status"] == "ok"]
        currencies = {r["currency"] for r in known if r["currency"]}
        by_underlying[name] = {
            "net": round(sum(r["exposure"] for r in known), 2) if known else None,
            "gross": round(sum(abs(r["exposure"]) for r in known), 2) if known else None,
            "currency": currencies.pop() if len(currencies) == 1 else ("mixed" if currencies else None),
            "keys": [r["key"] for r in items],
            "unknown": [r["key"] for r in items if r["exposure"] is None],
            "fx": [r["key"] for r in items if r["status"] == "fx_missing"],
            "via": [],
            "lookthrough": False,
        }
    for symbol, extra in contributions.items():
        group = by_underlying.setdefault(symbol, {
            "net": 0.0, "gross": 0.0, "currency": account_currency,
            "keys": [], "unknown": [], "fx": [], "via": [], "lookthrough": False,
        })
        group["net"] = round((group["net"] or 0.0) + extra["net"], 2)
        group["gross"] = round((group["gross"] or 0.0) + extra["gross"], 2)
        group["via"] = sorted(set(group.get("via") or []) | set(extra["via"]))
        group["lookthrough"] = True
    unresolved = [r["key"] for r in rows if r["status"] != "ok"]
    return {
        "rows": rows,
        "by_underlying": by_underlying,
        "unresolved": unresolved,
        "status": "ok" if not unresolved else ("partial" if rows else "empty"),
    }


def exposure_units_for(
    ticker: str,
    exposure: Mapping[str, Any] | None,
    price: float | None,
    account_currency: str | None = None,
) -> tuple[float, str | None]:
    """Existing exposure in ``ticker`` expressed in units of it, same side only.

    Returns ``(units, reason)``; ``reason`` is set when the exposure cannot be
    compared (currency mismatch) and the caller must treat it as unmodelled.
    """
    price = _positive(price)
    if price is None:
        return 0.0, None
    groups = (exposure or {}).get("by_underlying") or {}
    group = groups.get((ticker or "").strip().upper())
    if not group:
        return 0.0, None
    if group.get("fx"):
        # Kept out of `net` on purpose: adding USD to EUR would invent a rate.
        return 0.0, "fx"
    if group.get("net") is None:
        return 0.0, None
    currency = (group.get("currency") or "").upper() or None
    account_currency = (account_currency or "").upper() or None
    if currency and account_currency and currency != account_currency:
        return 0.0, "fx"
    same_side = max(0.0, group["net"])
    return same_side / price, None


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
    instruments: Mapping[str, Mapping[str, Any]] | None = None,
    fx_rates: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict:
    """The run's one arithmetic sizing plan plus the discipline checklist."""
    policy = {**DEFAULT_POLICY, **dict(policy or {})}
    proposal = dict(proposal or {})
    account = dict(account or {})
    levels = dict(levels or {})
    direction = direction_of(rating)
    # A trade against the caller's existing side is an exit, not a new position:
    # "Sell" while holding a long closes it, and closing spends no risk budget.
    # A partial trim is the narrative's business; the plan states the full exit
    # it can prove, so the count is never an interpretation of prose.
    held = _held_units(account, ticker)
    is_exit = ((direction == "long" and held < 0) or (direction == "short" and held > 0))
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
            # Closing a position needs no risk budget, so an exit is not blocked
            # by a missing equity; only a new position is.
            if not is_exit:
                miss("equity")
        else:
            currency = str(account.get("currency") or "").strip()
            check("equity", True, f"{equity:,.2f}{' ' + currency if currency else ''}")

    # --- existing exposure ------------------------------------------------
    # The book may hold the analyzed name directly, through a knock-out, or
    # through an ETF. Only the first two can be netted; the ETF has no
    # look-through weights, and saying so is better than guessing a share.
    account_currency = account.get("currency")
    exposure = book_exposure(account, instruments, account_currency, fx_rates, as_of=as_of)
    if has_book:
        rows = exposure.get("rows") or []
        unknown = [r["key"] for r in rows if r["status"] == "unknown"]
        foreign = [r["key"] for r in rows if r["status"] == "fx_missing"]
        reasons = []
        if unknown:
            reasons.append("no data: " + ", ".join(unknown))
        if foreign:
            reasons.append("not comparable (currency): " + ", ".join(foreign))
        check("book_exposure", not reasons,
              "every position resolved to an exposure" if not reasons
              else "; ".join(reasons),
              blocking=False)

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
    if (stop is None and not is_exit and atr is not None and entry is not None
            and direction in {"long", "short"}):
        offset = float(policy["stop_atr_multiple"]) * atr
        stop = entry - offset if direction == "long" else entry + offset
        stop_source = "atr_derived"
    if stop is None:
        if not is_exit:
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
    if is_exit:
        check("exit_plan", True,
              f"closing {_floor(abs(held))} unit(s) of the held position", blocking=False)
        # A stop is not a rule for a close, but a level the Trader stated is
        # still reported with its distance instead of a bare number.
        if entry is not None and stop is not None:
            risk_per_share = abs(entry - stop)
            stop_pct = risk_per_share / entry * 100
            if atr is not None:
                atr_multiple = risk_per_share / atr
    elif entry is not None and stop is not None:
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
    over_cap: list[str] = []
    if equity:
        for name, group in (exposure.get("by_underlying") or {}).items():
            if group.get("net") is None:
                continue
            pct = abs(group["net"]) / equity * 100
            group["pct_equity"] = round(pct, 2)
            group["over_cap"] = pct > max_pos_pct
            if group["over_cap"]:
                over_cap.append(f"{name} {pct:.1f} %")
    leveraged = [r for r in exposure.get("rows") or []
                 if r.get("leverage") and r.get("reset") == "daily"]
    if leveraged:
        # The exposure is exact for today; the reset compounds over a holding
        # period, so the horizon is named instead of a made-up drag number.
        detail = "; ".join(
            f"{r['key']} {r['leverage']:g}x daily"
            + (f", held {r['held_days']} day(s)" if r.get("held_days") is not None else "")
            for r in leveraged)
        check("leverage_reset", False,
              detail + " — exposure holds for one session; the compounding of the "
                       "daily reset over the holding period is not additive",
              blocking=False)
    if equity and (exposure.get("by_underlying") or {}):
        check("concentration", not over_cap,
              f"largest single-underlying exposure within {max_pos_pct:g} % of equity"
              if not over_cap else
              f"above {max_pos_pct:g} % of equity: " + ", ".join(over_cap),
              blocking=False)
    caps: dict[str, int | None] = {}
    position: dict[str, Any] = {}
    if is_exit and entry:
        units = _floor(abs(held))
        caps["by_holding"] = units
        position = {
            "kind": "exit",
            "shares": units,
            "notional": round(units * entry, 2),
            "risk": 0.0,
            "position_pct": round(units * entry / equity * 100, 2) if equity else None,
            "risk_pct": 0.0,
            "binding_constraint": "by_holding",
            "currency": account.get("currency"),
            "executable": False,
        }
    elif equity and entry and risk_per_share and side_ok and direction in {"long", "short"}:
        budget = equity * risk_pct / 100
        caps["by_risk"] = _floor(budget / risk_per_share)
        total_units = _floor(equity * max_pos_pct / 100 / entry)
        existing_units, fx_reason = exposure_units_for(
            ticker, exposure, entry, account.get("currency"))
        if fx_reason:
            check("exposure_currency", False,
                  "existing exposure is in another currency and was not netted",
                  blocking=False)
        held_same_side = max(0.0, held) if direction == "long" else max(0.0, -held)
        caps["by_position"] = max(0, total_units - _floor(held_same_side + existing_units))
        caps["by_cash"] = _floor(cash / entry) if (cash is not None and direction == "long") else None
        binding = {name: cap for name, cap in caps.items() if cap is not None}
        shares = min(binding.values()) if binding else 0
        position = {
            "kind": "entry",
            "shares": shares,
            "notional": round(shares * entry, 2),
            "risk": round(shares * risk_per_share, 2),
            "position_pct": round(shares * entry / equity * 100, 2),
            "risk_pct": round(shares * risk_per_share / equity * 100, 2),
            "binding_constraint": min(binding.items(), key=lambda kv: kv[1])[0] if binding else None,
            "existing_units_equivalent": round(existing_units, 2),
            "currency": account.get("currency"),
            "executable": False,
        }

    # --- target / reward-risk --------------------------------------------
    target = _positive(target)
    reward_risk = None
    if not is_exit and entry is not None and target is not None and risk_per_share:
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
                 position, caps, checks, missing, verdict=verdict, status=status,
                 exposure=exposure)


def _plan(ticker, as_of, rating, direction, trader_action, account, levels,
          policy, plan_levels, position, caps, checks, missing, *, verdict, status,
          exposure: Mapping[str, Any] | None = None) -> dict:
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
        "exposure": exposure or {"rows": [], "by_underlying": {}, "unresolved": [],
                                 "status": "empty"},
        "checks": checks,
        "missing": missing,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def _fmt(value, digits: int = 2, suffix: str = "") -> str:
    number = _num(value)
    return "not available" if number is None else f"{number:,.{digits}f}{suffix}"


def _position_cell(plan: Mapping[str, Any]) -> str:
    """One line for the report table: an entry names its risk, an exit its proceeds."""
    position = plan.get("position") or {}
    if not position:
        return "not available"
    if position.get("kind") == "exit":
        return (f"exit {position.get('shares')} units (proceeds "
                f"{_fmt(position.get('notional'), 2)}, "
                f"{_fmt(position.get('position_pct'), 2, ' % of equity')})")
    return (f"{position.get('shares')} units, {_fmt(position.get('notional'), 2)} "
            f"({_fmt(position.get('position_pct'), 2, ' % of equity')}), "
            f"risk {_fmt(position.get('risk'), 2)} on the stop")


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
        f"| Position | {_position_cell(plan)} |",
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
    exposure = plan.get("exposure") or {}
    if exposure.get("rows"):
        lines += ["", "### Exposure by underlying", "",
                  "| Underlying | Instruments | Net | Gross | % of equity | Status |",
                  "|---|---|---:|---:|---:|---|"]
        for name, group in exposure.get("by_underlying", {}).items():
            keys = ", ".join(group.get("keys") or [])
            currency = group.get("currency") or ""
            flags: list[str] = []
            if group.get("lookthrough"):
                flags.append("look-through via " + ", ".join(group.get("via") or []))
            if group.get("unknown"):
                flags.append("unknown: " + ", ".join(group["unknown"]))
            if group.get("fx"):
                flags.append("no FX rate: " + ", ".join(group["fx"]))
            if group.get("over_cap"):
                flags.append("over cap")
            status = ", ".join(flags) or "ok"
            lines.append(
                f"| {name} | {keys} | {_fmt(group.get('net'))} {currency} | "
                f"{_fmt(group.get('gross'))} | {_fmt(group.get('pct_equity'), 2)} | {status} |")
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
    if position.get("kind") == "exit":
        return "\n".join([
            "Deterministic sizing of the decision (computed in code, not estimated):",
            f"- Exit: {position.get('shares')} units of the held position at "
            f"{_fmt(levels.get('entry'), 4)} (proceeds {_fmt(position.get('notional'))}, "
            f"{_fmt(position.get('position_pct'), 2, ' % of equity')})",
            "- This closes an existing position: no risk budget is spent and no stop is required.",
            "Use these figures verbatim; do not propose a different share count.",
        ])
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
        f"- Existing exposure in {plan['ticker']}: "
        f"{_fmt(position.get('existing_units_equivalent'), 2)} unit-equivalents already "
        f"counted against the position cap",
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
    book = state.get("portfolio_account") or {}
    try:
        from tradingagents import instruments as instruments_module

        meta = instruments_module.for_book(book)
    except Exception:  # noqa: BLE001 -- a missing cache must not block a run
        meta = {}
    try:
        # Cache only: a run does not reach the network from inside the graph.
        rates = fx.cached_rates(_book_currencies(book, meta), book.get("currency"))
    except Exception:  # noqa: BLE001 -- a rate miss must not block a run
        rates = {}
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
        instruments=meta,
        fx_rates=rates,
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
