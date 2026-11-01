"""Offline run-level snapshot and trade-status regression tests."""
import pytest

from tradingagents import advisory

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("rating", ["Hold", None])
def test_no_direction_needs_no_stop(rating):
    plan = advisory.build_advisory(ticker="XYZ", as_of="2026-01-05", rating=rating,
                                   account={"currency": "USD"})
    assert plan["verdict"] == "NO_TRADE"
    assert plan["status"] != "fail"
    assert not [c for c in plan["checks"] if c["blocking"] and not c["passed"]]
    assert not plan["missing"]


def test_one_frozen_frame_serves_snapshot_csv_indicators_and_sizing(monkeypatch):
    import pandas as pd

    from tradingagents.dataflows import router
    from tradingagents.dataflows.vendors.yahoo import market, ohlcv, snapshot

    scope = getattr(snapshot, "market_snapshot_scope", None)
    assert callable(scope), "run-scoped snapshot context is missing"
    calls = []
    def moving_loader(symbol, as_of_date, fill_gaps=True):
        calls.append(symbol)
        close = 100.0 + len(calls)
        return pd.DataFrame({"Date": pd.date_range("2026-01-01", periods=5),
                             "Open": close, "High": close + 2, "Low": close - 2,
                             "Close": close, "Volume": 1000.0})
    monkeypatch.setattr(ohlcv, "load_ohlcv", moving_loader)
    with scope("XYZ", "2026-01-05"):
        verified = snapshot.build_verified_market_snapshot("XYZ", "2026-01-05")
        csv = router.VENDOR_METHODS["get_stock_data"]["yfinance"]("XYZ", "2026-01-01", "2026-01-05")
        indicator = market.get_stock_stats("XYZ", "close_10_ema", "2026-01-05")
        levels = advisory.market_levels("XYZ", "2026-01-05")
        assert "| Close | 101.00 |" in verified
        assert "101.0" in csv or "101.00" in csv
        assert float(indicator) == 101.0
        assert levels["last_close"] == 101.0
        assert levels["snapshot"]["metadata"]["bar_status"] == "unknown"
        assert levels["snapshot"]["metadata"]["venue_timezone"] is None
    assert calls == ["XYZ"]
    with scope("XYZ", "2026-01-05"):
        assert advisory.market_levels("XYZ", "2026-01-05")["last_close"] == 102.0


def test_graph_initial_state_freezes_data_for_stream_and_artifact(monkeypatch, tmp_path):
    import pandas as pd

    from tradingagents.dataflows.vendors.yahoo import ohlcv, snapshot
    from tradingagents.graph.propagation import Propagator
    from tradingagents.graph.trading_graph import TradingAgentsGraph
    graph = TradingAgentsGraph.__new__(TradingAgentsGraph)
    graph.config = {}
    graph.propagator = Propagator()
    graph.resolve_instrument_context = lambda *args: "synthetic instrument"
    calls = []
    def loader(*args, **kwargs):
        calls.append(1)
        return pd.DataFrame({"Date": [pd.Timestamp("2026-01-05")],
                             "Open": [100.0], "High": [102.0], "Low": [98.0],
                             "Close": [100.0 + len(calls)], "Volume": [1000.0]})
    monkeypatch.setattr(ohlcv, "load_ohlcv", loader)
    state = graph.create_run_state("XYZ", "2026-01-05")
    assert state["market_levels"]["last_close"] == 101.0
    class Stream:
        def stream(self, graph_input, **kwargs):
            assert "| Close | 101.00 |" in snapshot.build_verified_market_snapshot("XYZ", "2026-01-05")
            yield (), "values", {**graph_input, "market_levels": advisory.market_levels("XYZ", "2026-01-05")}
    graph.graph = Stream()
    out = list(graph.stream_run(state))[-1][1]
    assert out["market_levels"]["snapshot"] == state["market_levels"]["snapshot"]
    assert calls == [1]
    plan = advisory.build_from_state(out, rating="Hold")
    advisory.write_artifacts(tmp_path, plan)
    import json
    saved = json.loads((tmp_path / "market_snapshot.json").read_text())
    assert saved == state["market_levels"]["snapshot"]


