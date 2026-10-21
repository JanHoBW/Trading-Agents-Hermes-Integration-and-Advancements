# Post-Run Review — Finished Report vs Live Prices

Use when the user asks days after a run to look at the latest report against current prices: "schau dir den letzten <TICKER> Bericht an", "bewerte die Schwankungen der letzten X Tage aus Basis aktueller Kurse", "ist das ein technisches Einstiegssignal?". Distinct from running a new analysis (Procedure 1–6) and from A/B comparison mode.

## Procedure

### 1. Locate the newest run

```bash
ls -t ~/.tradingagents/reports/trading-scenarios/<TICKER>*/ | head
# (identical via the profile symlink: ~/.hermes/profiles/trading-agents/reports/…)
```

Ticker dirs carry exchange suffixes (`RHM.DE`, `NVDA.XNAS`) — glob by prefix if unsure which dir exists. Take the newest dated run dir (same-day re-runs get suffixes like `-r2` / backend slugs; pick the one the user means, usually the latest). Runs from 2026-10-18 on are self-contained: everything is in `<run-dir>/reports/`. Older runs carry their exact timestamp in an inner `reports/<TICKER>_<YYYYMMDD_HHMMSS>/` dir name.

Key fact: report data is as of the **last completed trading day at run time** — a 09:08 morning run predates that day's close. Never trust a report price for "today"; always re-fetch.

### 2. Read exactly two files

Inside `<run-dir>/reports/` (runs from 2026-10-18 on; older runs: inside the stamped dir `<run-dir>/reports/<TICKER>_<YYYYMMDD_HHMMSS>/`):

- `5_portfolio/decision.md` — rating, tranche entry zones + conditions, stop levels, reassessment triggers ("was würde die These kippen").
- `1_analysts/market.md` — indicator table (EMA/SMA/Bollinger/MACD/RSI/ATR as of report date) and the **explicit trigger rules** (long/short setups with their conditions).

Do not read `complete_report.md` for this task (~300 KB; everything above is in it plus debate noise).

### 3. Fetch live price data

Yahoo chart API via curl with a browser User-Agent header (see stock-quick-analysis step 2):

- `range=5d&interval=1d` → OHLCV table for the recent sessions the user asked about.
- `range=3mo&interval=1d` → enough history to recompute indicators.

**Today's row is LIVE until market close (Xetra 17:30 CEST):** its "close" differs between calls and must be labelled intraday before then. After 17:30 the last row is final.

### 4. Recompute indicators at live prices

Report indicator values are stale by design — recompute with `scripts/live_indicators.py <TICKER>` (RSI-14 Wilder, MACD 12/26/9, SMA20/50, EMA10 from daily closes incl. today's live close). Compare **trends and trigger states**, not exact equality: report values come from the run's own data feed and can differ by a few points (e.g. MACD −30.7 in the report vs −30.0 recomputed) — that is normal; flag only discrepancies > ~5 %.

### 5. Evaluate against the report's OWN trigger framework

Check each explicit rule in `market.md` / `decision.md` against live values:

- which triggers are met / unmet today (e.g. "MACD-Histogramm > +5 für 3 aufeinanderfolgende Tage" → day 1 of 3),
- hard rules still binding ("kein Long, solange Kurs < SMA50 und MACD < 0"),
- whether live price has already moved past the report's planned entry zones — if so, say chasing violates the staged-entry logic and offer the pullback level or confirmation trigger instead.

The verdict must be a clear yes / no / not-yet on "ist das ein Einstiegssignal?" with: what would confirm next (concrete levels/days) and what invalidates (report stop / broken level). Never contradict the report's hard rules; if live data broke a level the report called support/resistance, state it explicitly with numbers.

### 6. Output (German, compact)

- Table: last N sessions OHLCV + Δ % + volume.
- Indicator table: report value vs live value vs status against each trigger (✓/✗).
- Verdict paragraph: Einstiegssignal ja/nein/noch nicht + Bestätigungsbedingungen + Stop-Level aus dem Bericht.
- Disclaimer: research only, not financial advice.

## Pitfalls

- **Report price ≠ current price.** Always re-fetch; a report generated in the morning predates that day's session.
- **Don't chase into the verdict.** If live price is already clearly above the report's planned entry zone after a strong candle, "buy now" contradicts the staged-entry plan — name the pullback level or confirmation trigger instead.
- **Intraday vs final close:** before 17:30 CEST label today's value as live; two fetches minutes apart legitimately differ.
- **Indicator conventions must match** (RSI Wilder smoothing, MACD 12/26/9) — otherwise the comparison against report values is meaningless.
