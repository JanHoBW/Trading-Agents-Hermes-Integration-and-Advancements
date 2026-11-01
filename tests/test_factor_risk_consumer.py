"""Factor evidence reaches the existing market consumer, once per run."""
import copy

import numpy as np
import pandas as pd
import pytest

from tradingagents.dataflows.config import run_config
from tradingagents.dataflows.run_snapshot import market_snapshot_scope, snapshot_artifact
from tradingagents.dataflows.vendors.yahoo import ohlcv, snapshot

pytestmark = pytest.mark.unit


def _frames():
    dates = pd.bdate_range('2026-01-01', periods=160)
    x = .003 * np.sin(np.arange(160) * .29) + .002 * np.cos(np.arange(160) * .11)
    y = 1.2 * x + .0002 * np.cos(np.arange(160) * .53)
    asset = pd.DataFrame({'Date': dates, 'Close': 100*np.cumprod(1+y), 'Volume': 1000.})
    asset['Open'] = asset['Close']
    asset['High'] = asset['Close']+1
    asset['Low'] = asset['Close']-1
    factor = pd.DataFrame({'Adj Close': 100*np.cumprod(1+x)}, index=dates)
    return asset, factor


def test_factor_fit_is_frozen_in_price_artifact_and_market_prompt(monkeypatch):
    asset, factor = _frames()
    monkeypatch.setattr(ohlcv, 'load_ohlcv', lambda *a, **k: asset.copy())
    calls = []
    def batch(**kwargs):
        calls.append(kwargs)
        return {'SYNTHBENCH': factor.copy()}
    monkeypatch.setattr('yfinance.download', batch)
    config = {'factor_risk_enabled': True, 'benchmark_ticker': 'SYNTHBENCH'}
    with run_config(config), market_snapshot_scope('SYNTH', '2026-08-14') as scope:
        scope.rows("SYNTH", "2026-08-14")
        artifact = snapshot_artifact()
        assert 'factor_risk' in artifact
        fit = artifact['factor_risk']['factors']['benchmark']
        assert fit['status'] == 'available'
        assert fit['symbol'] == 'SYNTHBENCH'
        assert fit['beta'] == pytest.approx(1.2, abs=.02)
        rendered = snapshot.build_verified_market_snapshot('SYNTH', '2026-08-14')
        assert 'Observational factor risk' in rendered
        assert 'not a causal forecast' in rendered
        assert 'beta=' in rendered
        assert snapshot_artifact()['factor_risk'] == artifact['factor_risk']
    assert len(calls) == 1
    assert calls[0]['tickers'] == ['SYNTHBENCH']
    assert calls[0]['end'] == '2026-08-15'
    # A resumed run consumes the stored estimate, never a newly fitted market.
    with run_config(config), market_snapshot_scope('SYNTH', '2026-08-14', artifact) as scope:
        assert snapshot_artifact()['factor_risk'] == artifact['factor_risk']
        snapshot.build_verified_market_snapshot('SYNTH', '2026-08-14')
    assert len(calls) == 1


def test_factor_policy_has_explicit_default_and_environment_opt_out(monkeypatch):
    from tradingagents.default_config import build_default_config
    monkeypatch.delenv('TRADINGAGENTS_FACTOR_RISK_ENABLED', raising=False)
    assert build_default_config()['factor_risk_enabled'] is True
    monkeypatch.setenv('TRADINGAGENTS_FACTOR_RISK_ENABLED', 'false')
    assert build_default_config()['factor_risk_enabled'] is False


def test_factor_artifact_tampering_is_rejected_without_refetch(monkeypatch):
    asset, factor = _frames()
    monkeypatch.setattr(ohlcv, 'load_ohlcv', lambda *a, **k: asset.copy())
    monkeypatch.setattr('yfinance.download', lambda **k: {'SYNTHBENCH': factor.copy()})
    with run_config({'factor_risk_enabled': True, 'benchmark_ticker': 'SYNTHBENCH'}), market_snapshot_scope('SYNTH', '2026-08-14') as scope:
        scope.rows('SYNTH', '2026-08-14')
        artifact = copy.deepcopy(snapshot_artifact())
    artifact['factor_risk']['factors']['benchmark']['beta'] = 999
    with pytest.raises(ValueError, match='factor.*digest'), market_snapshot_scope('SYNTH', '2026-08-14', artifact):
        pass


def test_optional_fitter_failure_keeps_frozen_price_input_available(monkeypatch):
    asset, factor = _frames()
    monkeypatch.setattr(ohlcv, 'load_ohlcv', lambda *a, **k: asset.copy())
    monkeypatch.setattr('yfinance.download', lambda **k: {'SYNTHBENCH': factor.copy()})
    def broken(*args, **kwargs):
        raise RuntimeError('synthetic fitter failure')
    monkeypatch.setattr('tradingagents.dataflows.factor_risk.fit_factor_risk', broken)
    with run_config({'factor_risk_enabled': True}), market_snapshot_scope('SYNTH', '2026-08-14') as scope:
        rows = scope.rows('SYNTH', '2026-08-14')
        assert len(rows) == len(asset)
        assert 'RuntimeError' in snapshot_artifact()['metadata']['factor_risk_error']
        assert 'Factor risk unavailable' in snapshot.build_verified_market_snapshot('SYNTH', '2026-08-14')


def test_factor_loader_and_fitter_use_the_price_capture_timestamp(monkeypatch):
    from tradingagents.dataflows import factor_risk
    asset, factor = _frames()
    monkeypatch.setattr(ohlcv, 'load_ohlcv', lambda *a, **k: asset.copy())
    monkeypatch.setattr('yfinance.download', lambda **k: {'SYNTHBENCH': factor.copy()})
    loader, fitter = factor_risk.load_factor_inputs, factor_risk.fit_factor_risk
    seen = []
    def load(*args, **kwargs):
        seen.append(kwargs.get('current_time'))
        return loader(*args, **kwargs)
    def fit(*args, **kwargs):
        seen.append(kwargs.get('current_time'))
        return fitter(*args, **kwargs)
    monkeypatch.setattr(factor_risk, 'load_factor_inputs', load)
    monkeypatch.setattr(factor_risk, 'fit_factor_risk', fit)
    with run_config({'factor_risk_enabled': True, 'benchmark_ticker': 'SYNTHBENCH'}), market_snapshot_scope('SYNTH', '2026-08-14') as scope:
        scope.rows('SYNTH', '2026-08-14')
        artifact = snapshot_artifact()
    assert seen == [artifact['metadata']['observed_at']] * 2
    assert artifact['factor_risk']['observed_at'] == artifact['metadata']['observed_at']
