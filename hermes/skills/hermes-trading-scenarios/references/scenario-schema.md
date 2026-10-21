# Scenario output schema

Use this structure. Do not fill missing values by guessing.

## Evidence header
- Ticker / instrument
- Analysis date
- Latest market-data timestamp found
- TradingAgents rating/decision
- Data-quality confidence: High / Medium / Low

## Bull scenario
- Judgmental probability or confidence
- Trigger/confirmation
- Entry zone
- Target zone
- Invalidation
- Stop logic
- Horizon
- Catalysts
- Principal risks
- Estimated reward/risk

## Base scenario
Use the same fields.

## Bear scenario
Use the same fields. Treat this as downside analysis, not an instruction to short.

## Evidence ledger
For each material claim: claim, source/provider, observation date, report file, confidence, and whether it is fact or interpretation.

## Consistency rules
- Probabilities sum to 100% and are rounded to 5 percentage points.
- Targets and stops are conditional, not promises.
- No price level without support in the generated report.
- State conflicts and stale data explicitly.
- Distinguish company-specific risk from market, sector, currency, and event risk.
