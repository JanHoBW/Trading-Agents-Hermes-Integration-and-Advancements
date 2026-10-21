# Framework capability audit and backtesting

Topical depth for recurring questions: "do we use all the TradingAgents framework's possibilities?", "are there more features we haven't considered?", "does backtesting make sense?" Load when asked, not when running an analysis.

## Capability audit — how to answer

Answer from code + the run's artifacts, never from memory. Audit BOTH directions, never just one:

- `git log --oneline origin/main..HEAD` = the fork delta (what this fork added on top of the upstream base).
- The full base tree = upstream capabilities that may never have been wired up: `ls tradingagents/` (top-level modules such as `backtest.py`, `portfolio.py`, `report_html.py`), the entry points `main.py` + `cli/` (typer app: `--portfolio`, `--checkpoint`/`--clear-checkpoints`, `--save/--show/--html`, and the `tradingagents backtest` subcommand), and `tradingagents/default_config.py` (the full DEFAULT_CONFIG key list plus the env-var mapping at its top).

Pitfall: the fork delta alone only shows fork additions; base capabilities live at origin/main and a delta-only audit reports them as "nonexistent" (the upstream backtest CLI was exactly that case — missed until the base tree was listed). Verify a candidate feature with its real `--help`/signature before quoting it as available.

1. **Enumerate the tool surface** from `tradingagents/dataflows/router.py` (`TOOLS_CATEGORIES`: core_stock_apis, technical_indicators, fundamental_data, news_data, macro_data, prediction_markets) plus the sentiment analyst's direct vendor calls (reddit, stocktwits — not router-routed) and the fork vendors (european_official, sec_edgar, ureg_pdmr, issuer_rss, marketaux, finnhub, fred, polymarket, news_supplement).
2. **Distinguish three states per method** — conflating them produces false "unused" claims:
   - *configured*: key present — read provenance `data_coverage.flags` (`fred_api_key_missing`, `marketaux_token_missing`, `finnhub_token_missing`).
   - *attempted*: appears in provenance `data_coverage.methods` with a status (the router logged the call).
   - *used*: appears in a stage report — e.g. the news analyst's verified macro table (FRED values) or cited Polymarket probabilities.
   The LLM decides per run whether to call optional tools, so provenance alone cannot prove usage — grep `1_analysts/news.md` for the table/probabilities before claiming a category was or was not used.
