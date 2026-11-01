# Run snapshot integrity

## One price basis per run

A fresh `TradingAgentsGraph.create_run_state()` captures the target ticker's
adjusted Yahoo OHLCV once, before the agents run. The resulting artifact lives
in the existing `market_levels.snapshot` state field. Invoke and streaming
execution bind that artifact in a `ContextVar`; LangGraph propagates the context
to its parallel tool workers. A lock serializes the first capture; every reader
receives a separate DataFrame copy. Concurrent runs never share a mutable
market-data store.

The verified snapshot, routed Yahoo stock CSV, Yahoo stockstats indicators and
Trader sizing all read those same unfilled bars. Indicator calculations may be
repeated, but use the same input rows and stockstats implementation. Formatting
may round numbers for display; the stored artifact does not round prices.
There is no second live price lookup for the sizing close/ATR/ADV. Earlier tool
windows slice the frozen frame rather than fetching another price basis.

`dataflows/run_snapshot.py` installs stable compatibility dispatchers at the
existing Yahoo market loader and router CSV seams. The dispatchers contain no
market data, are installed under a lock, and retain legacy behavior outside a
run context. Explicitly injected/custom vendor implementations are not replaced.
These seams avoid editing the shared OHLCV, market and router modules.

**Scope:** this freezes the existing Yahoo snapshot path, not another vendor's
independent price/indicator service. Explicit Alpha Vantage overrides and
fallbacks remain their configured vendor, and can still differ in adjustment
basis. No configured provider is silently replaced. The separate settlement
price-history path is not frozen to today's analysis snapshot.

## Artifacts and metadata

`advisory.write_artifacts()` writes `market_snapshot.json` next to `sizing.json`
and `discipline.json`; sizing also contains `market_snapshot`. Its v1 schema
contains:

- `ohlcv`: all verified input rows, not just the latest displayed close;
- `metadata.symbol`, `requested_as_of`, `observed_at` (actual capture UTC time);
- `source = yfinance`, `price_adjustment = adjusted`;
- `bar_timestamp` and `timestamp_kind = session_date`;
- `quote_timestamp`, `venue`, `venue_timezone` if present in source metadata;
- `bar_status`: source-supplied `final` or `partial`, otherwise `unknown`;
- `sha256`: SHA-256 of canonical JSON of the stored OHLCV records.

The ordinary OHLCV loader normalizes daily timestamps and does not preserve a
verified venue timezone or finality flag. Those fields therefore remain unknown
unless supplied; a session date is **not** a quote timestamp. Capture time is
not a claim that the market quote was published at that time. No exchange hours
or bar finality are guessed from ticker suffixes or the machine timezone.

The report digest compacts full OHLCV history to row count, unrounded first/last
rows and stored provenance. Full replay rows remain on disk; the reader does not
revalidate their digest. This avoids repeatedly sending years of price rows to
the auditing model.

A failed first load is retained as an unavailable snapshot with a reason;
subsequent nodes do not retry it and drift into a different price basis.

Checkpoint resumes use the checkpoint's artifact, and initialization avoids
warming a new snapshot when that checkpoint already exists. The checkpoint
signature includes `market_snapshot=v1`, isolating older runs that could have
mixed live prices. Artifact reconstruction validates ticker/date identity and
the OHLCV digest. This is a reproducible price input, not a replay guarantee for
news, model output or dependency versions.

## Hold, exit and gate semantics

- `Hold` or an unknown/no structured direction yields `NO_TRADE`. No entry,
  stop or equity is required, and missing trade inputs do not fail integrity.
- A directional entry without required inputs remains `BLOCKED`.
- An exit against an existing holding remains valid without a stop or equity.
- The CRV/reward-risk threshold remains **warning-only**. No new hard-blocking
  policy default or configuration flag was introduced.

The gate keeps legacy `status = APPROVED | NEEDS_REVIEW` for structural checks.
It now also publishes:

- `structural_status`: same meaning as legacy status;
- `evidence_status = RECORDED | NEEDS_REVIEW`: reflects recorded coverage gaps,
  missing provenance and directional calls without an official/first-party
  source; `RECORDED` does not certify factual sufficiency;
- `trade_plan_status`: sizing verdict, or `NOT_RECORDED`.

A structurally approved report can thus have evidence needing review and a
`NO_TRADE` or `BLOCKED` plan without being mislabeled as a failed report.

The advisory also consumes an already cached issuer financial calendar. Events
0–7 calendar days ahead generate a non-blocking `calendar_event_near` warning,
with the event date and verification date. The cache must match the ticker, be
collected no later than the run date, and have non-stale verification no more
than 120 days old and no later than the run date. No network fetch or model-prose
parsing is used. Missing/unverified calendars produce no claim that no event
exists.

## Offline verification

```sh
env -u PYTHONPATH .venv/bin/python -m pytest tests/test_run_snapshot_integrity.py -q
```

Dedicated coverage exercises moving-price drift through snapshot/CSV/indicators/
sizing, initial-state→stream→artifact retention, failed-capture freeze, precise
price preservation, concurrent workers and independent runs, metadata finality,
artifact identity/digest rejection, checkpoint initialization, Hold/exit, CRV
warning-only behavior, calendar verification and separated gate statuses.
