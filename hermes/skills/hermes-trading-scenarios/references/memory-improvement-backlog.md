# TradingAgents Memory Subsystem — Improvement Program

Long-term decision evaluation for the memory subsystem (multi-month, not just
the single 5-trading-day scalar). This is the durable record of the framework's
memory-development program: what is implemented, the design invariants, and the
open backlog to pick up in any future framework-development session (any chat).

Repo: `~/hermes/workspace/Home/TradingAgents` (code only). The memory log is
`~/.tradingagents/memory/trading_memory.md`. Run everything from the repo root
with `env -u PYTHONPATH .venv/bin/python`. Track changes with git commits,
**pushed to the fork remote** (`fork` → JanHoBW/Trading-Agents-Hermes-Integration-and-Advancements;
token from profile `.env`; local-only convention retired 2026-10-20).

## Implemented (as of 2026-10-17)

- **Multi-horizon settlement.** `evaluation_horizons: [5, 21, 63, 126]` trading
  days in `default_config.py`. The first window is the **anchor** (fast signal;
  the `resolved` tag reflects it). Later windows show how the call aged over
  longer horizons. `None`/empty falls back to the legacy `[holding_period_days]`.
- **Structured HORIZONS block.** Per-horizon `{raw, alpha, resolution}` stored
  between `DECISION` and `REFLECTION` in the log. The DECISION regex stops
  before it; REFLECTION stays final (greedy to end). Legacy single-scalar
  entries still parse (fallback horizon from the tag scalar).
- **Calibration layer** (`memory/calibration.py`). Outcome win-rate per ticker
  and cross-ticker, per horizon: a "hit" is alpha landing on the rating's
  expected side (Buy/Overweight > 0, Sell/Underweight < 0, Hold ≈ 0 within a
  band). Injected into the PM `past_context` in `trading_graph._memory_step`,
  `as_of`-filtered so backtests stay look-ahead clean. Pure aggregation — no LLM
  call — so it recomputes cheaply every run and is empty (blank line) until
  decisions start settling.
- **Supersede idempotency.** `store_decision`: re-running the SAME rating for a
  ticker+date is a no-op; a *different* rating for a still-pending ticker+date
  replaces the entry (the freshest/delivered analysis wins); a finalized
  (resolved) entry is never superseded. This fixes the 2026-10-14 case where a
  lower-quality earlier run had claimed the slot.
- **Evolving multi-horizon lesson (replaces the legacy 5d reflection).**
  Settlement runs two phases: phase 1 finalises pending entries on the anchor
  and writes a lesson covering every measured window; phase 2 accumulates
  longer windows on resolved entries and *refreshes* the lesson whenever a new
  window closes. `REFLECTION_COVERS:` records which windows the stored lesson
  covers; a resolved entry whose scored set is not a subset of `covers` is
  flagged for settlement again (stale lesson — incl. the one-time legacy
  upgrade for fully-scored entries without covers). 5d is only the anchor of
  the default set `[5, 21, 63, 126]` now — no separate legacy path. The old
  `reflect_on_final_decision` name/signature is preserved (backward compat;
  the repo tests mock it by name).
- **Configurable evaluation windows.** `get_evaluation_horizons` is
  three-tiered: env var `TRADINGAGENTS_EVALUATION_HORIZONS` (comma/semicolon)
  > `config["evaluation_horizons"]` > legacy `[holding_period_days]`.
  Non-positive values are dropped; all-non-positive env values fall through to
  config (never a silent `[5]`).
- **Realized P&L from a manual CSV (option 9, real portfolio).**
  `memory/realized.py` parses the broker-agnostic CSV at `portfolio.path`
  (default `~/.tradingagents/portfolio.csv`, template shipped outside the
  repo): closed row = `close_date` AND `close_price` > 0 (half-filled close
  fields warn and keep the position open); rows sharing (ticker, entry_date)
  are tranches, aggregated (shares summed, prices share-weighted) into one
  outcome. `record_realized_trades()` writes the realized return into the
  matching log entry via `merge_realized` (idempotent — identical return is a
  no-op, a changed return re-records; it never flips pending).
  `attribute_realized()` joins closed trades with the decisions that opened
  them (per-trade P&L, direction hit, optional benchmark alpha via
  `settlement.get_closes` — monkeypatchable like the settlement tests);
  `render_realized_attribution()` gives the "REALIZED P&L" line. Wired into
  `trading_graph._memory_step`: record right after settlement (same run sees
  it), then the attribution line is appended after the calibration line in the
  PM `past_context`; both steps fail soft. `calibration.py` gained a
  `realized` bucket — ground truth, rendered FIRST, counted into the overall
  n. `path=None` keeps paper/research mode disabled.
