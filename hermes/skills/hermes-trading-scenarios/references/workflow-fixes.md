# Making generic fixes to the TradingAgents workflow

Depth for SKILL.md's "Extending the workflow" section. Applies to repo and wrapper changes alike: every fix is ticker-agnostic, provider-agnostic, and auditable.

## Credential plumbing

- Read provider keys from the active profile `.env` (mode 0600) and inject them only into the TradingAgents child process; never print, log, commit, or pass them as process arguments. When verifying, assert the key's *length* or presence, never its value.
- **Verify end-to-end through the consuming vendor, not just the env plumbing.** A key present in the child environment proves nothing until the vendor's own read path plus a real fetch succeeds; run one live probe through the actual vendor function (e.g. the macro fetch) before declaring a credential gap closed.
- A missing credential dead-ends an entire data category as `NO_DATA` with no error in the report body. Surface it via the run's `data_coverage`/log markers, and fix it at the wrapper's credential allowlist plus the profile `.env` — not by adding a new provider.

## Vendor-CSV annotation convention

- OHLCV vendor output carries leading `#` comment header lines, and downstream consumers tolerate them. New annotations (provider name, symbol resolution, price-adjustment basis) follow this convention instead of inventing new formats.
- Label the adjustment basis explicitly on *both* sides: adjusted and unadjusted series each carry their own header label. An unlabeled series is an audit gap, because the same divergence can be an adjustment-basis difference rather than a market move.

## Feed omissions: estimate periods and as-of dates

- **Forward metrics (forward P/E, forward EPS, PEG) arrive from vendor feeds without an estimate period, and the report inherits the silence.** A raw line like `Forward PE: 17.71` states no year, so a report citing "17,7× Forward-Gewinn" leaves the reference period open. Trace the claim back to the raw `[Data]` line in the child's `message_tool.log` before answering; if the feed states no period, label the metric as the next estimate period (NTM) relative to the as-of date and say the provider does not specify one. Never assert a fiscal year the feed did not provide — SKILL.md §5 checks for exactly this before an answer ships.
- **Analyst-consensus figures (price target, recommendation, analyst count) must always carry their data as-of date.** The feeds (e.g. Alpha Vantage OVERVIEW) serve `Analyst Target (Mean)` / `Analyst Recommendation` / `Analyst Opinions` with no publication date — the only date in the feed is the quote timestamp. A report citing a 1,580 € target and 21 "strong_buy" ratings without a date cannot be freshness-checked, and "the consensus is stale" arguments (analyst lag after a price collapse) become unfalsifiable. Cite the consensus together with the feed's as-of timestamp (e.g. "Konsens laut Datenstand 09.10.2026: …") and state explicitly that the provider gives no per-rating publication dates; judge freshness against the as-of date, never against an unspecified date.

## Secondary / cross-vendor data fetches

- **Gate on the condition that actually indicates risk, so the healthy path adds zero extra API calls.** The cross-vendor OHLCV note runs only when the less-trusted vendor (Alpha Vantage, detected by its header) served the primary data; a yfinance primary makes no second call. An unconditional fan-out doubles latency and rate-limit consumption on every run.
- Secondary fetches are best-effort: they never raise, never mutate or block the primary result, and a comparison-source failure is swallowed (loggable), not reported as a data gap.
- Compare the same metric on the same dates; align adjustment basis before comparing; report divergence as an informational note, never as a corrected value.

## Adding a data vendor (chain semantics + clean degradation)

When adding a vendor adapter, remember the router walks the configured chain and **stops at the first vendor that returns a string**; a `NoMarketDataError` or `Vendor*Error` means "try the next vendor." Two rules follow:

- A vendor that cannot authoritatively answer must **raise `NoMarketDataError`, never return a "no data" string** — a string stops the chain and silences every later vendor. A terminal "nothing exists" answer belongs only to the vendor that genuinely owns that state (typically the last, general-fallback vendor). An aggregator whose sample is *limited or clamped by its plan* (rate-capped article count, a ~12-month insider feed) is not authoritative about absence, so an empty or fully-out-of-window result is a coverage gap: raise `NoMarketDataError` with a "not proof that no X exists" detail and let the chain continue.
- Give an uncertain-coverage vendor a **cheap coverage gate first**: probe a minimal endpoint that a covered instrument fills and an uncovered one leaves empty. Empty → `NoMarketDataError` (clean fall-through), which stays distinct from a vendor outage (`VendorUnavailableError`) and from a covered-but-empty terminal answer (honest string). This is what lets a market-limited vendor chain ahead of the general vendors for *any* ticker: an uncovered ticker passes straight through instead of erroring or returning zeros as fact.

