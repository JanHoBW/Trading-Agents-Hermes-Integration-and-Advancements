# Advancement Log (Fork Changelog)

This file documents everything this fork adds on top of upstream
`TauricResearch/TradingAgents` — currently at **v0.7.0** (merged as `1ea3312`,
2026-10-17; the v1.0.0 base was v0.6.0, commit `1394a3f`, 2026-10-03).

The upstream [`CHANGELOG.md`](../CHANGELOG.md) stays authoritative for upstream
releases. This log covers the fork delta: features developed for running
TradingAgents as a long-lived Hermes-agent setup and for German/European
market coverage. Once a piece is merged upstream, the entry is marked with the
PR number.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## Architecture of the fork additions

The fork is organized as a thin set of layers around the unmodified upstream
pipeline. Nothing in the fork touches the LangGraph node logic itself — every
addition plugs in at a seam (vendor chain, config key, LLM factory, memory
log format, CLI subcommand).

```
 ┌──────────────────────────────────────────────────────────────────────────┐
 │  Human / Hermes agent (profile)                                          │
 │  run_analysis.py (single call point: run | news)                         │
 │  tradingagents CLI: run · backtest · memory feedback|show · login|logout │
 └──────────────────────────────────┬───────────────────────────────────────┘
                                    │
 ┌──────────────────────────────────▼───────────────────────────────────────┐
 │  Multi-agent pipeline (upstream, unchanged)                              │
 │  Analysts → Bull/Bear research debate → Trader → Risk → Portfolio Mgr    │
 │  Two model tiers: deep (managers) / quick (analysts)                     │
 └──────────────┬───────────────────────────────────────────────────────────┘
                │
 ┌──────────────▼───────────────────┐  ┌────────────────────────────────────┐
 │  Data layer — DE/EU (fork)       │  │  Memory subsystem                  │
 │  issuer registry (19 DE names)   │  │  (tradingagents/memory/, fork)     │
 │  ureg_pdmr   BaFin/UMR insiders  │  │  log.py      one markdown entry per│
 │  issuer_rss  first-party feeds   │  │              decision, block-based │
 │  marketaux   news aggregator     │  │              format:               │
 │  finnhub     analyst/insider     │  │                LESSON prose        │
 │  official_eu ECB/Eurostat macro  │  │                REFLECTION_JSON ←───┐│
 │  yfinance + alpha_vantage OHLCV  │  │                HORIZONS {5,21,63,  │
 │  cross-vendor OHLCV reconciliation│  │                                   │
 │  news_supplement (skill feeds)   │  │                126: outcomes}      │
 └──────────────────────────────────┘  │                HUMAN_FEEDBACK ─────┘│
                                       │  settlement.py window scoring from │
                                       │  the first close after the         │
                                       │  decision date (upstream #1518)    │
                                       │  reflection.py  Lesson(str), 6     │
                                       │  stable keys, parse+render         │
                                       │  calibration.py forecast-vs-       │
                                       │  outcome stats, per horizon        │
                                       │  realized.py    portfolio.csv      │
                                       │  (manual) — ground truth for cal.  │
                                       └────────────────────────────────────┘
 ┌──────────────────────────────────────────────────────────────────────────┐
 │  LLM chain (fork): factory.py — provider registry extended with         │
 │  openai_compatible (LM Studio / vLLM / Weber-Cloud), reasoning_effort    │
 │  forwarding, configurable llm_request_timeout                            │
 │  + upstream v0.7.0 chatgpt plan sign-in (chatgpt_auth.py)                │
 └──────────────────────────────────────────────────────────────────────────┘
```

### Decision-memory lifecycle

