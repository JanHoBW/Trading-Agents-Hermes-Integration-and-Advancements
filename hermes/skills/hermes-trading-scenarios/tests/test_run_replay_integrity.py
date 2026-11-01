"""Replay IDs identify effective inputs, not just prompt shape."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_analysis.py"
spec = importlib.util.spec_from_file_location("run_replay_integrity_under_test", SCRIPT)
assert spec and spec.loader
run = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run)


def test_replay_changes_with_selected_model():
    a = SimpleNamespace(ticker="SYNTH", date="2026-01-01", analysts="market", debate_rounds=1,
                        risk_rounds=1, custom_model="small-model")
    first = run.replay_id(a, "custom", {})
    a.custom_model = "other-model"
    assert first != run.replay_id(a, "custom", {})


def test_replay_changes_when_same_evidence_file_changes(tmp_path):
    evidence = tmp_path / "evidence.json"
    evidence.write_text('{"facts": "first"}')
    a = SimpleNamespace(ticker="SYNTH", date="2026-01-01", analysts="market", debate_rounds=1,
                        risk_rounds=1, news_supplement=str(evidence), portfolio=None)
    first = run.replay_id(a, "custom", {})
    evidence.write_text('{"facts": "second"}')
    assert first != run.replay_id(a, "custom", {})


def test_replay_tracks_effective_child_model_and_window():
    a = SimpleNamespace(ticker="SYNTH", date="2026-01-01", analysts="market", debate_rounds=1, risk_rounds=1)
    env = {"TRADINGAGENTS_QUICK_THINK_LLM": "first-model", run.NEWS_WINDOW_ENV: "7"}
    first = run.replay_id(a, "copilot", {}, env=env)
    env["TRADINGAGENTS_QUICK_THINK_LLM"] = "second-model"
    second = run.replay_id(a, "copilot", {}, env=env)
    assert first != second
    env[run.NEWS_WINDOW_ENV] = "30"
    assert second != run.replay_id(a, "copilot", {}, env=env)


def test_replay_tracks_actual_output_language_tier_endpoints_and_factor_policy():
    a = SimpleNamespace(ticker='SYNTH', date='2026-01-01', analysts='market', debate_rounds=1, risk_rounds=1)
    for key in ('TRADINGAGENTS_OUTPUT_LANGUAGE', 'TRADINGAGENTS_QUICK_THINK_BACKEND_URL',
                'TRADINGAGENTS_DEEP_THINK_BACKEND_URL', 'TRADINGAGENTS_FACTOR_RISK_ENABLED',
                'TRADINGAGENTS_BENCHMARK_TICKER'):
        assert run.replay_id(a, 'copilot', {}, env={key: 'first'}) != run.replay_id(a, 'copilot', {}, env={key: 'second'}), key


def test_replay_tracks_registry_configuration_bytes_not_only_its_path(tmp_path):
    registry = tmp_path / 'registry.json'
    registry.write_text('{"instruments": {"SYNTH": {"ir_facts": {"enabled": false}}}}')
    a = SimpleNamespace(ticker='SYNTH', date='2026-01-01', analysts='market', debate_rounds=1, risk_rounds=1)
    env = {'TRADINGAGENTS_ISSUER_REGISTRY': str(registry)}
    first = run.replay_id(a, 'copilot', {}, env=env)
    registry.write_text('{"instruments": {"SYNTH": {"ir_facts": {"enabled": true}}}}')
    assert first != run.replay_id(a, 'copilot', {}, env=env)
