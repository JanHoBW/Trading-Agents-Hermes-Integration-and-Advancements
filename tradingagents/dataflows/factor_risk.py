"""Observational covariance factor risk, independent of the snapshot consumer.

The fitter and renderer perform no I/O. Pass current_time explicitly for pure,
clock-free replay. Only load_factor_inputs may invoke an injected batch loader.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

import numpy as np
import pandas as pd

FACTOR_NAMES = ("benchmark", "fx", "rate")
MIN_OBS = 60
CURRENCY_KEYS = ("asset_currency", "currency_pair", "currency_context_source")


def _unavailable(reason):
    return {
        "status": "unavailable", "reason": reason, "n_obs": 0,
        "sample_start": None, "sample_end": None, "beta": None,
        "correlation": None, "beta_ci95": None, "scenario": None,
        "warnings": [], "coverage": {},
    }


def _effective_cutoff(as_of, current_time):
    current = pd.Timestamp.now(tz="UTC") if current_time is None else pd.Timestamp(current_time)
    current = current.tz_localize("UTC") if current.tzinfo is None else current.tz_convert("UTC")
    requested = pd.Timestamp(as_of)
    if pd.isna(requested) or pd.isna(current):
        raise ValueError("invalid_cutoff")
    return min(requested.tz_localize(None).normalize(), current.tz_localize(None).normalize())


def _nonempty_text(value):
    return isinstance(value, str) and bool(value.strip())


def _metadata_reason(name, meta):
    if name == "rate":
        if meta.get("input_type") not in (None, "yield_percent"):
            return "unsupported_rate_input_type"
        if (meta.get("input_type") != "yield_percent" or meta.get("units") != "percent"
                or not _nonempty_text(meta.get("units_source"))):
            return "rate_units_not_documented"
    elif meta.get("input_type", "price") != "price":
        return "unsupported_input_type"
    if name == "fx" and not all(_nonempty_text(meta.get(k)) for k in CURRENCY_KEYS):
        return "currency_context_not_documented"
    return None


def _levels(data, selected_column=None):
    if data is None:
        raise ValueError("missing_asset_input")
    state = "unknown_close_adjustment"
    if isinstance(data, pd.Series):
        series, column = data.copy(), data.name
    elif isinstance(data, pd.DataFrame):
        columns = {str(c).lower(): c for c in data.columns}
        column = (columns.get(str(selected_column).lower()) if selected_column is not None
                  else columns.get("adj close", columns.get("close")))
        if column is None:
            raise ValueError("missing_price_column")
        dates = data[columns["date"]].to_numpy() if "date" in columns else data.index
        series = pd.Series(data[column].to_numpy(), index=dates)
        if str(column).lower() == "adj close":
            state = "adjusted_close"
    else:
        raise ValueError("unsupported_data_container")
    if str(column).lower() == "adj close":
        state = "adjusted_close"
    elif data.attrs.get("auto_adjust") is True:
        state = "provider_declared_auto_adjusted_close"
    return series, {"price_column": str(column) if column is not None else None,
                    "adjustment_state": state}


def _session_dates(index):
    # Daily timestamps are venue-local session labels, not simultaneous instants.
    dates = []
    for value in index:
        try:
            stamp = pd.Timestamp(value)
            dates.append(pd.NaT if pd.isna(stamp) else stamp.tz_localize(None).normalize())
        except (ValueError, TypeError):
            dates.append(pd.NaT)
    return pd.DatetimeIndex(dates)


def _cut_levels(series, cutoff, *, positive_only=True):
    series.index = _session_dates(series.index)
    series = series.loc[series.index.notna() & (series.index <= cutoff)].sort_index()
    if series.index.has_duplicates:
        raise ValueError("duplicate_session_dates")
    series = pd.to_numeric(series, errors="coerce").replace([np.inf, -np.inf], np.nan)
    # Do not drop missing price rows before differencing: that would bridge gaps.
    return series.where(series > 0) if positive_only else series


def _intervals(levels, difference=False):
    values = levels.diff() if difference else levels.pct_change(fill_method=None)
    starts = pd.Series(levels.index, index=levels.index).shift()
    values.index = pd.MultiIndex.from_arrays([starts, levels.index], names=["start", "end"])
    return values.replace([np.inf, -np.inf], np.nan).dropna()


def fit_factor_risk(ticker, as_of, asset_ohlcv, factors=None, *, factor_metadata=None,
                    current_time=None):
    """Return JSON-compatible evidence from separate intercept-bearing covariance fits.

    factors maps benchmark/fx/rate to Series or daily DataFrames. Price frames
    prefer Adj Close over Close (case insensitive); rate Series are yield levels,
    never percent changes. Date column or index supplies daily session labels.
    No factor selection, unit conversion, gap filling or data fetching is inferred.
    """
    cutoff = _effective_cutoff(as_of, current_time)
    result = {
        "ticker": ticker, "as_of": str(as_of), "effective_cutoff": cutoff.strftime("%Y-%m-%d"),
        "asset": {}, "factors": {name: _unavailable("missing_input") for name in FACTOR_NAMES},
    }
    try:
        asset, asset_info = _levels(asset_ohlcv)
        asset = _cut_levels(asset, cutoff)
        result["asset"] = asset_info
    except (ValueError, KeyError, TypeError) as exc:
        result["factors"] = {name: _unavailable(str(exc)) for name in FACTOR_NAMES}
        return result
    y = _intervals(asset)
    for name in FACTOR_NAMES:
        out = result["factors"][name]
        out["coverage"] = {"asset_intervals": len(y), "factor_intervals": 0,
                           "unaligned_asset_intervals": len(y), "aligned_fraction": 0.0}
        data = (factors or {}).get(name)
        if data is None:
            continue
        meta = (factor_metadata or {}).get(name, {})
        reason = _metadata_reason(name, meta)
        if reason:
            out["reason"] = reason
            continue
        try:
            levels, info = _levels(data, meta.get("column") if name == "rate" else None)
            levels = _cut_levels(levels, cutoff, positive_only=name != "rate")
        except (ValueError, KeyError, TypeError) as exc:
            out["reason"] = str(exc)
            continue
        x = _intervals(levels, difference=name == "rate")
        info["input_type"] = "yield_percent" if name == "rate" else "price"
        info["beta_units"] = ("asset_decimal_return_per_percentage_point" if name == "rate"
                              else "asset_decimal_return_per_factor_decimal_return")
        if name == "rate":
            info.update(adjustment_state="not_applicable_yield_levels", units_source=meta["units_source"])
        if name == "fx":
            info["currency_context"] = {key: meta[key] for key in CURRENCY_KEYS}
        for key in ("symbol", "source", "region"):
            if key in meta:
                info[key] = meta[key]
        # Both interval endpoints must match. Never compare a multi-day factor
        # move with a one-day asset return, or forward-fill across market holidays.
        aligned = pd.concat([y.rename("y"), x.rename("x")], axis=1).dropna()
        n = len(aligned)
        ends = pd.DatetimeIndex(aligned.index.get_level_values("end"))
        out.update(
            info, n_obs=n, sample_start=ends.min().strftime("%Y-%m-%d") if n else None,
            sample_end=ends.max().strftime("%Y-%m-%d") if n else None,
            sample_interval_start=(pd.Timestamp(aligned.index.get_level_values("start").min()).strftime("%Y-%m-%d")
                                   if n else None),
            coverage={"asset_intervals": len(y), "factor_intervals": len(x),
                      "unaligned_asset_intervals": len(y) - n,
                      "aligned_fraction": n / len(y) if len(y) else 0.0},
            warnings=["Historical sample association; uncertainty does not establish causality.",
                      "IID OLS normal interval may understate uncertainty under serial correlation or heteroskedasticity.",
                      "Venue session labels do not establish synchronized closing times; no horizon forecast."],
        )
        if n < MIN_OBS:
            out["reason"] = "insufficient_sample"
            continue
        covariance = np.cov(aligned.x, aligned.y, ddof=1)
        if not np.isfinite(covariance).all() or min(covariance[0, 0], covariance[1, 1]) <= 1e-16:
            out["reason"] = "singular_sample"
            continue
        beta = float(covariance[0, 1] / covariance[0, 0])
        correlation = float(aligned.x.corr(aligned.y))
        xc = aligned.x.to_numpy() - aligned.x.mean()
        residual = aligned.y.to_numpy() - aligned.y.mean() - beta * xc
        se = float(np.sqrt(np.sum(residual ** 2) / (n - 2) / np.sum(xc ** 2)))
        if not np.isfinite([beta, correlation, se]).all() or abs(beta) <= 1.96 * se:
            out["reason"] = "unreliable_estimate"
            out["warnings"].append("Beta 95% interval includes zero or estimate is nonfinite; not evidence of no exposure.")
            continue
        out.update(status="available", reason=None, beta=beta, correlation=correlation,
                   beta_ci95=[beta - 1.96 * se, beta + 1.96 * se],
                   uncertainty_method="ols_iid_normal_approximation_95pct")
        if name in ("benchmark", "rate"):
            shock = -.10 if name == "benchmark" else 1.0
            outside = not float(aligned.x.min()) <= shock <= float(aligned.x.max())
            out["scenario"] = {
                "factor_shock": shock, "asset_return_proxy": beta * shock,
                "label": "index return -10%" if name == "benchmark" else "rate +100 bp",
                "interpretation": "observational_linear_proxy_not_causal_forecast",
                "outside_observed_factor_range": outside,
                "horizon": "same_return_interval_only_not_a_horizon_forecast",
            }
            if outside:
                out["warnings"].append("Stress shock is outside observed factor moves: linear extrapolation, not validated stress forecasting.")
    return result


def load_factor_inputs(as_of, specs=None, *, batch_loader=None, already_fetched=None,
                       current_time=None, lookback_days=400):
    """At most one Yahoo-compatible batch for <=3 explicitly configured factors.

    No default regional benchmark, FX or rate symbols. Callback receives download
    kwargs; returns a Yahoo frame or a symbol -> Series/DataFrame mapping. A batch
    invocation is not a claim that Yahoo internally makes only one HTTP request.
    """
    cutoff = _effective_cutoff(as_of, current_time)
    specs = specs or {}
    loaded = {"factors": dict(already_fetched or {}), "factor_metadata": {},
              "coverage": {}, "batch_calls": 0}
    pending = {}
    for name in FACTOR_NAMES:
        meta = dict(specs.get(name, {}))
        loaded["factor_metadata"][name] = meta
        if name in loaded["factors"]:
            loaded["coverage"][name] = "already_fetched"
            continue
        reason = _metadata_reason(name, meta)
        if reason:
            loaded["coverage"][name] = reason
        elif not meta.get("symbol"):
            loaded["coverage"][name] = "not_configured"
        elif (not isinstance(meta["symbol"], str)
              or re.fullmatch(r"[A-Za-z0-9^][A-Za-z0-9.^=_-]{0,31}", meta["symbol"]) is None):
            loaded["coverage"][name] = "invalid_factor_symbol"
        elif batch_loader is None:
            loaded["coverage"][name] = "loader_not_supplied"
        else:
            pending[name] = meta["symbol"]
    if not pending:
        return loaded
    window = max(90, min(int(lookback_days), 730))
    loaded["batch_calls"] = 1
    try:
        batch = batch_loader(tickers=list(dict.fromkeys(pending.values())),
                             start=(cutoff - pd.Timedelta(days=window)).strftime("%Y-%m-%d"),
                             end=(cutoff + pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
                             auto_adjust=False, threads=False, progress=False, timeout=10)
        for name, symbol in pending.items():
            if isinstance(batch, Mapping):
                frame = batch.get(symbol)
            elif isinstance(batch, pd.DataFrame) and isinstance(batch.columns, pd.MultiIndex):
                frame = None
                for level in range(batch.columns.nlevels):
                    if symbol in batch.columns.get_level_values(level):
                        frame = batch.xs(symbol, axis=1, level=level).copy()
                        break
            else:
                frame = batch if len(pending) == 1 else None
            if frame is None or frame.empty:
                loaded["coverage"][name] = "empty_or_missing_batch_series"
            else:
                loaded["factors"][name] = frame
                loaded["coverage"][name] = "loaded"
    except Exception as exc:  # Optional enrichment must never abort the market snapshot.
        for name in pending:
            loaded["coverage"][name] = f"loader_failed:{type(exc).__name__}"
    return loaded


def render_factor_risk(result):
    """Compact evidence block; wrap as untrusted at the consumer seam."""
    lines = ["### Observational factor risk (not a causal forecast)",
             f"- Cutoff: {result['effective_cutoff']}; asset adjustment: "
             f"{result['asset'].get('adjustment_state', 'unavailable')}",
             "- Separate historical covariance fits, not additive exposures. No equity duration inferred."]
    for name in FACTOR_NAMES:
        fit = result["factors"][name]
        sample = f"n={fit['n_obs']}, {fit['sample_start'] or 'N/A'} -> {fit['sample_end'] or 'N/A'}"
        coverage = fit["coverage"]
        sample += f"; unaligned={coverage.get('unaligned_asset_intervals', 0)}/{coverage.get('asset_intervals', 0)}"
        if fit["status"] != "available":
            lines.append(f"- {name}: unavailable ({fit['reason']}); {sample}")
            if fit["warnings"]:
                lines.append("  " + " ".join(fit["warnings"]))
            continue
        ci = fit["beta_ci95"]
        lines.append(f"- {name}: beta={fit['beta']:.3f}, corr={fit['correlation']:.3f}, "
                     f"95% beta interval=[{ci[0]:.3f}, {ci[1]:.3f}]; {sample}; "
                     f"units={fit['beta_units']}; adjustment={fit['adjustment_state']}")
        if fit.get("units_source"):
            lines.append(f"  Units evidence: {fit['units_source']}")
        if fit.get("currency_context"):
            lines.append(f"  Currency context: {fit['currency_context']}")
        if fit["scenario"]:
            scenario = fit["scenario"]
            lines.append(f"  {scenario['label']}: historical linear proxy "
                         f"{scenario['asset_return_proxy']:.2%}; not a causal forecast or price target.")
        lines.append("  " + " ".join(fit["warnings"]))
    return "\n".join(lines)
