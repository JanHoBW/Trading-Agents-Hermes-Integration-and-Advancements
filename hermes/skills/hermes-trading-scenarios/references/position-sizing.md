# Deterministic sizing and the pre-trade discipline gate

Load this when a run's position size has to be reproduced, when `sizing.json` /
`discipline.json` reads oddly, or when the risk policy has to be changed.

## What problem it solves

The Portfolio Manager writes prose; a share count in that prose was a model
estimate, and two runs of the same report could name different sizes. The fork
computes one plan in code from the Trader's **structured** proposal, the
caller's book, verified market data and the risk policy. The same plan goes into
the risk/PM prompts, the report (section VI) and the run's JSON artifacts.

Nothing is parsed from prose. If an input is missing, the plan says so
(`missing: ["equity"]`) instead of filling the gap.

## The two formulas

```
risk_per_share = |entry - stop|
shares = min(
    floor(equity * risk_per_trade_pct/100 / risk_per_share),   # by_risk
    floor(equity * max_position_pct/100 / entry) - held_units, # by_position
    floor(cash / entry)                                        # by_cash (longs)
)
risk   = shares * risk_per_share
notional = shares * entry
```

A short uses the same risk budget; cash does not cap it (the fork has no margin
model). The binding constraint is named in `position.binding_constraint`.

### Exits are not entries

A rating that points against the caller's existing side is a **close**, not a new
position: `Sell`/`Underweight` while holding a long, or `Buy`/`Overweight` while
holding a short. The plan then reports `position.kind = "exit"`, `shares` = the
units held on that side, `binding_constraint = "by_holding"` and `risk = 0.0` —
closing spends no risk budget and needs no stop, so an exit is not blocked by a
missing stop or a missing `equity`. Proceeds are `shares × entry`.

A partial trim ("sell 6 of 10") is the narrative's business; the plan states the
full exit it can prove rather than interpreting prose. With no position on the
other side, `Sell` sizes a new short as an entry.

## Inputs, in order of precedence

| Input | Source | Missing means |
|---|---|---|
| `equity` | portfolio JSON → required for sizing | `BLOCKED` (`missing: ["equity"]`) |
| `risk_per_trade_pct` | portfolio JSON → `TRADINGAGENTS_RISK_PER_TRADE_PCT` → 1.0 | default applies |
| `max_position_pct` | portfolio JSON → `TRADINGAGENTS_MAX_POSITION_PCT` → 15 | default applies |
| `entry` | Trader's typed `entry_price` → verified last close | `BLOCKED` |
| `stop` | Trader's typed `stop_loss` → `2 × ATR` (labelled `atr_derived`) | `BLOCKED` |
| `target` | PM's typed `price_target` | no reward/risk check |
| `atr`, `adv_value` | cached OHLCV at the analysis date (20-day ADV) | ATR band / liquidity check absent |

No book at all (`--portfolio` omitted) is **not** a defect: verdict
`NOT_APPLICABLE`, and the prompt tells the agents to give a percentage instead of
a share count.

## Verdicts

| Verdict | Meaning |
|---|---|
| `EXECUTE` | every blocking check passed, `shares > 0` |
| `NO_TRADE` | rating `Hold`, or a cap reduced the size to zero |
| `BLOCKED` | a blocking check failed (missing equity/levels, stop on the wrong side, stop too tight) |
| `NOT_APPLICABLE` | no portfolio book was provided |

`status` is the rule-level view: `pass`, `warn` (a non-blocking check failed),
`fail`, `not_assessable`.

A blocked plan blocks the **setup**, never the report. `quality_gate.json`
carries a non-blocking `position_sizing` check that names the verdict and, when
blocked, the failing checks.

## Reading the artifacts

```bash
run=~/.tradingagents/reports/trading-scenarios/RHM.DE/2026-10-30
jq '{verdict, status, account, levels, position, caps}' "$run/reports/sizing.json"
jq '.status, [.checks[] | select(.passed|not)]'  "$run/reports/discipline.json"
grep -n "VI. Position Sizing" "$run/reports/complete_report.md"
```

- `levels.stop_source` = `trader` (the Trader set it) or `atr_derived` (code
  derived it; the plan then also carries a `stop_derived` check).
- `position.executable` is true only for `EXECUTE`.
- `caps` shows every cap, so a surprising size is explainable in one look.

## Changing the policy

```bash
export TRADINGAGENTS_RISK_PER_TRADE_PCT=0.5
export TRADINGAGENTS_MAX_POSITION_PCT=10
export TRADINGAGENTS_MIN_REWARD_RISK=2.0
```

or per book:

```json
{"equity": 25000, "cash": 5000, "currency": "EUR",
 "risk_per_trade_pct": 0.5, "max_position_pct": 10, "positions": []}
```

`doctor TICKER --portfolio=FILE` reports `portfolio_equity` as `degraded` when a
given book has no positive `equity` — an unattended chain should stop there
rather than spend hours on a run whose sizing will be blocked.

## Derivatives, ETFs and exposure

A book is not made of shares only, so a position is valued as **exposure**, not
as a unit count. The term-sheet facts come from the instrument cache
(`~/.tradingagents/instruments.json`), which is filled by the resolver:

```bash
python -m tradingagents.instruments MK0L4Q FG40N8 --force   # WKN/ISIN -> record
```

Each record carries `kind` (`share`, `etf`, `etc`, `knock_out`, `option`),
`underlying` + its price/currency, the contractual `ratio`, the barrier-derived
`direction`, and `leverage`/`reset` for daily-leveraged notes, stamped with
`source` and `as_of`. Missing fields stay missing.

| Position | Exposure |
|---|---|
| Knock-out / option | `units × ratio × underlying price × direction` |
| Share, ETF, ETC | broker `market_value` (× `leverage` for a daily note) |
| Unresolved or no market value | `unknown` with a reason — never 1:1 |

`exposure.by_underlying` groups them per underlying; a group above
`max_position_pct` is a **warning** (`concentration`), because an existing
concentration is a fact to report, not a reason to fail a run.

The position cap nets what is already held in the analyzed name through a
derivative: 31 970 € of RHM exposure at a 1 012 € price is 31,59
unit-equivalents, so `by_position` falls from 45 to 14 units. A short derivative
does not offset a long entry, and a foreign-currency exposure is flagged
(`exposure_currency`) instead of being converted at a rate nobody verified.

`doctor TICKER --portfolio=FILE` adds `portfolio_instruments`, naming any booked
WKN without a term-sheet record.

### Known limits

- **ETF look-through** needs the issuer's daily weights. Without them the
  constituents inside a sector ETF stay unmodelled — the report says so rather
  than estimating a Rheinmetall share of a defence basket.
- **Daily-leveraged notes** are path-dependent; the model stores the leverage
  and a warning, not a constant delta.
- **Options** would need greeks; only the linear knock-out is modelled.
- **Currency**: exposures are compared only within one currency.

## What it deliberately does not do

No order, broker or wallet path; no options greeks; no portfolio optimisation;
no invented sensitivity. Beta and correlation are never derived — if the
reports did not state them, the plan does not either.
