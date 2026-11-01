# Observational factor-risk evidence

`tradingagents/dataflows/factor_risk.py` adds deterministic covariance analytics.
The pure module performs no graph/tool/model changes. Its current consumer
integration is in `run_snapshot.RunSnapshot` and the existing Yahoo market
snapshot; see [integration policy and artifacts](factor-risk-integration.md).
There are no holdings, order APIs, new agents or LLM calls.

## Public API

```python
fit_factor_risk(
    ticker, as_of, asset_ohlcv, factors=None, *,
    factor_metadata=None, current_time=None,
) -> dict

load_factor_inputs(
    as_of, specs=None, *, batch_loader=None, already_fetched=None,
    current_time=None, lookback_days=400,
) -> dict

render_factor_risk(result) -> str
```

The fitter and renderer do no I/O. For strictly pure/replayable evaluation, pass
an explicit run-frozen `current_time`. If omitted, the only environmental input
is the current UTC clock used defensively to reject future session dates.

`asset_ohlcv` is an **already fetched** Series or daily OHLCV DataFrame. `factors`
maps any of `benchmark`, `fx`, `rate` to Series/DataFrames; missing factors stay
unavailable. DataFrames accept a Date/date column or date index and prefer
Adj Close/adj close over Close/close. Series use their date index. Rate frames
may specify a `column` in their metadata (e.g. `yield`); otherwise use Close or
supply a Series. No arbitrary column or rate conversion is inferred.

### Metadata contract

- Benchmark: price levels; `input_type="price"` is the default. Optional `symbol`,
  `source`, `region` are retained. The caller explicitly selects a suitable regional
  index. No US/global index is automatically substituted for an EU instrument.
- FX: price levels, and **all** of `asset_currency`, `currency_pair`,
  `currency_context_source` must document the quotation/context. A currency code
  or ticker suffix alone is not proof of economic/revenue exposure. The result
  is historical quote-return co-movement, not a causal currency earnings model.
- Rate: only `input_type="yield_percent"`, `units="percent"`, and nonempty textual
  `units_source` are accepted. Example: levels `3.50 -> 3.60` are a `0.10`
  percentage-point move. `+100 bp` is exactly `+1.0` percentage point. Yield
  decimals, bp levels, bond-price/ETF returns and undocumented Yahoo rate-proxy
  scaling are rejected, not guessed/converted. No default US rate proxy exists.
  Choose an appropriate regional series explicitly; document units and source.

`units_source`/currency documentation are caller-supplied provenance assertions,
not independent verification by the fitter. Verify them before constructing the
metadata; do not populate them with guesses to unlock a number.

## Cutoff, alignment and adjustment

- Effective cutoff is `min(as_of session date, current_time UTC date)`, inclusive.
  The Yahoo loader sends `end=cutoff+1 day`, because Yahoo's end is exclusive;
  the fitter independently cuts asset and each factor before differencing.
- Normalize vendor daily timestamps to their **venue-local session date** by
  removing timezone without converting dates to UTC. Current clock timestamps
  are converted to UTC first. Session labels are not evidence of simultaneous
  closing times or quote availability. Parent must exclude incomplete/unavailable
  bars at source; this module does not determine whether today's close is final.
- Sorted duplicate session dates at/before the cutoff are rejected. Future
  duplicates cannot poison a historical fit. Invalid dates, nonfinite values,
  nonpositive price levels and missing cells do not become zero returns.
  Zero/negative yield levels are allowed. No forward/back filling.
- Simple price returns are `P(t)/P(previous)-1`. Rates use absolute yield
  differences. Intervals are matched on **both** start and end session dates,
  not only the end date. This rejects mismatched one-/two-day returns across
  holidays/gaps. Return intervals within each factor sample do not overlap.
