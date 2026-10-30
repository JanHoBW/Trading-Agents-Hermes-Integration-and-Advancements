"""Delta for listed options: the exposure is the contract, not the premium."""

from __future__ import annotations

import pytest

from tradingagents import options


@pytest.mark.unit
def test_an_occ_symbol_splits_into_its_contract():
    parsed = options.parse_occ("ORCL261218C00060000")
    assert parsed == {"underlying": "ORCL", "expiry": "2026-12-18",
                      "option_type": "call", "strike": 60.0}
    assert options.parse_occ("nonsense") is None


@pytest.mark.unit
def test_a_call_delta_rises_with_moneyness_and_a_put_is_negative():
    far_otm = options.black_scholes_delta(100, 150, "2027-01-15", "2026-10-30", 0.3)
    atm = options.black_scholes_delta(100, 100, "2027-01-15", "2026-10-30", 0.3)
    itm = options.black_scholes_delta(100, 60, "2027-01-15", "2026-10-30", 0.3)
    assert far_otm < atm < itm
    assert 0.4 < atm < 0.6, "an ATM call delta is about half"
    assert -0.6 < options.black_scholes_delta(100, 100, "2027-01-15", "2026-10-30", 0.3,
                                              "put") < -0.4


@pytest.mark.unit
def test_the_multiplier_is_the_callers_business_not_the_greek():
    """A delta stays 0..1; the 100-share multiplier belongs to the position."""
    delta = options.black_scholes_delta(100, 100, "2027-01-15", "2026-10-30", 0.3)
    assert 0 < delta < 1


@pytest.mark.unit
@pytest.mark.parametrize("vol", [0.0, 0.0001, 9.0, None])
def test_an_unusable_volatility_yields_no_delta(vol):
    assert options.black_scholes_delta(100, 100, "2027-01-15", "2026-10-30", vol) is None


@pytest.mark.unit
def test_an_expired_or_invalid_contract_yields_no_delta():
    assert options.black_scholes_delta(100, 100, "2026-10-30", "2026-10-30", 0.3) is None
    assert options.black_scholes_delta(0, 100, "2027-01-15", "2026-10-30", 0.3) is None


@pytest.mark.unit
def test_resolve_builds_a_record_with_delta_from_the_chain():
    contract = {"underlying": "ORCL", "expiry": "2026-12-18", "option_type": "call",
                "strike": 140.0, "underlying_price": 140.0, "implied_volatility": 0.35,
                "multiplier": 100.0, "source": "yahoo/ORCL/option_chain"}
    record = options.resolve_option("ORCL261218C00140000", as_of="2026-10-30",
                                    fetch=lambda symbol, as_of: contract)
    assert record["kind"] == "option"
    assert record["underlying"] == "ORCL"
    assert record["delta"] == pytest.approx(0.53, abs=0.08)
    assert record["multiplier"] == 100.0


@pytest.mark.unit
def test_a_missing_chain_is_reported_as_no_record():
    assert options.resolve_option("ORCL261218C00100000", as_of="2026-10-30",
                                  fetch=lambda symbol, as_of: None) is None


@pytest.mark.unit
def test_a_premium_inverts_to_the_volatility_that_produced_it():
    price = options.black_scholes_price(140.0, 140.0, "2026-11-20", "2026-10-30", 0.35)
    recovered = options.implied_volatility(price, 140.0, 140.0, "2026-11-20", "2026-10-30")
    assert recovered == pytest.approx(0.35, abs=1e-3)


@pytest.mark.unit
def test_a_zero_vendor_volatility_is_replaced_by_the_inverted_one():
    contract = {"underlying": "ORCL", "expiry": "2026-11-20", "option_type": "call",
                "strike": 140.0, "underlying_price": 139.56, "implied_volatility": 0.0,
                "bid": 0.0, "ask": 0.0, "last_price": 7.35, "multiplier": 100.0,
                "source": "yahoo/ORCL/option_chain"}
    record = options.resolve_option("ORCL261120C00140000", as_of="2026-10-30",
                                    fetch=lambda symbol, as_of: contract)
    assert record["iv_source"] == "derived_from_price"
    assert 0.2 < record["implied_volatility"] < 1.5
    assert 0.3 < record["delta"] < 0.8


@pytest.mark.unit
def test_a_contract_without_any_price_stays_unresolved():
    thin = {"underlying": "ORCL", "expiry": "2026-11-20", "option_type": "call",
            "strike": 140.0, "underlying_price": 139.56, "implied_volatility": 0.0,
            "bid": 0.0, "ask": 0.0, "last_price": 0.0}
    assert options.resolve_option("ORCL261120C00140000", as_of="2026-10-30",
                                  fetch=lambda symbol, as_of: thin) is None