def test_gate_separates_structure_evidence_and_trade_plan(tmp_path):
    import json

    from tradingagents.report_gate import run_quality_gate
    (tmp_path / "5_portfolio").mkdir()
    (tmp_path / "5_portfolio/decision.md").write_text("**Rating**: Hold")
    (tmp_path / "complete_report.md").write_text("Completed report. " * 160)
    (tmp_path / "data_coverage.json").write_text(json.dumps({"methods": {
        "get_stock_data": {"status": "degraded", "served_by": ["yfinance"]}}}))
    plan = advisory.build_advisory(ticker="XYZ", as_of="2026-01-05", rating="Hold",
                                   account={"currency": "USD"})
    advisory.write_artifacts(tmp_path, plan)
    gate = run_quality_gate(tmp_path)
    assert gate["status"] == "APPROVED"
    assert gate.get("structural_status") == "APPROVED"
    assert gate.get("evidence_status") == "NEEDS_REVIEW"
    assert gate.get("trade_plan_status") == "NO_TRADE"


@pytest.mark.parametrize("bar_status", ["final", "partial", "unknown"])
def test_snapshot_renders_verified_bar_metadata_without_inventing_finality(monkeypatch, bar_status):
    import pandas as pd

    from tradingagents.dataflows.vendors.yahoo import ohlcv, snapshot
    frame = pd.DataFrame({"Date": [pd.Timestamp("2026-01-05")], "Open": [100.0],
                          "High": [102.0], "Low": [98.0], "Close": [101.0], "Volume": [1000]})
    frame.attrs.update(bar_status=bar_status, venue_timezone="America/New_York",
                       venue="XNYS", quote_timestamp="2026-01-05T15:00:00-05:00")
    monkeypatch.setattr(ohlcv, "load_ohlcv", lambda *a, **kw: frame)
    with snapshot.market_snapshot_scope("XYZ", "2026-01-05"):
        text = snapshot.build_verified_market_snapshot("XYZ", "2026-01-05")
        assert f"- Bar status: {bar_status}" in text
        assert "- Venue timezone: America/New_York" in text
        assert "- Quote timestamp: 2026-01-05T15:00:00-05:00" in text


def test_failed_initial_snapshot_is_not_refetched_inside_stream(monkeypatch):
    from tradingagents.dataflows.errors import NoMarketDataError
    from tradingagents.dataflows.vendors.yahoo import ohlcv
    from tradingagents.graph.propagation import Propagator
    from tradingagents.graph.trading_graph import TradingAgentsGraph
    graph = TradingAgentsGraph.__new__(TradingAgentsGraph)
    graph.config = {}
    graph.propagator = Propagator()
    graph.resolve_instrument_context = lambda *a: "synthetic"
    calls = []
    def missing(*a, **kw):
        calls.append(1)
        raise NoMarketDataError("XYZ", "XYZ", "missing bars")
    monkeypatch.setattr(ohlcv, "load_ohlcv", missing)
    state = graph.create_run_state("XYZ", "2026-01-05")
    class Stream:
        def stream(self, graph_input, **kwargs):
            yield (), "values", {"market_levels": advisory.market_levels("XYZ", "2026-01-05")}
    graph.graph = Stream()
    out = list(graph.stream_run(state))[-1][1]
    assert not out["market_levels"]["available"]
    assert calls == [1]


def test_replay_rejects_wrong_ticker_artifact(monkeypatch):
    import pandas as pd

    from tradingagents.dataflows.run_snapshot import snapshot_artifact
    from tradingagents.dataflows.vendors.yahoo import ohlcv, snapshot
    frame = pd.DataFrame({"Date": [pd.Timestamp("2026-01-05")], "Open": [100.0],
                          "High": [102.0], "Low": [98.0], "Close": [101.0], "Volume": [1000]})
    monkeypatch.setattr(ohlcv, "load_ohlcv", lambda *a, **kw: frame)
    with snapshot.market_snapshot_scope("XYZ", "2026-01-05"):
        advisory.market_levels("XYZ", "2026-01-05")
        artifact = snapshot_artifact()
    with pytest.raises(ValueError, match="identity"), snapshot.market_snapshot_scope("OTHER", "2026-01-05", artifact):
        pass


