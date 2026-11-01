# IR facts snapshot transport

The schema remains **version 1**. `news_collect.py facts` / `ir_facts_snapshot.py`
produce a facts-only supplement (`items: []`), consumed by the public
`get_news_supplement(ticker, start_date, end_date)` vendor. No LLM or additional
collector is involved in transport.

## Data contract

- Annual/display `facts` mappings become `figures`. `release_facts` and
  `release_fact_evidence` also become figures. Explicit per-label evidence takes
  precedence over a display mapping and is not duplicated. Evidence-only,
  release-only and confirmation-only results do not require annual figures.
- Each figure retains `label`, `value`, public HTTPS `source_url` and its own
  `reference_date`. Optional fields are `publication_date`, `perimeter_note`,
  `raw_sentence`, `numeric_value`, `numeric_range`, and `direction`.
- A block's reference date supplies only missing figure reference dates. A
  block's optional publication date supplies only missing publication dates;
  **publication never replaces the annual/fiscal reference date**. Legacy
  `DD.MM.YYYY` reference strings remain accepted alongside ISO dates.
- Legacy string guidance and lists of strings remain valid. New guidance objects
  allow only `guidance_range`, `guidance_margin`, `guidance_sentence`,
  `guidance_numeric_range`, `guidance_raw_sentence`, `guidance_direction`, and
  `guidance_reason`. Nonempty objects require sibling `guidance_date` and
  `guidance_source`; optional `guidance_perimeter` states their basis. An absent
  perimeter is labelled “not stated; see release”, never inferred from annual
  continuing-operations figures.
- Numeric values stay JSON numbers, including zero. Ranges contain finite
  numeric `low`/`high` with `low <= high` and optional string `unit`/`currency`;
  booleans, numeric strings, NaN/infinity and unknown range keys are rejected.
  Directions are `raise`, `cut`, `confirm`, or `unspecified`.

## Cutoff and limits

`build --date YYYY-MM-DD` passes that exact historical `as_of` to
`collect_ir_facts`. Build/import validate the normalized payload using the same
validator as the consumer. The consumer also revalidates against the snapshot
`as_of`, which must equal its requested end date.

Future block/figure reference dates, publication dates, guidance publication
and release publication metadata are rejected, including imported evidence.
Build/import report the reason and return 2 without writing the requested
snapshot; the consumer raises `VendorUnavailableError`. Guidance target fiscal
years and future calendar appointments are not publication/reference evidence
and are not excluded merely because they describe the future.

The existing maximum is 40 figures (producer retains the first 40; consumer
rejects oversized lists), 30 headlines and a 100 KB consumer byte cap. Ordinary
fact text is limited to 200 characters. Collector display values, labels,
perimeters and guidance display sentences are bounded before serialization;
raw quotations have a separate **2,000-character** limit and are not silently
shortened. Imported oversized ordinary/raw fields are rejected. Legacy guidance
lists have at most 40 entries. Public HTTPS evidence URLs reject credentials,
invalid ports, private/non-global IP literals and local/internal hostnames.
Validation does not fetch URLs or claim that DNS resolution establishes public
source provenance; the supplying researcher must verify the publication.

Facts are outside the headline lookback window, but never outside the as-of
cutoff. Rendering includes per-figure source, reference/publication dates,
reporting basis and raw wording, and guidance's actual range/direction/wording
with its publication source/date/basis. A facts-only result explicitly says no
headline-window item was carried, rather than implying no issuer news existed.

## Synthetic example

```json
{
  "reference_date": "2025-12-31",
  "figures": [{
    "label": "Adjusted margin H1",
    "value": "17.1 %",
    "reference_date": "2026-06-30",
    "publication_date": "2026-08-06",
    "source_url": "https://issuer.example/results",
    "perimeter_note": "H1 group adjusted",
    "raw_sentence": "The H1 adjusted margin was 17.1 %."
  }],
  "guidance": {
    "guidance_direction": "confirm",
    "guidance_raw_sentence": "We confirm the FY2026 outlook."
  },
  "guidance_date": "2026-08-06",
  "guidance_source": "https://issuer.example/results",
  "guidance_perimeter": "FY2026 group"
}
```

## Regression and TDD evidence

`tests/test_ir_snapshot_transport.py` mocks only the external collector/config;
it exercises real CLI normalization, JSON serialization, validation and the
public consumer. New behaviors were introduced vertically with observed RED
failures followed by GREEN: missing historical collector argument, dropped
release evidence, rejected guidance objects, release/confirmation-only results,
future evidence at build/import/consumer boundaries, guidance type/allowlist
and budget failures, non-public sources, lost numeric fields, per-figure date
precedence, long display wording versus independent raw quotations, and lost
block publication metadata. Compatibility/limit regressions remain covered.

Run from the repository root:

```sh
env -u PYTHONPATH .venv/bin/python -m pytest tests/test_ir_snapshot_transport.py -q
env -u PYTHONPATH .venv/bin/python -m pytest tests/test_ir_snapshot_transport.py tests/test_issuer_facts.py tests/test_issuer_facts_integrity.py tests/test_ir_adapter_pilot.py tests/test_news_supplement.py -q
```

These tests use synthetic public-source fixtures, not positions, credentials or
live analyses. Real collector refreshes and model pilots are separate checks.
