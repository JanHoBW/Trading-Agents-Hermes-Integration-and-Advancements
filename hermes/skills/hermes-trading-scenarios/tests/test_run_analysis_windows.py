"""Wrapper side of the two analysis windows.

The repo owns the semantics (``dataflows/windows.py``); the wrapper only has to turn
flags into the env vars the repo reads, validate them, and record them in the
provenance so a run's horizons are auditable after the fact.
"""
import importlib.util
import os
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_analysis.py"
SPEC = importlib.util.spec_from_file_location("run_analysis_windows_under_test", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("could not load run_analysis.py for tests")
run_analysis = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(run_analysis)

CLEAN_ENV = {k: v for k, v in os.environ.items()
             if k not in (run_analysis.NEWS_WINDOW_ENV, run_analysis.EVENT_WINDOW_ENV,
                          run_analysis.EVENT_CONTEXT_ENV)}


class WindowFlagTests(unittest.TestCase):
    def test_flags_become_env_vars_for_the_child(self):
        args = Namespace(news_window_days=30, event_window_days=90, no_event_context=False)
        with patch.dict(os.environ, CLEAN_ENV, clear=True):
            run_analysis.apply_window_args(args)
            self.assertEqual(os.environ[run_analysis.NEWS_WINDOW_ENV], "30")
            self.assertEqual(os.environ[run_analysis.EVENT_WINDOW_ENV], "90")
            self.assertNotIn(run_analysis.EVENT_CONTEXT_ENV, os.environ)

    def test_no_event_context_disables_it_explicitly(self):
        args = Namespace(news_window_days=None, event_window_days=None, no_event_context=True)
        with patch.dict(os.environ, CLEAN_ENV, clear=True):
            run_analysis.apply_window_args(args)
            self.assertEqual(os.environ[run_analysis.EVENT_CONTEXT_ENV], "0")

    def test_no_flags_leaves_the_environment_untouched(self):
        args = Namespace(news_window_days=None, event_window_days=None, no_event_context=False)
        with patch.dict(os.environ, CLEAN_ENV, clear=True):
            run_analysis.apply_window_args(args)
            self.assertNotIn(run_analysis.NEWS_WINDOW_ENV, os.environ)
            self.assertNotIn(run_analysis.EVENT_WINDOW_ENV, os.environ)

    def test_defaults_are_seven_and_180_days(self):
        with patch.dict(os.environ, CLEAN_ENV, clear=True):
            summary = run_analysis.analysis_windows_summary("2026-10-21")
        self.assertEqual(summary["news_lookback_days"], 7)
        self.assertEqual(summary["headline_window"], ["2026-10-14", "2026-10-21"])
        self.assertTrue(summary["event_context_enabled"])
        self.assertEqual(summary["event_lookback_days"], 180)
        self.assertEqual(summary["event_window"], ["2026-04-24", "2026-10-21"])

    def test_summary_reflects_the_flags(self):
        with patch.dict(os.environ, CLEAN_ENV, clear=True):
            run_analysis.apply_window_args(
                Namespace(news_window_days=14, event_window_days=90, no_event_context=True))
            summary = run_analysis.analysis_windows_summary("2026-10-21")
        self.assertEqual(summary["headline_window"], ["2026-10-07", "2026-10-21"])
        self.assertEqual(summary["event_window"], ["2026-07-23", "2026-10-21"])
        self.assertFalse(summary["event_context_enabled"])

    def test_bad_env_value_falls_back_instead_of_crashing(self):
        env = dict(CLEAN_ENV, **{run_analysis.NEWS_WINDOW_ENV: "sieben"})
        with patch.dict(os.environ, env, clear=True):
            summary = run_analysis.analysis_windows_summary("2026-10-21")
        self.assertEqual(summary["news_lookback_days"], 7)

    def test_window_validation_rejects_out_of_range_and_garbage(self):
        for raw in ("0", "-5", "731", "abc"):
            with self.subTest(raw=raw), self.assertRaises(Exception):
                run_analysis.positive_days(raw)
        self.assertEqual(run_analysis.positive_days("180"), 180)

    def test_value_flags_are_registered_for_equals_form_enforcement(self):
        # main() rejects the space form only for flags in VALUE_FLAGS; a value
        # flag missing there would silently accept "… --news-window-days 30".
        self.assertIn("--news-window-days", run_analysis.VALUE_FLAGS)
        self.assertIn("--event-window-days", run_analysis.VALUE_FLAGS)

    def test_cli_exposes_the_flags_on_run_and_doctor(self):
        parser = run_analysis.build_parser()
        run = parser.parse_args(["run", "RHM.DE", "--news-window-days", "30",
                                 "--event-window-days", "90", "--no-event-context"])
        self.assertEqual((run.news_window_days, run.event_window_days,
                          run.no_event_context), (30, 90, True))
        doctor = parser.parse_args(["doctor", "RHM.DE", "--event-window-days", "180"])
        self.assertEqual(doctor.event_window_days, 180)


if __name__ == "__main__":
    unittest.main()


class TestQualityGateProvenance(unittest.TestCase):
    def test_reads_the_verdict_from_the_report_tree(self):
        import json
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            (out / "reports" / "6_coverage").mkdir(parents=True)
            (out / "reports" / "quality_gate.json").write_text(json.dumps({
                "status": "NEEDS_REVIEW",
                "checks": [{"name": "decision_rating", "passed": False, "blocking": True, "detail": "x"}],
                "gaps": [{"method": "get_news", "status": "degraded"}],
            }), encoding="utf-8")
            gate = run_analysis.read_quality_gate(out)
        self.assertEqual(gate["status"], "NEEDS_REVIEW")
        self.assertEqual(gate["failed_checks"], ["decision_rating"])
        self.assertEqual(gate["gap_count"], 1)

    def test_missing_gate_is_none(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(run_analysis.read_quality_gate(Path(tmp)))
