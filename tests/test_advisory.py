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
def test_a_sell_against_a_held_long_is_an_exit_not_a_new_short():
    """Selling what the caller holds closes the position; it does not open a short."""
    p = plan(rating="Sell", proposal={"action": "Sell", "entry_price": 100.0},
             account={**ACCOUNT, "positions": [{"ticker": "X.DE", "quantity": 10}]})
    assert p["position"]["kind"] == "exit"
    assert p["position"]["shares"] == 10
    assert p["position"]["risk"] == 0.0
    assert p["position"]["binding_constraint"] == "by_holding"
    assert p["verdict"] == "EXECUTE"
    # Closing needs no stop, so no stop check is raised against the exit.
    assert not any(c["name"] == "stop_loss" for c in p["checks"])
    # A stated stop is reported with its distance, not as a bare number.
    with_stop = plan(rating="Sell", proposal={"action": "Sell", "entry_price": 100.0,
                                              "stop_loss": 110.0},
                     account={**ACCOUNT, "positions": [{"ticker": "X.DE", "quantity": 10}]})
    assert with_stop["levels"]["stop_pct"] == 10.0
    assert with_stop["levels"]["atr_multiple"] == 2.0


@pytest.mark.unit
def test_a_buy_against_a_held_short_covers_it():
    p = plan(account={**ACCOUNT, "positions": [{"ticker": "X.DE", "quantity": -7}]})
    assert p["position"]["kind"] == "exit"
    assert p["position"]["shares"] == 7


@pytest.mark.unit
def test_an_exit_needs_no_equity():
    """A risk budget sizes new exposure; it does not gate closing one."""
    p = plan(rating="Sell", proposal={"action": "Sell", "entry_price": 100.0},
             account={"positions": [{"ticker": "X.DE", "quantity": 4}]})
    assert p["verdict"] == "EXECUTE"
    assert p["position"]["shares"] == 4
    assert "equity" not in p["missing"]


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
    # A direct caller without a rating but with levels: no side to size.
    unsided = advisory.render_context(plan(rating=None))
    assert "direction could not be established" in unsided


@pytest.mark.unit
def test_context_block_describes_an_exit_as_a_close():
    text = advisory.render_context(
        plan(rating="Sell", proposal={"action": "Sell", "entry_price": 100.0},
             account={**ACCOUNT, "positions": [{"ticker": "X.DE", "quantity": 10}]}))
    assert "Exit: 10 units" in text
    assert "no stop is required" in text


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


# --- exposure: derivatives and funds are not share counts -------------------

BIG = {"equity": 300_000.0, "cash": 50_000.0, "currency": "EUR", "positions": []}
KNOCK_OUT = {
    "MK0L4Q": {"kind": "knock_out", "underlying": "RHM.DE", "underlying_name": "Rheinmetall AG",
               "ratio": 0.1, "underlying_price": 1048.2, "underlying_currency": "EUR",
               "direction": "long", "source": "ariva/MK0L4Q", "as_of": "2026-10-30"},
}
LEVERAGED = {"A0V6ZV": {"kind": "etc", "leverage": 2.0, "reset": "daily",
                        "name": "WisdomTree Aluminium 2x Daily Leveraged"}}


@pytest.mark.unit
def test_a_knock_out_is_exposure_through_its_ratio():
    book = {**BIG, "positions": [{"ticker": "MK0L4Q", "quantity": 305, "market_value": 6234.46}]}
    p = plan(ticker="MK0L4Q", account=book, instruments=KNOCK_OUT)
    row = p["exposure"]["rows"][0]
    assert row["exposure"] == pytest.approx(31_970.1)   # 305 x 0.10 x 1048,20
    assert row["status"] == "ok"
    assert p["exposure"]["by_underlying"]["RHM.DE"]["net"] == pytest.approx(31_970.1)
    assert p["exposure"]["status"] == "ok"


@pytest.mark.unit
def test_a_daily_leveraged_note_counts_its_leverage():
    book = {**BIG, "positions": [{"ticker": "A0V6ZV", "quantity": 792, "market_value": 2005.34}]}
    p = plan(ticker="A0V6ZV", account=book, instruments=LEVERAGED)
    assert p["exposure"]["rows"][0]["exposure"] == pytest.approx(4010.68)


@pytest.mark.unit
def test_existing_derivative_exposure_shrinks_the_position_cap():
    """15 % of 300k is 45 000; 31 970 of it is already exposed through the turbo."""
    book = {**BIG, "positions": [{"ticker": "MK0L4Q", "quantity": 305, "market_value": 6234.46}]}
    p = plan(ticker="RHM.DE", account=book, instruments=KNOCK_OUT,
             proposal={"action": "Buy", "entry_price": 1000.0, "stop_loss": 950.0})
    assert p["caps"]["by_position"] == 45 - 31, "31 unit-equivalents are already held via the turbo"
    assert p["position"]["existing_units_equivalent"] == pytest.approx(31.97, abs=0.01)