- **Human feedback loop (#8).** `tradingagents memory feedback TICKER
  --note "..." [--rating gut|teilweise|falsch] [--date YYYY-MM-DD]` stores a
  one-line `HUMAN_FEEDBACK:` JSON block (`{datum, bewertung, vermerk}`) at the
  end of the entry — `log.add_human_feedback()` (idempotent: identical
  annotation is a no-op, a changed one replaces; works on pending and settled
  entries; unknown ticker/date errors with the available tickers listed).
  `tradingagents memory show [TICKER] [--limit N]` reads entries back (outcome,
  lesson, feedback). The regexes are order-independent (the line may sit before
  or after REFLECTION), and `merge_horizons` carries the stored annotation into
  the rebuilt block so settlement never erases it. `get_past_context` includes
  a *pending* entry when it carries feedback, so the loop closes on the very
  next run instead of waiting out the anchor horizon — but the `as_of`
  backtest filter still drops it (no resolution date), keeping look-ahead clean.
  Rendered in the PM context as a `HUMAN FEEDBACK (date, rating)` line via
  `_format_full`. 17 new tests (log + CLI); full suite 1454 passed.

## Files touched

- `default_config.py` — `evaluation_horizons`
- `memory/log.py` — HORIZONS regex/parse/format, `_pct_to_frac`,
  `_parse_horizons`, `get_entries_needing_settlement`, `merge_horizons`,
  `store_decision` supersede
- `memory/settlement.py` — `get_evaluation_horizons`, `fetch_horizons` (+ cheap
  date guard), `settle_pending`/`settle_all_pending` over the multi-horizon windows
- `memory/calibration.py` — new (win-rate aggregation + render; realized bucket)
- `memory/realized.py` — new (CSV reader, tranche aggregation, record/attribute/render)
- `memory/reflection.py` — lesson prompt (JSON contract + prose fallback),
  `Lesson` (str subclass), `parse_lesson`/`render_lesson`
- `graph/trading_graph.py` — calibration + realized-attribution in `past_context`
- `tests/test_memory_log.py` (114 tests, incl. structured-lesson JSON),
  `tests/test_realized_portfolio.py` (35)

## Design invariants (do not break)

- One entry per (ticker, date); **latest pending rating wins** (supersede),
  **resolved never overwritten** (the outcome is final).
- The **anchor finalises** a pending entry (tag → resolved, reflection written
  on the anchor figures); **longer horizons accumulate** on later settlement
  passes without re-resolving or re-reflecting.
- `fetch_horizons` **skips a window whose calendar span has not elapsed** without
  a price fetch (date guard) — a multi-horizon pass stays light while most
  windows are still in the future.
- Settlement writes each scored window as soon as it is computed (interrupt-safe);
  a still-pending entry and its unresolved windows simply retry next pass.
- Only `settled`/scored figures feed `get_past_context` and calibration; `as_of`
  gates point-in-time so a backtest at date D never sees an outcome resolved after D.
- `reflect_on_final_decision` returns a **str** — the repo tests mock it by name
  and the settlement path consumes str, so the structured payload rides on a
  `Lesson` (str) subclass (`.lesson_json`), never a dict return. Storage:
  `REFLECTION_JSON:` is one JSON line directly before the REFLECTION block;
  `merge_horizons(reflection_json=...)` — `None` preserves the stored lesson,
  a dict replaces it (window-accumulation passes must pass `None`).

## Verification done

- **Repo suite: 206/206** — `test_memory_log.py` (101), `test_realized_portfolio.py`
  (35), `test_dataflows_config`, `test_portfolio_context`, `test_backtest`,
  `test_graph_end_to_end`. All touched modules byte-compile; `realized` and
  `calibration` import by name.
- **Offline harness (scratch `test_memory_longterm.py`, no network/LLM): 50/50
  checks** — supersede semantics, anchor-then-accumulate settle, stale-lesson
  flag (incl. one-time legacy upgrade), env-var windows, explicit-anchor
  no-flip, reflection dispatch (multi-horizon vs legacy single-window), the
  REALIZED/REFLECTION_COVERS blocks, and the full manual-CSV → record →
  calibrate → attribute flow (open positions stay open, idempotent re-run,
  realized rendered first in the calibration line).
- The live 2026-10-14 RHM entry was corrected Overweight→Underweight through
  the supersede path (real-data demo).

## Verifying LLM-prompt changes without a full run

"Only verifiable via a full LLM run" means a **graph-level** change needs a
full run; a change that IS one LLM call (prompt format or output contract, e.g.
#2) is verified with the unit suite plus a targeted live probe — minutes, not
hours:

1. Unit suite under the venv (mocked LLM): proves the parse/merge/preserve
   code, not model compliance. Never claim a prompt change verified on mocks
   alone.
2. Targeted live probe (scratch script, repo venv Python): read the provider
   key from the profile `.env` via `dotenv_values` (never print it), construct
   `ChatOpenAI(base_url=<provider base URL>, api_key=key, model=<production
   quick-model>)`, pull one real decision text from the memory log via
   `TradingMemoryLog(...).load_entries()`, and call the changed code path
   directly (e.g. `Reflector(llm).reflect_on_final_decision(...)` — exercise
   both the multi-horizon and the legacy single-window signatures). Assert the
   output parses per the new contract and measure the rendered length.
3. Record which model was probed in the backlog entry before marking done. If
   the local backend is down, the cloud model is a valid probe target — the
   prompt is model-agnostic and the prose fallback covers non-compliance.

Pitfalls:
- A model asked for a JSON schema writes verbose values — a schema with no
  length budget yields ~2× the needed prose. Put an explicit compactness
  budget in the instruction (≤2 sentences per field, ~80 words total) and
  verify by word count, not parse success (parsing succeeds on a 200-word
  answer too).
- Mocked ChatOpenAI: `call_args[0][0]` is a list of `(role, content)` tuples —
  index `[i][1]` for the prompt text; a bare `system, human = ...` unpack
  yields tuples and every `in`-assertion silently tests tuple membership.
- `str` subclass attributes need a class-level annotation (e.g.
  `lesson_json: dict | None = None`) or pyright/LSP flags every access; the
  annotation doubles as the contract documentation.

## Open backlog (pick up for further development)

Recommended order by leverage/effort (the original 9 options, 7→1+2→3→4→5-6):

- [x] **#7 Idempotency fix** — supersede (done).
- [x] **#1 Multi-horizon** — done (above).
- [x] **#3 Calibration layer** — done (above).
- [x] **#2 Structured lesson JSON** — done (2026-10-17). `reflection.py` now
  asks the LLM for a single JSON object with the exact keys
  `{these_claim, vorhersage, outcome_evidenz, lektion, konfidenz, horizonte}`
  (compact budget: ≤2 sentences per field, ~80 words) and returns a
  `Lesson` — a `str` subclass, so every existing caller stays untouched —
  carrying `.lesson_json` (dict or None). `parse_lesson()` normalises
  (fences, prose padding, word/number confidence, horizon ints); invalid
  JSON falls back to storing the free prose verbatim (legacy behaviour).
  `log.py` stores/reparses a `REFLECTION_JSON:` line (one JSON line directly
  before the REFLECTION block); `merge_horizons(..., reflection_json=...)`
  writes/replaces it and preserves the stored lesson across window-
  accumulation passes. Verified: full repo suite 1437 passed; live check
  with the production cloud model (Weber-Cloud `qwen/qwen3.8-27b-medium`,
  real RHM decision text) → valid JSON on both the multi-horizon and the
  legacy single-window path (~100 words rendered, vs ~200 before the
  compactness budget). Local model (xhigh on the LM Studio host) re-check pending the
  host being back up — the prompt is model-agnostic, prose fallback covers
  non-compliance either way.
- [ ] **#4 Distribute memory to the 4 analysts** (currently only the Portfolio
  Manager gets `past_context`). Deferred: invasive (4 analyst factories +
  subgraph prompts) and **only verifiable via a full LLM run**.
- [ ] **#5 Trigger/milestone tracking** — parse stops/re-entry/invalidation from
  the decision, track the path over time (not just window-end alpha).
- [ ] **#6 Semantic retrieval** — embeddings + local index over the log (scale
  for a growing log), replacing the flat same-ticker/cross-ticker fetch.
- [x] **#8 Human feedback loop** — done (see Implemented): `memory feedback`
  + `memory show` CLI, `HUMAN_FEEDBACK` block on the entry, survives
  settlement, pending entries with feedback reach the next run's PM context.
- [x] **#9 Realized P&L attribution** — done (see Implemented). User confirmed a
  real portfolio (2026-10-15); data source = manually maintained CSV
  (option 1): template + parser + broker-agnostic attribution.
- [ ] **#10 Execution/refusal tracking** (openInvest-inspired decision
  accounting) — link each logged decision to what actually happened:
  executed / refused + reason; auto-match a trade within 7 days against the
  same-ticker/same-direction decision. New `EXECUTION:` block next to
  `HUMAN_FEEDBACK` in the log entry; extend `tradingagents memory feedback`
  with `--executed/--refused --reason=...`. Highest-value port from
  openInvest's decision accounting (their "how often did I actually follow?"
  loop). Fits the existing pending-entry-with-annotation mechanism.
- [ ] **#11 Discipline ledger** — counterfactual P&L: what would have happened
  if we had followed the advice, and what safety rules blocked. Built on #10 +
  existing settlement window scoring; rendered in PM context / report like the
  calibration line.
- [ ] **#12 Follow-through report** — aggregate "how often did I actually
  follow?" across the decision log: adoption rate per ticker/horizon,
  refused-with-reason breakdown. Belongs in `memory show` and the HTML report;
  natural seed for a small read-only dashboard over trading_memory.md +
  portfolio.csv.
- [ ] **#13 MCP server wrapper** (optional) — thin stdio MCP exposing
  status/decisions/run as tools for agent integration; adopt openInvest's
  readOnlyHint/destructiveHint conventions. Additive only, no core changes.
- [x] **#14 Single report tree per run** — DONE (2026-10-18). Upstream v0.7.0 wrote TWO overlapping trees into results_dir; now unified via opt-in `results_layout` config (`TRADINGAGENTS_RESULTS_LAYOUT`, default `"shared"` = upstream behavior unchanged): in `"run_dir"` layout the three writers (`cli/run.py:_run_directory`, `trading_graph.py:default_report_path` + state-log path) put everything into the run's own directory — message_tool.log + full_states_log at top level, all reports in `reports/` unstamped. Wrapper sets it; out-dir moved to `~/.tradingagents/reports/trading-scenarios/<TICKER>/<DATE>[-suffix]` (user overturned the 2026-10-11 "nichts verschieben" decision) + profile symlink `<profile>/reports → ~/.tradingagents/reports`; existing runs migrated in place. Tests: `tests/test_results_layout_run_dir.py`. Docs: `docs/advancements.md` (Fork v1.0.2), skill `references/directory-layout.md`. Upstream-able PR like #1529–#1531 (still to open).
- [ ] **#15 Bull/Bear fact-check round** (openInvest-style cross-challenge,
  prompt-level) — after the debate rounds, one targeted round where bull/bear
  verify each other's contested claims with cited sources; no new agents, no
  IPC seam. Rationale: debate is the biggest time sink (~37% of run wall time);
  quality lever without adding latency. Check upstream-ability first (prompt-only).
- [ ] **#16 Bull/Bear debate prefix-caching (LM Studio)** — analysis done
  2026-10-19, implementation pending. Findings on the current state
  (`bull_researcher.py` / `bear_researcher.py`, flat single prompt per side):
  the static prefix (role + focus + analyst reports) is already byte-stable
  across rounds → LM Studio's prefix cache hits that block every round; no
  truncation/sliding window in the debate code ✓; same model for all steps ✓.
  Waste: "Last bear argument:" duplicates the last history line (re-prefilled
  each round); the static tail instruction sits after `history`, so a round is
  not a strict prefix extension of the previous one. Plan (env-gated seam,
  LangGraph nodes untouched): `TRADINGAGENTS_DEBATE_CHAT_MODE=1` → send a chat
  message list `[system(role+focus), user(reports block + instructions)]` and
  append exactly one turn per debate step = strictly append-only prefix; drop
  the duplicated "Last X argument" line. Verify per the documented protocol
  (unit suite + targeted live probe) and measure LM Studio cache-hit stats
  before/after. User-side: context ≥ 32k on the LM Studio host, model kept loaded across
  all steps (VRAM lock, no reloads).
  **Phase 1 baseline — measured 2026-10-20** (controlled probe against
  <lmstudio-host>, Q4, prefill ≈ 340 tok/s; real reports as static prefix + real debate
  turns from the 2026-10-17/19 runs; client-side timing, no host access needed):
  - Full prefill of 22,475 tokens: **66.25 s** (CTRL request — matches 340 tok/s).
  - Strict prefix extension of identical size: **12.93 s** → **prefix cache works**,
    ~5× speedup; one byte changed early in the prefix destroys it (CTRL vs R3).
  - Code check (`bull_researcher.py`): no timestamps/round counters anywhere, and
    `history` grows append-only (`history + "\n" + argument`) → consecutive same-side
    calls share the prefix through all previously included history; each later round
    re-prefills only new turns + duplicated "Last X argument" (~1.5k) + tail (~0.3k)
    ≈ 15–18 s instead of ~50–66 s. The flat prompt already captures most of the benefit.
  - **Interference finding (user-confirmed):** Hermes runs on the same machine/server
    (one request at a time); any interleaved Hermes request destroys the prefix cache
    for the pipeline and adds queue delay — an operational issue affecting ALL caching,
    independent of prompt structure. Protecting TA runs from interference is worth more
    than #16 itself.
  - **Gate decision:** later debate rounds already get ~85 %+ prefix reuse when
    uninterrupted → chat-mode's incremental gain ≈ duplicate + tail only (~20 s per
    full run). **Not worth implementing in isolation.** Revisit bundled with #15
    (fact-check round) if/when that work happens.

## Run timings (reference)

Baseline wall times for planning (measured from message_tool.log / final log
lines; provenance.json carries started_utc/finished_utc per completed run):

- **Last full run: RHM.DE 2026-10-17** — lmstudio `unsloth/qwen3.8-27b` xhigh,
  debate 3 / risk 2 rounds: 16:12:26 → 19:52:06 = **3 h 39 min 40 s**.
  Per-analyst wall time (final log line): Market 2161 s | Sentiment 1284 s |
  News 1720 s | Fundamentals 634 s. Debate ≈ 37 % of run wall time (see #15).
- Quick-profile reference: RHM.DE 2026-10-19 (--quick, medium effort, 1/1
  rounds): start 09:15 → all four analyst reports written ~10:25 (~70 min);
  run aborted before completion (transient backend issue).
- Hardware reference (LM Studio host, Q4, measured 2026-10-20): prefill ≈ **340 tok/s**;
  a full debate prompt (~22k tokens) costs ~66 s uncached vs ~13 s with a prefix-cache
  hit. One request at a time on the server — any interleaved consumer (e.g. Hermes
  chat on the same model) destroys the cache for the pipeline and adds queue delay.

## Resolved decisions (2026-10-15, user)

- **Real portfolio/orders vs research-only?** Real portfolio — #9 is
  implemented and active by default (`portfolio.path` → template CSV).
- **Multi-horizon: replace or supplement the 5d reflection?** Replace — the
  evolving multi-horizon lesson supersedes the legacy 5d reflection; 5d is
  only the anchor of the default set, and the windows are freely
  configurable (env var > config > legacy fallback).

## Git checkpoints (local only — do not push)

- `e3a3735` — baseline checkpoint (empty commit) before the work = primary
  rollback point: `git reset --hard e3a3735`.
- `3873dd3` — multi-horizon settlement, calibration layer, supersede
  idempotency (5 files).
- `79d03ee` — evolving multi-horizon lesson, configurable windows,
  realized-P&L seam (`merge_realized`).
- `40d8fc8` — realized-P&L reader (manual CSV), attribution, calibration
  ground truth (5 files, +816/−18).
- `7c37f7d` — reddit per-ticker overrides (vendor: queries + subreddits from
  `TRADINGAGENTS_REDDIT_OVERRIDES`).
- `868a674` — structured lesson JSON (#2): `Lesson`/`parse_lesson`/
  `render_lesson`, `REFLECTION_JSON:` block, `merge_horizons(reflection_json=...)`,
  14 tests.
- The branch sits ahead of `origin/main`; nothing is pushed by design.
