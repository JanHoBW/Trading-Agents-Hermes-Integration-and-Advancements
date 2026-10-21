"""Calibration confidence label.

A hit-rate over a handful of settled calls must not read like a track record.
The level is decided on pooled evidence and never raised by a small per-ticker
sample; these tests pin the three levels, the chance band, and the render.
"""
from __future__ import annotations

import pytest

from tradingagents.memory import calibration


def _entry(ticker, rating, alpha, date="2026-01-15"):
    return {"ticker": ticker, "date": date, "rating": rating,
            "horizons": {5: {"alpha": alpha}}}


@pytest.mark.unit
def test_small_sample_is_low_confidence():
    entries = [_entry("RHM.DE", "buy", 0.05), _entry("RHM.DE", "buy", 0.03)]
    calib = calibration.build_calibration(entries, "RHM.DE", 5)
    assert calib["confidence"]["level"] == "low"
    assert "not evidence" in calib["confidence"]["reason"]

    line = calibration.render_calibration(calib, "RHM.DE")
    assert "memory confidence: low" in line
    assert "anecdote" in line


@pytest.mark.unit
def test_medium_sample_is_medium_confidence():
    entries = [_entry("AAA", "buy", 0.05)] * 9
    calib = calibration.build_calibration(entries, "AAA", 5)
    assert calib["confidence"]["level"] == "medium"
    assert "indicative, not conclusive" in calib["confidence"]["reason"]


@pytest.mark.unit
def test_large_decisive_sample_is_high_confidence():
    entries = ([_entry("AAA", "buy", 0.05)] * 18) + ([_entry("AAA", "buy", -0.04)] * 7)
    calib = calibration.build_calibration(entries, "AAA", 5)
    assert calib["confidence"]["level"] == "high"
    assert "72%" in calib["confidence"]["reason"]


@pytest.mark.unit
def test_large_but_coin_flip_record_is_only_medium():
    entries = ([_entry("AAA", "buy", 0.05)] * 12) + ([_entry("AAA", "buy", -0.05)] * 12)
    calib = calibration.build_calibration(entries, "AAA", 5)
    assert calib["confidence"]["level"] == "medium"
    assert "indistinguishable from chance" in calib["confidence"]["reason"]


@pytest.mark.unit
def test_single_instrument_outlier_caps_confidence_at_medium():
    # 24 pooled, 12 of them one instrument whose own rate is 100 % vs. 50 % pooled.
    entries = ([_entry("AAA", "buy", 0.05)] * 12
               + [_entry("BBB", "buy", 0.03)] * 1
               + [_entry("BBB", "buy", -0.03)] * 9)
    calib = calibration.build_calibration(entries, "AAA", 5)
    # The pooled record is decisive; the per-instrument label is the cautious one.
    assert calib["confidence"]["level"] == "medium"
    assert calib["ticker"]["confidence"]["level"] == "medium"
    assert "disagrees with the pooled" in calib["ticker"]["confidence"]["reason"]


@pytest.mark.unit
def test_no_outcomes_renders_nothing():
    assert calibration.render_calibration(
        calibration.build_calibration([], "AAA", 5), "AAA") == ""


@pytest.mark.unit
def test_realized_trades_count_toward_the_evidence():
    entries = [{**_entry("AAA", "buy", None), "horizons": {},
                "realized": {"return": 0.04, "n": 1}}]
    entries += [{"ticker": "AAA", "date": "2026-02-01", "rating": "buy", "horizons": {},
                 "realized": {"return": 0.03}} for _ in range(8)]
    calib = calibration.build_calibration(entries, "AAA", 5)
    assert calib["confidence"]["n"] == 9
    assert calib["confidence"]["level"] == "medium"
