"""The deterministic sizing policy is flat config, so TRADINGAGENTS_* can set it.

A nested block would not be coerced by the environment overlay (it casts to the
type of the existing default), so every policy knob is a top-level scalar.
"""

import pytest

from tradingagents.default_config import build_default_config


@pytest.mark.unit
def test_defaults_are_the_house_policy():
    cfg = build_default_config()
    assert cfg["risk_per_trade_pct"] == 1.0
    assert cfg["max_position_pct"] == 15.0
    assert cfg["min_reward_risk"] == 1.5
    assert cfg["stop_atr_multiple"] == 2.0
    assert cfg["min_stop_pct"] == 0.2
    assert cfg["max_adv_participation_pct"] == 1.0


@pytest.mark.unit
def test_env_overrides_are_floats(monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_RISK_PER_TRADE_PCT", "0.75")
    monkeypatch.setenv("TRADINGAGENTS_MAX_POSITION_PCT", "10")
    cfg = build_default_config()
    assert cfg["risk_per_trade_pct"] == 0.75
    assert cfg["max_position_pct"] == 10.0


@pytest.mark.unit
def test_a_bad_number_fails_loudly(monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_MIN_REWARD_RISK", "hart")
    with pytest.raises(ValueError):
        build_default_config()