```
 Run N:     decision logged as pending entry
            (LESSON + REFLECTION_JSON + HORIZONS skeleton)
 Run N+k:   settlement pass — a horizon whose window has traded is scored
            (alpha vs benchmark from the first close after the decision
            date); the lesson evolves as later horizons resolve
 Human:     tradingagents memory feedback RHM.DE --rating=gut --note="..."
            (writes HUMAN_FEEDBACK; pending or settled entry; re-annotating
            replaces the previous verdict)
 Run N+k+1: get_past_context() includes pending entries that carry a
            HUMAN_FEEDBACK, so the human verdict reaches the portfolio
            manager on the very next run — the feedback loop closes without
            waiting for the anchor horizon. Backtest (as_of) filtering still
            drops unannotated pending entries: look-ahead stays clean.
```

### Where each addition plugs in

| Subsystem | Seam | Upstream impact |
|---|---|---|
| DE/EU data layer | vendor chain in `dataflows/` + `issuer_registry_path` config | none (degrades keyless) |
| LLM plumbing | `factory.build_llm_kwargs` + `openai_client` | none (additive branches) |
| Multi-horizon memory | markdown entry blocks parsed by `memory/log.py` | none (old entries still parse) |
| Structured lessons | `REFLECTION_JSON` block + `Lesson(str)` in `reflection.py` | none (prose fallback) |
| Human feedback | `HUMAN_FEEDBACK` block + `cli memory` subcommand | none |
| Realized P&L | `portfolio` config + `memory/realized.py` CSV reader | none (`path=None` = paper mode) |
| News supplement | `news_supplement` vendor + `TRADINGAGENTS_NEWS_SUPPLEMENT` | none (opt-in) |
| One-call wrapper | `run_analysis.py` in `hermes/skills/hermes-trading-scenarios` | none (process level) |

---

## [Fork v1.0.6] — 2026-10-21

Four gaps from the improvement review: official German disclosures, ETF
holdings, a consent gate for the Copilot endpoint, and a calibration record that
says how much it can carry.

### Added — UReg capital-market disclosures (`disclosure_data`)

- **`get_capital_market_disclosures`** (vendor `ureg_capital`) reuses the
  `ureg_pdmr` search session and entity validation, and serves the categories the
  register files next to PDMR: **79** Art. 17 MAR ad-hoc, **81** WpHG §40/41
  voting rights, **83** capital-market information (buy-backs, AGM, capital
  measures), **89** §50 WpHG. Category ids were observed live against Rheinmetall,
  Infineon and Deutsche Bank; 69 (bond admission notices) is excluded because it
  carries no issuer disclosure content.
- New **optional** category `disclosure_data` (`DEFAULT_CONFIG["data_vendors"]`),
  so a non-German ticker gets a scoped "no configured vendor covers this
  instrument" sentinel instead of a failed tool call — the router's optional-path
  message now distinguishes "not configured" (coverage limit) from "could not be
  retrieved".
- Each entry: publication date, category label, title, language, and a text
  excerpt from `/api/pub-html`. The register files a DE and an EN twin per
  disclosure; they are merged to one entry (German wins) on
  (date, publicationType, issuer) and the merge count is printed in the header.
- Offered to the **News Analyst**, whose prompt now says to use it for German
  listings instead of inferring corporate events from articles, and never to
  read an absent disclosure as an absent event.

### Added — ETF top holdings

- The ETF profile block lists the top 10 holdings with weights, ranked,
  token-free from `yf.Ticker(...).funds_data.top_holdings`. No as-of date is
  claimed because Yahoo supplies none; a failed or empty lookup returns no block
  and never costs the rest of the fundamentals.

### Changed — Copilot is gated behind explicit consent (wrapper 2.2.0)

- `--allow-copilot` (per run) or `TRADINGAGENTS_ALLOW_COPILOT=1` is required for
  the fork-only Copilot endpoint; otherwise it drops out of the `auto` chain and
  an explicit `--llm=copilot` fails with a message naming the flag. Flag is
  forwarded to `--bg` children and honoured by `doctor`.
- Every Copilot use prints a warning on stderr (stdout stays a JSON contract)
  and records `copilot_tos_note` / `copilot_enabled` in `provenance.json`.
- `build_parser()` split out of `main()` so the CLI surface is testable.