def test_advisory_warns_on_near_verified_cached_calendar_event(tmp_path):
    import json
    folder = tmp_path / "ir_facts"
    folder.mkdir()
    (folder / "XYZ.json").write_text(json.dumps({"ticker": "XYZ", "as_of": "2026-01-05",
        "calendar_verified": "2026-01-04", "calendar_stale": False,
        "calendar": [{"date": "2026-01-07", "event": "Quarterly results"}]}))
    plan = advisory.build_from_state({"company_of_interest": "XYZ", "trade_date": "2026-01-05",
                                      "final_rating": "Hold"},
                                     config={"data_cache_dir": str(tmp_path)})
    near = [c for c in plan["checks"] if c["name"] == "calendar_event_near"]
    assert near and not near[0]["passed"] and not near[0]["blocking"]
    assert "2026-01-07" in near[0]["detail"]
    assert "verified 2026-01-04" in near[0]["detail"]
    assert plan["verdict"] == "NO_TRADE"


def test_snapshot_artifact_preserves_unrounded_vendor_prices(monkeypatch):
    import pandas as pd

    from tradingagents.dataflows.vendors.yahoo import ohlcv, snapshot
    precise = 101.12345678901234
    frame = pd.DataFrame({"Date": [pd.Timestamp("2026-01-05")], "Open": [100.0],
                          "High": [102.0], "Low": [98.0], "Close": [precise], "Volume": [1000]})
    monkeypatch.setattr(ohlcv, "load_ohlcv", lambda *a, **kw: frame)
    with snapshot.market_snapshot_scope("XYZ", "2026-01-05"):
        assert advisory.market_levels("XYZ", "2026-01-05")["last_close"] == precise


def test_parallel_tool_workers_share_one_locked_snapshot(monkeypatch):
    import time
    from concurrent.futures import ThreadPoolExecutor
    from contextvars import copy_context

    import pandas as pd

    from tradingagents.dataflows.vendors.yahoo import market, ohlcv, snapshot
    calls = []
    def loader(*a, **kw):
        calls.append(1)
        time.sleep(0.02)
        return pd.DataFrame({"Date": pd.date_range("2026-01-01", periods=5), "Open": 100.0,
                             "High": 102.0, "Low": 98.0, "Close": 101.0, "Volume": 1000})
    monkeypatch.setattr(ohlcv, "load_ohlcv", loader)
    with snapshot.market_snapshot_scope("XYZ", "2026-01-05"):
        with ThreadPoolExecutor(max_workers=8) as workers:
            jobs = [workers.submit(copy_context().run, market.load_ohlcv, "XYZ", "2026-01-05")
                    for _ in range(8)]
            frames = [job.result() for job in jobs]
        frames[0].loc[0, "Close"] = -1
        assert advisory.market_levels("XYZ", "2026-01-05")["last_close"] == 101.0
        assert frames[1]["Close"].iloc[0] == 101.0
    assert calls == [1]