`coverage_gap()` bounds what a source actually observed and takes **datetime objects, not date strings** — it calls `.date()`/`.isoformat()` on each. Mirror the established news-vendor split: keep `pub()` returning the date *string* for the in-window filter, and add a `pub_dt()` returning the real `datetime` (or `None`) to feed `coverage_gap`. Passing strings crashes the moment a source returns items that all fall outside the window (the "everything is recent" case a limited plan produces), and the empty-window path hides the bug because an empty generator uses a default.

**One config category string drives every tool in that category** — the router filters the configured vendor list per method, so a single string re-wires all tools in the category together. After changing it, resolve *each* tool's effective chain (config string ∩ vendors implementing that method): a vendor in the string but not implementing a method is silently absent from that tool, and one implementing it but absent from the string is never called. Order the chain by provenance priority — first-party issuer feed > licensed aggregators > general fallbacks — and never let a licensed aggregator shadow the ticker's own feed.

## Running LLM backends and time budgets

- **The wrapper must never discard child output on timeout or failure.** It must persist the partial `tradingagents.log` and write `provenance.json` with `timed_out`/`partial` flags before failing — a killed run is the run you need to diagnose most, and without this every timeout is a black box. This is a wrapper-code requirement; the run-side handling (backend fallback on a hard quota block, timeout budgeting for a slow local backend, live monitoring, and the stale-partial-artifacts pitfall) is covered by SKILL.md §2a.
- **The LLM fallback chain (`auto` = copilot → lmstudio → webercloud) must fall back only on LLM-backend faults** (quota, connection, auth), detected by matching the child log tail against a failure regex. A data-vendor degradation (marketaux timeout, StockTwits 404) never crashes the run, so it must NOT trigger a fallback — otherwise one flaky vendor burns the whole chain. A timeout that produced report files is real partial progress and must not be discarded either. Keep per-attempt logs (`tradingagents.<profile>.log`) and list every attempt in provenance `attempts`.
- **Reasoning models burn `max_tokens` on hidden thinking tokens** (Weber-Cloud `qwen/qwen3.8-27b-medium` returned `content: None` with `max_tokens=25`, all 25 as `reasoning_tokens`; at 200 it delivered 154 thinking + 46 visible). For reasoners leave `max_tokens` at the default/unbounded rather than capping it, or the visible answer starves.
- **A reachability probe for an OpenAI-compatible endpoint must treat any HTTP answer (200/401/403) as reachable** — only connection-level failures (DNS, timeout, no route) count as unreachable. Weber-Cloud's `/models` returns 401 without a key, which wrongly disqualified the backend from the auto chain until the probe caught `HTTPError` separately.

## Testing and mocking

- Add unit tests alongside each fix; the full suite must stay green before claiming done.
- **Mock the layer the code under test actually calls, at a level that is not already patched.** If a test base class already patches the module's fetch helper, patching the lower-level transport it wraps is dead code — that patch never fires and the test still "passes" without exercising the path you think it does. Set the already-mocked helper's `side_effect` instead.
- **Patch module-level names where the symbol is *used*, not where it is defined.** `from x import VENDOR_METHODS` copies the binding into the importing module — mock `tools.VENDOR_METHODS`, not `x.VENDOR_METHODS`; a test patching the wrong side silently exercises the real object and still passes.
- **Price-history code goes through the `settlement.get_closes` seam.** The repo's tests monkeypatch `settlement.get_closes` as a module attribute, and the suite stays offline (no network, no LLM). New code that fetches price history must call it the same way (via the module attribute, `settlement.get_closes(...)`) instead of importing the fetch function directly from the vendor layer — otherwise the established seam cannot cover it and the new path is untestable offline. Grep the test tree for the patched symbol before writing a new fetch helper and align with the existing convention.
- **A stubbed price series must span the queried window.** Production code date-filters whatever `get_closes` returns (`start_date..end_date`); a stub series whose bars fall outside the window produces an empty filter result, and the code degrades silently to `None` (no exception). An "alpha is None" / empty-window test failure is therefore usually a stub-window bug, not a code bug. Compute the expected value from the stub series itself (first/last bar inside the window), never hardcode the window's return.
- Pure parsing/note-building helpers belong in their own module with no network or side effects, so they are unit-testable with fixture CSVs shaped like the real vendor outputs.
