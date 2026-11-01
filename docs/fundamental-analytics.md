# Period-aware fundamental analytics

Fundamental arithmetic is deterministic, not another model role. SEC/Yahoo
statement tools emit CSV-safe `# PERIOD_FACTS: <JSON>` and
`# FUNDAMENTAL_ANALYTICS: <JSON>` comments. The Fundamentals Analyst merges only
statement **tool messages**, recomputes the block for the analysis cutoff,
injects it as delimited data, and appends the code-produced result to its final
report. Neither the helper nor its tests call an LLM.

## Period and vintage contract

Each fact carries `metric`, `value`, `unit`, exact `start`/`end`, duration,
publication `filed`, accession, form, source, reporting perimeter, reporting
basis and tax basis where known. SEC `fy`/`fp` are retained as
`filing_fiscal_year`/`filing_fiscal_period`: these describe the **filing**, not
necessarily a comparative fact's fiscal year or quarter. They must not be
relabelled as the comparative period. Source values stay in their original
units in JSON; the SEC CSV still scales USD to millions.

The publication cutoff is applied before latest-vintage selection. A period
that has ended but was not yet published is not available. Missing publication,
period starts or units do not acquire synthetic dates/currencies. Malformed,
non-finite, conflicting same-vintage and boolean amounts are rejected.

## SEC quarterly cash flow

The backward-compatible CSV is explicitly a **RAW filing table**. Its labelled
6/9-month columns remain YTD amounts; they must never be summed as quarters.
Comparable discrete quarters appear in the deterministic JSON block:

- A 60–115-day reported duration is a discrete quarter.
- Six/nine-month cumulative amounts can be differenced only against the previous
  cumulative amount with the same fiscal start, metric, unit, tag, perimeter,
  reporting basis and tax basis. The difference must itself span 60–115 days.
- Q4 can be derived from an annual 10-K/20-F/40-F amount minus matching nine-month
  YTD. A rolling twelve-month 10-Q amount is not an annual fiscal period.
- Both input records and publication/accession vintages accompany a difference.
  An unmatched amendment or a prior input published after the current input is
  not silently reconciled across filings. Missing baselines remain
  `limitations: unreconciled`, rather than invented quarters.
- Reported discrete values take precedence over derived values.

Synthetic example, in source USD: CFO YTD `30m, 80m, 150m` and capex YTD
`10m, 30m, 60m` yield quarterly CFO `30m, 50m, 70m`, capex `10m, 20m, 30m`
and FCF `20m, 30m, 40m` — not summed YTD amounts.

Metadata contains the most recent eight period ends; arithmetic displays at
most four period/unit rows. The raw CSV retains its existing history.

## Arithmetic and unavailability

- FCF is CFO minus positive **cash capex outflow**, only for identical start,
  end and unit, with a matching reporting perimeter and explicit outflow sign.
  Missing/ambiguous capex is unavailable; a perimeter difference is unreconciled.
- Conversion means CFO / Net Income and FCF / Net Income, expressed as ratios
  (`2.0` means `2x`, not `2%`). Net Income must be strictly positive and cover
  the same period/unit. Missing, zero or negative denominators are unavailable.
  Tax, adjusted-vs-reported or perimeter differences/unknowns are unreconciled.
  EBITDA is not substituted for net income.
- TTM is computed per metric only from its latest **four exactly contiguous**
  discrete quarters, identical units and unchanged perimeter/reporting/tax
  basis. Overlaps, gaps, sparse historical dates and annual periods do not
  qualify. Quarterly EPS is not summed: weighted share denominators can differ.
- No causes are generated from numerical divergences. The block states
  `causes: not inferred`; a cause requires separate dated source evidence.

## Yahoo, estimates and funds

Yahoo statement period ends and signed cash-flow values are preserved. Its
statement feed does not disclose exact period starts, durations or filing
publication vintages; those fields remain null/UNKNOWN. The overview likewise
cannot support CFO/FCF conversion by mixing undated snapshot fields. This
conservative limitation is explicit in the tool output.

Yahoo's undated forward EPS/PE has **Forward estimate year: UNKNOWN**. Neither
the analysis date, a fiscal year-end nor the word “forward” assigns it to FY or
NTM. The analyst may use a fiscal year only if a provider explicitly supplies the
estimate period.

ETF company cash-flow analytics return `not_applicable`, including when the
instrument state is ETF. No issuer-specific adapter was changed. Remaining-year
guidance arithmetic is deliberately not inferred from free-form IR prose:
matching typed guidance/actual period, unit and perimeter data would be needed.

## Offline verification

```sh
env -u PYTHONPATH .venv/bin/python -m pytest tests/test_fundamental_analytics.py tests/test_sec_edgar.py -q
```

Dedicated tests cover raw-to-quarter cash flows through the real SEC output,
publication vintages/amendments, Q4, consecutive vs sparse TTM, denominator and
perimeter/tax/unit boundaries, malformed/duplicate payloads, Yahoo UNKNOWN
metadata, ETF handling and the actual analyst prompt/final-report consumer.
Fixtures use only synthetic companies and financial amounts.
