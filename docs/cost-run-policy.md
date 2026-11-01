# Low-cost runs and input identity

## Local policy (no credentials)

The wrapper reads `$TRADINGAGENTS_RUN_DEFAULTS` when set, otherwise
`~/.tradingagents/run_defaults.json`. No policy file is shipped enabled and the
Copilot feature gate remains off for other installations. Example **only after
explicit consent** to this fork-only endpoint:

```json
{
  "llm": "copilot",
  "allow_copilot": true,
  "copilot_model": "gpt-6-luna",
  "reasoning_effort": "medium",
  "debate_rounds": 1,
  "risk_rounds": 1
}
```

Credentials still come from the active profile `.env` at launch, never from the
policy. Invalid fields, model literals, efforts or round counts fail loudly.
CLI values override policy; explicit `--quick`, `--llm=lmstudio` or custom
endpoint selection does not inherit Copilot model/effort. `--quick` remains
local swift/low. Without a local policy a full run remains local, but debate
and risk defaults are now **1/1**, not 3/2. Zero debate rounds are not supported;
one turn per side preserves the graph's existing manager/trader workflow.

```bash
python3 scripts/run_analysis.py run XYZ --ui --llm=copilot --allow-copilot --copilot-model=gpt-6-luna --reasoning-effort=medium --debate-rounds=1 --risk-rounds=1
```

All value flags require equals form, including `--copilot-model=`; the model and
effort survive `--bg` forwarding. Copilot IDs are not repaired from colloquial
names: resolve the exact `/models` identifier and probe the actual configured
TradingAgents client, including a function-call round trip. The SDK credential
variable depends on provider (`openai`: `OPENAI_API_KEY`; compatible registry:
`OPENAI_COMPATIBLE_API_KEY`); the wrapper maps the saved Copilot token correctly.
The endpoint uses Responses plus editor headers and retains its ToS warning.

No model price or Copilot billing multiplier was provided by `/models` in the
2026-11-01 verification. Fewer rounds/medium effort reduce request and reasoning
budgets, but are **not** a verified currency saving or quality equivalence.
Relative to 3/2, 1/1 removes four bull/bear and three risk-debater calls. Actual
usage/cache accounting must come from the run/provider, not estimated prices.

## Replay ID schema 2

`replay_id` is a configuration/input fingerprint, not a promise of identical
model output. It includes selected and effective quick/deep models/providers,
backend and tier endpoints, output language, factor/benchmark policy, effort,
prompt hashes, analysis windows, and SHA-256 of supplement/book/issuer-registry
contents (not their filenames). Secrets are never part of the fingerprint.
Every provenance branch uses the effective child environment. Old IDs should
not be compared with the new schema. Live vendor evidence remains outside the
input fingerprint; market snapshot artifacts document that specific sample.
A full offline, all-vendor response replay is still a separate extension.

## Calibration integrity

Counts now distinguish `n` decisions, `n_outcomes` horizon/realized records and
`n_evidence` decisions with anchor or realized evidence. A repeated ticker/date
record is not a new decision. Anchor confidence uses the configured anchor,
not the shortest available horizon, and an anchor plus realized record for the
same decision counts once. Historical reads filter each horizon's resolution
and realized close date separately; later windows cannot leak merely because
the anchor has settled. Hold makes no directional forecast, matching backtest
semantics, so a small alpha is no longer counted as a directional Hold success.

Distinct decisions can still overlap in time and share market factors. Counts
are **not** an estimate of statistically independent sample size; confidence is
an existing heuristic, not a significance test or proof of investment edge.
Live memory format and settlement writes are unchanged: no storage migration.

Tests: wrapper `test_run_cost_policy.py`, `test_run_replay_integrity.py`;
`tests/test_calibration_integrity.py` plus existing confidence/backtest/realized
regressions. Fixtures in confidence tests now use distinct synthetic decision
dates instead of repeating one record to manufacture sample size.
