# TradingAgents-Verzeichnislayout (Stand 18.10.2026)

**User-Entscheidung 18.10.2026:** Die Entscheidung vom 11.10. („3 Orte bleiben unverändert") ist **gekippt**. Alle App-Daten — inkl. Reports — liegen jetzt unter EINEM Root: `~/.tradingagents/`. Das Hermes-Profil hält einen Symlink dorthin (Komfort, kein zweiter Ort). Bei künftigen Requests auf diesem Layout aufbauen; Änderungen nur auf explizite Anweisung.

## Die Orte

### A. Repository — nur Code, Tests, Doku (NIEMALS Reports)
`~/hermes/workspace/Home/TradingAgents`
- Fork von `TauricResearch/TradingAgents`, Branch `main`; Basis Upstream **v0.7.0** (Merge `1ea3312`). Veröffentlicht als `JanHoBW/Trading-Agents-Hermes-Integration-and-Advancements` (Remote `fork`; `origin` = Upstream-Klon). Fork-Delta dokumentiert in `docs/advancements.md`.
- Laufzeit: Python-3.14.7-venv in `.venv`; immer `env -u PYTHONPATH .venv/bin/python`.
- Der Skill-Wrapper liegt NICHT im Repo (siehe D).

### B + C. App-Daten — EIN Root: `~/.tradingagents/`
```
~/.tradingagents/
├── memory/trading_memory.md      # Entscheidungs-Log (+ .lock)
├── portfolio.csv                 # echtes Portfolio (#9, P&L-Ground-Truth)
├── german_equities.json          # Issuer-Register (ISIN/WKN → Symbole)
├── cli_prefs.json
├── cache/                        # YFin-CSVs, sec_edgar/, Checkpoints — regenerierbar
└── reports/trading-scenarios/<TICKER>/<Datum>[-suffix]/   ← EIN Ordner pro Lauf
```
- `~/.tradingagents` ist hartkodiert im Framework (`_TRADINGAGENTS_HOME`, `default_config.py`) — Memory/Portfolio/Register/Cache defaulten dorthin. Seit 18.10. zeigt auch der Wrapper-Report-Root hierher (Backlog #14).
- **Symlink:** `~/.hermes/profiles/trading-agents/reports → ~/.tradingagents/reports` — beide Pfade identisch; in neuen Skripten den kanonischen Pfad verwenden.
- Zuweisbare Env-Overrides: `TRADINGAGENTS_CACHE_DIR`, `TRADINGAGENTS_MEMORY_LOG_PATH`, `TRADINGAGENTS_ISSUER_REGISTRY`, `TRADINGAGENTS_RESULTS_DIR`, `TRADINGAGENTS_RESULTS_LAYOUT` (Wrapper setzt `run_dir`).

### Laufstruktur — EIN Baum pro Lauf (Backlog #14, seit 18.10.)
```
~/.tradingagents/reports/trading-scenarios/RHM.DE/2026-10-18/
├── message_tool.log              # Timestamps je Nachricht (Timing-Analyse)
├── full_states_log_<Datum>.json  # kompletter LangGraph-State
├── provenance.json               # Provenance: prompt_versions, replay_id,
│                                 #   data_coverage, analysis_windows (beide Fenster),
│                                 #   quality_gate (Status + fehlgeschlagene Checks)
├── tradingagents.log
└── reports/                      # ALLE Report-Dateien, ungestempelt
    ├── market_report.md … investment_plan.md   (flache Stage-Dateien)
    ├── 1_analysts/ … 5_portfolio/             (Phasenordner)
    ├── data_coverage.json                     (Coverage-Matrix, maschinenlesbar)
    ├── quality_gate.json                      (APPROVED / NEEDS_REVIEW + Checks)
    └── complete_report.md + complete_report.html
```
- `run_dir` ist opt-in (`results_layout`, Default `shared` = Upstream-Verhalten byte-identisch). Ticker-Validierung läuft in beiden Layouts.
- **Alte Läufe (vor 18.10.)** wurden am 18.10. an den neuen Root migiert, behalten aber ihr altes internes Layout: Live-Dateien unter `<out>/<TICKER>/<Datum>/`, Reports im gestempelten Ordner `<out>/reports/<TICKER>_<YYYYMMDD_HHMMSS>/`. Stages finden mit `find <out> -name '*.md'` — nicht „reparieren", das sind historische Artefakte.
- Neuesten Lauf finden: `ls -t ~/.tradingagents/reports/trading-scenarios/<TICKER>*/ | head`; Ticker-Ordner tragen Börsensuffix (`RHM.DE`, `NVDA.XNAS`), Tages-Reruns Suffixe wie `-r2`.

### D. Wrapper + Skill (Profilbereich)
`~/.hermes/profiles/trading-agents/skills/hermes-trading-scenarios/`
- `scripts/run_analysis.py` = EINZIGES öffentliches Entry-Point (`run|news|doctor`, **v2.4.0** seit 2026-10-21; `ta.sh` gelöscht; Analysefenster `--news-window-days=N` / `--event-window-days=N` / `--no-event-context`); setzt `TRADINGAGENTS_RESULTS_DIR=~/.tradingagents/reports/trading-scenarios/<TICKER>/<Datum>[-suffix]` + `TRADINGAGENTS_RESULTS_LAYOUT=run_dir`. Enthält Orchestrierung (News-Pipeline, YAML-Metadaten, `--bg`, `--quick`), Argument-Validierung, LLM-Fallback-Chain und Provenance in einem Prozess/Skript.
- **Versioniert im Fork-Repo** unter `hermes/skills/hermes-trading-scenarios/` (seit Fork v1.0.4). Der Profilpfad `$HERMES_HOME/skills/hermes-trading-scenarios` ist ein **Symlink** auf dieses Repo-Verzeichnis — eine Quelle, keine Kopie. Änderungen also direkt im Repo machen, dann `git commit` + `git push fork main`; Hermes liest sie sofort über den Symlink.
- Referenzen: `scenario-schema.md`, `provider-integration.md`, `market-data-coverage.md`, `post-run-review.md`, `memory-improvement-backlog.md`, `directory-layout.md` (diese Datei).
- API-Keys: nur Profil-`.env` (0600); Wrapper reicht ausschließlich benötigte Keys an den Kindprozess weiter.

### E. Scratch / Auslieferung
`~/.hermes/profiles/trading-agents/cache/scratch/` (24 h-Pruning)
- `deliver/` = Auslieferungskopien; `pytest-of-jan/`, `node-compile-cache/` regenerierbar.

## Ständige Regeln
1. Reports nie ins Repo; Repo enthält ausschließlich Code/Tests/Doku.
2. Befehle: `env -u PYTHONPATH` vor dem venv-Python.
3. Auslieferung: Kopie nach `scratch/deliver/` oder `~/hermes/workspace/Home/`, dann `MEDIA:` mit absolutem Pfad.
4. PDF-Rendering: Headless-Chromium über localhost-HTTP (snap-Chromium: `file://` = `ERR_ACCESS_DENIED`, `--print-to-pdf` braucht absoluten Pfad im Home; `/tmp` ist snap-privat → Zieldatei immer verifizieren). Details in SKILL.md „Report delivery“.
5. Neue Analyse: `--date <ISO>` für saubere Ordner; Suffixe nur mit Absicht (z. B. `-audit`).
6. **Doku-Disziplin:** jede Änderung, die dieses Layout betrifft, aktualisiert diese Datei im selben Schritt (User-Anforderung 18.10., sessionsübergreifend).

## Offene Punkte (Stand 18.10.2026)
- Backlog #10–#13, #15 offen (siehe `memory-improvement-backlog.md`); #14 erledigt (dieses Layout).
- Alpha-Vantage: im Chat offengelegter Schlüssel sollte rotiert sein; gültiger liegt im Profil-`.env`.
