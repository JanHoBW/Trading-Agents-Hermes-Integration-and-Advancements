"""The deterministic sizing plan is part of every saved run.

Section VI carries it under ``5_portfolio/`` (it belongs to the decision, not to
the coverage appendix), and the machine-readable twins sit next to the report.
"""

from __future__ import annotations

import json

import pytest

from tradingagents import advisory
from tradingagents.reporting import write_report_tree


def _state(with_plan: bool = True):
    state = {
        "company_of_interest": "X.DE", "trade_date": "2026-10-30",
        "final_trade_decision": "**Rating**: Buy", "final_rating": "Buy",
        "market_report": "m",
        "data_coverage": {"methods": {"get_news": {"status": "ok",
                                                   "served_by": ["issuer_ir"], "skips": []}}},
    }
    if with_plan:
        state["advisory"] = advisory.build_advisory(
            ticker="X.DE", as_of="2026-10-30", rating="Buy",
            proposal={"action": "Buy", "entry_price": 100.0, "stop_loss": 90.0},
            account={"equity": 25000.0, "cash": 5000.0, "currency": "EUR"},
            levels={"available": True, "last_close": 100.0, "atr": 5.0, "adv_value": 1e7},
        )
    return state


@pytest.mark.unit
def test_the_sizing_section_is_written(tmp_path):
    write_report_tree(_state(), "X.DE", tmp_path, html=False)
    section = (tmp_path / "5_portfolio" / "sizing.md").read_text(encoding="utf-8")
    assert "**Verdict**: EXECUTE" in section
    report = (tmp_path / "complete_report.md").read_text(encoding="utf-8")
    assert "VI. Position Sizing & Discipline" in report
    assert "VII. Data Coverage" in report
    # The section comes before the coverage appendix.
    assert report.index("VI. Position Sizing") < report.index("VII. Data Coverage")


@pytest.mark.unit
def test_save_reports_writes_the_plan_and_its_artifacts(tmp_path):
    """The end-to-end seam: a saved run carries sizing.json and discipline.json."""
    from types import SimpleNamespace

    from tradingagents.graph.trading_graph import TradingAgentsGraph

    state = {
        "company_of_interest": "X.DE", "trade_date": "2026-10-30",
        "final_trade_decision": "**Rating**: Buy", "final_rating": "Buy",
        "final_price_target": 120.0, "market_report": "m",
        "portfolio_account": {"equity": 25000.0, "cash": 5000.0, "currency": "EUR"},
        "trader_proposal": {"action": "Buy", "entry_price": 100.0, "stop_loss": 90.0},
        "market_levels": {"available": True, "last_close": 100.0, "atr": 5.0,
                          "adv_value": 1e7},
        "data_coverage": {"methods": {"get_news": {"status": "ok",
                                                   "served_by": ["issuer_ir"], "skips": []}}},
    }
    graph = SimpleNamespace(run_settings=lambda: {}, config={})
    TradingAgentsGraph.save_reports(graph, state, "X.DE", save_path=tmp_path, html=False)

    sizing = json.loads((tmp_path / "sizing.json").read_text(encoding="utf-8"))
    assert sizing["position"]["shares"] == 25
    assert sizing["levels"]["reward_risk"] == 2.0
    assert json.loads((tmp_path / "discipline.json").read_text(encoding="utf-8"))["status"] == "pass"
    gate = json.loads((tmp_path / "quality_gate.json").read_text(encoding="utf-8"))
    assert next(c for c in gate["checks"] if c["name"] == "position_sizing")["passed"] is True


@pytest.mark.unit
def test_without_a_plan_the_section_is_absent(tmp_path):
    write_report_tree(_state(with_plan=False), "X.DE", tmp_path, html=False)
    assert not (tmp_path / "5_portfolio" / "sizing.md").exists()
    report = (tmp_path / "complete_report.md").read_text(encoding="utf-8")
    assert "Position Sizing" not in report
    assert "VI. Data Coverage" in report      # numbering untouched without the plan


@pytest.mark.unit
def test_the_exposure_table_is_written_when_the_book_holds_instruments():
    state = _state(with_plan=False)
    state["advisory"] = advisory.build_advisory(
        ticker="RHM.DE", as_of="2026-10-30", rating="Buy",
        proposal={"action": "Buy", "entry_price": 1000.0, "stop_loss": 950.0},
        account={"equity": 300000.0, "cash": 50000.0, "currency": "EUR",
                 "positions": [{"ticker": "MK0L4Q", "quantity": 305, "market_value": 6234.46}]},
        levels={"available": True, "last_close": 1000.0, "atr": 20.0, "adv_value": 1e7},
        instruments={"MK0L4Q": {"kind": "knock_out", "underlying": "RHM.DE", "ratio": 0.1,
                                "underlying_price": 1048.2, "underlying_currency": "EUR",
                                "direction": "long"}},
    )
    section = advisory.render_markdown(state["advisory"])
    assert "### Exposure by underlying" in section
    assert "| RHM.DE | MK0L4Q |" in section