@pytest.mark.unit
def test_a_short_derivative_does_not_block_a_long_entry():
    book = {**BIG, "positions": [{"ticker": "MK0L4Q", "quantity": 305, "market_value": 6234.46}]}
    short = {"MK0L4Q": {**KNOCK_OUT["MK0L4Q"], "direction": "short"}}
    p = plan(ticker="RHM.DE", account=book, instruments=short,
             proposal={"action": "Buy", "entry_price": 1000.0, "stop_loss": 950.0})
    assert p["caps"]["by_position"] == 45
    assert p["position"]["existing_units_equivalent"] == 0.0


@pytest.mark.unit
def test_an_unresolved_instrument_stays_unknown():
    book = {**BIG, "positions": [{"ticker": "MK0L4Q", "quantity": 305}]}
    p = plan(ticker="RHM.DE", account=book, instruments={},
             proposal={"action": "Buy", "entry_price": 1000.0, "stop_loss": 950.0})
    assert p["exposure"]["rows"][0]["status"] == "unknown"
    assert p["exposure"]["rows"][0]["exposure"] is None
    assert "MK0L4Q" in p["exposure"]["unresolved"]
    assert _check(p, "book_exposure")["passed"] is False
    assert _check(p, "book_exposure")["blocking"] is False


@pytest.mark.unit
def test_a_foreign_currency_exposure_is_not_netted_without_a_rate():
    book = {**BIG, "positions": [{"ticker": "FG40N8", "quantity": 441, "market_value": 1217.16}]}
    oracle = {"FG40N8": {"kind": "knock_out", "underlying": "ORCL", "ratio": 0.1,
                         "underlying_price": 139.56, "underlying_currency": "USD",
                         "direction": "short"}}
    p = plan(ticker="ORCL", account=book, instruments=oracle)
    assert p["exposure"]["rows"][0]["status"] == "fx_missing"
    assert _check(p, "exposure_currency")["passed"] is False


@pytest.mark.unit
def test_a_stamped_rate_converts_the_exposure_into_the_book_currency():
    book = {**BIG, "positions": [{"ticker": "FG40N8", "quantity": 441, "market_value": 1217.16}]}
    oracle = {"FG40N8": {"kind": "knock_out", "underlying": "ORCL", "ratio": 0.1,
                         "underlying_price": 139.56, "underlying_currency": "USD",
                         "direction": "short"}}
    rates = {"USDEUR": {"pair": "USDEUR", "base": "USD", "quote": "EUR", "rate": 0.89,
                        "as_of": "2026-10-30", "source": "yahoo/USDEUR=X"}}
    p = plan(ticker="ORCL", account=book, instruments=oracle, fx_rates=rates)
    row = p["exposure"]["rows"][0]
    assert row["status"] == "ok"
    assert row["exposure_native"] == pytest.approx(-6_154.6, abs=0.1)
    assert row["exposure"] == pytest.approx(-6_154.6 * 0.89, abs=0.5)
    assert row["fx"]["pair"] == "USDEUR" and row["fx"]["as_of"] == "2026-10-30"
    assert p["exposure"]["by_underlying"]["ORCL"]["currency"] == "EUR"


@pytest.mark.unit
def test_a_concentrated_book_is_flagged_without_blocking():
    book = {**BIG, "positions": [{"ticker": "A40Y9K", "quantity": 8171.8, "market_value": 262_151.34}]}
    etf = {"A40Y9K": {"kind": "etf", "name": "WisdomTree Europe Defence UCITS ETF"}}
    p = plan(ticker="RHM.DE", account=book, instruments=etf,
             proposal={"action": "Buy", "entry_price": 1000.0, "stop_loss": 950.0})
    group = p["exposure"]["by_underlying"]["A40Y9K"]
    assert group["pct_equity"] == pytest.approx(87.38, abs=0.01)
    assert group["over_cap"] is True
    assert _check(p, "concentration")["passed"] is False
    assert _check(p, "concentration")["blocking"] is False
    assert p["verdict"] == "EXECUTE", "a concentrated book is a warning, not a verdict"


@pytest.mark.unit
def test_a_fund_hands_its_constituents_to_their_own_group():
    book = {**BIG, "positions": [{"ticker": "A40Y9K", "quantity": 8171.8, "market_value": 262_151.34}]}
    fund = {"A40Y9K": {"kind": "etf", "name": "WisdomTree Europe Defence UCITS ETF",
                       "holdings": [{"symbol": "RHM.DE", "name": "Rheinmetall AG", "weight": 0.05}],
                       "holdings_coverage": 0.62, "holdings_as_of": "2026-10-30"}}
    p = plan(ticker="RHM.DE", account=book, instruments=fund,
             proposal={"action": "Buy", "entry_price": 1000.0, "stop_loss": 950.0})
    groups = p["exposure"]["by_underlying"]
    assert groups["RHM.DE"]["net"] == pytest.approx(13_107.57, abs=0.01)
    assert groups["RHM.DE"]["lookthrough"] is True
    assert groups["RHM.DE"]["via"] == ["A40Y9K"]
    assert p["exposure"]["rows"][0]["coverage"] == 0.62