### Changed — calibration carries a confidence label

- `build_calibration` returns `confidence` per record (level, reason, n): **low**
  below 8 pooled settled outcomes, **medium** below 20 or inside a 35–65 % chance
  band, **high** otherwise. Per-instrument rates can only lower the level — a
  ticker whose own rate disagrees with the pooled one by more than 25 points is
  flagged unstable.
- No Brier score: the rating scale is ordinal, so there is no probability to
  score. The rendered line states the level and, at `low`, explicitly calls the
  record an anecdote the Portfolio Manager must not treat as a track record.

### Testing

- `tests/test_ureg_capital_markets.py` (15): category filter/label map, DE-EN
  merge, entity validation, point-in-time, empty-window wording, detail failure,
  router wiring and the optional sentinel.
- `tests/test_etf_holdings.py` (6) and `tests/test_calibration_confidence.py` (7).
- `hermes/skills/.../tests/test_run_analysis_copilot_flag.py` (9): gate, env
  consent, parser flags, bg forwarding.
- Full repo suite: 1595 passed, 1 skipped.

## [Fork v1.0.5] — 2026-10-21

Closing the two audit holes that mattered most: a run's data gaps now reach the
decision and the report, and an interactive `--ui` run finally leaves a record.

### Added — per-run data-coverage matrix (repo + report)

- **`tradingagents/dataflows/coverage.py`** — a per-run registry the routing
  layer writes into: which vendor served each data method, which were skipped
  and why (`not_configured` / `unavailable` / `failed` / `no_data`). Repeated
  identical skips collapse, so a vendor refused on every bar of a window is one
  gap, not fifty.
- **`router.route_to_vendor`** records the outcome at every branch. This is the
  only place that knows a call's true provenance, so the matrix is derived from
  what happened rather than re-parsed from logs.
- **Portfolio Manager** receives a `DATA COVERAGE` block before the risk debate:
  how many expected inputs for the asset type arrived cleanly, which are
  degraded/unavailable, which were never requested, and the rule that an
  unavailable input must be reported as unknown rather than assumed.
- **Report** gains section `VI. Data Coverage` (markdown + HTML) with a
  per-method table (`Input | Status | Served by | Skipped`) and the concrete
  gaps; `save_reports` also writes a machine-readable `data_coverage.json` next
  to it. A method nobody called is labelled *not requested*, never as a gap.
- Coverage resets per run in `create_run_state`, so sequential backtests do not
  inherit each other's records.

### Changed — `--ui` runs write `provenance.json` (wrapper)

- A `--ui` run returned before the attempt loop, so the default interactive path
  left **no** `provenance.json` and no `tradingagents.log` — the console output
  went to the TTY. The windowed shell now records the child's exit code in a
  hand-off file; the wrapper writes a full provenance record (profile, model,
  backend URL, timing, report hashes, exit code, `partial`) and removes the
  hand-off file.
- Both paths now carry the repo's coverage matrix (`data_coverage.matrix`,
  `data_coverage_source`) alongside the legacy log-derived `data_coverage`,
  which keeps working for runs that produced no report tree.
- Shared helpers `report_files()` / `write_provenance()` (mode 600) so the two
  paths cannot drift again.

### Added — `run_analysis.py doctor` (wrapper pre-flight)

- `doctor [TICKER] [DATE] [--llm=] [--portfolio=] [--supplement=]` checks the
  installation (root, venv python, CLI), `hardcoded_sources.yaml` and the
  ticker's entry, optional portfolio/supplement inputs, and — through one child
  process in the repo venv — the resolved benchmark, evaluation horizons, issuer
  registry, memory log (entries, pending count, writability) and directory
  write access. LLM backends are probed per profile.
- Exit codes: **0** ok, **3** degraded, **4** blocked. Every check reports its
  own status, so an unattended job can refuse to start instead of failing two
  hours in.

### Testing

- `tests/test_data_coverage.py` (10): registry semantics, router recording for
  skip/success/untyped failure, PM prompt and state, report section + JSON twin.
