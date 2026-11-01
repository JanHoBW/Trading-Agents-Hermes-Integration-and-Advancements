# IR factual integrity

`issuer_facts` remains a deterministic data vendor, not another analyst. Existing
`get_ir_facts(ticker, as_of=None, *, include_delta=True)` and
`collect_ir_facts(ticker, config=None)` calls remain compatible. The collector
also accepts keyword-only `as_of="YYYY-MM-DD"`.

## Point-in-time boundaries

- Sitemap release dates and index publication dates must be on or before
  `as_of`. Selection defensively filters the returned sitemap entries, and the
  index cutoff is applied **before** its entry cap. Each index body still ends
  at the next entry, even when that next publication is excluded by the cutoff.
- Historical runs do not fetch today's facts or quote endpoints or expose the
  currently configured calendar. A prior fiscal-year reference date alone does
  not prove that today's mutable endpoint was available at the historical date.
  `historical_unavailable` and rendered `GAP` lines state this current-only gap.
- Historical runs can use dated release/index evidence. Missing historical
  publications stay unavailable; current observations do not fill that gap.
- Historical `get_ir_facts` runs neither read nor overwrite the current ticker
  delta state. No current-to-historical comparison is rendered. HTTP retrieval
  caching is separate from that delta basis.
- Parseable future reference dates in endpoint figures, and future configured
  or captured reference dates in release facts, are withheld. Missing or invalid
  release reference dates are not guessed; the publication date is the default
  only when no separate reference date was provided.

## Guidance evidence

Existing string keys `guidance_range`, `guidance_margin`, and
`guidance_sentence` remain. Additive optional `GuidanceEvidence` fields are:

| Field | Meaning |
| --- | --- |
| `guidance_direction` | Explicit `raise`, `cut`, `confirm`, or `unspecified` |
| `guidance_numeric_range` | Typed `{low: float, high: float, unit: str, currency: str}` in the quoted unit; absent without a recognized range |
| `guidance_raw_sentence` | Full whitespace-normalized action sentence, including punctuation |
| `guidance_reason` | Bounded quoted causal context, not a generated explanation |

“Umsatzerwartung … reduziert sich” is an explicit cut even when the following
sentence says the company “nun” expects the new band. The noun *Erwartung* must
be recognized alongside *erwartet*.

“Now”/“nun” or expected sales growth does **not** establish a forecast raise.
Confirmation wins over a relative clause describing a previous raise in the
same sentence. Confirmation and upper-end wording count as guidance evidence
without a repeated numeric range. Direction, causal context and raw wording
are rendered and included in current-state deltas.

Regressions use short public release excerpts: Rheinmetall's H1 2026 text says
its sales forecast is **reduced by €300 million** because of F126 cancellation,
then says it “now expects” €13.7–14.2 billion. Siemens Energy's Q3 FY2026 text
**confirms** an outlook raised after H1 and targets the upper end without
repeating the numeric range in that sentence. Neither is reinterpreted from
“now” or from a previously raised range.

## Optional labelled release facts

There is no global backlog regex. Extraction uses the issuer's existing
`prose_facts` configuration only. Onboarding must specify the label, relevant
scope and exact wording. Optional metadata extends the existing pattern shape:

```json
{
  "label": "Group backlog",
  "regex": "Group backlog at (?P<date>\\d{4}-\\d{2}-\\d{2}) was EUR(?P<v>\\d+)bn",
  "suffix": " billion EUR",
  "perimeter_note": "group; includes expected framework call-offs"
}
```

Alternatively set `reference_date` and optional `date_format` (default
`%Y-%m-%d`). A captured named `date` takes precedence. `prefix`, `suffix`,
named `v` or the first value capture retain their previous meanings. If using
both a date capture and a value capture, name the latter `v`.

`release_fact_evidence` contains typed records with label, value, reference date,
publication date, perimeter note, source URL and exact matched wording
(`raw_sentence`, which may be a sentence fragment). No perimeter is inferred:
without configured metadata it reads `not stated; see release`.

Annual endpoint figures remain in their annual block. Release values have a
separate publication-dated section, with per-fact dates and scope in the evidence
lines. The newest matching release for configured figures is selected separately
from the newest release containing guidance, so a newer backlog/FCF update is
not lost merely because it repeats no outlook. A release-index-only issuer
continues to expose its figures in the primary block dated to that release.

For FCF, labels must distinguish pre-tax/post-tax and operating/free cash flow.
For backlog, labels and wording must distinguish group/segment and firm orders,
framework call-offs, nomination or RPO. Pattern specificity is the onboarding
contract, not an automated accounting classification.

## Verification and limits

Run the isolated vendor regressions with the repository Python:

```sh
env -u PYTHONPATH .venv/bin/python -m pytest tests/test_issuer_facts.py tests/test_issuer_facts_integrity.py -q
```

Coverage includes future-source filtering, historical current-only gaps, byte
and mtime preservation of the current delta cache, index caps, old annual/new
release separation, independently dated guidance/figures, labelled backlog/FCF,
ENR confirmation, RHM's reduction and German numeric separators. Tests isolate
HTTP and use public wording or synthetic figures, not live analyses.

Limitations: no archival version store for mutable endpoints or revised release
pages; publication dates identify the source, not a guarantee of unchanged page
content. Release scanning is bounded, German sitemap variants are preferred,
and index bodies are bounded. Regex extraction covers a narrow DE/EN vocabulary,
not arbitrary language, complex tables, all currencies, multi-metric direction
changes or every fiscal/accounting definition. A missing pattern remains missing.
Full-suite integration and analysis/report acceptance belong to the orchestrator.
