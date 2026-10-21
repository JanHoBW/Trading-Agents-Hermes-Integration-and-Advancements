"""Tests for the deterministic market-data verification snapshot (#830/#881)."""

from __future__ import annotations

import pandas as pd
import pytest

import tradingagents.dataflows.vendors.yahoo.snapshot as validator
from tradingagents.dataflows.errors import NoMarketDataError


def _sample_ohlcv() -> pd.DataFrame:
    dates = pd.bdate_range("2026-04-01", "2026-05-20")
    closes = [100 + i for i in range(len(dates))]
    return pd.DataFrame({
        "Date": dates,
        "Open": [c - 0.5 for c in closes],
        "High": [c + 1.0 for c in closes],
        "Low": [c - 1.0 for c in closes],
        "Close": closes,
        "Volume": [1_000_000 + i for i in range(len(dates))],
    })


@pytest.mark.unit
class TestVerifiedSnapshot:
    def test_excludes_future_rows(self, monkeypatch):
        data = pd.concat([
            _sample_ohlcv(),
            pd.DataFrame({"Date": [pd.Timestamp("2026-06-01")], "Open": [999.0],
                          "High": [999.0], "Low": [999.0], "Close": [999.0], "Volume": [999]}),
        ], ignore_index=True)
        monkeypatch.setattr(validator, "load_ohlcv", lambda s, d, fill_gaps=True: data)

        snap = validator.build_verified_market_snapshot("COF", "2026-05-13")
        assert "Verified market data snapshot for COF" in snap
        assert "Requested analysis date: 2026-05-13" in snap
        assert "Latest trading row used: 2026-05-13" in snap
        assert "999.00" not in snap          # future row excluded
        assert "boll_lb" in snap             # indicators present

    def test_uses_previous_trading_day_when_date_is_weekend(self, monkeypatch):
        monkeypatch.setattr(validator, "load_ohlcv", lambda s, d, fill_gaps=True: _sample_ohlcv())
        # 2026-05-16 is a Saturday; latest row should be Fri 2026-05-15
        snap = validator.build_verified_market_snapshot("COF", "2026-05-16")
        assert "Latest trading row used: 2026-05-15" in snap
        assert "Recent verified closes" in snap

    def test_raises_when_no_rows_on_or_before_date(self, monkeypatch):
        monkeypatch.setattr(validator, "load_ohlcv", lambda s, d, fill_gaps=True: _sample_ohlcv())
        with pytest.raises(NoMarketDataError):
            validator.build_verified_market_snapshot("COF", "2020-01-01")

    def test_raises_on_empty_data(self, monkeypatch):
        monkeypatch.setattr(validator, "load_ohlcv", lambda s, d, fill_gaps=True: pd.DataFrame())
        with pytest.raises(NoMarketDataError):
            validator.build_verified_market_snapshot("COF", "2026-05-13")

    def test_look_back_window_capped_at_30(self, monkeypatch):
        monkeypatch.setattr(validator, "load_ohlcv", lambda s, d, fill_gaps=True: _sample_ohlcv())
        snap = validator.build_verified_market_snapshot("COF", "2026-05-20", look_back_days=999)
        # last-N closes table has at most 30 data rows
        close_rows = [ln for ln in snap.splitlines() if ln.startswith("| 2026-")]
        assert 0 < len(close_rows) <= 30


@pytest.mark.unit
class TestTool:
    def test_tool_delegates_to_builder(self, monkeypatch):
        from tradingagents.agents.tools import get_verified_market_snapshot
        monkeypatch.setattr(validator, "load_ohlcv", lambda s, d, fill_gaps=True: _sample_ohlcv())
        out = get_verified_market_snapshot.invoke(
            {"symbol": "COF", "curr_date": "2026-05-20"}
        )
        assert "Verified market data snapshot for COF" in out


@pytest.mark.unit
class TestWeeklySection:
    """Daily indicators hide the weekly trend; the snapshot now carries both."""

    def _long_ohlcv(self, start="2024-01-01", end="2026-05-20"):
        dates = pd.bdate_range(start, end)
        closes = [100 + i * 0.2 for i in range(len(dates))]
        return pd.DataFrame({
            "Date": dates,
            "Open": [c - 0.1 for c in closes],
            "High": [c + 0.5 for c in closes],
            "Low": [c - 0.5 for c in closes],
            "Close": closes,
            "Volume": [10_000] * len(dates),
        })

    def test_weekly_indicators_are_reported(self, monkeypatch):
        monkeypatch.setattr(validator, "load_ohlcv",
                            lambda s, d, fill_gaps=True: self._long_ohlcv())
        snap = validator.build_verified_market_snapshot("COF", "2026-05-13")
        assert "### Weekly trend (calendar weeks resampled from the daily bars)" in snap
        assert "Weekly bars available:" in snap
        assert "| rsi |" in snap
        assert "52-week range of weekly closes" in snap

    def test_insufficient_history_says_so_instead_of_nan(self, monkeypatch):
        monkeypatch.setattr(validator, "load_ohlcv",
                            lambda s, d, fill_gaps=True: _sample_ohlcv())  # ~7 weeks
        snap = validator.build_verified_market_snapshot("COF", "2026-05-13")
        weekly = snap.split("### Weekly trend")[1]
        assert "Insufficient weekly history" in weekly
        assert "Do not infer a weekly trend" in weekly
        assert "| rsi |" not in weekly          # no weekly indicators at all

    def test_weekly_label_is_the_last_session_not_the_nominal_friday(self, monkeypatch):
        # 2026-05-13 is a Wednesday; a W-FRI label would claim a future bar.
        monkeypatch.setattr(validator, "load_ohlcv",
                            lambda s, d, fill_gaps=True: self._long_ohlcv())
        snap = validator.build_verified_market_snapshot("COF", "2026-05-13")
        latest = [line for line in snap.splitlines() if line.startswith("- Latest weekly bar:")]
        assert latest and "2026-05-13" in latest[0]

    def test_weekly_bars_aggregate_the_daily_rows(self):
        frame = pd.DataFrame({
            "Date": pd.to_datetime(["2026-05-04", "2026-05-05", "2026-05-06",
                                    "2026-05-08", "2026-05-11"]),
            "Open": [10.0, 11.0, 12.0, 13.0, 20.0],
            "High": [10.5, 11.5, 12.5, 13.5, 20.5],
            "Low": [9.5, 10.5, 11.5, 12.5, 19.5],
            "Close": [10.2, 11.2, 12.2, 13.2, 20.2],
            "Volume": [100, 200, 300, 400, 500],
        })
        weekly = validator._weekly_bars(frame)
        first = weekly.iloc[0]
        assert first["Date"] == pd.Timestamp("2026-05-08")   # last session of that week
        assert first["Open"] == 10.0 and first["Close"] == 13.2
        assert first["High"] == 13.5 and first["Low"] == 9.5
        assert first["Volume"] == 1000                        # summed within the week
        assert weekly.iloc[1]["Date"] == pd.Timestamp("2026-05-11")
