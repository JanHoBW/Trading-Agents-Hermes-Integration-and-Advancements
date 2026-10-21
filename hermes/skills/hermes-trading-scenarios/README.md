# Hermes Trading Scenarios

A read-only Hermes skill that wraps the local `TauricResearch/TradingAgents` CLI and converts its reports into auditable Bull/Base/Bear scenarios.

## Install

1. Review every file in this package.
2. Install and configure TradingAgents separately in an isolated environment.
3. Copy this folder into the active Hermes profile's skills directory:

```bash
mkdir -p "$HERMES_HOME/skills"
cp -R hermes-trading-scenarios "$HERMES_HOME/skills/"
export TRADINGAGENTS_HOME="$HOME/hermes/workspace/Home/TradingAgents"
```

4. Smoke test without running an analysis:

```bash
env -u PYTHONPATH "$TRADINGAGENTS_HOME/.venv/bin/python" "$HERMES_HOME/skills/hermes-trading-scenarios/scripts/run_analysis.py" \
  --ticker NVDA --date 2026-10-09 --dry-run
```

5. In Hermes, ask: `Trade scenario NVDA for a 1-3 month horizon.`

## Security properties

- no broker or wallet integration
- no order execution
- strict ticker/date/analyst validation
- subprocess argument list with `shell=False`
- bounded timeout
- per-run provenance and SHA-256 hashes
- generated reports only are read
- external content is explicitly treated as untrusted data

## Important limitation

The exact TradingAgents output directory is version-dependent. The wrapper sets `TRADINGAGENTS_RESULTS_DIR`; verify that your installed TradingAgents version honors it. If it writes elsewhere, configure that installation to write into the requested directory before using the skill.