@pytest.mark.unit
def test_a_fund_holding_and_a_turbo_add_up_in_the_same_underlying():
    book = {**BIG, "positions": [
        {"ticker": "A40Y9K", "quantity": 8171.8, "market_value": 262_151.34},
        {"ticker": "MK0L4Q", "quantity": 305, "market_value": 6234.46},
    ]}
    meta = {"A40Y9K": {"kind": "etf", "holdings": [
                {"symbol": "RHM.DE", "weight": 0.05}], "holdings_coverage": 0.62},
            "MK0L4Q": {"kind": "knock_out", "underlying": "RHM.DE", "ratio": 0.1,
                       "underlying_price": 1048.2, "underlying_currency": "EUR",
                       "direction": "long"}}
    p = plan(ticker="RHM.DE", account=book, instruments=meta,
             proposal={"action": "Buy", "entry_price": 1000.0, "stop_loss": 950.0})
    group = p["exposure"]["by_underlying"]["RHM.DE"]
    assert group["net"] == pytest.approx(13_107.57 + 31_970.1, abs=0.1)
    # 45 000 caps it: 44 950 of exposure leaves no room for another unit.
    assert p["caps"]["by_position"] == 0


@pytest.mark.unit
def test_an_option_is_delta_adjusted_exposure():
    book = {**BIG, "positions": [{"ticker": "ORCL261218C00140000", "quantity": 2,
                                  "market_value": 1_200.0}]}
    option = {"ORCL261218C00140000": {
        "kind": "option", "underlying": "ORCL", "underlying_price": 140.0,
        "underlying_currency": "EUR", "delta": 0.53, "multiplier": 100.0,
        "option_type": "call"}}
    p = plan(ticker="ORCL", account=book, instruments=option)
    row = p["exposure"]["rows"][0]
    assert row["exposure"] == pytest.approx(2 * 100 * 0.53 * 140.0, abs=0.01)
    assert row["delta"] == 0.53 and row["multiplier"] == 100.0


@pytest.mark.unit
def test_an_option_without_a_delta_is_unknown_not_the_premium():
    book = {**BIG, "positions": [{"ticker": "ORCL261218C00140000", "quantity": 2,
                                  "market_value": 1_200.0}]}
    option = {"ORCL261218C00140000": {"kind": "option", "underlying": "ORCL",
                                      "underlying_price": 140.0, "delta": None}}
    p = plan(ticker="ORCL", account=book, instruments=option)
    row = p["exposure"]["rows"][0]
    assert row["status"] == "unknown" and row["exposure"] is None


@pytest.mark.unit
def test_a_fund_is_netted_under_its_tradable_symbol():
    # Depot WKN A40Y9K is analyzed as EUDF.DE; a run on EUDF.DE must see the
    # holding instead of sizing a fresh position (or a short) next to it.
    book = {**ACCOUNT, "equity": 100_000.0, "positions": [
        {"ticker": "A40Y9K", "quantity": 400.0, "market_value": 40_000.0,
         "analysis_ticker": "EUDF.DE"}]}
    fund = {"A40Y9K": {"kind": "etf", "name": "WisdomTree Europe Defence",
                       "fund_symbol": "EUDF.DE"}}
    p = plan(ticker="EUDF.DE", account=book, instruments=fund,
             proposal={"action": "Buy", "entry_price": 100.0, "stop_loss": 95.0})
    groups = p["exposure"]["by_underlying"]
    assert groups["EUDF.DE"]["net"] == pytest.approx(40_000.0)
    assert "A40Y9K" not in groups
    assert p["caps"]["by_position"] == 0
    assert p["verdict"] == "NO_TRADE"


@pytest.mark.unit
def test_a_venue_override_maps_an_adr_line_to_the_analysis_symbol():
    book = {**ACCOUNT, "equity": 100_000.0, "positions": [
        {"ticker": "A420WD", "quantity": 133.0, "market_value": 17_035.0,
         "analysis_ticker": "ENR.DE"}]}
    meta = {"A420WD": {"kind": "share", "underlying": "SMERY",
                       "name": "Siemens Energy"}}
    p = plan(ticker="ENR.DE", account=book, instruments=meta,
             proposal={"action": "Buy", "entry_price": 100.0, "stop_loss": 95.0})
    groups = p["exposure"]["by_underlying"]
    assert groups["ENR.DE"]["net"] == pytest.approx(17_035.0)
    assert groups["ENR.DE"]["keys"] == ["A420WD"]
    assert "SMERY" not in groups


@pytest.mark.unit
def test_a_daily_reset_note_names_its_horizon_instead_of_a_drag_number():
    book = {**BIG, "positions": [{"ticker": "A0V6ZV", "quantity": 792,
                                  "market_value": 2005.34,
                                  "acquired_on": "2026-08-01"}]}
    note = {"A0V6ZV": {"kind": "etc", "leverage": 2.0, "reset": "daily"}}
    p = plan(ticker="A0V6ZV", account=book, instruments=note)
    row = p["exposure"]["rows"][0]
    assert row["held_days"] == 90
    check = _check(p, "leverage_reset")
    assert check["passed"] is False and check["blocking"] is False
    assert "2x daily" in check["detail"] and "held 90 day(s)" in check["detail"]
