"""One decision is not five independent observations."""
import pytest

from tradingagents.memory.calibration import build_calibration, render_calibration

pytestmark = pytest.mark.unit


def test_horizons_and_realized_do_not_inflate_decision_count():
    entry = {"ticker": "SYNTH", "date": "2026-01-01", "rating": "Buy",
             "horizons": {h: {"alpha": .1} for h in [5, 21, 63, 126]},
             "realized": {"return": .1}}
    result = build_calibration([entry], "SYNTH", 21)
    assert result["cross"]["n"] == 1
    assert result["confidence"]["n"] == 1
    assert result["cross"]["n_outcomes"] == 5
    assert result["cross"]["anchor"] == 21
    assert "1 calls" in render_calibration(result, "SYNTH")


def test_duplicate_decision_identity_is_not_new_evidence():
    e = {"ticker": "SYNTH", "date": "2026-01-01", "rating": "Buy", "horizons": {5: {"alpha": .1}}}
    result = build_calibration([e, dict(e)], "SYNTH", 5)
    assert result["cross"]["n"] == 1
    assert result["cross"]["by_horizon"][5]["n"] == 1


def test_historical_calibration_filters_future_horizons_of_resolved_entry():
    e = {"ticker": "SYNTH", "date": "2026-01-01", "resolved": "2026-01-09", "rating": "Buy",
         "horizons": {5: {"alpha": .1, "resolution": "2026-01-09"},
                      21: {"alpha": .2, "resolution": "2026-02-01"}}}
    result = build_calibration([e], "SYNTH", 5, as_of="2026-01-15")
    assert 5 in result["cross"]["by_horizon"]
    assert 21 not in result["cross"]["by_horizon"]


def test_hold_is_not_a_directional_calibration_success():
    e = {"ticker": "SYNTH", "date": "2026-01-01", "rating": "Hold", "horizons": {5: {"alpha": .001}}}
    result = build_calibration([e], "SYNTH", 5)
    assert result["cross"]["anchor_rate"] is None
    assert result["cross"]["n"] == 0
