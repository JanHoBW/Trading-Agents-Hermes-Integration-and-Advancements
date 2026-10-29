"""Two horizons, one source of truth.

The headline window (news/sentiment/supplement) and the first-party event window
are separate on purpose: 7 days answers "what is happening now", 180 days answers
"what shaped this thesis" and matches the longest settlement horizon. These tests
pin the arithmetic, the config/env plumbing, and the two integration points that
must never silently fall back to 7 days (news analyst prompt, report).
"""
from __future__ import annotations

import pytest

from tradingagents.dataflows import windows
from tradingagents.dataflows import coverage, event_context
from tradingagents.dataflows.errors import VendorNotConfiguredError, VendorUnavailableError


# ---------------------------------------------------------------------------
# Window arithmetic
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_window_is_calendar_days_before_the_trade_date():
    assert windows.window("2026-10-21", 7) == ("2026-10-14", "2026-10-21")
    assert windows.window("2026-10-21", 180) == ("2026-04-24", "2026-10-21")
    # month and year boundaries are plain calendar arithmetic
    assert windows.window("2026-03-01", 7) == ("2026-02-22", "2026-03-01")


@pytest.mark.unit
@pytest.mark.parametrize("days", [0, -5])
def test_window_rejects_a_non_positive_lookback(days):
    with pytest.raises(ValueError, match="at least 1 day"):
        windows.window("2026-10-21", days)


@pytest.mark.unit
def test_window_rejects_a_malformed_trade_date():
    with pytest.raises(ValueError, match="not a YYYY-MM-DD"):
        windows.window("21.10.2026", 7)


@pytest.mark.unit
def test_defaults_are_seven_and_one_hundred_eighty_days():
    assert windows.headline_window("2026-10-21", {}) == ("2026-10-14", "2026-10-21")
    assert windows.event_window("2026-10-21", {}) == ("2026-04-24", "2026-10-21")


@pytest.mark.unit
def test_config_overrides_both_windows():
    cfg = {"news_lookback_days": 30, "event_context_lookback_days": 90}
    assert windows.headline_window("2026-10-21", cfg) == ("2026-09-21", "2026-10-21")
    assert windows.event_window("2026-10-21", cfg) == ("2026-07-23", "2026-10-21")


@pytest.mark.unit
def test_event_context_switch_is_respected():
    assert windows.event_context_enabled({}) is True
    assert windows.event_context_enabled({"event_context_enabled": False}) is False


@pytest.mark.unit
def test_env_override_reaches_the_effective_config(monkeypatch):
    """The wrapper sets env vars; DEFAULT_CONFIG folds them in (documented seam)."""
    from tradingagents.default_config import _apply_env_overrides

    monkeypatch.setenv("TRADINGAGENTS_NEWS_LOOKBACK_DAYS", "30")
    monkeypatch.setenv("TRADINGAGENTS_EVENT_LOOKBACK_DAYS", "90")
    monkeypatch.setenv("TRADINGAGENTS_EVENT_CONTEXT", "0")
    cfg = {"news_lookback_days": 7, "event_context_lookback_days": 180,
           "event_context_enabled": True}
    _apply_env_overrides(cfg)
    assert cfg == {"news_lookback_days": 30, "event_context_lookback_days": 90,
                   "event_context_enabled": False}
    assert windows.headline_window("2026-10-21", cfg)[0] == "2026-09-21"


