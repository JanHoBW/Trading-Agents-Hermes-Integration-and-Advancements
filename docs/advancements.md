# Advancement Log (Fork Changelog)

This file documents everything this fork adds on top of upstream
`TauricResearch/TradingAgents` **v0.6.0** (commit `1394a3f`, 2026-10-03).

The upstream [`CHANGELOG.md`](../CHANGELOG.md) stays authoritative for upstream
releases. This log covers the fork delta: features developed for running
TradingAgents as a long-lived Hermes-agent setup and for German/European
market coverage. Once a piece is merged upstream, the entry is marked with the
PR number.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [Fork v1.0.0] — 2026-10-15

Everything below is relative to upstream v0.6.0.

### Added — DE/EU equity & ETF data layer

The vendor set was US-centric; German/European equities and ETFs had
systematic coverage gaps (no official insider feed, thin news, no official
macro for the Euro area). This layer adds first-party and licensed sources in
a provenance-prioritized chain. Upstream-eligible:
[PR #1529](https://github.com/TauricResearch/TradingAgents/pull/1529).

- **`ureg_pdmr`** (new vendor, keyless) — German insider disclosures from the
  official Unternehmensregister (PDMR feed, Art. 19 MAR): search pagination,
  DE/EN HTML parsing, offline fixtures. Self-selects via the issuer registry:
  a ticker without a configured UReg identity raises
  `VendorNotConfiguredError` and the chain falls through.
  (`0a637f6`, `6a2ed01`)
- **`finnhub`** (new vendor) — analyst data and insider feed (SEC-sourced,
  Form 4 filing dates). US-coverage only; its profile gate falls through for
  other markets. Key: `FINNHUB_API_KEY` (or `FINNHUB_API_TOKEN`).
- **`marketaux`** (new vendor) — news aggregator. Key: `MARKETAUX_API_TOKEN`.
- **`issuer_rss`** (new vendor) — first-party issuer news RSS feeds from the
  registry; authoritative for a configured ticker (returns a terminal "no
  news" string even when empty). Keyless.
- **`news_supplement`** (new vendor) — optional supplemental news source
  activated by `TRADINGAGENTS_NEWS_SUPPLEMENT` (path to a supplement config).
- **`official_europe`** (new vendor, keyless) — official ECB/Eurostat
  statistical series for Euro-area macro, ahead of FRED in the chain.
- **Yahoo fundamentals** — analyst consensus spread surfaced in the
  fundamentals profile (`efcddb3`); a generic ETF profile so fund tickers get
  an overview instead of an equity-shaped gap (`7ae943e`).
- **Cross-vendor reconciliation** — when the primary OHLCV came from a
  less-trusted source (detected via the Alpha Vantage header), closes are
  compared against the other configured OHLCV vendor and an informational note
  is appended on meaningful divergence. Best-effort, never changes the primary
  result. Config: `cross_vendor_reconciliation` (default `true`).
- **Offline regression tests** for `ureg_pdmr` search pagination and all new
  vendors ship with fixtures (`aa9911b` and the data-layer commit).
- **German market documentation** — `docs/german-market-data.md` (coverage
  matrix, gaps, which source serves which tool) and
  `config/german_equities.example.json` (registry sample).

### Added — LLM plumbing for local/compatible servers

Upstream-eligible: [PR #1530](https://github.com/TauricResearch/TradingAgents/pull/1530).

- **`reasoning_effort` forwarding** (`9576e07`) — the model-name gate that
  strips `reasoning_effort` now only protects *native* OpenAI (where
  non-reasoning models 400 on the parameter). OpenAI-*compatible* servers
  (LM Studio with thinking models, vLLM, …) decide for themselves — the
  parameter is forwarded there unconditionally.
- **`llm_request_timeout`** (`ce290cc`) — configurable per-request timeout in
  seconds (`TRADINGAGENTS_LLM_REQUEST_TIMEOUT`). Local thinking models
  routinely exceed the SDK default (600 s) on a single long
  high-reasoning turn; e.g. set `1800`.

### Added — multi-horizon long-term memory

Upstream-eligible:
[PR #1531](https://github.com/TauricResearch/TradingAgents/pull/1531).

- **Multi-horizon settlement** (`3873dd3`) — a logged decision is settled per
  evaluation window instead of one cutoff, so short-horizon and long-horizon
  lessons get independent outcomes.
- **Calibration layer** (`3873dd3`, new `memory/calibration.py`) — tracks
  forecast-vs-outcome statistics per lesson horizon and feeds calibrated
  confidence back into the graph.
- **Supersede idempotency** (`3873dd3`) — re-applying a superseding lesson no
  longer duplicates memory-log entries.
- **Evolving lesson + configurable windows** (`79d03ee`) — the lesson text
  evolves as later windows resolve; windows are configurable:
  `TRADINGAGENTS_EVALUATION_HORIZONS` (comma/semicolon-separated trading-day
  counts, e.g. `"5,21,63,126"`) or `config["evaluation_horizons"]`
  (default `[5, 21, 63, 126]`; first entry is the anchor).
- **Realized-P&L seam** (`79d03ee`) — `config["portfolio"]` (path/format/
  currency/benchmark ticker) and `memory_log.merge_realized(...)`: a real
  portfolio's realized return is the ground truth, preferred over predicted
  horizon alphas for calibration. `path=None` keeps pure paper-trading mode.
- **Real-portfolio CSV reader** (`40d8fc8`, new `memory/realized.py`) —
  broker-independent, manually maintained CSV
  (`ticker,shares,entry_date,entry_price,close_date,close_price,note`,
  template `~/.tradingagents/portfolio.csv`); closed rows record their
  realized return on the next run, tranches on one day aggregate. 35 tests.

### Added — Hermes-agent integration (fork-only)

Kept out of upstream by design (see "Fork-only" below).

- **GitHub Copilot endpoint** (`openai_client.py`) — opt-in by hostname: when
  `backend_url` points at `*.githubcopilot.com`, the client switches to the
  Responses API and adds the editor-attribution headers the endpoint requires
  (`Editor-Version`, `Copilot-Integration-Id`, `Openai-Intent`, …). This is
  how the fork reaches GPT-5+/GPT-6-class models through a Copilot subscription.
- **Profile-safe test isolation** (`tests/conftest.py`) — an autouse fixture
  pins credential tests to a tmp directory. Pre-v1.0 upstream,
  `test_api_key_env.py` could write into a real parent `.env` when tests ran
  inside an agent profile shell; the fork fixes that.
- **`.gitignore`** — `.hermes/` planning notes are ignored so the project
  tree stays clean when an agent works in it (`03afce6`).

### Changed

- Default `core_stock_apis` chain: `yfinance` → `yfinance,alpha_vantage`
  (Alpha Vantage fallback for series Yahoo lacks).
- Default `news_data` chain: `ureg_pdmr,issuer_rss,marketaux,finnhub,yfinance,alpha_vantage`.
- Default `macro_data` chain: `official_europe,fred`.
- New config keys: `issuer_registry_path` (env `TRADINGAGENTS_ISSUER_REGISTRY`,
  default `~/.tradingagents/german_equities.json`), `news_supplement_path`
  (env `TRADINGAGENTS_NEWS_SUPPLEMENT`), `llm_request_timeout`,
  `evaluation_horizons`, `portfolio`, `cross_vendor_reconciliation`.
- `evaluation_horizons` default `[5, 21, 63, 126]` (trading days) replaces the
  implicit single 5-day window; the legacy behavior is reachable via the env
  var or `config["evaluation_horizons"] = None`.

### Fork-only (never upstream)

- Copilot endpoint handling and its headers (ToS gray zone).
- `tests/test_copilot_responses.py` (exercises the block above).
- This file and `docs/hermes-integration.md`.
- `.gitignore` entry for `.hermes/` (harmless upstream, but part of the
  profile workflow — shipped fork-only to keep the data-layer PR focused).

### Upstream status

| Change | PR | State (2026-10-15) |
|---|---|---|
| DE/EU data layer + test hardening | [#1529](https://github.com/TauricResearch/TradingAgents/pull/1529) | open |
| LLM plumbing | [#1530](https://github.com/TauricResearch/TradingAgents/pull/1530) | open |
| Multi-horizon memory | [#1531](https://github.com/TauricResearch/TradingAgents/pull/1531) | open |

### Commit index

| SHA | Area |
|---|---|
| `0a637f6` `efcddb3` `7ae943e` `6a2ed01` `aa9911b` | DE/EU data layer |
| `9576e07` `ce290cc` | LLM plumbing |
| `e3a3735` | (empty baseline checkpoint) |
| `3873dd3` `79d03ee` `40d8fc8` | multi-horizon memory |
| `03afce6` | `.gitignore` |

### Testing

- Fork main: 1389 passed, 1 skipped (offline-capable suite, Python venv).
- All new vendors degrade gracefully without keys and are tested offline via
  shipped fixtures.
