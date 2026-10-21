"""Settlement: terminal statuses for unscorable decisions, and the price basis.

A missing price series used to leave an entry pending on every future run; a
delisted instrument or an absent benchmark never fixes itself, so it is recorded
as a terminal status instead. Transient gaps (window not yet traded) must keep
the old behaviour. Every scored horizon now states which price basis it used.
"""
from __future__ import annotations

from datetime import date, timedelta
from unittest import TestCase, mock

import pandas as pd
import pytest

from tradingagents.memory import TradingMemoryLog, settlement


def _series(days: list[str], values: list[float]) -> pd.Series:
    return pd.Series(values, index=pd.DatetimeIndex(days))


OLD = "2026-01-05"                      # clearly past the 30-day grace window
RECENT = (date.today() - timedelta(days=3)).isoformat()


class FetchReturnsStatusTests(TestCase):
    def _call(self, stock, bench, trade_date=OLD):
        def fake_closes(symbol, start, end):
            return stock if symbol == "RHM.DE" else bench

        with mock.patch.object(settlement, "get_closes", side_effect=fake_closes):
            return settlement.fetch_returns("RHM.DE", trade_date, 5, benchmark="^GDAXI")

    def test_empty_series_well_after_the_decision_is_delisted(self):
        with self.assertRaises(settlement.UnscorableError) as ctx:
            self._call(pd.Series(dtype=float), _series(["2026-01-02"], [24000.0]))
        self.assertEqual(ctx.exception.status, "delisted")
        self.assertIn("no price bars", ctx.exception.reason)

    def test_empty_benchmark_is_benchmark_missing(self):
        stock = _series(["2026-01-06", "2026-01-07", "2026-01-08", "2026-01-09",
                         "2026-01-12", "2026-01-13", "2026-01-14"], [100, 101, 102, 103, 104, 105, 106])
        with self.assertRaises(settlement.UnscorableError) as ctx:
            self._call(stock, pd.Series(dtype=float))
        self.assertEqual(ctx.exception.status, "benchmark_missing")

    def test_recent_empty_series_stays_pending_instead_of_terminal(self):
        # A fresh listing or a vendor hiccup must not be branded delisted.
        out = self._call(pd.Series(dtype=float), _series(["2026-01-02"], [24000.0]),
                         trade_date=RECENT)
        self.assertEqual(out, (None, None, None, None))

    def test_window_not_yet_traded_stays_pending(self):
        stock = _series(["2026-01-06", "2026-01-07"], [100, 101])
        bench = _series(["2026-01-02", "2026-01-06", "2026-01-07"], [24000, 24100, 24200])
        out = self._call(stock, bench)
        self.assertEqual(out, (None, None, None, None))


@pytest.mark.unit
def test_unscorable_entry_is_marked_and_no_longer_retried(tmp_path):
    log = TradingMemoryLog({"memory_log_path": str(tmp_path / "mem.md")})
    log.store_decision("DEAD.DE", OLD, "Rating: Hold")

    def raise_unscorable(*_a, **_k):
        raise settlement.UnscorableError("delisted", "no price bars for DEAD.DE")

    with mock.patch.object(settlement, "fetch_horizons", side_effect=raise_unscorable):
        result = settlement.settle_pending("DEAD.DE", log, mock.Mock(), {}, wait=False)

    assert result.unscorable == [("DEAD.DE", OLD, "delisted")]
    assert result.failed == []
    entry = log.load_entries()[0]
    assert entry["unscorable"]["status"] == "delisted"
    # Second pass: nothing left to do, so no further price fetch.
    with mock.patch.object(settlement, "fetch_horizons") as fetch:
        again = settlement.settle_pending("DEAD.DE", log, mock.Mock(), {}, wait=False)
    assert again.unscorable == [] and not fetch.called


@pytest.mark.unit
def test_scored_horizon_records_the_price_basis(tmp_path):
    log = TradingMemoryLog({"memory_log_path": str(tmp_path / "mem.md")})
    log.store_decision("RHM.DE", OLD, "Rating: Buy\nEnter.")

    def fake_horizons(ticker, trade_date, missing, benchmark="SPY"):
        return {h: (0.03, 0.01, h, "2026-01-12") for h in missing}

    reflector = mock.Mock()
    reflector.reflect_on_final_decision.return_value = "Lesson."
    with mock.patch.object(settlement, "fetch_horizons", side_effect=fake_horizons):
        settlement.settle_pending("RHM.DE", log, reflector, {}, wait=False)

    entry = log.load_entries()[0]
    assert entry["horizons"][5]["adjustment"] == settlement.PRICE_ADJUSTMENT
    assert settlement.PRICE_ADJUSTMENT == "adjusted"


@pytest.mark.unit
def test_unscorable_requires_a_known_status(tmp_path):
    log = TradingMemoryLog({"memory_log_path": str(tmp_path / "mem.md")})
    log.store_decision("X.DE", OLD, "Rating: Hold")
    with pytest.raises(ValueError):
        log.mark_unscorable("X.DE", OLD, "maybe")
