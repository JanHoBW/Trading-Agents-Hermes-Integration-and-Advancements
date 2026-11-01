"""Deterministic synthetic fixtures; no network or LLM calls."""

import importlib.util

import numpy as np
import pandas as pd
import pytest

pytestmark = pytest.mark.unit


def api():
    from tradingagents.dataflows.factor_risk import fit_factor_risk
    return fit_factor_risk


def prices(returns, dates=None):
    dates = pd.bdate_range("2025-01-02", periods=len(returns) + 1) if dates is None else dates
    return pd.DataFrame({"Date": dates, "Close": 100 * np.cumprod(np.r_[1, 1 + returns])})


def fixture_pair(n=130):
    rng = np.random.default_rng(2026)
    x = rng.normal(0, .01, n)
    return prices(1.5 * x), prices(x), x


def test_covariance_beta_uses_actual_aligned_return_sample():
    assert importlib.util.find_spec("tradingagents.dataflows.factor_risk") is not None
    asset, benchmark, x = fixture_pair()
    result = api()("SYNTH", "2025-12-31", asset, {"benchmark": benchmark})
    fit = result["factors"]["benchmark"]
    assert fit["status"] == "available"
    assert fit["n_obs"] == len(x)
    assert fit["sample_start"] == asset.Date.iloc[1].strftime("%Y-%m-%d")
    assert fit["sample_end"] == asset.Date.iloc[-1].strftime("%Y-%m-%d")
    assert fit["beta"] == pytest.approx(1.5)
    assert fit["correlation"] == pytest.approx(1.0)
    assert fit["beta_ci95"][0] == pytest.approx(1.5)
    assert fit["scenario"]["asset_return_proxy"] == pytest.approx(-.15)
    assert fit["scenario"]["factor_shock"] == -.10
    assert fit["scenario"]["interpretation"] == "observational_linear_proxy_not_causal_forecast"
    assert "sample" in " ".join(fit["warnings"]).lower()
    assert result["factors"]["fx"]["reason"] == "missing_input"
    assert result["factors"]["rate"]["beta"] is None
    assert result["asset"]["adjustment_state"] == "unknown_close_adjustment"


@pytest.mark.parametrize("as_of,current_time,expected_n", [
    ("2025-04-01", "2025-12-31T23:00:00Z", 63),
    ("2026-01-01", "2025-04-01T23:00:00Z", 63),
])
def test_session_timezone_normalization_cuts_as_of_and_current(as_of, current_time, expected_n):
    asset, benchmark, _ = fixture_pair()
    asset["Adj Close"] = asset.Close
    asset["Close"] = 1.0
    asset.Date = pd.DatetimeIndex(asset.Date).tz_localize("Europe/Berlin")
    benchmark.Date = pd.DatetimeIndex(benchmark.Date).tz_localize("America/New_York")
    before = asset.copy(deep=True)
    result = api()("SYNTH", as_of, asset, {"benchmark": benchmark}, current_time=current_time)
    fit = result["factors"]["benchmark"]
    assert fit["n_obs"] == expected_n
    assert fit["sample_end"] == "2025-04-01"
    assert fit["beta"] == pytest.approx(1.5)
    assert result["asset"]["adjustment_state"] == "adjusted_close"
    assert result["effective_cutoff"] == "2025-04-01"
    pd.testing.assert_frame_equal(asset, before)


def test_alignment_requires_identical_nonoverlapping_start_and_end_dates():
    asset, benchmark, _ = fixture_pair()
    # Missing factor sessions must not compare its two-day return to a one-day asset return.
    benchmark = benchmark.drop(index=[10, 30, 50]).reset_index(drop=True)
    result = api()("SYNTH", "2025-12-31", asset, {"benchmark": benchmark})
    fit = result["factors"]["benchmark"]
    assert fit["n_obs"] == 124
    assert fit["beta"] == pytest.approx(1.5)
    assert fit["coverage"]["asset_intervals"] == 130
    assert fit["coverage"]["unaligned_asset_intervals"] == 6


