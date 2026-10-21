"""Data coverage: the routing layer's own record of what each run received.

The point of these tests is wiring, not formatting: a vendor skip must reach the
Portfolio Manager's prompt and the saved report, and a method nobody called must
never be dressed up as a data gap.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from tradingagents.agents.managers.portfolio_manager import create_portfolio_manager
from tradingagents.dataflows import coverage
from tradingagents.dataflows.errors import VendorNotConfiguredError, VendorUnavailableError
from tradingagents.reporting import write_report_tree


@pytest.fixture(autouse=True)
def _clean_registry():
    coverage.reset()
    yield
    coverage.reset()


@pytest.mark.unit
def test_skips_collapse_and_success_marks_degraded():
    coverage.record("get_news", "issuer_rss", coverage.NOT_CONFIGURED)
    coverage.record("get_news", "issuer_rss", coverage.NOT_CONFIGURED)  # same skip again
    coverage.record("get_news", "finnhub", coverage.UNAVAILABLE, "token missing")
    coverage.record("get_news", "yfinance", coverage.OK)

    snap = coverage.snapshot()
    rec = snap["methods"]["get_news"]
    assert rec["status"] == "degraded"
    assert rec["served_by"] == ["yfinance"]
    assert len(rec["skips"]) == 2          # deduped, not four
    assert snap["totals"]["gaps"] == 2
    assert snap["gaps"][0]["state"] in {"not_configured", "unavailable"}


@pytest.mark.unit
def test_all_vendors_skipped_is_unavailable_not_ok():
    coverage.record("get_insider_transactions", "ureg_pdmr", coverage.NOT_CONFIGURED)
    coverage.record("get_insider_transactions", "finnhub", coverage.NOT_CONFIGURED)

    rec = coverage.snapshot()["methods"]["get_insider_transactions"]
    assert rec["status"] == "unavailable"
    assert rec["served_by"] == []


@pytest.mark.unit
def test_context_names_gaps_and_never_invents_missing_inputs():
    coverage.record("get_fundamentals", "yfinance", coverage.OK)
    coverage.record("get_news", "issuer_rss", coverage.NOT_CONFIGURED)

    text = coverage.render_context(coverage.snapshot(), "stock")
    assert "issuer_rss" in text
    assert "not_configured" in text
    assert "not requested" in text.lower()          # the untouched methods
    assert "do not assume" in text.lower()          # the consequence, stated
    assert overview_line(text).startswith("DATA COVERAGE")


def overview_line(text: str) -> str:
    return text.splitlines()[0]


@pytest.mark.unit
def test_context_is_empty_without_any_routed_call():
    assert coverage.render_context(coverage.snapshot(), "stock") == ""
    assert coverage.render_markdown(coverage.snapshot(), "stock") == ""


@pytest.mark.unit
def test_router_records_the_vendor_that_served_and_the_one_it_skipped(monkeypatch):
    from tradingagents.dataflows import router

    def missing_token():
        raise VendorNotConfiguredError("issuer_rss")

    def working():
        return "NEWS"

    monkeypatch.setitem(router.VENDOR_METHODS, "get_news",
                        {"issuer_rss": missing_token, "yfinance": working})
    monkeypatch.setattr(router, "get_vendor", lambda category, method=None: "issuer_rss,yfinance")

    assert router.route_to_vendor("get_news") == "NEWS"
    rec = coverage.snapshot()["methods"]["get_news"]
    assert rec["served_by"] == ["yfinance"]
    assert rec["skips"][0]["vendor"] == "issuer_rss"
    assert rec["status"] == "degraded"


@pytest.mark.unit
def test_router_records_an_untyped_failure_with_its_reason(monkeypatch):
    from tradingagents.dataflows import router

    def broken(*args, **kwargs):
        raise RuntimeError("boom")

    def working(*args, **kwargs):
        return "OK"

    monkeypatch.setitem(router.VENDOR_METHODS, "get_stock_data",
                        {"alpha_vantage": broken, "yfinance": working})
    monkeypatch.setattr(router, "get_vendor", lambda category, method=None: "alpha_vantage,yfinance")

    assert router.route_to_vendor("get_stock_data", "AAPL") == "OK"
    skip = coverage.snapshot()["methods"]["get_stock_data"]["skips"][0]
    assert skip["state"] == "failed"
    assert "boom" in skip["reason"]


@pytest.mark.unit
def test_portfolio_manager_sees_the_gaps_and_returns_the_record():
    from tradingagents.agents.schemas import PortfolioDecision, PortfolioRating

    coverage.record("get_news", "issuer_rss", coverage.NOT_CONFIGURED)
    coverage.record("get_news", "yfinance", coverage.OK)

    captured = {}
    structured = MagicMock()
    structured.invoke.side_effect = lambda prompt: (
        captured.__setitem__("prompt", prompt)
        or PortfolioDecision(rating=PortfolioRating.HOLD, executive_summary="x", investment_thesis="y")
    )
    llm = MagicMock()
    llm.with_structured_output.return_value = structured

    risk = {
        "history": "h", "aggressive_history": "a", "conservative_history": "c",
        "neutral_history": "n", "current_aggressive_response": "",
        "current_conservative_response": "", "current_neutral_response": "",
        "latest_speaker": "Neutral", "count": 1,
    }
    out = create_portfolio_manager(llm)({
        "company_of_interest": "RHM.DE", "asset_type": "stock",
        "risk_debate_state": risk, "investment_plan": "plan",
        "trader_investment_plan": "trader plan",
    })

    assert "DATA COVERAGE" in _prompt_text(captured["prompt"])
    assert "issuer_rss" in _prompt_text(captured["prompt"])
    assert out["data_coverage"]["methods"]["get_news"]["status"] == "degraded"


@pytest.mark.unit
def test_report_contains_a_coverage_section_and_a_machine_readable_twin(tmp_path):
    state = {
        "asset_type": "stock",
        "final_trade_decision": "PM DECISION",
        "data_coverage": {
            "methods": {
                "get_news": {"status": "degraded", "served_by": ["yfinance"],
                             "skips": [{"vendor": "issuer_rss", "state": "not_configured",
                                        "reason": ""}]},
            },
            "gaps": [{"method": "get_news", "vendor": "issuer_rss",
                      "state": "not_configured", "reason": ""}],
            "totals": {"calls": 2, "methods": 1, "degraded": 1, "gaps": 1},
        },
    }
    out = write_report_tree(state, "RHM.DE", tmp_path, html=False)
    text = out.read_text()
    assert "VI. Data Coverage" in text
    assert "issuer_rss" in text
    assert (tmp_path / "6_coverage" / "coverage.md").exists()


@pytest.mark.unit
def test_report_has_no_coverage_section_without_a_record(tmp_path):
    out = write_report_tree({"final_trade_decision": "PM DECISION"}, "RHM.DE", tmp_path, html=False)
    assert "Data Coverage" not in out.read_text()


@pytest.mark.unit
def test_save_reports_writes_data_coverage_json(tmp_path):
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    graph = SimpleNamespace(run_settings=lambda: {})
    state = {"final_trade_decision": "PM", "asset_type": "stock",
             "data_coverage": {"methods": {"get_news": {"status": "ok", "served_by": ["yfinance"],
                                                        "skips": []}},
                               "gaps": [], "totals": {}}}
    out = TradingAgentsGraph.save_reports(graph, state, "RHM.DE", save_path=tmp_path, html=False)
    coverage_json = out.parent / "data_coverage.json"
    assert coverage_json.exists()
    assert '"get_news"' in coverage_json.read_text()


def _prompt_text(prompt) -> str:
    if isinstance(prompt, str):
        return prompt
    parts = []
    for m in prompt:
        parts.append(m.get("content", "") if isinstance(m, dict) else getattr(m, "content", ""))
    return "\n".join(str(p) for p in parts)