3. **Optional ≠ unused.** `OPTIONAL_CATEGORIES = {macro_data, prediction_markets}` only means the run survives a vendor failure. With `FRED_API_KEY` in the profile `.env` (it is) and Polymarket keyless, both are live enrichment: in a live RHM.DE run the news analyst produced a verified macro table (ECB deposit rate, HICP, 10Y yields) and cited Polymarket probabilities (ceasefire ~10–14 %, ECB rate cut ~4 %).
4. **Known gaps to report honestly:** StockTwits (Cloudflare blocks this host — its "unavailable" marker is not "no social sentiment"), Reddit for German defense names (US subreddits do not discuss them; empty results are the truthful answer, the YAML overrides are the fix path), crypto/forex asset types (no use case), and independent web research inside debate agents (by design: bull/bear/risk nodes have no tools — compensate via the news supplement + the Hermes subagent research path).
5. **DEFAULT_CONFIG surface — knobs not in the env mapping** (from `default_config.py`): `global_news_queries` (default is US-centric — Fed, S&P 500, ECB, tariffs, US-China; for a defense-sector run, curated queries like NATO budgets / DE procurement are the right fix; it is NOT env-mapped, so it is a one-line fork change, not a flag), `news_article_limit` / `global_news_article_limit` (per-source item caps), `max_tool_rounds` / `max_recur_limit` (loop caps), `temperature` / `max_tokens` / `anthropic_effort` / `google_thinking_level` (model knobs), `memory_log_max_entries` (log trimming), `holding_period_days` / `evaluation_horizons` (settlement windows, fork). Env-mapped extras worth knowing: `TRADINGAGENTS_BENCHMARK_TICKER` (benchmark override) and `TRADINGAGENTS_ISSUER_REGISTRY` (path, default `~/.tradingagents/german_equities.json`).
6. **Two-tier LLM (deep vs quick).** `TRADINGAGENTS_DEEP_THINK_LLM/PROVIDER/BACKEND_URL` + `TRADINGAGENTS_QUICK_THINK_*`. Node mapping in `graph/setup.py`: the 4 analysts + bull + bear + trader run on `quick_thinking_llm`; research manager + portfolio manager on `deep_thinking_llm`. With both tiers pinned to one model (the current setup) the split buys nothing locally — it only pays off on a cloud backend where quick can be a cheaper/faster tier while deep keeps the high reasoning effort for the decision nodes.
7. **Position book for the decision agents (`--portfolio` JSON, upstream).** `tradingagents/portfolio.py` defines `PortfolioContext {cash, currency, positions[{ticker, quantity, average_price}]}`; the upstream CLI's `--portfolio <file.json>` feeds it to `propagate(..., portfolio=...)` so trader, risk debaters and the portfolio manager size against the user's ACTUAL positions. It is a different object from the fork's `~/.tradingagents/portfolio.csv` (closed trades → memory settlement/calibration): JSON = current book for decisioning, CSV = history for the memory loop; both can be in use at once. Known gap: the Hermes wrapper `run_analysis.py` passes no portfolio, so every run currently decides as if the user held nothing — worth a wrapper flag when the user holds the ticker under analysis.
8. **Issuer registry — root cause of `get_news` `issuer_rss not_configured` degradations.** The `issuer_rss` vendor (head of the get_news chain) requires an https `news_feed_url` in the registry entry (`~/.tradingagents/german_equities.json`, shape `{schema_version: 1, instruments: {TICKER: {issuer, isin, wkn, ticker, mic, currency, yahoo_symbol, alpha_vantage_symbol, official_ir_url, news_feed_url, aliases[], ureg_query_name, ureg_entity_name, ureg_pdmr, ir{enabled, terms_checked, sitemap, url_patterns}}}`); `null` → clean degradation to the next vendor, and the run logs it as degraded. The registry holds 19 German large-caps (RHM.DE included, with the keyless Unternehmensregister PDMR fields); RHM's `news_feed_url` is `null` because the issuer publishes no public RSS (Nuxt SPA, all feed-candidate URLs 404) — check the registry entry before debugging the vendor for a missing news chain, and when a first-party feed ever appears, setting that one JSON field is the whole fix (no code change).

## Analysis windows — what the run actually looks at

Two horizons, deliberately different (`tradingagents/dataflows/windows.py`, config
keys `news_lookback_days` / `event_context_lookback_days`, env
`TRADINGAGENTS_NEWS_LOOKBACK_DAYS` / `TRADINGAGENTS_EVENT_LOOKBACK_DAYS`):

| Horizon | Default | Feeds | Config/env |
|---|---|---|---|
| Headline | 7 d | ticker `get_news`, sentiment (Reddit/StockTwits/headlines), news supplement (`--window-days`) | `news_lookback_days`, `TRADINGAGENTS_NEWS_LOOKBACK_DAYS`, `--news-window-days` |
| Event context | 180 d (~126 trading days) | first-party only: issuer releases (`issuer_ir`), UReg capital-market disclosures, UReg PDMR | `event_context_lookback_days`, `TRADINGAGENTS_EVENT_LOOKBACK_DAYS`, `--event-window-days`, `--no-event-context` |
| Macro (FRED) | 365 d | `get_macro_indicators` default | tool argument |
| Prices/technicals | LLM-chosen range, loader holds 5 y | `get_stock_data`, indicators (30 d trailing default, SMA200 needs ~200 bars) | tool arguments |
| Fundamentals | quarterly series (Yahoo ~5 quarters) + live profile | statements/tools | tool arguments |
| Insider/regulatory | full history up to `as_of` (caps: PDMR 40, capital 12) | UReg tools | — |
| Settlement | 5/21/63/126 trading days | outcome measurement, not input | `HORIZONS` |