- `hermes/skills/.../tests/test_run_analysis_provenance_doctor.py` (7):
  newest-matrix selection, `--ui` provenance with exit code 0 and 1 (partial),
  doctor ok/blocked/degraded paths and exit codes.
- Full repo suite: see commit message.

## [Fork v1.0.4] — 2026-10-21

One wrapper instead of two — and the skill that drives it is versioned in this repo.

### Changed — the wrapper is one Python entry point (skill)

- **`ta.sh` deleted.** The 252-line bash layer duplicated `run_analysis.py`'s
  validation and defaults, and the two had already drifted:
  `--custom-api-key-env=` was documented but implemented only in Python, and round
  limits were checked in one place and enforced in another. All orchestration —
  news pipeline (one call into `news_collect.py`), ticker metadata from
  `references/hardcoded_sources.yaml`, `--bg`, `--quick`, output layout, LLM
  profile defaults — now lives in `run_analysis.py` v2.0.0 with two subcommands,
  `run TICKER [DATE]` and `news TICKER [DATE]`.
- **Self re-exec.** The script re-executes itself under the fork venv's Python when
  started with another interpreter, so `python3 …/run_analysis.py run RHM.DE` works
  from any shell — the guarantee the bash layer used to provide.
- **`--quick` redefined** (user decision): local `swift-1.5-qwen3.8-27b`,
  `reasoning-effort=low`, 1/1 rounds; it used to mean the webercloud profile. An
  explicit `--reasoning-effort` / `--lmstudio-model` / rounds flag still beats it.
- **Checkpointing on by default** for unattended runs
  (`TRADINGAGENTS_CHECKPOINT_ENABLED=true`, overridable from the environment).
- **Skill source moved into this repo** under `hermes/skills/hermes-trading-scenarios/`
  (SKILL.md, references, scripts, tests). Hermes loads the skill from the profile
  path; this directory is the versioned copy — change both in the same step.

### Testing

- `pytest tests/ -q` → 15 passed. The suite had been 7/8 red: it still called the
  three-argument `build_child_env`, the old `qwen/qwen3.8-27b` model id and a
  reachability gate that no longer exists.
- Dry runs: default, `--quick`, and a pinned model+effort → `ok: true` with the
  expected model, rounds and output directory. Nine negative cases (unknown flag,
  bad ticker/date/suffix, space-form flag, `--api-url` without `--model`,
  `--ui --bg`, rounds=7, unknown ticker) → exit 2 with one clear message.
- End to end: RHM.DE 2026-10-21, `openai/gpt-6-luna` at `xhigh` through the
  `custom` profile (OpenRouter), debate 1 / risk 2 → rc=0 in 610 s, 26 report
  files, `provenance.json` and the data-coverage block written. LM Studio on
  the local LM Studio host was offline, which is what the custom profile is for.

## [Fork v1.0.3] — 2026-10-20

Everything below is relative to upstream v0.7.0.

### Added — generic custom LLM endpoint for unattended runs (wrapper)

The Hermes wrapper previously only knew the built-in profiles
(`copilot` / `lmstudio` / `webercloud`); any other OpenAI-compatible server
required editing `run_analysis.py`. Now:

- **`custom` profile** (`run_analysis.py`): `--llm custom --custom-api-url <url>
  --custom-model <id>` points the whole run at any OpenAI-compatible endpoint.
  Optional `--custom-api-key-env <NAME>` reads the key from a profile `.env`
  variable (a remote host without one gets a warning, not a failure). The
  preflight reachability check covers the custom endpoint like the other profiles.
- **`run_analysis.py` shortcuts**: `--api-url=<url> --model=<id>` implies `--llm custom`;
  both flags are validated (http(s) URL, model charset, set-together rule) and
  stay mutually exclusive with an explicit `--llm`.

This decouples unattended runs from a specific local server: the same command
line works against LM Studio on any host, vLLM, or a cloud relay.

