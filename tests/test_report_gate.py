"""Tests for the post-run quality gate (tradingagents/report_gate.py)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from tradingagents import report_gate


REPORT_BODY = "# RHM.DE\n\n" + ("Analysis text with numbers and dates. " * 120) + "\n"
DECISION = "**Rating**: Buy\n\nPositionsgröße ...\n"


def _tree(tmp_path, *, report=REPORT_BODY, decision=DECISION, coverage=True,
          coverage_md="# Data Coverage\n\n**Analysis windows**\n- Headline window: ...\n",
          methods=None, sections=True):
    (tmp_path / "1_analysts").mkdir(exist_ok=True)
    if sections:
        for name in ("market", "sentiment", "news", "fundamentals"):
            (tmp_path / "1_analysts" / f"{name}.md").write_text("analyst body", encoding="utf-8")
    (tmp_path / "2_research").mkdir(exist_ok=True)
    (tmp_path / "3_trading").mkdir(exist_ok=True)
    (tmp_path / "5_portfolio").mkdir(exist_ok=True)
    (tmp_path / "6_coverage").mkdir(exist_ok=True)
    if sections:
        (tmp_path / "2_research" / "manager.md").write_text("plan", encoding="utf-8")
        (tmp_path / "3_trading" / "trader.md").write_text("plan", encoding="utf-8")
    if report is not None:
        (tmp_path / "complete_report.md").write_text(report, encoding="utf-8")
    if decision is not None:
        (tmp_path / "5_portfolio" / "decision.md").write_text(decision, encoding="utf-8")
    if coverage_md is not None:
        (tmp_path / "6_coverage" / "coverage.md").write_text(coverage_md, encoding="utf-8")
    if coverage:
        snapshot = {"methods": methods if methods is not None else {
            "get_stock_data": {"status": "ok", "served_by": ["yfinance"], "skips": []},
            "get_news": {"status": "ok", "served_by": ["issuer_ir"], "skips": []},
        }, "gaps": [], "totals": {}}
        (tmp_path / "data_coverage.json").write_text(json.dumps(snapshot), encoding="utf-8")
    return tmp_path


def _check(verdict, name):
    return next(c for c in verdict["checks"] if c["name"] == name)


@pytest.mark.unit
class TestQualityGate:
    def test_complete_run_is_approved(self, tmp_path):
        verdict = report_gate.run_quality_gate(_tree(tmp_path))
        assert verdict["status"] == "APPROVED"
        assert all(c["passed"] for c in verdict["checks"] if c["blocking"])
        assert verdict["gaps"] == []

    def test_writes_the_verdict_next_to_the_report(self, tmp_path):
        report_gate.run_quality_gate(_tree(tmp_path))
        written = json.loads((tmp_path / "quality_gate.json").read_text(encoding="utf-8"))
        assert written["status"] == "APPROVED"
        assert written["report_dir"] == str(tmp_path)

    def test_missing_report_is_blocking(self, tmp_path):
        verdict = report_gate.run_quality_gate(_tree(tmp_path, report=None))
        assert verdict["status"] == "NEEDS_REVIEW"
        assert _check(verdict, "report_present")["passed"] is False

    def test_short_report_is_blocking(self, tmp_path):
        verdict = report_gate.run_quality_gate(_tree(tmp_path, report="too short"))
        assert verdict["status"] == "NEEDS_REVIEW"
        assert _check(verdict, "report_substantial")["passed"] is False

    def test_missing_decision_is_blocking(self, tmp_path):
        verdict = report_gate.run_quality_gate(_tree(tmp_path, decision=None))
        assert verdict["status"] == "NEEDS_REVIEW"
        assert _check(verdict, "decision_present")["passed"] is False

    def test_decision_without_a_rating_is_blocking(self, tmp_path):
        verdict = report_gate.run_quality_gate(
            _tree(tmp_path, decision="We looked at it and decided later."))
        assert _check(verdict, "decision_rating")["passed"] is False

    def test_two_ratings_without_an_explicit_line_are_ambiguous(self, tmp_path):
        verdict = report_gate.run_quality_gate(
            _tree(tmp_path, decision="A Buy case versus a Sell case; no conclusion."))
        assert _check(verdict, "decision_rating")["passed"] is False

    def test_explicit_rating_line_wins_over_prose_alternatives(self, tmp_path):
        verdict = report_gate.run_quality_gate(_tree(
            tmp_path, decision="**Rating**: Hold\n\nThis is not a Buy signal.\n"))
        assert _check(verdict, "decision_rating")["passed"] is True
        assert _check(verdict, "decision_rating")["detail"] == "rating found: Hold"

    def test_placeholder_in_the_report_is_blocking(self, tmp_path):
        verdict = report_gate.run_quality_gate(
            _tree(tmp_path, report=REPORT_BODY + "\nTODO: fill in the peer table\n"))
        assert verdict["status"] == "NEEDS_REVIEW"
        assert "TODO" in _check(verdict, "no_placeholders")["detail"]

    def test_missing_coverage_record_is_blocking(self, tmp_path):
        verdict = report_gate.run_quality_gate(_tree(tmp_path, coverage=False))
        assert verdict["status"] == "NEEDS_REVIEW"
        assert _check(verdict, "coverage_recorded")["passed"] is False

    def test_gaps_are_reported_but_do_not_block(self, tmp_path):
        methods = {
            "get_news": {"status": "degraded", "served_by": ["yfinance"], "skips": [
                {"vendor": "issuer_ir", "status": "not_configured", "reason": "no ir block"}]},
            "get_fundamentals": {"status": "ok", "served_by": ["yfinance"], "skips": []},
        }
        verdict = report_gate.run_quality_gate(_tree(tmp_path, methods=methods))
        assert verdict["status"] == "APPROVED"
        assert _check(verdict, "no_data_gaps")["passed"] is False
        assert verdict["gaps"] == [{"method": "get_news", "status": "degraded",
                                     "served_by": ["yfinance"], "reason": "no ir block"}]

    def test_missing_section_and_windows_are_warnings_only(self, tmp_path):
        verdict = report_gate.run_quality_gate(
            _tree(tmp_path, sections=False, coverage_md="# Data Coverage\n"))
        assert verdict["status"] == "APPROVED"
        assert _check(verdict, "sections_complete")["passed"] is False
        assert _check(verdict, "coverage_windows_named")["passed"] is False

    def test_a_blocked_discipline_plan_is_a_warning_not_a_failure(self, tmp_path):
        """A blocked setup is an honest report: the gate flags it, never fails it."""
        tree = _tree(tmp_path)
        (tree / "sizing.json").write_text(json.dumps({
            "schema": "tradingagents.advisory.v1", "verdict": "BLOCKED", "status": "fail",
            "checks": [{"name": "equity", "passed": False, "blocking": True,
                        "detail": "not available"}],
        }), encoding="utf-8")
        (tree / "discipline.json").write_text(
            json.dumps({"status": "fail", "verdict": "BLOCKED"}), encoding="utf-8")

        verdict = report_gate.run_quality_gate(tree)

        assert verdict["status"] == "APPROVED"
        check = _check(verdict, "position_sizing")
        assert check["passed"] is False
        assert "blocked by equity" in check["detail"]

    def test_an_executable_plan_passes_the_sizing_check(self, tmp_path):
        tree = _tree(tmp_path)
        (tree / "sizing.json").write_text(json.dumps({
            "schema": "tradingagents.advisory.v1", "verdict": "EXECUTE", "status": "pass",
            "checks": [],
        }), encoding="utf-8")
        (tree / "discipline.json").write_text(
            json.dumps({"status": "pass", "verdict": "EXECUTE"}), encoding="utf-8")

        verdict = report_gate.run_quality_gate(tree)

        assert _check(verdict, "position_sizing")["passed"] is True
        assert "verdict EXECUTE" in _check(verdict, "position_sizing")["detail"]

    def test_a_missing_sizing_record_is_a_warning(self, tmp_path):
        verdict = report_gate.run_quality_gate(_tree(tmp_path))
        assert verdict["status"] == "APPROVED"
        assert _check(verdict, "position_sizing")["passed"] is False

    def test_gate_never_raises_on_an_unwritable_directory(self, tmp_path):
        tree = _tree(tmp_path)
        (tree / "quality_gate.json").mkdir()  # a directory where the file goes
        verdict = report_gate.run_quality_gate(tree)   # must not raise
        assert verdict["status"] in {"APPROVED", "NEEDS_REVIEW"}

    def test_save_reports_runs_the_gate(self, tmp_path):
        from tradingagents.graph.trading_graph import TradingAgentsGraph

        graph = SimpleNamespace(run_settings=lambda: {})
        state = {"final_trade_decision": "**Rating**: Buy", "asset_type": "stock",
                 "market_report": "m", "sentiment_report": "s", "news_report": "n",
                 "fundamentals_report": "f",
                 "data_coverage": {"methods": {"get_news": {"status": "ok",
                                                            "served_by": ["yfinance"], "skips": []}}}}
        out = TradingAgentsGraph.save_reports(graph, state, "RHM.DE",
                                              save_path=tmp_path, html=False)
        gate_file = out.parent / "quality_gate.json"
        assert gate_file.exists()
        assert json.loads(gate_file.read_text(encoding="utf-8"))["status"] in {"APPROVED", "NEEDS_REVIEW"}
        assert state["quality_gate"]["status"] in {"APPROVED", "NEEDS_REVIEW"}


@pytest.mark.unit
class TestExposureGate:
    def _with_sizing(self, tmp_path, exposure):
        _tree(tmp_path)
        (tmp_path / "sizing.json").write_text(json.dumps({
            "verdict": "EXECUTE", "status": "pass", "checks": [], "exposure": exposure,
        }), encoding="utf-8")
        return report_gate.run_quality_gate(tmp_path)

    def test_an_unmodelled_holding_is_a_warning_not_a_failure(self, tmp_path):
        verdict = self._with_sizing(tmp_path, {
            "rows": [{"key": "MK0L4Q", "exposure": None, "status": "unknown"}],
            "by_underlying": {}, "unresolved": ["MK0L4Q"], "status": "partial",
        })
        check = _check(verdict, "book_exposure")
        assert check["passed"] is False and check["blocking"] is False
        assert "MK0L4Q" in check["detail"]
        assert verdict["status"] == "APPROVED"

    def test_a_concentrated_underlying_is_named(self, tmp_path):
        verdict = self._with_sizing(tmp_path, {
            "rows": [{"key": "A40Y9K", "exposure": 262151.34, "status": "ok"}],
            "by_underlying": {"A40Y9K": {"net": 262151.34, "over_cap": True}},
            "unresolved": [], "status": "ok",
        })
        check = _check(verdict, "book_exposure")
        assert "A40Y9K" in check["detail"] and check["passed"] is False