Why two: the recommendation horizon (3–6 months) and the settlement horizons
(21/63/126 trading days) are far longer than a week of headlines. The event block
therefore covers ~180 calendar days — the longest horizon the run is later scored
against — while the headline block stays narrow to keep the prompt about *now*.
An absent source renders as `GAP: …`, never as "nothing happened". The report's
Data Coverage section prints both windows (`**Analysis windows**`), and
`provenance.json` records them as `analysis_windows`.

## Backtesting — native engine plus tiers

The framework HAS a backtest engine (upstream base, present in the tree; CLI `--help` verified): `tradingagents/backtest.py` + the `tradingagents backtest` subcommand of the repo's typer app:

```bash
.venv/bin/tradingagents backtest RHM.DE --start 2026-09-01 --end 2026-10-15 --every 7 \
  [--analysts market,news] [--asset-type stock] [--portfolio file.json] [--run-id my-sweep]
```

- Runs the FULL graph on a (ticker × date) grid (every N days, up to today) — one model sample per cell.
- Isolated memory log per sweep (`results_dir/backtest/<run-id>/trading_memory.md`) — the live memory log is never polluted.
- **Resumable**: `--run-id` continues an interrupted sweep; already-logged cells are skipped. A long grid is therefore a background job, not an all-or-nothing bet.
- Settles each ticker's decisions at the end of its column; `summarize()` scores the finished log per rating: hit rate (did the direction come true?) + average alpha vs the regional benchmark (`benchmark_map` by exchange suffix; if the ticker's suffix is not in the map, set `TRADINGAGENTS_BENCHMARK_TICKER` explicitly before trusting alpha numbers).
- Leak protection of historical mode applies (`date_window.py`: `withhold_live_profile` / `withhold_undated_statements` / `withhold_undisclosed_trades`) — a past-date cell cannot see the future.
- Honest caveat from the module docstring, always state it when reporting results: text feeds are not archived, so news in past cells is indicative, not reproducible — a backtest result is a decision-quality estimate, not a recorded replay.

**Do the cost math first:** one full run ≈ 30–90 min (cloud `--quick`-class config) or 3–4 h (local 27B xhigh). A 6-cell single-ticker grid ≈ 3–9 h on cloud; shrink with `--analysts` (2 instead of 4) and a coarser `--every`. Not routine operation; fine as a one-off calibration program the user asked for.

Tiers, cheapest first:
1. **Realized-P&L loop (default, zero cost)** — record real trades in `~/.tradingagents/portfolio.csv` (column contract in the SKILL.md hardcoded-sources section); the next run settles the decision against real prices. The only true backtest in the stack; keep it running.
2. **Event-driven spot checks (recommended for calibration)** — one historical run for a decision-relevant date: `scripts/run_analysis.py run TICKER YYYY-MM-DD --quick` (local swift, low effort, 1/1 rounds, leak-protected, ~30–90 min). Use when the user asks "what would it have said at event X?" — never for sweeps.
3. **Native backtest sweep (the CLI above)** — for a bounded research program ("how has the pipeline scored on RHM over the last two months?"): resumable, scored (hit rate + alpha), isolated from the live log. The first sweep in this environment is still a discovery run — expect to triage its per-cell logs the same way as any run.
4. **Rule-based proxy (no LLM)** — extract the report's trigger rules (entry zone, invalidation) and test them against historical prices with a script (yfinance + pandas). Cheap and fast, but it tests the rules, not the LLM's judgment — label it as such.

Recommendation: tier 1 always on; tier 2 per concrete past event; tier 3 when the user asks for a research program; tier 4 for entry-zone sanity checks.

**Pitfall:** a past-date run with a news supplement must contain no articles published after the as-of date. The pipeline builders filter by date, but a manually merged or reused supplement bypasses that — verify every item's `published_at` ≤ as-of date before a historical run (single spot check OR backtest grid cell), or the "historical" verdict silently contains future news.