# ---------------------------------------------------------------------------
# Event context block
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_event_context_names_the_window_and_the_gap_semantics(monkeypatch):
    monkeypatch.setattr(event_context, "get_news_issuer_ir",
                        lambda t, s, e, **kw: "# Releases\n10.10. Ad-hoc: Auftrag")
    monkeypatch.setattr(event_context, "get_ureg_capital_market_disclosures",
                        lambda t, **kw: "# Disclosures\n11.10. Stimmrechtsmitteilung")
    monkeypatch.setattr(event_context, "get_ureg_pdmr_insider_transactions",
                        lambda t, **kw: "# PDMR\n12.10. Vorstand kauft 5.000 Aktien")

    out = event_context.build_event_context("RHM.DE", "2026-10-21", {})
    assert "EVENT CONTEXT — first-party sources, 2026-04-24 to 2026-10-21" in out
    assert "not a substitute for" in out
    assert 'do not read that as\n"nothing happened"' in out
    assert "Auftrag" in out and "Stimmrechtsmitteilung" in out and "Vorstand kauft" in out


@pytest.mark.unit
def test_event_context_passes_the_lower_bound_to_the_ureg_sources(monkeypatch):
    seen = {}

    def capital(ticker, as_of_date=None, since_date=None, **kw):
        seen["capital"] = (as_of_date, since_date)
        return "# x"

    def pdmr(ticker, as_of_date=None, since_date=None, **kw):
        seen["pdmr"] = (as_of_date, since_date)
        return "# y"

    monkeypatch.setattr(event_context, "get_news_issuer_ir", lambda t, s, e, **kw: "# z")
    monkeypatch.setattr(event_context, "get_ureg_capital_market_disclosures", capital)
    monkeypatch.setattr(event_context, "get_ureg_pdmr_insider_transactions", pdmr)

    event_context.build_event_context("RHM.DE", "2026-10-21", {})
    assert seen == {"capital": ("2026-10-21", "2026-04-24"),
                    "pdmr": ("2026-10-21", "2026-04-24")}


@pytest.mark.unit
def test_missing_sources_are_explicit_gaps_not_absences(monkeypatch):
    def gone(*a, **kw):
        raise VendorNotConfiguredError("no registry entry")

    monkeypatch.setattr(event_context, "get_news_issuer_ir", gone)
    monkeypatch.setattr(event_context, "get_ureg_capital_market_disclosures", gone)
    monkeypatch.setattr(event_context, "get_ureg_pdmr_insider_transactions", gone)

    out = event_context.build_event_context("NVDA", "2026-10-21", {})
    # Three identical "not configured" blocks would be noise, so they collapse
    # into one sentence — but the semantics stay explicit.
    assert out.lower().count("no first-party source is configured") == 1
    assert "nothing happened" in out


@pytest.mark.unit
def test_an_unavailable_source_is_labelled_differently_from_an_unconfigured_one(monkeypatch):
    def unreachable(*a, **kw):
        raise VendorUnavailableError("timeout")

    monkeypatch.setattr(event_context, "get_ir_facts", unreachable)
    monkeypatch.setattr(event_context, "get_news_issuer_ir", unreachable)
    monkeypatch.setattr(event_context, "get_ureg_capital_market_disclosures", unreachable)
    monkeypatch.setattr(event_context, "get_ureg_pdmr_insider_transactions", unreachable)

    out = event_context.build_event_context("RHM.DE", "2026-10-21", {})
    # Four first-party sources: IR facts, issuer releases, capital markets, insider.
    assert out.count("GAP: source could not be retrieved") == 4
    assert "GAP: not configured" not in out