@pytest.mark.parametrize("case,reason", [
    ("short", "insufficient_sample"),
    ("constant_factor", "singular_sample"),
    ("constant_asset", "singular_sample"),
    ("unrelated", "unreliable_estimate"),
    ("duplicate", "duplicate_session_dates"),
    ("missing_asset", "missing_asset_input"),
    ("invalid_prices", "insufficient_sample"),
])
def test_unusable_samples_never_publish_beta(case, reason):
    asset, benchmark, _ = fixture_pair()
    if case == "short":
        benchmark = benchmark.iloc[:60]
    elif case == "constant_factor":
        benchmark.Close = 100.0
    elif case == "constant_asset":
        asset.Close = 100.0
    elif case == "unrelated":
        # Explicit orthogonal sample, not a random pair that may correlate by chance.
        x = np.random.default_rng(2026).normal(0, .01, 130)
        z = np.random.default_rng(9).normal(0, .01, 130)
        xc = x - x.mean()
        z = z - z.mean() - np.dot(z, xc) / np.dot(xc, xc) * xc
        asset = prices(z)
    elif case == "duplicate":
        benchmark = pd.concat([benchmark, benchmark.iloc[[0]]])
    elif case == "missing_asset":
        asset = None
    elif case == "invalid_prices":
        benchmark.Close = -100.0
    fit = api()("SYNTH", "2025-12-31", asset, {"benchmark": benchmark})["factors"]["benchmark"]
    assert fit["status"] == "unavailable"
    assert fit["reason"] == reason
    assert fit["beta"] is None
    assert fit["correlation"] is None
    assert fit["beta_ci95"] is None
    assert fit["scenario"] is None


@pytest.mark.parametrize("metadata,reason", [
    ({}, "rate_units_not_documented"),
    ({"input_type": "price", "units": "percent", "units_source": "synthetic fixture"}, "unsupported_rate_input_type"),
    ({"input_type": "yield_percent", "units": "basis_points", "units_source": "synthetic fixture"}, "rate_units_not_documented"),
    ({"input_type": "yield_percent", "units": "percent"}, "rate_units_not_documented"),
    ({"input_type": "yield_percent", "units": "percent", "units_source": "synthetic fixture"}, None),
])
def test_rate_100bp_requires_documented_percent_yield_levels(metadata, reason):
    rng = np.random.default_rng(42)
    changes = rng.normal(0, .03, 130)
    rate = pd.Series(np.r_[0, np.cumsum(changes)], index=pd.bdate_range("2025-01-02", periods=131))
    # Zero and negative yields are legitimate, unlike nonpositive equity prices.
    result = api()("SYNTH", "2025-12-31", prices(-.20 * changes), {"rate": rate},
                   factor_metadata={"rate": metadata})
    fit = result["factors"]["rate"]
    assert fit["reason"] == reason
    if reason:
        assert fit["beta"] is None
        assert fit["scenario"] is None
    else:
        assert fit["n_obs"] == 130
        assert fit["beta"] == pytest.approx(-.20)
        assert fit["beta_units"] == "asset_decimal_return_per_percentage_point"
        assert fit["scenario"]["factor_shock"] == 1.0
        assert fit["scenario"]["label"] == "rate +100 bp"
        assert fit["scenario"]["asset_return_proxy"] == pytest.approx(-.20)
        assert fit["units_source"] == "synthetic fixture"
        assert fit["adjustment_state"] == "not_applicable_yield_levels"
        assert "duration" not in result


@pytest.mark.parametrize("documented", [False, True])
def test_fx_requires_documented_currency_context(documented):
    asset, fx, _ = fixture_pair()
    meta = {"asset_currency": "EUR", "currency_pair": "EUR/USD",
            "currency_context_source": "synthetic fixture: USD per EUR"} if documented else {}
    fit = api()("SYNTH", "2025-12-31", asset, {"fx": fx},
                factor_metadata={"fx": meta})["factors"]["fx"]
    assert fit["status"] == ("available" if documented else "unavailable")
    if documented:
        assert fit["beta"] == pytest.approx(1.5)
        assert fit["currency_context"] == meta
        assert fit["scenario"] is None
    else:
        assert fit["reason"] == "currency_context_not_documented"