- `n_obs`, `sample_start` (first aligned return's end), `sample_end`, and
  `sample_interval_start` (its opening price date) describe the actual aligned
  sample. `coverage` reports usable asset/factor intervals, unaligned asset
  intervals and aligned fraction. Different factors can have different samples;
  estimates must not be added together as multivariate risk exposures.
- Adjustment state is explicit: `adjusted_close`,
  `provider_declared_auto_adjusted_close` (only `attrs["auto_adjust"] is True),
  `unknown_close_adjustment`, or `not_applicable_yield_levels`. Unknown Close is
  not silently called adjusted. Adjusted history retrieved today may be revised
  for later corporate actions; the date cutoff is **not** a guarantee of
  historically archived point-in-time data. Freeze raw inputs/provenance for replay.

## Estimates and limitations

Each factor is a separate intercept-bearing bivariate association:

```
beta = sample_cov(asset_return, factor_move) / sample_var(factor_move)
correlation = ordinary Pearson correlation on the same aligned observations
SE(beta) = sqrt(SSE / (n - 2) / sum((factor_move - mean)^2))
approximate beta 95% interval = beta +/- 1.96 * SE
```

No scipy/statsmodels dependency. The interval assumes IID residuals and is a
normal approximation, not a robust/HAC or bootstrap interval. Serial correlation,
heteroskedasticity, asynchronous closes, omitted factors and regime changes can
make it optimistic. Warnings remain visible even when a fit is available.

Estimates/scenarios are unavailable for missing/invalid inputs, `n < 60`,
nonfinite covariance, asset/factor variance `<= 1e-16`, or a beta interval including
zero/nonfinite estimate. The zero-including rule is a conservative availability
gate, **not evidence that exposure is zero**. Unavailable output has `beta`,
`correlation`, `beta_ci95`, `scenario` set to `None`, not fabricated fallbacks.
Sample counts/periods remain available when the alignment could be evaluated.

For an available benchmark fit: `index return -10% -> beta * -0.10`.
For a valid/documented percent-yield fit: `rate +100 bp -> beta * 1.0`.
These are observational linear proxy responses for the fitted return interval,
**not causal scenario forecasts, multi-month forecasts, fair values or price
targets**. Shocks outside the observed factor-move range are explicitly marked
as extrapolation. FX beta is reported only with documented currency context;
there is no invented FX stress definition. No equity duration, P/E multiple
compression, or valuation-to-rate elasticity is inferred.

## Existing consumer integration

The run-local price store captures the optional batch and fit once, from its
unfilled Yahoo asset bars. Loader and fitter receive the same UTC price-capture
timestamp. The existing market snapshot renders the stored evidence; the usual
untrusted-source wrapping remains in place, with no new agent or LLM call.

The parent integration reuses the existing configured benchmark policy. FX and
rate series still need explicit currency/unit evidence. Coverage and factor-input
fingerprints are retained; the artifact contains the stored estimate and its
integrity hash. Checkpoint reconstruction verifies the stored result rather than
refetching factors. Full factor bars are not archived, so result reuse is not a
complete evidence-vintage replay. A configured regional-index proxy does not
claim geographic or portfolio-holdings equivalence.

For other consumers, the independent module API remains usable directly:

```python
loaded = load_factor_inputs(
    as_of_date, factor_specs, batch_loader=yf.download,
    already_fetched=prefetched_factors, current_time=snapshot_time,
)
evidence = fit_factor_risk(
    symbol, as_of_date, raw_asset_bars, loaded["factors"],
    factor_metadata=loaded["factor_metadata"], current_time=snapshot_time,
)
lines.extend(["", render_factor_risk(evidence)])
```

One bounded loader batch invocation can entail multiple Yahoo HTTP requests.
The consumer integration and request/error/reconstruction contracts are covered
by `tests/test_factor_risk_consumer.py`.

## Verification

Offline deterministic tests:

```sh
env -u PYTHONPATH .venv/bin/python -m pytest tests/test_factor_risk.py -q
```

Final targeted verification: **41 passed**, plus successful `py_compile` for the
module and test file. The parent separately installed the CI Ruff version,
verified the consumer integration and performed combined regression checks.
Final combined-suite/CI status is recorded in the implementation handoff.

RED failures were observed before each implemented behavioral slice, followed
by GREEN runs. Synthetic fixtures cover sample gating, covariance, uncertainty,
cutoffs/timezones, interval endpoints, missing/duplicate data, adjustment,
currency/unit documentation, bounded loading and rendering.

One optional public-data check was actually run on 2026-11-01 via one
`yfinance.download` invocation for `SAP.DE`, `^GDAXI`, `EURUSD=X`, history 2025-01-01
through exclusive 2026-01-01. It returned 257 union rows. The actual aligned
SAP/DAX sample was 252 returns, 2025-01-03..2025-12-30 (opening price 2025-01-02),
beta 1.1879142156, correlation 0.7161133127, approximate beta interval
[1.0443867908, 1.3314416404]. Both used Adj Close. The index -10% proxy was
-11.8791421558%, explicitly outside observed moves and not a forecast.
FX had 250 aligned observations but was unavailable under the uncertainty gate;
rates were unavailable/missing. These are verified retrieval-time results,
not offline fixtures or integration evidence. No rate source or units were guessed.