### Data — per-ticker Reddit subreddits for RHM.DE

`hardcoded_sources.yaml` (wrapper data) maps `RHM.DE` to r/wallstreetbetsGER and
r/RHM_Rheinmetall via the fork's per-ticker override seam (`7c37f7d`), so the
Sentiment Analyst reads German retail discussion for Rheinmetall.

### Notes — prefix-cache baseline (backlog #16, Phase 1)

Measured on the local server (Q4, prefill ≈ 340 tok/s): a full debate prompt
(~22k tokens) costs ~66 s uncached vs ~13 s with a prefix-cache hit; one byte
changed early in the prefix destroys the cache. The flat debate prompt is already
append-only in history, so later rounds reuse most of the prefix — chat-mode
(#16) would only save the duplicated tail (~20 s per run): not worth it in
isolation. Bigger lever: keep other consumers (e.g. Hermes chat on the same
model) off the single-slot server during a run. Details: skill reference
`memory-improvement-backlog.md`.

---

## [Fork v1.0.2] — 2026-10-18

Everything below is relative to upstream v0.7.0.

### Added — single report tree per run (backlog #14)

Upstream v0.7.0 writes a run's artifacts into **two overlapping trees** under
`results_dir`: the live tree `<ticker>/<date>/` (`message_tool.log`, flat
stage `.md` files, `TradingAgentsStrategy_logs/full_states_log_*.json`) and
the final tree `reports/<TICKER>_<stamp>/` (phase folders + complete report).
This fork adds an opt-in layout that unifies them:

- **`results_layout` config key** (`TRADINGAGENTS_RESULTS_LAYOUT`, default
  `"shared"` = upstream behavior, unchanged): in the `"run_dir"` layout the
  results directory already belongs to this one run, so every writer puts its
  artifacts directly there — live log and full-states log at the top level,
  all report files in `reports/` (no ticker/date layers, no timestamp stamp).
- **Three writers honor it** — `_run_directory()` (`cli/run.py`),
  `default_report_path()` and the state-log path
  (`tradingagents/graph/trading_graph.py`). Ticker validation still runs even
  when nothing is appended.
- **Wrapper switch** (fork-only, `hermes/skills/hermes-trading-scenarios`): `run_analysis.py`
  now set `TRADINGAGENTS_RESULTS_DIR=~/.tradingagents/reports/trading-scenarios/<TICKER>/<DATE>[-<SUFFIX>]`
  plus `TRADINGAGENTS_RESULTS_LAYOUT=run_dir`. All app data (memory, portfolio,
  registry, cache **and** reports) now lives under one root, `~/.tradingagents/`;
  the profile keeps a symlink `<profile>/reports → ~/.tradingagents/reports`
  for convenience. Existing runs were moved in place (2026-10-18).

### Testing

- `tests/test_results_layout_run_dir.py` — run-dir layout: `_run_directory`
  returns the results dir itself, ticker validation still enforced, report path
  unstamped; shared-layout paths byte-identical to before. Existing
  path-pinning tests (`test_cli_symbol_handling.py`, e2e) pass unchanged.

---

## [Fork v1.0.1] — 2026-10-17

Everything below is relative to upstream v0.7.0.

### Synced — upstream v0.7.0 (merge `1ea3312`)

- **ChatGPT plan provider** — `tradingagents chatgpt login|logout`: sign in
  with a ChatGPT Plus/Pro plan and run the `chatgpt` provider without an API
  key (`chatgpt_auth.py`, `SIGNED_IN_PROVIDERS`).
- **Startup release note** — `cli/updates.py` notes a newer release at start-up.
- **Memory scoring fix (upstream #1518)** — decisions are now scored from the
  *first close after* the decision date (`fetch_returns` enters the position
  there and the end-of-window span is one close longer). This semantics
  applies to the fork's multi-horizon paths too, since `fetch_horizons`
  delegates to `fetch_returns`.

### Added — structured lesson JSON (backlog #2, `868a674`)

- **`Lesson(str)`** in `memory/reflection.py` — the reflector's structured
  output is a subclass of `str`: it stays a plain string in every existing
  code path while carrying six stable, German-language keys
  (`LESSON_KEYS`: theseins, handlung, treffer, fehler, lektion, folge).
- **`REFLECTION_JSON:` block** — settlement appends one machine-readable
  JSON line per entry next to the prose lesson; `_parse_lesson_json()` reads
  it with a prose fallback when the block is absent (old entries).
- **Parse + render** — `parse_lesson()` / `render_lesson()` round-trip the
  keys; reflection prompt carries a compactness budget (~80 words).

### Added — human feedback loop (backlog #8, `e352088`)

- **`HUMAN_FEEDBACK:` block** — one JSON line per entry:
  `datum`, `bewertung` (`gut` / `teilweise` / `falsch`), `vermerk`.
  Order-independent regexes; written via
  `add_human_feedback(ticker, trade_date, note, rating, feedback_date)`.
- **CLI** — `tradingagents memory feedback TICKER --note="..."
  [--rating=gut|teilweise|falsch] [--date=YYYY-MM-DD]` and
  `tradingagents memory show [TICKER] [--limit=N]`.
- **Semantics** — re-annotating replaces the previous verdict (identical
  input is a no-op); works on pending *and* settled entries and survives
  further settlement (`merge_horizons` preserves the block).
- **Feedback reaches the next run** — `get_past_context()` now includes
  pending entries that carry a `HUMAN_FEEDBACK`: the human verdict on a
  still-open call is valid before settlement, so the loop closes on the very
  next run instead of waiting out the anchor horizon.
- **Backtest stays look-ahead clean** — the `as_of` filter still drops
  unannotated pending entries.

### Added — fork-only dataflow: per-ticker Reddit overrides (`7c37f7d`)

- `TRADINGAGENTS_REDDIT_OVERRIDES` (JSON path): per-ticker query + subreddit
  overrides in `dataflows/reddit.py` — e.g. DE tickers whose default English
  query returns nothing.

### Testing

- Fork main after the merge: see "Testing" below (full suite, offline-capable).

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

| Change | PR | State (2026-10-17) |
|---|---|---|
| DE/EU data layer + test hardening | [#1529](https://github.com/TauricResearch/TradingAgents/pull/1529) | open |
| LLM plumbing | [#1530](https://github.com/TauricResearch/TradingAgents/pull/1530) | open (fork carries the `openai_compatible` branch alongside upstream v0.7.0's `chatgpt` provider) |
| Multi-horizon memory | [#1531](https://github.com/TauricResearch/TradingAgents/pull/1531) | open |

Upstream sync state: **v0.7.0 merged (`1ea3312`)** — no upstream commits left
unintegrated (`HEAD..origin/main` empty as of 2026-10-17).

### Commit index

| SHA | Area |
|---|---|
| `0a637f6` `efcddb3` `7ae943e` `6a2ed01` `aa9911b` | DE/EU data layer |
| `9576e07` `ce290cc` | LLM plumbing |
| `e3a3735` | (empty baseline checkpoint) |
| `3873dd3` `79d03ee` `40d8fc8` | multi-horizon memory |
| `03afce6` | `.gitignore` |
| `6e01217` `e7ec3a9` | fork docs (advancement log, banner) |
| `7c37f7d` | per-ticker Reddit overrides (fork-only) |
| `868a674` | structured lesson JSON (backlog #2) |
| `e352088` | human feedback loop (backlog #8) |
| `1ea3312` | merge of upstream v0.7.0 |

### Testing

- Fork main after the v0.7.0 merge: **1553 passed, 1 skipped, 1 deselected**
  (offline-capable suite, Python venv, 2026-10-17). The previous fork-only
  total (1389 passed) grew with upstream v0.7.0's new test modules
  (ChatGPT auth/provider, CLI updates).
- All new vendors degrade gracefully without keys and are tested offline via
  shipped fixtures.
