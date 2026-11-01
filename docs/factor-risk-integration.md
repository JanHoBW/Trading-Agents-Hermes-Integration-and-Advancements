# Factor risk: existing market-snapshot integration

`run_snapshot.RunSnapshot` captures factor estimates beside the existing adjusted
Yahoo price input. The market analyst's verified snapshot renders the compact
block; it flows through the existing research/trader/risk roles. There is no new
agent, tool or LLM call.

## Policy and request budget

- `factor_risk_enabled = true` by default. Opt out with
  `TRADINGAGENTS_FACTOR_RISK_ENABLED=false`; the wrapper already forwards this
  namespace. The environment choice affects the schema-2 replay fingerprint.
- The benchmark reuses `benchmark_ticker` / `benchmark_map` from the existing
  settlement policy. The displayed benchmark is a **configured proxy**, not a
  claim of geographic or holdings-match. A German-listed pan-European ETF's
  DAX comparison does not replace constituent look-through.
- `factor_risk_inputs` may explicitly configure benchmark, FX and rate metadata.
  FX requires documented asset currency, currency pair and evidence source;
  rates require yield-percent units and their source. None is inferred from a
  listing suffix. Missing FX/rate evidence remains unavailable.
- A fresh run makes at most one Yahoo-compatible factor batch invocation, using
  the bounded loader window. Loader and fitter share the exact UTC price-capture
  timestamp and consume unfilled asset bars. Yahoo may make several HTTP requests internally.
  A checkpoint resume uses the stored estimate and does not refetch factors.
- A failed optional enrichment cannot invalidate valid price rows. Its error is
  recorded in snapshot metadata and rendered as unavailable.

## Artifacts and interpretation

`market_snapshot.json` and its sizing mirror contain `factor_risk`, with sample
counts/dates, input coverage, units, adjustment state, estimate/interval and
observational stress proxy. `factor_risk_sha256` detects stored-result changes
on reconstruction; the input fingerprints identify the fetched factor frames.
The full factor bars are not archived here: this is stored-result reuse, not a
complete factor-evidence vintage replay. OHLCV price rows remain fully stored.

Separate bivariate fits are not additive portfolio exposures. Their IID OLS
normal intervals may understate uncertainty under dependence; venue session-date
alignment is not synchronized market closing. A large stress outside observed
moves is extrapolation, not a validated forecast. No equity duration, causal
rate sensitivity, expected return or target price is inferred.

## Verified offline contracts

`tests/test_factor_risk_consumer.py` traces a synthetic factor with beta near 1.2
through capture → artifact → existing market prompt → checkpoint reconstruction.
It verifies one batch, cutoff forwarding, stored-result tamper rejection,
explicit policy/opt-out and price preservation on an optional fitter failure.
The pure fitter's independent tests live in `tests/test_factor_risk.py`.
