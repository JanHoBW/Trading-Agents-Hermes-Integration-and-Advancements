"""Rates are facts with a date; a missing one is reported, never assumed to be 1."""

from __future__ import annotations

import json

import pytest

from tradingagents import fx


@pytest.mark.unit
def test_pair_names_and_symbols():
    assert fx.pair_name("usd", "eur") == "USDEUR"
    assert fx.yahoo_symbol("USD", "EUR") == "USDEUR=X"


@pytest.mark.unit
def test_an_identity_rate_is_one_without_a_quote():
    rate = fx.rate_for("EUR", "eur", {})
    assert rate["rate"] == 1.0 and rate["source"] == "identity"


@pytest.mark.unit
def test_the_inverse_pair_is_used_when_only_it_is_quoted():
    rates = {"EURUSD": {"pair": "EURUSD", "rate": 1.25, "as_of": "2026-10-30"}}
    rate = fx.rate_for("USD", "EUR", rates)
    assert rate["rate"] == pytest.approx(0.8)
    assert "inverse" in rate["source"]
    assert fx.convert(100.0, rate) == pytest.approx(80.0)


@pytest.mark.unit
def test_no_rate_means_no_conversion():
    assert fx.rate_for("USD", "EUR", {}) is None
    assert fx.convert(100.0, None) is None


@pytest.mark.unit
def test_resolve_fetches_stale_pairs_only(tmp_path):
    cache = tmp_path / "fx.json"
    calls = []

    def fetch(base, quote, as_of):
        calls.append((base, quote))
        return {"pair": "USDEUR", "base": "USD", "quote": "EUR", "rate": 0.89,
                "as_of": as_of, "source": "yahoo/USDEUR=X"}

    first = fx.resolve([("USD", "EUR")], as_of="2026-10-30", cache_path=cache, fetch=fetch)
    assert calls == [("USD", "EUR")]
    assert first["USDEUR"]["rate"] == 0.89
    assert json.loads(cache.read_text())["rates"]["USDEUR"]["as_of"] == "2026-10-30"

    fx.resolve([("USD", "EUR")], as_of="2026-10-30", cache_path=cache, fetch=fetch)
    assert calls == [("USD", "EUR")], "a fresh rate must not be refetched"

    fx.resolve([("USD", "EUR")], as_of="2026-11-05", cache_path=cache, fetch=fetch)
    assert calls == [("USD", "EUR"), ("USD", "EUR")]


@pytest.mark.unit
def test_a_failed_fetch_keeps_the_old_rate(tmp_path):
    cache = tmp_path / "fx.json"
    fx.resolve([("USD", "EUR")], as_of="2026-10-30", cache_path=cache,
               fetch=lambda b, q, a: {"pair": "USDEUR", "base": "USD", "quote": "EUR",
                                      "rate": 0.89, "as_of": a, "source": "x"})
    kept = fx.resolve([("USD", "EUR")], as_of="2026-11-05", cache_path=cache,
                      fetch=lambda b, q, a: None)
    assert kept["USDEUR"]["rate"] == 0.89
    assert kept["USDEUR"]["as_of"] == "2026-10-30", "an old rate keeps its date"


@pytest.mark.unit
def test_needed_pairs_skip_the_account_currency_and_duplicates():
    pairs = fx.needed_pairs(["USD", "EUR", "GBP", "usd"], "EUR")
    assert pairs == [("USD", "EUR"), ("GBP", "EUR")]
    assert fx.needed_pairs(["USD"], None) == []


@pytest.mark.unit
def test_cached_rates_returns_only_what_was_asked_for(tmp_path):
    cache = tmp_path / "fx.json"
    fx.resolve([("USD", "EUR"), ("GBP", "EUR")], as_of="2026-10-30", cache_path=cache,
               fetch=lambda b, q, a: {"pair": f"{b}{q}", "base": b, "quote": q,
                                      "rate": 1.0, "as_of": a, "source": "x"})
    only_usd = fx.cached_rates(["USD"], "EUR", cache_path=cache)
    assert set(only_usd) == {"USDEUR"}
