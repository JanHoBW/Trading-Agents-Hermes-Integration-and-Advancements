"""First-party event context for the news analyst.

The headline window (7 days) answers "what is happening now". It structurally
cannot answer "what shaped this thesis" — a downgrade twelve days ago, the Q2
release, an ad-hoc disclosure from last quarter. This module supplies that second
horizon from sources that are authoritative and cheap:

* issuer releases (``issuer_ir``, sitemap-selected, per issuer configured),
* capital-market disclosures (UReg: ad-hoc, voting rights, buy-backs, AGM),
* PDMR insider filings (UReg, Art. 19 MAR).

Only first-party/regulatory sources are used: general news over 180 days would be
mostly noise and would flood the prompt. Every source is optional and gated, and a
missing source is rendered as an explicit gap — never as "nothing happened".
"""
from __future__ import annotations

import logging

from tradingagents.dataflows import windows
from tradingagents.dataflows import coverage
from tradingagents.dataflows.errors import VendorNotConfiguredError, VendorUnavailableError
from tradingagents.dataflows.vendors.issuer_ir import get_news_issuer_ir
from tradingagents.dataflows.vendors.ureg_pdmr import (
    get_ureg_capital_market_disclosures,
    get_ureg_pdmr_insider_transactions,
)

logger = logging.getLogger(__name__)

EVENT_ISSUER_LIMIT = 6
EVENT_ISSUER_EXCERPT = 320
MAX_BLOCK_CHARS = 14000


_NOISE_BULLETS = ("- emittent:", "- LEI:", "- instrument:", "- ISIN:",
                  "- Emittent:", "- LEI-Nummer:")


def compact_ureg(text: str, max_records: int = 8, max_bullets: int = 3,
                 bullet_chars: int = 220) -> str:
    """Keep the register blocks readable inside a prompt.

    The UReg vendors repeat emitter/LEI/ISIN on every record and quote long filing
    text; for the event context the useful part is *what* happened and *when*.
    """
    lines = text.splitlines()
    header = [line for line in lines if line.startswith("#") and not line.startswith("## ")][:3]
    records: list[dict] = []
    current: dict | None = None
    for line in lines:
        if line.startswith("## "):
            if current:
                records.append(current)
            current = {"heading": line, "bullets": []}
        elif current is not None:
            stripped = line.strip()
            if stripped.startswith("- ") and not stripped.startswith(_NOISE_BULLETS):
                current["bullets"].append(stripped)
    if current:
        records.append(current)
    if not records:
        # Prose without "## " record headings (e.g. a vendor's own summary):
        # compaction would delete the content, so leave it alone.
        return text

    out = list(header)
    for record in records[:max_records]:
        out += ["", record["heading"]]
        for bullet in record["bullets"][:max_bullets]:
            out.append(bullet if len(bullet) <= bullet_chars else bullet[:bullet_chars].rstrip() + " …")
    if len(records) > max_records:
        out.append(f"\n_(showing {max_records} of {len(records)} records)_")
    return "\n".join(out)


def _render(ticker: str, start: str, end: str, blocks: list[str], missing: int) -> str:
    header = [
        f"## EVENT CONTEXT — first-party sources, {start} to {end}",
        "This block is deliberately wider than the headline window: it carries the",
        "corporate events that shaped the current thesis (releases, ad-hoc and",
        "voting-rights disclosures, insider filings). It is not a substitute for",
        "current news, and issuer wording is not independent corroboration.",
        "A source listed as a gap produced no data here — do not read that as",
        "\"nothing happened\".",
        "",
    ]
    if missing == 3:
        return "\n".join(header + [
            "_No first-party source is configured for this instrument (no issuer",
            "registry entry), so no event context is available._",
        ])
    body = "\n\n".join(blocks)
    if len(body) > MAX_BLOCK_CHARS:
        body = body[:MAX_BLOCK_CHARS].rsplit("\n", 1)[0] + "\n\n_(event context truncated)_"
    return "\n".join(header) + body


def _source(
    method: str,
    vendor: str,
    label: str,
    loader,
) -> str:
    """Run one source, record its coverage, render it or an explicit gap."""
    try:
        text = loader()
    except VendorNotConfiguredError as exc:
        coverage.record(method, vendor, coverage.NOT_CONFIGURED, str(exc))
        return f"### {label}\n- GAP: not configured for this instrument ({exc})"
    except VendorUnavailableError as exc:
        coverage.record(method, vendor, coverage.UNAVAILABLE, str(exc))
        return f"### {label}\n- GAP: source could not be retrieved ({exc})"
    except Exception as exc:  # noqa: BLE001 — one source must not sink the analyst
        logger.warning("event context: %s failed: %s", vendor, exc)
        coverage.record(method, vendor, coverage.FAILED, f"{type(exc).__name__}: {exc}")
        return f"### {label}\n- GAP: source failed ({type(exc).__name__}: {exc})"
    coverage.record(method, vendor, coverage.OK)
    return f"### {label}\n\n{text.strip()}"


def build_event_context(ticker: str, trade_date: str, config: dict | None = None) -> str:
    """The event-context block for ``ticker`` as of ``trade_date`` ('' if disabled)."""
    if not windows.event_context_enabled(config):
        return ""
    try:
        start, end = windows.event_window(trade_date, config)
    except ValueError as exc:
        logger.warning("event context: no window for %s (%s)", trade_date, exc)
        return ""

    blocks: list[str] = []
    missing = 0
    loaders = (
        ("event_context/issuer_releases", "issuer_ir", "Issuer releases",
         lambda: get_news_issuer_ir(ticker, start, end,
                                    limit=EVENT_ISSUER_LIMIT,
                                    excerpt_chars=EVENT_ISSUER_EXCERPT)),
        ("event_context/capital_markets", "ureg_capital", "Capital-market disclosures (UReg)",
         lambda: compact_ureg(get_ureg_capital_market_disclosures(
             ticker, as_of_date=end, since_date=start))),
        ("event_context/insider", "ureg_pdmr", "Insider filings (PDMR, Art. 19 MAR)",
         lambda: compact_ureg(get_ureg_pdmr_insider_transactions(
             ticker, as_of_date=end, since_date=start))),
    )
    for method, vendor, label, loader in loaders:
        text = _source(method, vendor, label, loader)
        if "- GAP: not configured" in text:
            missing += 1
        blocks.append(text)

    return _render(ticker, start, end, blocks, missing)
