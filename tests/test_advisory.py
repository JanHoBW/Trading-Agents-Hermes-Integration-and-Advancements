"""Deterministic sizing: arithmetic over existing inputs, never a model estimate.

The two formulas are the fork's answer to "the report names a position size but
nobody can reproduce it": the size comes from the Trader's structured levels,
the caller's book, the verified market data and the configured risk policy.
"""

from __future__ import annotations

import json

import pandas as pd
import pytest

from tradingagents import advisory


@pytest.mark.unit
def test_direction_of():
    assert advisory.direction_of("Buy") == "long"
    assert advisory.direction_of("Overweight") == "long"
    assert advisory.direction_of("Sell") == "short"
    assert advisory.direction_of("Underweight") == "short"
    assert advisory.direction_of("Hold") == "none"
    assert advisory.direction_of(None) == "unknown"


@pytest.mark.unit
def test_market_levels_reads_close_atr_and_adv(monkeypatch):
    rows = 30
    frame = pd.DataFrame({
        "Date": pd.date_range("2026-09-01", periods=rows, freq="D"),
        "Open": [100.0] * rows, "High": [102.0] * rows, "Low": [98.0] * rows,
        "Close": [100.0] * rows, "Volume": [1_000_000] * rows,
    })
    monkeypatch.setattr("tradingagents.dataflows.vendors.yahoo.ohlcv.load_ohlcv",
                        lambda *a, **k: frame)
    levels = advisory.market_levels("X.DE", "2026-10-30")
    assert levels["available"] is True
    assert levels["last_close"] == 100.0
    assert levels["atr"] and levels["atr"] > 0
    assert levels["adv_value"] == pytest.approx(100_000_000.0)


ACCOUNT = {"equity": 25000.0, "cash": 5000.0, "currency": "EUR", "positions": []}
PROPOSAL = {"action": "Buy", "entry_price": 100.0, "stop_loss": 90.0}
LEVELS = {"available": True, "last_close": 100.0, "atr": 5.0, "adv_value": 10_000_000.0}


def plan(**kw):
    kw.setdefault("ticker", "X.DE")
    kw.setdefault("as_of", "2026-10-30")
    kw.setdefault("rating", "Buy")
    kw.setdefault("proposal", PROPOSAL)
    kw.setdefault("account", ACCOUNT)
    kw.setdefault("levels", LEVELS)
    return advisory.build_advisory(**kw)


def _check(record, name):
    return next(c for c in record["checks"] if c["name"] == name)


@pytest.mark.unit
def test_risk_budget_sets_the_size():
    # 1 % of 25,000 = 250; stop distance 10 -> 25 units; 15 % cap = 37; cash = 50
    p = plan()
    assert p["position"]["shares"] == 25
    assert p["position"]["risk"] == 250.0
    assert p["position"]["binding_constraint"] == "by_risk"
    assert p["verdict"] == "EXECUTE" and p["status"] == "pass"


@pytest.mark.unit
def test_position_cap_binds_before_the_risk_budget():
    p = plan(proposal={"action": "Buy", "entry_price": 100.0, "stop_loss": 99.0})
    assert p["position"]["shares"] == 37
    assert p["position"]["binding_constraint"] == "by_position"


@pytest.mark.unit
def test_cash_caps_a_long_and_is_named_as_the_constraint():
    p = plan(account={**ACCOUNT, "cash": 1000.0})
    assert p["position"]["shares"] == 10
    assert p["position"]["binding_constraint"] == "by_cash"


@pytest.mark.unit
def test_floors_to_whole_units():
    p = plan(proposal={"action": "Buy", "entry_price": 100.0, "stop_loss": 97.0},
             account={**ACCOUNT, "cash": 100_000.0}, policy={"max_position_pct": 100.0})
    assert p["position"]["shares"] == 83          # 250 / 3 = 83.33


@pytest.mark.unit
def test_adding_to_an_existing_position_respects_the_cap():
    p = plan(account={**ACCOUNT, "positions": [{"ticker": "X.DE", "quantity": 30}]})
    assert p["position"]["shares"] == 7           # cap 37, 30 already held


@pytest.mark.unit
def test_hold_sizes_nothing():
    p = plan(rating="Hold")
    assert p["verdict"] == "NO_TRADE"
    assert not p["position"]


