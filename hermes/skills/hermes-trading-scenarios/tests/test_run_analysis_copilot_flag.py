"""Copilot is a fork-only, ToS-gray endpoint: never a silent default.

The flag gates both the auto chain and an explicit --llm=copilot, and the consent
is per run (--allow-copilot) or per environment (TRADINGAGENTS_ALLOW_COPILOT=1).
"""
import importlib.util
import os
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import mock_open, patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_analysis.py"
SPEC = importlib.util.spec_from_file_location("run_analysis_copilot_under_test", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("could not load run_analysis.py for tests")
run_analysis = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(run_analysis)

CLEAN_ENV = {k: v for k, v in os.environ.items()
             if k not in (run_analysis.ALLOW_COPILOT_ENV, "COPILOT_GITHUB_TOKEN")}


class CopilotGateTests(unittest.TestCase):
    def test_disabled_by_default_even_with_a_token(self):
        with patch.dict(os.environ, CLEAN_ENV, clear=True), \
                patch.object(run_analysis, "_copilot_token", return_value="gho_token"):
            ok, reason = run_analysis._profile_precheck("copilot")
        self.assertFalse(ok)
        self.assertIn("feature flag", reason)
        self.assertIn(run_analysis.ALLOW_COPILOT_ENV, reason)

    def test_enabled_by_environment(self):
        with patch.dict(os.environ, {**CLEAN_ENV, run_analysis.ALLOW_COPILOT_ENV: "1"}, clear=True), \
                patch.object(run_analysis, "_copilot_token", return_value="gho_token"):
            ok, reason = run_analysis._profile_precheck("copilot")
        self.assertTrue(ok, reason)

    def test_enabled_token_still_required(self):
        with patch.dict(os.environ, {**CLEAN_ENV, run_analysis.ALLOW_COPILOT_ENV: "yes"}, clear=True), \
                patch.object(run_analysis, "_copilot_token", return_value=""):
            ok, reason = run_analysis._profile_precheck("copilot")
        self.assertFalse(ok)
        self.assertIn("COPILOT_GITHUB_TOKEN", reason)

    def test_auto_chain_skips_copilot_when_not_enabled(self):
        with patch.dict(os.environ, CLEAN_ENV, clear=True), \
                patch.object(run_analysis, "_copilot_token", return_value="gho_token"), \
                patch.object(run_analysis, "_endpoint_reachable", return_value=True):
            chain = [c for c in run_analysis.LLM_CHAIN if run_analysis._profile_precheck(c)[0]]
        self.assertNotIn("copilot", chain)
        self.assertIn("webercloud", chain)


class CopilotFlagTests(unittest.TestCase):
    def test_run_parser_accepts_allow_copilot(self):
        args = run_analysis.build_parser().parse_args(["run", "RHM.DE", "--allow-copilot"])
        self.assertTrue(args.allow_copilot)

    def test_doctor_parser_accepts_allow_copilot(self):
        args = run_analysis.build_parser().parse_args(["doctor", "RHM.DE", "--allow-copilot"])
        self.assertTrue(args.allow_copilot)

    def test_flag_turns_into_the_consent_env_and_reaches_the_run(self):
        captured = {}

        def fake_execute(spec):
            captured.update(vars(spec))

        argv = run_analysis.build_parser().parse_args(
            ["run", "RHM.DE", "2026-10-21", "--llm=copilot", "--allow-copilot", "--dry-run"])
        with patch.dict(os.environ, CLEAN_ENV, clear=True), \
                patch.object(run_analysis, "load_ticker_meta", return_value=(["Rheinmetall"], "")), \
                patch.object(run_analysis, "safe_dir", return_value=Path("/tmp/ta-test")), \
                patch.object(run_analysis, "execute_run", side_effect=fake_execute):
            run_analysis.cmd_run(argv)
            self.assertEqual(os.environ.get(run_analysis.ALLOW_COPILOT_ENV), "1")
        self.assertEqual(captured["llm"], "copilot")

    def test_without_the_flag_the_env_stays_unset(self):
        argv = run_analysis.build_parser().parse_args(["run", "RHM.DE", "--llm=copilot", "--dry-run"])
        with patch.dict(os.environ, CLEAN_ENV, clear=True), \
                patch.object(run_analysis, "load_ticker_meta", return_value=(["Rheinmetall"], "")), \
                patch.object(run_analysis, "safe_dir", return_value=Path("/tmp/ta-test")), \
                patch.object(run_analysis, "execute_run", side_effect=lambda spec: None):
            run_analysis.cmd_run(argv)
            self.assertNotIn(run_analysis.ALLOW_COPILOT_ENV, os.environ)

    def test_bg_child_forwards_the_flag(self):
        argv = run_analysis.build_parser().parse_args(
            ["run", "RHM.DE", "2026-10-21", "--bg", "--allow-copilot", "--dry-run"])
        started = {}

        class _Proc:
            pid = 4242

        def fake_popen(cmd, **kwargs):
            started["cmd"] = cmd
            return _Proc()

        with patch.dict(os.environ, CLEAN_ENV, clear=True), \
                patch.object(run_analysis, "load_ticker_meta", return_value=(["Rheinmetall"], "")), \
                patch.object(run_analysis, "safe_dir", return_value=Path("/tmp/ta-test")), \
                patch.object(run_analysis.subprocess, "Popen", side_effect=fake_popen), \
                patch("builtins.open", mock_open()), \
                patch("builtins.print"):
            run_analysis.cmd_run(argv)
        self.assertIn("--allow-copilot", started["cmd"])


if __name__ == "__main__":
    unittest.main()
