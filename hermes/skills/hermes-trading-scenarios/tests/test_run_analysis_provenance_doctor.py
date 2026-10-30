"""Audit-trail and pre-flight coverage for the wrapper.

Two holes this fixes, both verified here end to end:
* a ``--ui`` run used to leave no ``provenance.json`` at all (console output went
  to a TTY), so the default interactive path had no machine-readable record;
* a long unattended run had no pre-flight, so a missing benchmark or an
  unwritable memory log was discovered hours in.
"""
import importlib.util
import io
import json
import os
import shutil
import tempfile
import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_analysis.py"
SPEC = importlib.util.spec_from_file_location("run_analysis_audit_under_test", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("could not load run_analysis.py for tests")
run_analysis = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(run_analysis)


def _run_args(out: Path) -> Namespace:
    """The Namespace execute_run expects, minimal but complete."""
    return Namespace(
        ticker="RHM.DE", date="2026-10-21", analysts="market,news,fundamentals,social",
        output_dir=str(out), timeout=600, news_supplement=None, portfolio=None,
        dry_run=False, ui=True, debate_rounds=3, risk_rounds=2, llm="lmstudio",
        lmstudio_url=None, lmstudio_model=None, lmstudio_reasoning_effort=None,
        weber_url=None, weber_model=None, custom_api_url=None, custom_model=None,
        custom_reasoning_effort=None, custom_api_key_env=None,
    )


def _last_json(text: str) -> dict:
    """The last complete JSON object in ``text`` (the wrapper prints a banner first)."""
    decoder = json.JSONDecoder()
    for i in range(len(text) - 1, -1, -1):
        if text[i] != "{":
            continue
        try:
            obj, end = decoder.raw_decode(text[i:])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and not text[i + end:].strip():
            return obj
    raise AssertionError(f"no JSON object in wrapper output: {text!r}")


class CoverageMatrixReadTests(unittest.TestCase):
    def test_newest_matrix_wins_and_invalid_files_are_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            old = out / "reports" / "old" / "data_coverage.json"
            new = out / "reports" / "new" / "data_coverage.json"
            for path in (old, new):
                path.parent.mkdir(parents=True)
                path.write_text(json.dumps({"methods": {"get_news": {"status": "ok"}}}),
                                encoding="utf-8")
            broken = out / "broken.json"
            broken.write_text("{not json", encoding="utf-8")
            os.utime(old, (1_000_000, 1_000_000))
            os.utime(new, (2_000_000, 2_000_000))

            matrix = run_analysis.read_coverage_matrix(out)
            self.assertEqual(matrix["source_path"], str(new))

    def test_absent_matrix_is_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(run_analysis.read_coverage_matrix(Path(tmp)))


class UiProvenanceTests(unittest.TestCase):
    """A --ui run must write provenance.json, including the child's exit code."""

    def _execute(self, out: Path, exit_code: int):
        repo = out / "_repo"
        (repo / "cli").mkdir(parents=True)
        (repo / "cli" / "main.py").write_text("# stub", encoding="utf-8")

        def fake_terminal(cmd, env=None, **kwargs):
            # Stand in for the windowed shell: write the code the run "returned"
            # and the artifacts a finished run leaves behind.
            (out / ".run_exit_code").write_text(str(exit_code), encoding="utf-8")
            reports = out / "reports"
            reports.mkdir(parents=True, exist_ok=True)
            (reports / "complete_report.md").write_text("# report", encoding="utf-8")
            (reports / "data_coverage.json").write_text(json.dumps({
                "methods": {"get_news": {"status": "degraded", "served_by": ["yfinance"],
                                         "skips": [{"vendor": "issuer_rss",
                                                    "state": "not_configured", "reason": ""}]}},
                "gaps": [{"method": "get_news", "vendor": "issuer_rss",
                          "state": "not_configured", "reason": ""}],
                "totals": {"calls": 1, "methods": 1, "degraded": 1, "gaps": 1},
            }), encoding="utf-8")
            return None

        buf = io.StringIO()
        with patch.object(run_analysis, "find_install_root", return_value=repo), \
                patch.object(run_analysis, "_profile_precheck", return_value=(True, "")), \
                patch.object(run_analysis, "_resolve_profile", return_value={
                    "TRADINGAGENTS_QUICK_THINK_LLM": "test-model",
                    "TRADINGAGENTS_LLM_BACKEND_URL": "http://127.0.0.1:1234/v1"}), \
                patch.object(run_analysis, "build_child_env", return_value={"PATH": "/usr/bin"}), \
                patch.object(run_analysis.shutil, "which", return_value="/usr/bin/gnome-terminal"), \
                patch.dict(os.environ, {"DISPLAY": ":0"}, clear=False), \
                patch.object(run_analysis.subprocess, "run", side_effect=fake_terminal), \
                redirect_stdout(buf):
            run_analysis.execute_run(_run_args(out))
        return _last_json(buf.getvalue())

    def test_ui_run_writes_provenance_with_exit_code_and_coverage(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            result = self._execute(out, exit_code=0)
            prov_path = out / "provenance.json"
            self.assertTrue(prov_path.exists())
            prov = json.loads(prov_path.read_text(encoding="utf-8"))
            self.assertTrue(prov["ui"])
            self.assertEqual(prov["returncode"], 0)
            self.assertFalse(prov["partial"])
            self.assertEqual(prov["ticker"], "RHM.DE")
            self.assertEqual(prov["data_coverage"]["methods"]["get_news"]["status"], "degraded")
            self.assertTrue(prov["report_files"])
            self.assertEqual(result["provenance"], str(prov_path))
            # The transient hand-off file must not survive into the run dir.
            self.assertFalse((out / ".run_exit_code").exists())

    def test_ui_run_with_failing_child_is_marked_partial(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            self._execute(out, exit_code=1)
            prov = json.loads((out / "provenance.json").read_text(encoding="utf-8"))
            self.assertTrue(prov["partial"])
            self.assertEqual(prov["returncode"], 1)


class DoctorTests(unittest.TestCase):
    def _doctor(self, ticker, health=None):
        repo = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, repo, ignore_errors=True)
        (repo / "cli").mkdir()
        (repo / "cli" / "main.py").write_text("# stub", encoding="utf-8")
        (repo / ".venv" / "bin").mkdir(parents=True)
        (repo / ".venv" / "bin" / "python").write_text("", encoding="utf-8")
        (repo / "registry.json").write_text("{}", encoding="utf-8")
        (repo / "memory").mkdir()
        args = Namespace(ticker=ticker, date="2026-10-21", llm="lmstudio",
                         portfolio=None, supplement=None)
        buf = io.StringIO()
        with patch.object(run_analysis, "find_install_root", return_value=repo), \
                patch.object(run_analysis, "_repo_health", return_value=health or {
                    "benchmark": "^GDAXI", "horizons": [5, 21, 63, 126],
                    # A configured issuer keeps this a positive-path test; the
                    # missing-adapter case has its own degraded check.
                    "ir_facts": {"configured": True, "reason": "ok"},
                    "memory_log_path": str(repo / "memory" / "log.md"),
                    "issuer_registry_path": str(repo / "registry.json"),
                    "memory_entries": 3, "memory_pending": 1, "memory_writable": True}), \
                patch.object(run_analysis, "_profile_precheck", return_value=(True, "")), \
                redirect_stdout(buf):
            with self.assertRaises(SystemExit) as ctx:
                run_analysis.cmd_doctor(args)
        return ctx.exception.code, json.loads(buf.getvalue())

    def test_doctor_ok_exits_zero(self):
        code, payload = self._doctor("RHM.DE")
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(code, 0)
        names = {c["check"] for c in payload["checks"]}
        self.assertIn("benchmark", names)
        self.assertIn("memory_log", names)

    def test_doctor_blocks_on_unknown_ticker(self):
        code, payload = self._doctor("NOPE.XYZ")
        self.assertEqual(payload["status"], "blocked")
        self.assertEqual(code, run_analysis.DOCTOR_EXIT["blocked"])
        self.assertEqual(payload["exit_code"], 4)

    def test_doctor_degrades_on_unwritable_memory_dir(self):
        code, payload = self._doctor("RHM.DE", health={
            "benchmark": "^GDAXI", "horizons": [5],
            "memory_log_path": "/root/nope/log.md", "issuer_registry_path": "/nonexistent/x.json",
            "memory_entries": 0, "memory_pending": 0, "memory_writable": False})
        self.assertEqual(payload["status"], "blocked")   # memory not writable is fatal
        self.assertEqual(code, 4)
        details = {c["check"]: c for c in payload["checks"]}
        self.assertEqual(details["evaluation_horizons"]["status"], "degraded")
        self.assertEqual(details["issuer_registry"]["status"], "degraded")


if __name__ == "__main__":
    unittest.main()
