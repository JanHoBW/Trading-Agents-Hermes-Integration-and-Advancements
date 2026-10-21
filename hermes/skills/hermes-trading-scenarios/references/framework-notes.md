# TradingAgents Framework — auditierbare Fakten (Stand: 2026-10-17)

Dauerhaftes Wissen über den Fork + Upstream v0.7.0. Für andere Sessions:
dieses File ist die einzige Quelle für Framework-Details; SKILL.md verweist hierher.

## 1. Repo-Stand

- Fork: `JanHoBW/Trading-Agents-Hermes-Integration-and-Advancements` (remote `fork`), Upstream = remote `origin` (TauricResearch/TradingAgents).
- HEAD == `origin/main`: v0.6.0 (14 Upstream-Commits, 2026-10-16) + v0.7.0 (11 Commits, Merge 2026-10-17; 2 Konflikte gelöst: `cli/main.py`, `llm_clients/factory.py`), `git log HEAD..origin/main` = 0. Neueste Upstream-Blocke: `tradingagents/backtest.py`, `tradingagents/portfolio.py`, `cli/` (Typer-CLI), `main.py`.
- Locale Fork-Commits (AHEAD von Upstream): News-Supplement-Vendor, Reddit-Overrides (`vendors/reddit.py`: Multi-Term, `TRADINGAGENTS_REDDIT_OVERRIDES`, crypto_base), Checkpointing-Wrapper-Kompatibilität.
- Commit-Autor: Noreply-Email; Copilot-Block nur im Fork, nie in Upstream-PRs (Upstream-PRs #1529/#1530/#1531).

## 2. CLI (Upstream v0.6.0)

Entry-Point: `.venv/bin/tradingagents` (Typer; `main.py` → `cli/main.py`).

- Subcommands: `run` (Default) + `backtest`.
- `run`-Flags: `--ticker --date --analysts --checkpoint/--no-checkpoint --save --no-show --html --portfolio FILE`.
- `--portfolio FILE`: JSON → `load_portfolio()` (`tradingagents/portfolio.py`) → `PortfolioContext {cash, currency, positions[{ticker, quantity, average_price}]}` → `propagate(..., portfolio=...)` injiziert es in den Graph-State. **Trader, Risk-Debatter und Portfolio-Manager dimensionieren dann gegen die echte Position** statt eines leeren Buchs. `quantity` negativ = Short.
- `backtest`-Flags: `--start --end --every N` (Tages-Schritt), `--analysts`, `--portfolio`, `--run-id` (Resume), Output-Dir-Optionen.
- v0.7.0-Neuigkeiten (2026-10-17): `login`/`logout`-Subcommands (CLI-Login) + Update-Check, neuer `ChatGPT`-LLM-Provider, `REASONING_EFFORT_PROVIDERS` (Config-Liste: welche Provider `reasoning_effort` erhalten), Memory-Scoring-Fix, Reflection-Prompt-Fix.

## 3. Backtest-Engine (`tradingagents/backtest.py`)

- Gitter (Ticker × Datum): vollständiger Graph auf jeder Zelle, Leak-Schutz via `date_window.py` (`is_historical` + Withholding — keine Future-Daten).
- Isoliertes Memory-Log pro Backtest-Run: `results_dir/backtest/<run-id>/` (vermischt nicht mit dem Live-Memory).
- Resumierbar: `--run-id` führt eine unterbrochene Gitter-Iteration fort.
- `summarize()`: Hit-Rate + Ø-Alpha vs. Benchmark. Benchmark-Map: `.DE → DAX` (überschreibbar via `TRADINGAGENTS_BENCHMARK_TICKER`).
- **Einschränkungen:** 1 Model-Sample pro Zelle (keine Monte-Carlo), Text-Feeds (News) sind nicht archivierbar → Ergebnisse sind *indicative, not reproducible*. Kosten: 6 Zellen × 30–90 min ≈ 3–9 h (LM-Studio xhigh). Empfohlen: event-getriebene Einzel-Checks statt großer Gitter.
- Beispiel: `tradingagents backtest RHM.DE --start 2026-09-01 --end 2026-10-15 --every 7 --analysts market,news --portfolio`.

## 4. Zwei-Ebenen-LLM (`tradingagents/graph/setup.py`)

- `quick_thinking_llm`: 4 Analysten (market, news, fundamentals, social) + Bull + Bear + Trader.
- `deep_thinking_llm`: Research-Manager + Portfolio-Manager (Aggregation/Entscheidung).
- Env-Overrides: `TRADINGAGENTS_QUICK_THINK_LLM`/`_PROVIDER`, `TRADINGAGENTS_DEEP_THINK_LLM`/`_PROVIDER`.
- Aktuell (2026-10-16): beide = Qwen3.8-27B-xhigh (LM-Studio, Host <lmstudio-host>).
- **Cloud-Optimierung:** quick-Ebene = kleines/low-Effort-Modell (8 Agents, viel Volumen, einfache Tasks), deep-Ebene = xhigh (2 Agents, Qualitätskern). Spart Kosten bei gleicher Report-Qualität.

## 5. Config-Fläche (`tradingagents/default_config.py`)

- `global_news_queries`: US-Default (Fed, S&P 500, EZB, Zölle, US-China). **Für DE-Rüstung überschreiben:** NATO-Rüstungsbudget, deutsche Beschaffungen, europäische Rüstungsindustrie (via Config/Env im Wrapper).
- Weitere Keys: `news_article_limit`, `global_news_article_limit`, `max_tool_rounds`, `max_recur_limit`, `temperature`, `max_tokens`, `anthropic_effort`, `google_thinking_level`, `memory_log_max_entries`, `holding_period_days`, `evaluation_horizons`.
- `benchmark_map`: `.DE → DAX` (Backtest-Alphabericht).

## 6. Issuer-Registry (`~/.tradingagents/german_equities.json`)

- 19 deutsche Werte, u. a. RHM.DE (ISIN **DE0007030009**, WKN 703000, PDMR aktiv → Insider-Deals laufen).
- **`news_feed_url: null` bei RHM.DE** → `issuer_rss`-Vendor degradiert (kein öffentlicher Rheinmetall-RSS: Nuxt-SPA, 6 Kandidaten 404; Ariva: keine Per-Ticker-RSS — beide 2026-10-16 verifiziert). Kompensation: unser News-Supplement (dpa/gnews/de_feeds + Foren) via `--news-supplement`.

## 7. Agent-Tool-Nutzung (RHM-Run 2026-10-16, Provenance)

- `get_news` 4× (News-Analyst + Sentiment-Analyst, je 2 Aufrufe), `get_indicators` 2×.
- FRED + Polymarket **aktiv** (im News-Report sichtbar).
- `get_macro_indicators` / `get_prediction_markets` wurden von keinen Agents aufgerufen.
- Bull/Bear + Risk-Debatter haben **keine Tools** — Debatte = reines LLM über State (keine zusätzlichen API-Calls pro Runde).

## 8. Memory / Realization

- Memory-Log aktiv: `~/.tradingagents/memory/trading_memory.md` (akkumuliert Entscheidungen).
- `tradingagents/memory/realized.py`: settled offene Trades gegen reale Kurse bei späteren Runs.
- CSV-Contract (`~/.tradingagents/portfolio.csv`, case-insensitive): `ticker,shares,entry_date,entry_price,close_date,close_price,note`. Zeile ist *open*, bis close_date+close_price gefüllt; Tranchen-Splits aggregieren (gewichteter Ø).
- **Unterscheidung:** `portfolio.csv` = abgeschlossene Trades (Memory/Settlement). `--portfolio`-JSON = AKTUELLES Positionsbuch (Sizing für den laufenden Run). Beide kombinierbar.

## 9. Unsere News-Pipeline (Skill-Schicht)

- **Ein Entry-Point:** `scripts/news_collect.py` (fetch | merge | sources). Module in `scripts/news_sources/`: `dpa.py` (finanznachrichten-RSS + EQS/ADHOC), `gnews.py` (gnews.io 7-Tage + Request-Cache + merge), `de_feeds.py` (YAML-Feeds + boersennews-Forum-Parser + Sektor-Keywords).
- Quellen-Registry: `references/hardcoded_sources.yaml` (menschenlesbar, wächst über die Zeit: feeds, sectors, tickers mit forums + reddit-Overrides).
- **`run_analysis.py` = das EINZIGE öffentliche Entry-Point** (run | news | doctor; **v2.4.0** seit 2026-10-21, hat `ta.sh` absorbiert — ein Skript, eine Sprache; Flags: --quick = swift low 1/1, --no-news, --bg, **--ui**, --dry-run, --llm=NAME, **--portfolio=FILE** + Durchreichung: --analysts=, --debate-rounds=, --risk-rounds=, --timeout=, --lmstudio-model=, --weber-model=, --reasoning-effort=, --suffix=, --supplement=, --allow-copilot, --news-window-days=, --event-window-days=, --no-event-context; neu: doctor-Subcommand mit Exit 0/3/4, provenance.json auch bei --ui, prompt_versions + replay_id + analysis_windows; zwei Analysefenster: Headline 7d (Default, News/Sentiment/Supplement) vs. First-Party-Event-Kontext 180d — Details in `framework-capabilities-and-backtesting.md`).
- Workflow: `run_analysis.py run TICKER` → `news_collect.py fetch` → Supplement (schema v1, **harter Vendor-Cap 10 Items** — `vendors/news_supplement.py` lehnt >10 ab, also Pipeline mit `--max 10`) → `TRADINGAGENTS_NEWS_SUPPLEMENT` im Child-Env → `news_supplement`-Vendor in der `get_news`-Kette.
- Arbeitende Feeds (2026-10-16): tagesschau, finanzmarktwelt, n-tv, hartpunkt.de, esut.de. Disabled (Akamai/403): finanzen.net, boerse.de.
- StockTwits: Cloudflare-Bot-Schutz blockt diese Hosts (einziger toter Social-Kanal; Reddit + Forum tragen den Social-Sentiment-Beweis für RHM).

## 10. TUI-Modus `--ui` (Wrapper-Erweiterung, 2026-10-17)

- `run_analysis.py run … --ui`: dieselbe Pipeline, aber auf einer echten TTY in einem eigenen `gnome-terminal`-Fenster (Titel `TradingAgents <TICKER> <DATE>`, 170x48), damit das upstream `Live`-Layout rendert (Message-Log, Agenten-Panels, Report-Panels, Timings) statt in ein Log gepipet zu werden. Fenster-Script: `cd <repo>` → Run-Command → Exit-Code + „Fenster bleibt offen, Enter schliesst". `--ui` + `--bg` werden im Wrapper abgewiesen (Fenster braucht offene Interaktion).
- **Pitfall (kostete einen Smoke-Run):** der `subprocess.run(["gnome-terminal", …])`-Aufruf MUSS `env=gt_env` bekommen. Ohne `env=` fehlt der TUI die `TRADINGAGENTS_*`-Prefill-Umgebung (Modell, Provider, Runden, Sprache) → sie fällt auf interaktive Prompts zurück und der Run steht still. `gt_env = dict(build_child_env(...))` + Desktop-Session-Variablen aus `os.environ`: `DISPLAY`, `WAYLAND_DISPLAY`, `XDG_RUNTIME_DIR`, `XDG_DATA_HOME`, `XDG_CONFIG_HOME`, `XDG_SESSION_TYPE`, `XDG_CURRENT_DESKTOP`, `DBUS_SESSION_BUS_ADDRESS`, `GDK_BACKEND` — gnome-terminal ist Client/Server: der Client braucht die Session-Variablen für den Terminal-Server, und die Fenstershell erbt die übergebene Env (inkl. aller Prefills).
- Fenster-Command = Headless-Command mit `--show` statt `--no-show` (+ `--portfolio`, wenn gesetzt); `discover_command(root)`, `shlex`-quoted. Backend = `chain[0]` (Pre-Run-Reachability-Check läuft vorher — **kein reaktiver Fallback im Fenster**).
- Prompts bleiben supprimiert, weil alle TUI-Auswahl-Schritte die `TRADINGAGENTS_*`-Env-Checks in `cli/selections.py` honorieren; `openai_compatible` ist key-optional (`cli/prompts.py` `ensure_api_key`) → kein API-Key-Prompt-Risiko.
- **Kein `tradingagents.log`/`provenance.json`/`data_coverage` im `--ui`-Modus** (UI-Block returnt vor dem Attempt-Loop; Konsolen-Output war auf der TTY). Einzige Artefakte: gespeicherte Report-Dateien + das TUI-eigene `message_tool.log` (Live-Monitoring per `tail -f` bleibt möglich). Gap-/Fehl-Analysen im `--ui`-Modus nur aus Reports + `message_tool.log` — oder per headless Re-Run.
