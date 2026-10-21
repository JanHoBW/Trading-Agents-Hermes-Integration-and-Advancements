# Hermes Integration — How-To

This fork is designed to run as a **long-lived agent setup**: built and tested
under a Hermes Agent profile, it works from any shell with the same
environment layout. The repo itself is a plain Python package with no Hermes
dependency — the "integration" is the stable home layout, the env/config
surface, and fork-only LLM endpoints that fit how an agent actually runs
(nightly, unattended, with local or subscription backends).

## What differs from a one-shot CLI run

- One home directory (`~/.tradingagents`) accumulates **memory across runs**
  — decisions are logged, settled against what actually happened, and used to
  calibrate future lessons.
- Runs are **unattended**: every CLI question can be pre-answered with a
  flag or a `TRADINGAGENTS_*` variable, so an agent can launch
  `tradingagents --ticker … --save --no-show` and read the report from a
  predictable path.
- LLM backends fit an agent: local thinking models with long timeouts, or a
  Copilot subscription endpoint (fork-only).

## 1. Install

```bash
git clone https://github.com/JanHoBW/Trading-Agents-Hermes-Integration-and-Advancements.git
cd Trading-Agents-Hermes-Integration-and-Advancements
python3 -m venv .venv && source .venv/bin/activate   # Python 3.11+
pip install .
```

## 2. Home directory

Defaults live under `~/.tradingagents` (override per path):

| Path | Env override |
|---|---|
| `~/.tradingagents/logs/` (saved reports) | `TRADINGAGENTS_RESULTS_DIR` |
| `~/.tradingagents/cache/` (data cache) | `TRADINGAGENTS_CACHE_DIR` |
| `~/.tradingagents/memory/trading_memory.md` | `TRADINGAGENTS_MEMORY_LOG_PATH` |
| `~/.tradingagents/german_equities.json` (issuer registry) | `TRADINGAGENTS_ISSUER_REGISTRY` |

For a per-profile agent setup, keep one home per profile so memory never
crosses between agents.

## 3. Keys (`.env`)

```bash
cp .env.example .env
```

The package loads `.env` via `find_dotenv(usecwd=True)` — run from the repo
root (or any directory whose ancestor chain contains your `.env`). Fork-
relevant keys, all optional:

| Key | Purpose |
|---|---|
| `FINNHUB_API_KEY` (or `FINNHUB_API_TOKEN`) | Finnhub analyst/insider data (US) |
| `MARKETAUX_API_TOKEN` | MarketAux news |
| `TRADINGAGENTS_NEWS_SUPPLEMENT` | Path to a news-supplement config (activates the vendor) |
| `FRED_API_KEY`, `ALPHA_VANTAGE_API_KEY`, LLM keys … | upstream sources, as usual |

Keyless sources: `ureg_pdmr` (German insider), `issuer_rss` (first-party
feeds), `official_europe` (ECB/Eurostat), `polymarket`.

## 4. LLM backends

### Local thinking models (LM Studio, vLLM, llama.cpp)

```bash
export TRADINGAGENTS_LLM_PROVIDER=openai_compatible
export TRADINGAGENTS_LLM_BACKEND_URL=http://localhost:1234/v1
export TRADINGAGENTS_QUICK_THINK_LLM=<model> TRADINGAGENTS_DEEP_THINK_LLM=<model>
# thinking models: long turns exceed the 600 s SDK default
export TRADINGAGENTS_LLM_REQUEST_TIMEOUT=1800
# thinking effort is forwarded to compatible servers (no model-name gate)
export TRADINGAGENTS_OPENAI_REASONING_EFFORT=high
```

### GitHub Copilot endpoint (fork-only)

```bash
export TRADINGAGENTS_LLM_PROVIDER=openai_compatible
export TRADINGAGENTS_LLM_BACKEND_URL=https://api.githubcopilot.com/v1
export TRADINGAGENTS_DEEP_THINK_LLM=gpt-6-luna   # or gpt-5.x per your plan
```

When the base URL's host is `*.githubcopilot.com`, the client automatically
switches to the Responses API and adds the editor-attribution headers the
endpoint requires. **Keep this fork private or accept the ToS risk**: the
headers mimic a VS Code editor session, which is a gray zone — this block
never ships upstream.

## 5. Unattended (agent) runs

```bash
export TRADINGAGENTS_OUTPUT_LANGUAGE=German
export TRADINGAGENTS_MAX_DEBATE_ROUNDS=1 TRADINGAGENTS_MAX_RISK_ROUNDS=1
tradingagents --ticker SAP.DE --date 2026-10-15 --analysts market,news,fundamentals --save --no-show
```

The saved report lands in
`~/.tradingagents/logs/reports/SAP.DE_<timestamp>/` — a stable, predictable
path an agent can read back without shell history. A run also settles every
logged decision whose window has passed (for any ticker), so memory upkeep
rides along with each analysis. `settle_all_pending()` does the same without
an analysis, for a scheduled job.

## 6. Memory that compounds

- **Multi-horizon settlement**: each decision is scored at several horizons.
  `TRADINGAGENTS_EVALUATION_HORIZONS="5,21,63,126"` (trading days, first
  entry = anchor). Default: `5,21,63,126`.
- **Calibration**: `memory/calibration.py` tracks forecast-vs-outcome per
  horizon; calibrated confidence feeds the risk/manager agents.
- **Real portfolio (optional)**: `~/.tradingagents/portfolio.csv`, one row per
  position:

  ```csv
  ticker,shares,entry_date,entry_price,close_date,close_price,note
  XYZ.DE,50,2026-01-15,100.00,2026-03-01,110.00,cut first tranche at stop
  ```

  Closed rows (both close fields filled) record their **realized return** as
  ground truth on the next run — it leads the predicted horizon alphas in
  calibration. Tranches opened on the same day aggregate. Set
  `portfolio.path=None` in the config to stay in pure paper-trading mode.
  `portfolio.benchmark_ticker` (e.g. `^GDAXI`) computes realized alpha.

## 7. German/European coverage

1. Copy `config/german_equities.example.json` to
   `~/.tradingagents/german_equities.json` and add your tickers. The registry
   maps each ticker to (a) its Unternehmensregister identity (for the
   keyless PDMR insider feed) and (b) first-party news RSS feeds.
2. The chains then resolve per tool: insiders → `ureg_pdmr` first
   (official, keyless), news → `issuer_rss` first for registered issuers,
   then licensed aggregators; Euro-area macro → `official_europe`, FRED
   fallback.
3. Coverage matrix, gaps and per-source notes: [`german-market-data.md`](german-market-data.md).

## 8. Testing

```bash
python -m pytest -q        # ~2 min, fully offline-capable (fixtures shipped)
```

Safe inside an agent profile shell: an autouse fixture in `tests/conftest.py`
pins credential tests to a tmp directory — pre-v1.0 upstream, one test could
write into a real parent `.env` in exactly that situation.

## 9. Profile-level wrapping (outside this repo)

What a Hermes profile adds on top (none of it is in this repo):

- a **launcher** that pins the env for the profile (provider keys,
  `TRADINGAGENTS_*` run settings, home paths),
- a **skill** with the operating manual: report locations, memory semantics,
  when to settle, how to read a calibrated lesson,
- per-ticker **report archiving** (e.g. `reports/<TICKER>/<date>/`).

Because the repo stays Hermes-free, the same fork works equally well as a
plain cron job or from a terminal — the profile wrapping is the convenience
layer, not a dependency.