def test_concurrent_runs_do_not_share_mutable_market_data(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    import pandas as pd

    from tradingagents.dataflows.vendors.yahoo import ohlcv, snapshot
    barrier = Barrier(2)
    def loader(symbol, *a, **kw):
        return pd.DataFrame({"Date": [pd.Timestamp("2026-01-05")], "Open": [100.0],
                             "High": [202.0], "Low": [98.0],
                             "Close": [101.0 if symbol == "AAA" else 201.0], "Volume": [1000]})
    monkeypatch.setattr(ohlcv, "load_ohlcv", loader)
    def run(symbol):
        with snapshot.market_snapshot_scope(symbol, "2026-01-05"):
            barrier.wait(timeout=5)
            return advisory.market_levels(symbol, "2026-01-05")["last_close"]
    with ThreadPoolExecutor(max_workers=2) as workers:
        assert list(workers.map(run, ["AAA", "BBB"])) == [101.0, 201.0]


def test_exit_without_stop_and_warning_only_crv_remain_valid():
    exit_plan = advisory.build_advisory(ticker="XYZ", as_of="2026-01-05", rating="Sell",
        account={"positions": [{"ticker": "XYZ", "quantity": 10}]}, levels={"last_close": 100})
    assert exit_plan["verdict"] == "EXECUTE"
    assert exit_plan["position"]["kind"] == "exit"
    assert "stop_loss" not in exit_plan["missing"]
    entry = advisory.build_advisory(ticker="XYZ", as_of="2026-01-05", rating="Buy",
        account={"equity": 10000, "cash": 5000}, proposal={"entry_price": 100, "stop_loss": 90},
        target=105)
    crv = next(c for c in entry["checks"] if c["name"] == "reward_risk")
    assert not crv["passed"] and not crv["blocking"]
    assert entry["verdict"] == "EXECUTE"


@pytest.mark.parametrize("patch", [{"calendar_stale": True}, {"calendar_verified": "2026-01-06"},
                                     {"as_of": "2026-01-06"}, {"calendar_verified": "2025-01-01"}])
def test_unverified_or_future_cached_calendar_is_not_an_event_warning(tmp_path, patch):
    import json
    folder = tmp_path / "ir_facts"
    folder.mkdir()
    facts = {"ticker": "XYZ", "as_of": "2026-01-05", "calendar_verified": "2026-01-04",
             "calendar": [{"date": "2026-01-07", "event": "Quarterly results"}], **patch}
    (folder / "XYZ.json").write_text(json.dumps(facts))
    plan = advisory.build_from_state({"company_of_interest": "XYZ", "trade_date": "2026-01-05",
                                      "final_rating": "Hold"}, config={"data_cache_dir": str(tmp_path)})
    assert not [c for c in plan["checks"] if c["name"] == "calendar_event_near"]


def test_replay_rejects_changed_bars(monkeypatch):
    import pandas as pd

    from tradingagents.dataflows.run_snapshot import snapshot_artifact
    from tradingagents.dataflows.vendors.yahoo import ohlcv, snapshot
    frame = pd.DataFrame({"Date": [pd.Timestamp("2026-01-05")], "Open": [100.0],
                          "High": [102.0], "Low": [98.0], "Close": [101.0], "Volume": [1000]})
    monkeypatch.setattr(ohlcv, "load_ohlcv", lambda *a, **kw: frame)
    with snapshot.market_snapshot_scope("XYZ", "2026-01-05"):
        advisory.market_levels("XYZ", "2026-01-05")
        artifact = snapshot_artifact()
    artifact["ohlcv"][0]["Close"] = 999
    with pytest.raises(ValueError, match="digest"), snapshot.market_snapshot_scope("XYZ", "2026-01-05", artifact):
        pass


def test_resume_initialization_does_not_fetch_new_bars(monkeypatch, tmp_path):
    from tradingagents.dataflows.vendors.yahoo import ohlcv
    from tradingagents.graph import trading_graph
    from tradingagents.graph.propagation import Propagator
    graph = trading_graph.TradingAgentsGraph.__new__(trading_graph.TradingAgentsGraph)
    graph.config = {"checkpoint_enabled": True, "data_cache_dir": str(tmp_path)}
    graph.propagator = Propagator()
    graph.resolve_instrument_context = lambda *a: "synthetic"
    graph._run_signature = lambda *a: "test-signature"
    monkeypatch.setattr(trading_graph, "checkpoint_step", lambda *a: 1)
    calls = []
    monkeypatch.setattr(ohlcv, "load_ohlcv", lambda *a, **kw: calls.append(1))
    graph.create_run_state("XYZ", "2026-01-05")
    assert calls == []


def test_checkpoint_signature_isolates_pre_snapshot_runs():
    from tradingagents.default_config import DEFAULT_CONFIG
    from tradingagents.graph.trading_graph import TradingAgentsGraph
    graph = TradingAgentsGraph.__new__(TradingAgentsGraph)
    graph.config = DEFAULT_CONFIG
    graph.selected_analysts = ("market",)
    assert "market_snapshot=v1" in graph._run_signature("stock")