def test_optional_loader_batches_only_documented_missing_factors_once():
    from tradingagents.dataflows import factor_risk
    assert hasattr(factor_risk, "load_factor_inputs")
    asset, benchmark, _ = fixture_pair()
    calls = []
    specs = {"benchmark": {"symbol": "^GDAXI", "source": "Yahoo", "region": "Germany"},
             "fx": {"symbol": "EURUSD=X"}, "rate": {"symbol": "^TNX"}}

    def batch_loader(**kwargs):
        calls.append(kwargs)
        return {"^GDAXI": benchmark}

    loaded = factor_risk.load_factor_inputs("2025-12-31", specs, batch_loader=batch_loader,
                                           current_time="2025-12-31T23:00:00Z")
    assert len(calls) == loaded["batch_calls"] == 1
    assert calls[0]["tickers"] == ["^GDAXI"]
    assert calls[0]["end"] == "2026-01-01"  # Yahoo's exclusive bound.
    assert calls[0]["auto_adjust"] is False
    assert calls[0]["threads"] is False
    assert calls[0]["timeout"] == 10
    assert loaded["coverage"]["fx"] == "currency_context_not_documented"
    assert loaded["coverage"]["rate"] == "rate_units_not_documented"
    result = api()("SYNTH", "2025-12-31", asset, loaded["factors"],
                   factor_metadata=loaded["factor_metadata"])
    assert result["factors"]["benchmark"]["beta"] == pytest.approx(1.5)
    loaded = factor_risk.load_factor_inputs("2025-12-31", specs, batch_loader=batch_loader,
                                           already_fetched={"benchmark": benchmark})
    assert loaded["batch_calls"] == 0
    assert len(calls) == 1


def test_optional_loader_normalizes_yahoo_multiindex_and_never_retries():
    from tradingagents.dataflows import factor_risk
    assert hasattr(factor_risk, "load_factor_inputs")
    _, benchmark, _ = fixture_pair()
    history = benchmark.set_index("Date")
    history.columns = pd.MultiIndex.from_product([history.columns, ["^STOXX50E"]])
    specs = {"benchmark": {"symbol": "^STOXX50E"}}
    loaded = factor_risk.load_factor_inputs("2025-12-31", specs,
                                           batch_loader=lambda **kwargs: history)
    assert loaded["factors"]["benchmark"].Close.equals(history[("Close", "^STOXX50E")])
    calls = []

    def failed(**kwargs):
        calls.append(kwargs)
        raise TimeoutError("synthetic network failure")

    loaded = factor_risk.load_factor_inputs("2025-12-31", specs, batch_loader=failed)
    assert len(calls) == 1
    assert loaded["coverage"]["benchmark"] == "loader_failed:TimeoutError"
    assert not loaded["factors"]
    assert factor_risk.load_factor_inputs("2025-12-31", specs)["batch_calls"] == 0


def test_render_is_concise_and_exposes_samples_units_missingness_and_noncausality():
    from tradingagents.dataflows import factor_risk
    assert hasattr(factor_risk, "render_factor_risk")
    asset, benchmark, _ = fixture_pair()
    result = api()("SYNTH", "2025-12-31", asset, {"benchmark": benchmark})
    text = factor_risk.render_factor_risk(result)
    assert "Observational" in text
    assert "not a causal forecast" in text
    assert "n=130" in text
    assert "2025-01-03" in text
    assert "95%" in text
    assert "unknown_close_adjustment" in text
    assert "index return -10%" in text
    assert "-15.00%" in text
    assert "fx: unavailable (missing_input)" in text
    assert "rate: unavailable (missing_input)" in text
    assert "No equity duration" in text
    assert len(text) < 3000


def test_uncertainty_reports_sample_assumptions_and_stress_extrapolation():
    asset, benchmark, _ = fixture_pair()
    # Small independent residual avoids a zero-width interval.
    x = np.random.default_rng(2026).normal(0, .01, 130)
    noise = np.random.default_rng(8).normal(0, .001, 130)
    asset = prices(1.5 * x + noise)
    fit = api()("SYNTH", "2025-12-31", asset, {"benchmark": benchmark})["factors"]["benchmark"]
    assert fit["status"] == "available"
    assert fit["beta_ci95"][0] < fit["beta"] < fit["beta_ci95"][1]
    assert fit["uncertainty_method"] == "ols_iid_normal_approximation_95pct"
    assert fit["scenario"]["outside_observed_factor_range"] is True
    assert fit["scenario"]["horizon"] == "same_return_interval_only_not_a_horizon_forecast"
    assert "serial correlation" in " ".join(fit["warnings"])
    assert fit["coverage"]["aligned_fraction"] == 1.0
    assert fit["input_type"] == "price"


