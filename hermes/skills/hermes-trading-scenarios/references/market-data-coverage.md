# Equity data-source coverage and provider selection

Use this reference when diagnosing missing market/news/fundamental data or comparing providers for one exchange, country, or equity universe. Keep the result focused on the user's requested universe; do not treat one ticker's successful lookup as proof of broad coverage.

## Procedure

1. **Define the universe and required fields.** Record whether the user means primary listings or every venue/line, and whether the universe includes only large caps or also mid/small caps. Separate price history, corporate actions, fundamentals, issuer news/filings, macro, and sentiment; they do not need to come from one vendor.
2. **Audit the actual run before proposing a fix.** Read the wrapper provenance and only the generated report paths it lists. Inspect the run log for each method's selected provider, fallback order, missing-key/configuration messages, HTTP failures, stale data, and empty-result messages. Compare the reports against one another for conflicting values, periods, currencies, or timestamps.
3. **Treat a news gap as a source-coverage failure until checked.** A vendor returning no articles, an HTTP error, an unconfigured provider, and a successful query with zero matches are different states. When the user asks whether company news is missing, cross-check the issuer's official IR/news page and its official filing/disclosure channel for the same time window. For German issuers, inspect the issuer's EQS News company page and relevant categories (e.g. Directors' Dealings/EQS-DD, ad-hoc, voting-rights and total-voting-rights announcements) when present. Do not report “no news” or “no insider trades” from an unavailable or narrow vendor feed.
4. **Inspect the live integration points.** Read the current provider router, configuration, symbol normalizer, and relevant vendor adapters in the checked-out version. Add providers at the narrowest data-method boundary available; do not assume a documented vendor is wired into the installed code. Existing generic/provider examples and CLI banners do not prove a data endpoint is supported. When the vendor also offers a skill or MCP server, classify them separately: an API adapter serves deterministic code-level data, MCP exposes callable agent tools, and a skill supplies workflow instructions; none proves the dataflow is routed. Search the local Hermes catalog, record official versus community provenance, inspect community-skill source before recommending it, and do not install or configure it unless the user asks. Prefer a direct API adapter for repeatable TradingAgents runs and MCP for interactive exploration, unless a tested architecture shows otherwise.
5. **Build a candidate matrix from official documentation.** Compare each option on German instrument coverage (test exact ISINs and venues), field coverage, as-of freshness/delay, price source/method, fundamentals periods and currency, news/disclosure coverage, API limits, subscription price, personal/commercial use and redistribution rights, privacy/logging, and implementation/maintenance effort. Verify current prices and entitlements on the provider's own plan/docs pages; search snippets are discovery only. A consumer subscription or signed-in browser session does not establish API access or rights to automate, cache, or pass data to an LLM. Distinguish widgets/embed tools from a licensed data API/export, and confirm permitted use, caching/processing, quotas and delay in writing. Until verified, use the service only for manual cross-checks; do not scrape logged-in pages or reuse session cookies. If the authorized route is unavailable in the agent session, report that access failure as distinct from a provider/data failure and request the provider's API/export documentation or a provider-generated sample that the user confirms is permitted to share and process. Do not claim data retrieval or integration until a real sample has been parsed and its provenance checked. Mark exchange or symbol coverage as unverified until probed.
6. **Use stable instrument identity.** Maintain ISIN as the primary identifier, with WKN, issuer, share class, listing MIC, trading currency, and each vendor's native symbol as mappings. Do not infer venue, share class, or provider syntax from a suffix such as `.DE`; an ADR, secondary listing, and Xetra line can represent different instruments or price series. When provider search returns several issuer matches, compare the listing/venue rows and record the exact vendor-native symbol selected; issuer-name match alone is insufficient.
7. **Pilot before committing or coding a broad integration.** Test representative large-, mid-, and small-cap names across the intended venue universe. Measure symbol resolution, OHLCV continuity, corporate-action handling, stale/missing fields, financial-statement coverage and units, issuer-news recall, API-credit consumption, latency, and discrepancy versus an independent or exchange reference. Include at least one known recent issuer disclosure as a retrieval test. For a new fallback, test both the exact trigger and provider order under the typed failure, plus a negative case proving rate limits or unrelated failures do not activate an entitlement-only fallback; rerun these tests after the final code edit. Then perform a bounded live probe for one known instrument/date range and verify native symbol, dates, currency, adjustment basis, and provenance. Report the sample size and observed coverage; do not extrapolate to all German equities without evidence.

   For yfinance, compare the live profile quote and its market timestamp with the latest daily-history row after the listing's close. A returned bar can contain OHLC/volume fields while `Close`/`Adj Close` are missing; do not treat that as a settled candle or substitute the live quote silently. Record the last complete bar date separately. When a quote/history mismatch suggests a timezone issue, inspect the source exchange timezone and compare the raw provider payload with yfinance's parsed row: if raw `close` is already null, converting timestamps cannot recover it; only investigate timezone conversion when the raw value exists but the parsed date/value differs. Test `repair=True` only when its optional dependencies are installed, and label repair behavior unverified if it cannot run.
8. **Preserve provenance and failure states.** For every returned value retain ISIN, listing MIC, provider symbol, vendor/source, publication/observation timestamp, retrieval timestamp, reporting period, currency/units, price adjustment basis, and any delay. Represent `not configured`, `source unavailable`, `no matching records`, `stale`, and `not covered` distinctly. Log the fallback actually used.
9. **Do not silently blend vendors.** Configure a primary source per field and an explicit ordered fallback where needed. If fallback changes source, keep the provenance and surface material divergence; compare values only after aligning venue, time, currency, and adjustment basis. A missing vendor response is not a neutral value and must not be passed to the analyst as if it were.
10. **Keep sentiment optional and evidence-weighted.** Social services often have weak coverage outside US-listed tickers. Treat a missing feed as unavailable and a zero-result query as limited platform evidence, not broad investor sentiment. Prioritize primary issuer disclosures and reliable fundamentals over sentiment expansion.

## Auditing a finished run for workflow/data gaps

Use this when the user asks what gaps a *completed* run's workflow still has — deliverable and auditability, not just which provider to buy. Audit the run's own artifacts: the report body only shows what the analysts chose to write, while the log and provenance show what actually failed.

1. **Triage in order:** `provenance.json` (read the `data_coverage` block first when present — wrapper v1.0.2 and later pre-compute per-method `status` and the exact skipped vendor/reason from the run's own log markers, so it replaces most manual log-grepping), the run's `tradingagents.log`, the child's live file `<output-dir>/<TICKER>/<date>/message_tool.log`, any manually curated supplement file in the run dir, then the report. Read only the report paths provenance lists plus these run-level logs and provenance. The child log is the only artifact with raw provider `[Data]` rows: use it to verify which vendor actually served each method and to trace claims and the automatic `CROSS-VENDOR NOTE` (it is appended to the data row, not written to the wrapper log) back to their source. If `data_coverage` is absent, the run predates v1.0.2: fall back to grepping the log below.
2. **Check provenance for schema drift, not just presence.** Verify it carries every field the wrapper is documented to write (e.g. the LLM profile/model, and `data_coverage`). A file that exists but is missing a documented field is a version/schema mismatch to flag — never assume provenance is complete because the file is there.
3. **Grep the log for silent vendor death** (skip only when `data_coverage` is present AND you need no raw detail — but the coverage block only reflects the router methods the wrapper's marker parser tracks; analyst-level tool failures (e.g. a social-source HTTP 404 inside the sentiment analyst) can be absent from it, so still grep both logs for `failed`/`unavailable` when a coverage block reads clean). A missing credential dead-ends an entire data category as `NO_DATA` with no error in the report body. Search for `environment variable is not set`, `not configured for`, `DATA_UNAVAILABLE`, `NO_DATA_AVAILABLE`, and HTTP errors. A whole category (e.g. all US macro series) showing as missing alongside a `..._API_KEY ... is not set` line is a missing-credential setup gap — the fix is the wrapper's credential allowlist plus the profile `.env`, not a new provider.
4. **Confirm routing per data method, not per vendor.** A vendor wired into one method is not routed for another — `get_news` and `news_data` are separate router entries. A log line "vendor X not configured for <method>" is a routing mismatch, distinct from the vendor being absent or empty.
5. **Reconcile the same metric across analyst sections.** One indicator can carry two values in different sections because each came from a different vendor (verified snapshot vs fundamentals feed). Surface the divergence with its provenance and align venue, time, currency, and adjustment basis before comparing — do not silently pick one. OHLCV closes now get an *automatic* cross-check: when a less-trusted source (Alpha Vantage) served the primary price data, the `get_stock_data` tool compares it against the other configured vendor (normally yfinance) and appends a `CROSS-VENDOR NOTE` to the data if they diverge >0.5% over the last 30 shared days — informational only, it never changes the primary. A yfinance primary (the healthy path) adds no second call, so a missing note just means the trusted source served and nothing was in question; a present note is a real divergence to weigh.
6. **Check the required trade-ability and provenance-completeness fields.** An actionable report needs entry, stop, and target; `not provided` for stop or target is a workflow gap, not a neutral omission. Confirm the instrument identity (ISIN/WKN) and the price adjustment basis (adjusted vs unadjusted) appear somewhere — a dividend series with no stated adjustment basis is an unlabeled gap.
7. **Report each gap as a distinct state** — missing-credential vs unconfigured-route vs vendor-absent vs empty-result — and name the data category it dead-ended. Never fold a silent `NO_DATA` into "no news" or "no trades."

### Auditing the framework's full input coverage (all tools, whole universe)

Use when the user asks what data inputs the workflow still lacks *in total* for a universe (e.g. DE equities + ETFs) — not one run's gaps. Deliver the per-tool gap matrix in German, classified and prioritized.

1. **Inventory the data inputs the workflow actually calls** — the agent tools in `agents/tools.py` (market snapshot/stock/indicators, fundamentals + statements, news, global news, insider, macro, prediction markets, consensus via the fundamentals/overview feeds). This is the full input set; a report using only a subset is not the universe of inputs.
2. **Resolve each tool's effective chain**: the configured category string in `default_config.py` ∩ the vendor methods that actually implement it. One category string re-wires all tools in the category; a vendor absent from the string, or implementing the method but missing from the string, is silently absent from that tool (see workflow-fixes.md).
3. **Complement the structural matrix with a recent run's `provenance.json` `data_coverage`** for a ticker of the target universe: per-method status (ok/degraded) and the exact skipped vendor/reason — what empirically worked, not just what is configured.
4. **Classify every gap — the fix differs per kind**:
   - *Configuration gap*: a vendor implements the method but is not configured for the instrument (e.g. `news_feed_url: null` in the issuer registry → `issuer_rss` `not_configured`). Fix: a config entry, no code — cheapest.
   - *Credential gap*: vendor configured, key missing (the provenance flags make it explicit). Fix: profile `.env` + wrapper credential allowlist.
   - *Structural gap*: no vendor implements the method for the universe. Fix: new vendor adapter or new data method (workflow-fixes.md, "Adding a data vendor"). US-only vendors (SEC EDGAR, Finnhub) never close a DE structural gap — they need a DE equivalent, not re-chaining.
5. **Prioritize by effort/effect**: config entry > extending an existing vendor > new vendor for an existing method > new data method.

**Current checkout state for the DE universe** (verify against the checkout before acting — this drifts as gaps get closed): `issuer_rss` is wired but the registry example's `news_feed_url` is `null` (first-party feed = configuration gap) — **and for Rheinmetall/RHM specifically no public first-party XML feed exists** (verified 2026-10-12: ~20 candidate feed paths on rheinmetall.com all return HTML/404 or redirect to the Nuxt SPA shell; the site's internal `/api/` Nuxt-Axios endpoint serves page-JSON only, no news-list endpoint; EQS exposes no public RSS for any issuer — confirmed against a reference ISIN; DGAP has none). Consequence: the `issuer_rss` vendor hard-parses RSS/Atom/RDF XML (`ET.fromstring`), so writing an HTML page URL into `news_feed_url` would break **every** RHM run with `VendorUnavailableError("official feed is invalid")` — worse than the current clean `not_configured` fall-through. The user (RHM analyzed frequently) chose to **leave `issuer_rss` unconfigured** and keep the working Marketaux+EQS chain; a genuine first-party RHM channel later needs a **new vendor** (CMS-API or HTML-list parser) wired into the `get_news` chain — not a registry URL. The DE insider/PDMR gap is now closed by a keyless `ureg_pdmr` vendor (see the Unternehmensregister route below) wired first in the insider chain, though no DE regulatory-filing source exists (sec_edgar = US EDGAR only); DE fundamentals have no second vendor (yfinance alone, so cross-vendor reconciliation does not apply); analyst consensus is single-source without per-rating dates; ETF coverage improved but is not complete: the fund profile block (NAV/TER/YTD/AUM/inception) and the **top-10 holdings** now come keyless via yfinance (`funds_data.top_holdings`, integrated into the fundamentals report), while tracking-difference and full constituent weights still have no vendor and ETFs ride the equity path with empty statements. Working and covered: prices/indicators with cross-vendor note, macro (official_europe ahead of FRED), prediction markets (keyless), news (marketaux + finnhub + news_supplement when injected).

### Evaluating open-source projects as gap-fillers

When assessing whether a free/open-source GitHub project can close a DE coverage gap,
verify two things independently before recommending it: **(1) project maturity/license**
(stars, last push, SPDX id via `api.github.com/repos/<owner>/<repo>`) and **(2) that its
underlying free data registry actually covers your target universe.** A well-maintained,
correctly-licensed parser is worthless for DE if its source registry carries no German issuers.

Verified finding: **`filings.xbrl.org` indexes no German filings.** Sampling ~6,500 of its
26,009 ESEF/UKSEF records gave count(DE)=0 while AT/DK/FR/ES etc. were present; its
`country=DE` param is silently ignored (returns an unfiltered page) and `filter[country]=DE`
returns HTTP 400/invalid JSON:API filter. Consequence: strong OSS such as
[brel](https://github.com/BrelLibrary/brel) (Apache-2.0) and
[xbrl-filings-api](https://github.com/lsalmela/xbrl-filings-api) (MIT) parse XBRL well but do
**not** close the DE-fundamentals gap — SEC EDGAR stays US-only and DE fundamentals remain
single-vendor Yahoo until a German-source path exists.

Pitfall when probing such registries: **never trust one filter parameter to prove coverage —
an invalid/unsupported filter can be silently ignored and return an unfiltered page whose rows
are all other countries (a false positive).** Page broadly (`page[size]=100&page[number]=N`)
and count your target country code before concluding it is absent.

Also verified this pass: **ETF top-N holdings are keyless via yfinance**
(`Ticker(...).funds_data.top_holdings`, name + holding share; probed live on `EUNL.DE` and
`IWDA.L`) — no scraper or subscription needed since yfinance is already installed. That closes
part of the documented ETF-holdings gap; full constituent weights / TER / tracking-difference
still need a dedicated source.

### Evaluating provider tiers as gap-fillers (keyless vs registered-free vs paid)

When the user asks whether a gap can be closed *with registration but at zero cost*, separate
the three access modes and evaluate each one against your actual target universe, because their
coverage rarely matches:

1. **Keyless public endpoint** (yfinance funds_data, Eurostat, ECB, XBRL registries) — no credential.
2. **Free registered tier** — credential required; often US-only or data-limited.
3. **Paid tier** — usually what actually unlocks structured DE/EU data.

The governing rule: **a provider's marketing coverage ("global", "100+ countries",
"international") describes its paid tier. Each lower tier must be probed separately AND
empirically for your universe.** Free-tier claims routinely return nothing for non-US symbols.

Pitfall — distinguish three response shapes rather than treating any non-200-JSON as failure:

* HTTP error / non-JSON body → endpoint or plan problem
* `Information:`/`Note:` demo-key or rate-limit message → throttle / demo capability
* **HTTP 200 with an empty payload (`{}`, empty array)** → provider's signature for "symbol not
  covered by this plan/tier".

An empty-200 result is NOT "no data exists" and NOT an error — it is "not covered on this tier".
A test that asserts on `{}` counts as success and silently hides a coverage failure: inspect the
full payload and report an empty result as "not covered by this plan", never as "no data".

Method to verify empirically:
1. Use the actual credential already in the profile `.env` (e.g.
   `ALPHA_VANTAGE_API_KEY`) — never confirm coverage with a demo key (demo returns only
   "demo purposes only" for every non-trivial endpoint).
2. Probe with the provider's *native* symbol format for your universe (Alpha Vantage
   `SAP.DEX`/`RHM.DEX`, not US-style `SAP`).
3. Pace requests past per-second limits so one rate-limit message is not mistaken for a
   coverage answer.
4. Record per symbol: HTTP status, response keys, row count, latest period end, currency.

Key structural insight for DE/EU gaps: they are **data-license / region limits, not API-access
limits.** A free registered account does not lift them because the underlying EU data (EU
PDMR/insider disclosures, house consensus across EU issuers, EU annual reports) is licensable to
US consolidators — registering still yields nothing. Separating an *access-gated* gap (free tier
forbids it) from a *license-gated* gap (no tier returns it) tells you whether even paying would
help before recommending any purchase.

Verified free-registered-tier sample for DE tickers (all probed/already documented): Alpha
Vantage fundamentals return empty `{}` for `SAP.DEX`/`RHM.DEX` (~25 req/day free); FMP analyst
ratings/targets sit behind its TipRanks partnership (= premium) and its paid plans state "US
Coverage"; EODHD fundamentals require a paid plan and its insider-transactions feed is explicitly
US-only; Finnhub recommendation/price-target/insider-transactions are premium or plan-403 for EU
tickers; Marketaux free tier is news-only and market-stats entities require Standard ($49). Net:
registering free closes none of the DE/EU structured gaps.

### Manual authenticated-UI coverage audit

Use this workflow only when the user asks for manual inspection of a provider interface and has authenticated themselves. Apply step 5's licensing gate: a signed-in subscription or visible export button does not establish permission for automated access, local caching, or LLM processing.

1. Resolve the instrument before collecting data. Search the issuer, compare duplicate listings, and record the selected provider symbol, exchange/MIC, currency, and its mapping to the local instrument registry.
2. Inspect menu layers separately: watchlist/custom views, issuer-page tabs, data-explorer categories, and individual metric search. Map those menus back to the fields TradingAgents actually needs; product navigation labels do not prove field coverage.
3. Probe a small representative set of required fields: current quote and freshness, historical daily OHLCV, a dated financial metric, complete income/balance-sheet/cash-flow statements, risk/technical fields, forecasts, issuer news/filings, and macro or insider data where relevant. Stop once the coverage gaps are clear; do not click every menu without a data question.
4. For each field, record its page URL and menu path, exact label, displayed value, currency/units, reporting period or frequency, observation/publication time, retrieval time, access tier, and whether the UI returned one value or a verifiable series. Use distinct states such as `visible single value`, `history verified`, `paywalled`, `not found`, and `indeterminate`.
5. Do not infer history from a metric catalog entry or a `Daily` frequency label: verify a dated range/series. Likewise, access to individual metrics does not prove that full statements or exports are available; check the exact page's access gate.
6. Report only what the UI actually showed and label incomplete coverage. Register the observed page URLs in the citation ledger. Treat UI values as manual research evidence, not as proof of an API, bulk export, stable schema, or license; do not export or integrate until the permitted route and processing rights are documented.

## Common architecture choices

| Pattern | Strength | Main trade-off |
|---|---|---|
| Official issuer disclosures + official macro APIs + existing price feed | Low starting cost; high source traceability for events and macro | Issuer sites vary; prices/fundamentals may remain weak; connectors need ongoing maintenance |
| Commercial global market-data API | Fewer adapters and broad structured coverage | Recurring fees, plan-specific venue/field limits, vendor lock-in, and the need to validate source methodology and licensing |
| Exchange-origin or exchange-licensed market feed | Strongest provenance for venue-specific quotes/trades | Market-data contract, exchange licensing, and integration complexity; does not replace fundamentals or issuer news |
| Institutional all-in-one provider | Broadest packaged coverage and service options | Often disproportionate cost and contract scope for a personal research setup |

A practical design is usually hybrid: issuer news/filings and official macro from primary sources; one tested provider for prices/fundamentals; explicit per-field fallback and provenance. Never choose a provider solely because its exchange directory lists Germany—probe representative ISINs, the exact German listing, and the actual plan available to the user.

### Local Stage-1 implementation (TradingAgents checkout)

The checkout currently wires `official_europe` ahead of FRED for macro, with validated names `germany_hicp`, `euro_hicp`, `germany_unemployment`, `germany_real_gdp`, `euro_real_gdp`, and `ecb_deposit_rate`. Eurostat historical data is the current API vintage (not point-in-time vintage reconstruction); the connector filters observations after the requested analysis date and includes this revision caveat.

### Public statistics API adapter workflow

1. Probe the exact dataset with the intended filters before coding. For Eurostat, inspect JSON-stat `id`, `size`, each dimension's `category.index`, sparse `value` keys, and `updated`; filter every non-time dimension to one code and verify `time` is the last dimension before relying on a one-dimensional parser.
2. Map sparse values through the time category's explicit index; never assume object key order or that every period has a value. Reject unexpected extra varying dimensions rather than flattening a different series into the report.
3. Bound the request and filter again after parsing. Convert day/month/quarter observations to period-end dates and exclude any observation whose period ends after `as_of`; include a regression test for `YYYY-Qn`, which is easy to misparse as a month because both labels have the same length.
4. Preserve source endpoint, retrieval time, observation period, units, dataset update/vintage, and the current-vintage caveat. A date-bounded current Eurostat response is not a historical point-in-time snapshot.
5. Route typed conditions correctly: a recognized series with no rows or an unsupported alias raises `NoMarketDataError` so the configured next source can run; HTTP, malformed JSON/CSV, or an invalid dataset shape raises `VendorUnavailableError` so the report distinguishes a source failure from a clean no-data response.
6. Test the parser and date boundary with sparse fixtures, then smoke-test each intended series through the router against the live official API. A module-level function test alone does not verify that the configured fallback chain actually reaches the adapter.

Ticker news can use an issuer-controlled RSS/Atom feed through `issuer_rss`. Its instrument file defaults to `~/.tradingagents/german_equities.json` (override with `TRADINGAGENTS_ISSUER_REGISTRY`) and should map ticker/ISIN/WKN to issuer, MIC, currency and each provider's own symbol. Add a `news_feed_url` only after confirming it is the issuer's official feed; a missing feed is a configuration gap and should fall back explicitly, never be reported as proof of no news. This adapter does not scrape logged-in Investing.com or issuer pages. A checked-in stage-1 note lives at `docs/german-market-data.md` in the TradingAgents repository.

### Licensed news route: Marketaux (EQS/DGAP as published sources)

When a licensed news API is available (the operator holds a Marketaux token), the documented-
API gate in the section below is satisfied for *news and official disclosure articles*: the
`marketaux` vendor in the repository is the primary `get_news` vendor, ticker-agnostic (entity
search by ticker root + exchange-suffix country), and returns per-article `sentiment_score`,
source domain, and publication date with a provenance header. EQS/DGAP appear there as
published articles (e.g. `eqs-news.com`), which closes the issuer-disclosure *news* gap for
80+ markets. Its honest limits, repeated in every output header: it is a news sample, not a
structured PDMR/insider-transactions dataset; its sentiment is news-based, not retail investor
sentiment; and an empty window is reported as a coverage caveat, never as absence. The token
travels `MARKETAUX_API_TOKEN` profile `.env` → wrapper child-env only, and is scrubbed out of
every error path (`get_scrubbed` secret + vendor-level re-scrub); a missing token degrades the
chain, it never aborts a run.

### Licensed alternative source: Finnhub (US-coverage, structured insider)

`finnhub` is wired as a *secondary/alternative* vendor (not primary), selected only by its
configured chain position and degrading cleanly so any ticker stays analyzable. Empirically
verified free-plan scope: US quote, `/stock/profile2`, `/stock/metric`,
`/stock/insider-transactions` (SEC Form 4, with `filingDate`), and `/company-news` (legacy,
Yahoo-sourced). It does **not** cover DE tickers (`RHM:DE` returns zeros / `{}` / `{"data":[]}`),
does not serve candle history, and `.F`/`.DE` symbol forms are paid-only. Three guarantees,
each covered by a test:

* **Coverage gate** — every entry point queries `/stock/profile2` first; an empty profile
  means "not covered by this plan" → `NoMarketDataError`, so the router falls through. This
  is what makes Finnhub safe to chain ahead of the general vendors for any ticker (DE
  tickers pass straight through it).
* **No token** — a missing `FINNHUB_API_KEY`/`FINNHUB_API_TOKEN` is a *configuration* error
  (`VendorNotConfiguredError`), distinct from a vendor outage; the chain continues.
* **Point-in-time insider** — SEC Form 4 records carry `filingDate`, so a historical run is
  filtered to filings already public on/before the run date. The free feed is a clamped
  ~12-month sample; a date before the earliest covered filing raises
  `NoMarketDataError` (the chain continues) rather than claiming "no filings existed".

Chain position: first for `get_insider_transactions` (only vendor with `filingDate`, so it
stands ahead of yfinance/alpha_vantage, whose undated trades are withheld entirely on past
runs); third for `get_news` — after the first-party `issuer_rss` and the `marketaux`
disclosure aggregator, ahead of the Yahoo/Alpha Vantage fallbacks; and second for
`get_fundamentals` (behind alpha_vantage, opt-in via `fundamental_data`, not on by default).
Token travels
`FINNHUB_API_KEY` (canonical, what the wrapper injects; the wrapper reads both
`FINNHUB_API_KEY` and `FINNHUB_API_TOKEN` from the profile `.env`) → child-env only, and is
scrubbed out of every error path. A missing token or an empty window degrades the chain; it
never aborts a run.

### EQS Directors' Dealings / PDMR disclosure pilot

For a German-listed issuer whose vendor insider feed is empty, first resolve the exact issuer/listing by ISIN and inspect the public issuer page on EQS News. The page can expose separate Directors' Dealings (often called EQS-DD), ad-hoc and voting-rights categories; one available category is evidence of a useful disclosure channel, not proof of complete history or licensed API access.

Before proposing an adapter, open one recent, known EQS-DD announcement and verify the raw fields and publication context. Preserve the original notice URL; issuer/LEI and instrument ISIN; reporting person; whether they are the PDMR or a closely associated person and the stated relationship/role; initial, amendment or correction status; transaction nature; transaction date/time and timezone; publication timestamp; price and currency; reported quantity or amount with its stated unit; aggregate information; venue and MIC. Never infer share count from a monetary `volume` value, and never collapse transaction date into announcement date. Keep every figure as disclosed and distinguish late or corrected notices where the source labels them.

These are legally reportable PDMR/associated-person disclosures, not a complete real-time feed of every employee, executive or beneficial owner's trades. Treat an empty EQS search as bounded source coverage, not proof of no transactions. A public HTML announcement page does not itself establish permission for automated retrieval, caching, or LLM processing: verify a documented API/feed and its use rights before adding an automated provider; otherwise retain it as a manual cross-check. In the pilot, compare the known announcement against the current vendor insider result, then test one correction/duplicate case and retain source provenance.

### Public primary-source route: German PDMR/insider via Unternehmensregister (keyless plain-HTTP)

The one DE-insider structured gap that can be closed **at zero cost and without any vendor**
is Art. 19 MAR PDMR (managing-body / directors' dealings). When a regulator's own data page has
no downloadable dataset, read its body: BaFin's Eigengeschäfte page exposes only metadata/JSON-LD
and states that **the primary store is the Unternehmensregister
(www.unternehmensregister.de)**. That is where the insider-transaction adapter lives.

**The whole flow is keyless plain `requests` — no browser, no CDP.** The site is Next.js and the
search results arrive as a server-rendered RSC payload embedded in the returned HTML, so a
stateless plain-HTTP client reads everything. (CDP/headless Chromium was the earlier assumption;
use it only if the RSC contract changes.)

1. **Warm the session.** `GET /de` then `GET /de/suche/kapitalmarktinformationen` so the server
sets its base cookies.
2. **Get a fresh search token per search.** `GET /api/search-token` → `{"token": "…"}`. The token
is short-lived; request one per search, never reuse one across calls.
3. **Set the search cookie.** `SEARCH_PARAMS` = base64 of
`companyName=<NAME>&drilldownFilter=21&companyFederalState=&searchToken=<TOK>&formType=CAPITAL_MARKET`.
`drilldownFilter=21` = publicationPart 21 "Eigengeschäfte von Führungskräften"; it also carries
other categories, so filter client-side in step 6.
4. **Fetch the list.** `GET /de/suche?companyName=<NAME>&drilldownFilter=21&companyFederalState=&
searchToken=<TOK>&from=<FROM>&size=30` with the cookie → SSR HTML carrying the RSC payload.
Paginate `from=(page-1)*size&size=30`; stop on `totalPages`/`currentPage`.
5. **Decode the RSC payload.** Rows live in `self.__next_f.push([1,"…"])` script chunks.
Reassemble each chunk and decode with **`json.loads`, never `unicode_escape`** — the latter
corrupts umlauts. Locate the `elasticSearchDtos` array with a string-aware bracket matcher, then
`json.loads` it.
6. **Filter each row client-side, per issuer.** Each row carries
`publicationDto{title, companyName, sourceDate, language, publicationCategory{id},
publicationPart{id}}` + `encryptedPayload` + `companyNameAtTimeOfPublication`. Keep only rows
with `publicationCategory.id==80` (PDMR, Art. 19 MAR) **and** whose
`companyNameAtTimeOfPublication` equals the target issuer (umlaut-fold + casefold) — a broad
name search returns rows from many companies, so the row filter *is* the per-issuer
disambiguation, not the search itself.
7. **Fetch the detail.** `GET /api/pub-html?payload=<encryptedPayload>` — stateless (no cookie,
no warm-up). Returns the structured HTML: Person, Position/status, Emittent, LEI, instrument type,
**ISIN**, transaction nature, price, volume, transaction date/time, place, and the disclosure
date ("Pflichtveröffentlichung am").
8. **Normalize dates.** The list RSC gives ISO dates; the detail HTML gives `DD.MM.YYYY`. Route
every date through an ISO normalizer before filtering/returning. `sourceDate` on a row is the
disclosure/publication date and the correct point-in-time filter field.

Pitfalls (each cost real time):

- **The detail is bilingual.** The register emits the same PDMR structure in German *and* English
  (chosen per filing). A German-only label parser silently under-parses English records (only
  Name/LEI survive). Match labels on a normalized cross-language map (umlaut-fold + casefold),
  never on position or single-language strings; disambiguate the two "Name" rows (person vs issuer)
  by their section headings, not by order.
- **Name search is fuzzy.** `companyName` matching can hit the wrong legal entity (group name,
  share-class variant, namesake). Never trust the search alone — validate every row against a
  per-issuer registry entry (`ureg_query_name` for the search term, `ureg_entity_name` for the
  `companyNameAtTimeOfPublication` match) using exact names from a verified capture.
- **Use the full legal-form name, not an acronym.** Fuzzy search fails on short forms: query
  "Siemens Aktiengesellschaft", not "Siemens AG"; "Bayerische Motoren Werke", not "BMW"; a holding
  acronym ("IFX") can return zero PDMR rows where the operating company ("Infineon Technologies AG")
  has dozens. Seed the registry's `ureg_query_name` from the row-level
  `companyNameAtTimeOfPublication`, not from the ticker.
- **An empty window is not a vendor error.** A configured issuer with no PDMR in the requested
  window returns a terminal "none" string (optionally naming the last known disclosure and how many
  foreign rows were dropped), not `NoMarketDataError` — absence of filings in a window is a real,
  reportable answer.
- **Pages mix publication categories — early-stop on the full list, not the subset.** A given
  page can carry *zero* PDMR (cat 80) rows while still holding other drilldown categories
  (observed: Infineon's page 1 had 24 cat-81 + 6 cat-83 rows and none of its 62 PDMR rows, which
  sat on pages 2–4). An early-stop condition of "no PDMR rows on this page" reports false absence
  for exactly those issuers. Stop pagination only when a page yields no rows of *any*
  category (list exhausted), while collecting only the cat-80 subset. Bound with a `MAX_PAGES`
  constant so a registry change can't silently multiply requests.
- **Stale token/session → homepage redirect or garbled counts.** Fresh navigation + fresh
  `/api/search-token` per search; never reuse.

This makes **DE insider/PDMR keyless and plain-HTTP**, no browser. It still does not establish
rights to automate/cache/pass to an LLM if that is outside your license — treat it as research
evidence until permitted use is verified. It also does **not** close house/market consensus price
targets or EU fundamentals: the register carries disclosures, not analyst recommendations or
financial statements.

Repo integration (built, live-verified on SAP.DE and IFX.DE, full suite green): a keyless
`ureg_pdmr` vendor (`tradingagents/dataflows/vendors/ureg_pdmr.py`) implements only
`get_insider_transactions`, self-gates off the issuer registry
(`~/.tradingagents/german_equities.json`, override with `TRADINGAGENTS_ISSUER_REGISTRY`) on
`ureg_query_name`/`ureg_entity_name`, sits **first** in the insider chain and first in the default
`news_data` chain, and degrades to `VendorNotConfiguredError` for unregistered tickers (e.g.
AAPL) so the chain falls through to finnhub/yfinance/alpha_vantage. Point-in-time filter on
`sourceDate`; detail fetches capped at 25 per call with a "showing N of M" note in the output.
The registry carries 19 verified German issuers; seed `ureg_query_name`/`ureg_entity_name` from
row-level `companyNameAtTimeOfPublication` in a verified capture — never from the ticker or from
a guessed legal form. Real HTML fixtures (trimmed list page carrying 30 rows, 18 of them cat-80,
plus one DE and one EN detail of the same filing) live under `tests/fixtures/`; offline tests
cover extraction, umlaut-decode regression, the registry gate, entity filter, point-in-time
filter, absence paths, and chain position — and a dedicated class drives the *real* `_search`
pagination loop (zero-PDMR page + PDMR page 2, the Infineon shape; category exclusion; list-
exhaustion / duplicate-page stops; the `MAX_RECORDS` cap) to keep the category-mixing early-stop
from regressing (regression-verified: a PDMR-only early-stop returns `[]` on that shape). All in
`tests/test_ureg_pdmr.py`.

**Second method on the same source (fork v1.0.6): `get_capital_market_disclosures`.**
The same search page carries the rest of the issuer's capital-market record, so one vendor
(`ureg_capital`, category `disclosure_data`, optional) serves it without a second scraper:
category **79** Art. 17 MAR ad-hoc, **81** WpHG §40/41 voting rights, **83** capital-market
information (buy-backs, AGM, capital measures), **89** §50 WpHG — ids observed live against
Rheinmetall, Infineon and Deutsche Bank; **69** (bond admission notices) is excluded because it
carries no issuer disclosure content, and **80** stays with the insider tool so nothing is
reported twice. Same session, same per-emitter entity validation, same point-in-time filter on
`sourceDate`; each entry gets a text excerpt from `/api/pub-html` (capped at 8 fetches, 700
chars). The register files a DE and an EN twin per disclosure under the same
(date, publicationType, issuer) key — those are merged to one entry (German kept) and the merge
count is printed in the header. Because the category is optional and the vendor self-selects via
the registry, a non-German ticker receives a scoped "no configured vendor covers this
instrument" sentinel instead of a failed tool call. Offered to the News Analyst; tests in
`tests/test_ureg_capital_markets.py` (filter, label map, merge, entity gate, point-in-time,
empty-window wording, detail failure, router wiring).

## How to present recommendations

For provider-selection questions, answer in German for this user. Lead with the recommendation, then use a compact table of three or four materially different options. Compare cost, coverage/data quality, implementation effort, and licensing/operational risks; label prices with their check date and plan type. State assumptions and unverified coverage plainly. Recommend a staged pilot before implementation when no provider has passed the symbol/field/quality test. Do not add a vendor, buy a plan, or change configuration when the user asked only for analysis or proposals.
