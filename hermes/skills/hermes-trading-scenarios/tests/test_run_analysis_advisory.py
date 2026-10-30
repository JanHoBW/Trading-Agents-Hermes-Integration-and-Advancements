"""The wrapper surfaces the run's deterministic sizing verdict.

Without it the orchestration layer (Hermes, cron) would have to know the run
directory layout and open ``sizing.json`` itself; the wrapper already reads
``quality_gate.json`` the same way.
"""
import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_analysis.py"
SPEC = importlib.util.spec_from_file_location("run_analysis_advisory_under_test", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("could not load run_analysis.py for tests")
run_analysis = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(run_analysis)


def test_read_advisory_returns_the_verdict_and_size(tmp_path):
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports" / "sizing.json").write_text(json.dumps({
        "schema": "tradingagents.advisory.v1",
        "verdict": "EXECUTE", "status": "pass",
        "position": {"shares": 7, "binding_constraint": "by_position"},
    }), encoding="utf-8")

    got = run_analysis.read_advisory(tmp_path)

    assert got["verdict"] == "EXECUTE"
    assert got["shares"] == 7
    assert got["binding_constraint"] == "by_position"
    assert got["path"].endswith("sizing.json")


def test_read_advisory_without_a_file_is_none(tmp_path):
    assert run_analysis.read_advisory(tmp_path) is None


def test_read_advisory_ignores_a_corrupt_file(tmp_path):
    (tmp_path / "sizing.json").write_text("{not json", encoding="utf-8")
    assert run_analysis.read_advisory(tmp_path) is None
