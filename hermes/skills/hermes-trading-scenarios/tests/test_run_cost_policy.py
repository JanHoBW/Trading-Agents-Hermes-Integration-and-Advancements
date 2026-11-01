"""Explicit local consent pins cost settings; CLI overrides remain effective."""
import importlib.util
import json
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_analysis.py"
spec = importlib.util.spec_from_file_location("run_cost_policy_under_test", SCRIPT)
assert spec and spec.loader
run = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run)


def test_local_cost_policy_reaches_actual_run_spec(tmp_path, monkeypatch):
    settings = tmp_path / "defaults.json"
    settings.write_text(json.dumps({"llm": "copilot", "allow_copilot": True,
                                   "copilot_model": "gpt-6-luna", "reasoning_effort": "medium",
                                   "debate_rounds": 1, "risk_rounds": 1}))
    monkeypatch.setenv("TRADINGAGENTS_RUN_DEFAULTS", str(settings))
    args = run.build_parser().parse_args(["run", "SYNTH", "2026-01-01", "--dry-run"])
    with patch.object(run, "safe_dir", return_value=tmp_path), \
         patch.object(run, "load_ticker_meta", return_value=([], "")), \
         patch.object(run, "execute_run") as launch:
        run.cmd_run(args)
    got = launch.call_args.args[0]
    assert got.llm == "copilot"
    assert got.copilot_model == "gpt-6-luna"
    assert got.copilot_reasoning_effort == "medium"
    assert (got.debate_rounds, got.risk_rounds) == (1, 1)
    assert run.os.environ[run.ALLOW_COPILOT_ENV] == "1"


def test_copilot_model_rejects_invalid_literal_before_launch(tmp_path, monkeypatch):
    import pytest
    monkeypatch.setenv("TRADINGAGENTS_RUN_DEFAULTS", str(tmp_path / "missing"))
    args = run.build_parser().parse_args(["run", "SYNTH", "--dry-run", "--llm=copilot",
                                          "--allow-copilot", "--copilot-model=bad model"])
    with patch.object(run, "execute_run"), patch.object(run, "safe_dir", return_value=tmp_path), \
         patch.object(run, "load_ticker_meta", return_value=([], "")), pytest.raises(SystemExit):
        run.cmd_run(args)


def test_quick_does_not_inherit_cloud_policy(tmp_path, monkeypatch):
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps({"llm": "copilot", "allow_copilot": True, "reasoning_effort": "medium"}))
    monkeypatch.setenv("TRADINGAGENTS_RUN_DEFAULTS", str(policy))
    args = run.build_parser().parse_args(["run", "SYNTH", "--quick", "--dry-run"])
    with patch.object(run, "safe_dir", return_value=tmp_path), \
         patch.object(run, "load_ticker_meta", return_value=([], "")), patch.object(run, "execute_run") as launch:
        run.cmd_run(args)
    got = launch.call_args.args[0]
    assert got.llm == "lmstudio"
    assert got.lmstudio_reasoning_effort == "low"
    assert (got.debate_rounds, got.risk_rounds) == (1, 1)


def test_cli_overrides_policy_rounds_and_effort(tmp_path, monkeypatch):
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps({"llm": "copilot", "allow_copilot": True, "reasoning_effort": "medium",
                                 "debate_rounds": 1, "copilot_model": "gpt-6-luna"}))
    monkeypatch.setenv("TRADINGAGENTS_RUN_DEFAULTS", str(policy))
    args = run.build_parser().parse_args(["run", "SYNTH", "--dry-run", "--reasoning-effort=low", "--debate-rounds=2"])
    with patch.object(run, "safe_dir", return_value=tmp_path), \
         patch.object(run, "load_ticker_meta", return_value=([], "")), patch.object(run, "execute_run") as launch:
        run.cmd_run(args)
    got = launch.call_args.args[0]
    assert got.copilot_reasoning_effort == "low"
    assert got.debate_rounds == 2