@pytest.mark.parametrize("case,reason", [
    ("empty", "missing_price_column"),
    ("invalid_dates", "insufficient_sample"),
    ("lowercase", None),
    ("known_adjusted", None),
    ("invalid_type", "unsupported_input_type"),
])
def test_input_normalization_reports_adjustment_and_malformed_coverage(case, reason):
    asset, benchmark, _ = fixture_pair()
    meta = {}
    if case == "empty":
        benchmark = pd.DataFrame()
    elif case == "invalid_dates":
        benchmark["Date"] = "invalid session"
    elif case == "lowercase":
        benchmark = benchmark.rename(columns={"Date": "date", "Close": "close"})
    elif case == "known_adjusted":
        benchmark.attrs["auto_adjust"] = True
    elif case == "invalid_type":
        meta = {"input_type": "yield_percent"}
    fit = api()("SYNTH", "2025-12-31", asset, {"benchmark": benchmark},
                factor_metadata={"benchmark": meta})["factors"]["benchmark"]
    assert fit["reason"] == reason
    if reason:
        assert fit["beta"] is None
    elif case == "known_adjusted":
        assert fit["adjustment_state"] == "provider_declared_auto_adjusted_close"
    else:
        assert fit["beta"] == pytest.approx(1.5)


def test_current_cutoff_uses_utc_day_not_the_callers_local_future_day():
    asset, benchmark, _ = fixture_pair()
    result = api()("SYNTH", "2026-12-31", asset, {"benchmark": benchmark},
                   current_time="2025-04-02T01:00:00+02:00")
    assert result["effective_cutoff"] == "2025-04-01"
    assert result["factors"]["benchmark"]["n_obs"] == 63


@pytest.mark.parametrize("proof", [None, "", "   ", 42])
def test_nontext_units_proof_cannot_enable_rate_scenario(proof):
    rate = prices(np.random.default_rng(4).normal(0, .01, 130))
    fit = api()("SYNTH", "2025-12-31", rate, {"rate": rate}, factor_metadata={
        "rate": {"input_type": "yield_percent", "units": "percent", "units_source": proof}
    })["factors"]["rate"]
    assert fit["reason"] == "rate_units_not_documented"
    assert fit["beta"] is None


def test_rate_dataframe_explicit_column_and_missing_level_does_not_bridge_gap():
    changes = np.random.default_rng(42).normal(0, .03, 130)
    asset = prices(-.20 * changes)
    rates = pd.DataFrame({"Date": asset.Date, "yield": np.r_[3, 3 + np.cumsum(changes)]})
    rates.loc[10, "yield"] = np.nan
    fit = api()("SYNTH", "2025-12-31", asset, {"rate": rates}, factor_metadata={
        "rate": {"input_type": "yield_percent", "units": "percent", "units_source": "synthetic fixture", "column": "yield"}
    })["factors"]["rate"]
    assert fit["n_obs"] == 128
    assert fit["beta"] == pytest.approx(-.20)
    assert fit["sample_interval_start"] == "2025-01-02"


def test_future_duplicate_rows_cannot_change_historical_fit():
    asset, benchmark, _ = fixture_pair()
    future = benchmark.iloc[[-1]].copy()
    future.Date = pd.Timestamp("2027-01-01")
    future.Close = 1e12
    polluted = pd.concat([benchmark, future, future])
    fit = api()("SYNTH", "2025-12-31", asset, {"benchmark": polluted})["factors"]["benchmark"]
    assert fit["beta"] == pytest.approx(1.5)
    assert fit["n_obs"] == 130


@pytest.mark.parametrize("symbol", ["AAPL MSFT", "AAPL,MSFT", " ^GDAXI", "^GDAXI ", 42])
def test_loader_rejects_malformed_symbol_without_normalizing_or_fetching(symbol):
    from tradingagents.dataflows.factor_risk import load_factor_inputs
    calls = []
    loaded = load_factor_inputs("2025-12-31", {"benchmark": {"symbol": symbol}},
                                batch_loader=lambda **kwargs: calls.append(kwargs))
    assert loaded["coverage"]["benchmark"] == "invalid_factor_symbol"
    assert loaded["batch_calls"] == 0
    assert not calls


def test_render_shows_unaligned_coverage_and_does_not_hide_failed_fit_warnings():
    from tradingagents.dataflows.factor_risk import render_factor_risk
    asset, benchmark, _ = fixture_pair()
    benchmark = benchmark.drop(index=[10, 30, 50])
    result = api()("SYNTH", "2025-12-31", asset, {"benchmark": benchmark})
    text = render_factor_risk(result)
    assert "unaligned=6/130" in text
    assert "IID OLS" in text


def test_none_currency_documentation_is_not_proof():
    asset, fx, _ = fixture_pair()
    fit = api()("SYNTH", "2025-12-31", asset, {"fx": fx}, factor_metadata={
        "fx": {"asset_currency": "EUR", "currency_pair": "EUR/USD", "currency_context_source": None}
    })["factors"]["fx"]
    assert fit["reason"] == "currency_context_not_documented"