@pytest.mark.unit
def test_a_book_without_equity_blocks_the_plan():
    p = plan(account={"cash": 5000.0})
    assert p["verdict"] == "BLOCKED" and p["status"] == "fail"
    assert p["missing"] == ["equity"]


@pytest.mark.unit
def test_no_book_is_not_a_defect():
    p = plan(account={})
    assert p["verdict"] == "NOT_APPLICABLE" and p["status"] == "not_assessable"
    assert p["position"] == {}


@pytest.mark.unit
def test_a_stop_on_the_wrong_side_blocks_instead_of_sizing():
    p = plan(proposal={"action": "Buy", "entry_price": 100.0, "stop_loss": 110.0})
    assert p["verdict"] == "BLOCKED"
    assert p["position"] == {}
    assert _check(p, "levels_match_direction")["passed"] is False


@pytest.mark.unit
def test_a_missing_stop_is_derived_from_atr_and_labelled():
    p = plan(proposal={"action": "Buy", "entry_price": 100.0})
    assert p["levels"]["stop"] == 90.0
    assert p["levels"]["stop_source"] == "atr_derived"
    assert _check(p, "stop_derived")["passed"] is True


@pytest.mark.unit
def test_a_short_sizes_from_the_same_budget():
    p = plan(rating="Sell", proposal={"action": "Sell", "entry_price": 100.0, "stop_loss": 110.0})
    assert p["position"]["shares"] == 25
    assert p["direction"] == "short"


@pytest.mark.unit
def test_reward_risk_warns_but_does_not_block():
    bad = plan(target=105.0)
    assert bad["status"] == "warn" and bad["verdict"] == "EXECUTE"
    good = plan(target=120.0)
    assert good["status"] == "pass" and good["levels"]["reward_risk"] == 2.0


@pytest.mark.unit
def test_liquidity_participation_warns():
    p = plan(levels={**LEVELS, "adv_value": 50_000.0})
    assert _check(p, "liquidity")["passed"] is False
    assert p["verdict"] == "EXECUTE"            # a warning, not a block


@pytest.mark.unit
def test_artifacts_are_written(tmp_path):
    p = plan()
    advisory.write_artifacts(tmp_path, p)
    assert (tmp_path / "sizing.json").exists()
    discipline = json.loads((tmp_path / "discipline.json").read_text(encoding="utf-8"))
    assert discipline["schema"] == "tradingagents.discipline.v1"
    assert discipline["verdict"] == "EXECUTE"


# --- rendering ---------------------------------------------------------------

@pytest.mark.unit
def test_markdown_states_the_verdict_and_every_check():
    text = advisory.render_markdown(plan())
    assert "**Verdict**: EXECUTE" in text
    assert "| Position | 25 units" in text
    assert "| levels_match_direction | pass |" in text


@pytest.mark.unit
def test_markdown_names_missing_inputs_instead_of_zeros():
    text = advisory.render_markdown(plan(account={"cash": 5000.0}))
    assert "**Not available**: equity" in text
    assert "| Position | not available |" in text
    assert "0 units" not in text


@pytest.mark.unit
def test_context_block_forbids_an_invented_share_count_when_blocked():
    text = advisory.render_context(plan(account={"cash": 5000.0}))
    assert "blocked" in text.lower()
    assert "Do not invent a share count" in text


@pytest.mark.unit
def test_context_block_says_no_trade_instead_of_none_units():
    hold = advisory.render_context(plan(rating="Hold"))
    assert "no trade" in hold.lower() and "None units" not in hold
    # A cap that reduces the size to zero is named, not rendered as "None".
    tiny = advisory.render_context(plan(account={**ACCOUNT, "cash": 50.0}))
    assert "no executable size" in tiny and "None units" not in tiny


@pytest.mark.unit
def test_context_block_carries_the_numbers():
    text = advisory.render_context(plan(target=120.0))
    assert "25 units" in text and "1.00 %" in text and "2.00x ATR" in text


@pytest.mark.unit
def test_market_levels_fails_open(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("vendor down")

    monkeypatch.setattr("tradingagents.dataflows.vendors.yahoo.ohlcv.load_ohlcv", boom)
    levels = advisory.market_levels("X.DE", "2026-10-30")
    assert levels["available"] is False
    assert levels["reason"].startswith("RuntimeError: vendor down")