@pytest.mark.unit
def test_a_crashing_source_never_sinks_the_block(monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("parser bug")

    monkeypatch.setattr(event_context, "get_news_issuer_ir", boom)
    monkeypatch.setattr(event_context, "get_ureg_capital_market_disclosures", boom)
    monkeypatch.setattr(event_context, "get_ureg_pdmr_insider_transactions", boom)

    out = event_context.build_event_context("RHM.DE", "2026-10-21", {})
    assert "GAP: source failed (RuntimeError: parser bug)" in out


@pytest.mark.unit
def test_disabling_the_block_produces_nothing(monkeypatch):
    monkeypatch.setattr(event_context, "get_news_issuer_ir",
                        lambda *a, **kw: pytest.fail("source must not be called"))
    assert event_context.build_event_context("RHM.DE", "2026-10-21",
                                             {"event_context_enabled": False}) == ""


@pytest.mark.unit
def test_event_context_records_coverage(monkeypatch):
    coverage.reset()
    monkeypatch.setattr(event_context, "get_news_issuer_ir", lambda t, s, e, **kw: "# a")

    def gone(*a, **kw):
        raise VendorNotConfiguredError("nope")

    monkeypatch.setattr(event_context, "get_ureg_capital_market_disclosures", gone)
    monkeypatch.setattr(event_context, "get_ureg_pdmr_insider_transactions", gone)
    event_context.build_event_context("RHM.DE", "2026-10-21", {})

    snap = coverage.snapshot()["methods"]
    assert snap["event_context/issuer_releases"]["status"] == coverage.OK
    # an aggregate status of "unavailable" with a not_configured skip reason is the
    # documented pair: nothing served the call, and the reason is a config gap
    assert snap["event_context/capital_markets"]["status"] == "unavailable"
    assert snap["event_context/capital_markets"]["skips"][0]["state"] == coverage.NOT_CONFIGURED
    assert snap["event_context/insider"]["skips"][0]["vendor"] == "ureg_pdmr"
    coverage.reset()


@pytest.mark.unit
def test_long_source_text_is_truncated_with_a_marker(monkeypatch):
    monkeypatch.setattr(event_context, "get_news_issuer_ir",
                        lambda t, s, e, **kw: "x" * (event_context.MAX_BLOCK_CHARS + 500))
    monkeypatch.setattr(event_context, "get_ureg_capital_market_disclosures", lambda t, **kw: "")
    monkeypatch.setattr(event_context, "get_ureg_pdmr_insider_transactions", lambda t, **kw: "")

    out = event_context.build_event_context("RHM.DE", "2026-10-21", {})
    assert "_(event context truncated)_" in out
    assert len(out) < event_context.MAX_BLOCK_CHARS + 1200


# ---------------------------------------------------------------------------
# Integration: prompt and report must carry the real windows
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_news_analyst_prompt_states_both_windows(monkeypatch):
    from tradingagents.agents.analysts import news_analyst

    captured = {}

    def fake_take_turn(prompt, llm, tools, messages):
        captured["prompt"] = prompt
        return (None, "report")

    monkeypatch.setattr(news_analyst, "take_turn", fake_take_turn)
    monkeypatch.setattr(news_analyst, "build_event_context",
                        lambda ticker, trade_date: "## EVENT CONTEXT test-block")
    node = news_analyst.create_news_analyst(llm=None)
    node({"trade_date": "2026-10-21", "company_of_interest": "RHM.DE",
          "asset_type": "stock", "messages": []})

    text = " ".join(m.content for m in captured["prompt"].format_messages(messages=[]))
    assert "headline window 2026-10-14 to 2026-10-21 (7 days)" in text
    assert "get_news(start_date='2026-10-14', end_date='2026-10-21')" in text
    assert "EVENT CONTEXT test-block" in text


@pytest.mark.unit
def test_report_states_the_windows_it_used():
    from tradingagents.reporting import _window_lines

    lines = _window_lines("2026-10-21")
    assert "Headline window: 2026-10-14 → 2026-10-21 (7 days)" in lines[0]
    assert "Event context: 2026-04-24 → 2026-10-21 (180 days)" in lines[1]
    assert _window_lines(None) == []


@pytest.mark.unit
def test_coverage_section_renders_the_windows_above_the_table():
    from tradingagents.dataflows import coverage as cov

    snap = {"methods": {"get_news": {"status": "ok", "served_by": ["issuer_ir"], "skips": []}},
            "gaps": []}
    md = cov.render_markdown(snap, "stock",
                             ["Headline window: 2026-10-14 → 2026-10-21 (7 days)"])
    assert "**Analysis windows**" in md
    assert md.index("**Analysis windows**") < md.index("| Input |")
