"""Regression tests for secret-safe TradingAgents child environments, LLM profiles,
and the merged ta.sh orchestration (ticker metadata, output layout, quick profile)."""
import importlib.util
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_analysis.py"
SPEC = importlib.util.spec_from_file_location("run_analysis_under_test", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("could not load run_analysis.py for tests")
run_analysis = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(run_analysis)

COPILOT_DEFAULTS = {
    "TRADINGAGENTS_LLM_PROVIDER": "openai",
    "TRADINGAGENTS_QUICK_THINK_PROVIDER": "openai",
    "TRADINGAGENTS_DEEP_THINK_PROVIDER": "openai",
    "TRADINGAGENTS_QUICK_THINK_LLM": "gpt-6-luna",
    "TRADINGAGENTS_DEEP_THINK_LLM": "gpt-6-luna",
    "TRADINGAGENTS_LLM_BACKEND_URL": "https://api.githubcopilot.com",
    "TRADINGAGENTS_OPENAI_REASONING_EFFORT": "high",
}
LMSTUDIO_DEFAULTS = {
    "TRADINGAGENTS_LLM_PROVIDER": "openai_compatible",
    "TRADINGAGENTS_QUICK_THINK_PROVIDER": "openai_compatible",
    "TRADINGAGENTS_DEEP_THINK_PROVIDER": "openai_compatible",
    "TRADINGAGENTS_QUICK_THINK_LLM": "unsloth/qwen3.8-27b",
    "TRADINGAGENTS_DEEP_THINK_LLM": "unsloth/qwen3.8-27b",
    "TRADINGAGENTS_LLM_BACKEND_URL": "http://127.0.0.1:1234/v1",
}


def build_env(home: Path, llm_name: str, defaults: dict) -> dict:
    """build_child_env with the current full signature (rounds + supplement)."""
    return run_analysis.build_child_env(
        home / "reports", llm_name, defaults, 3, 2, None)


class ChildEnvironmentTests(unittest.TestCase):
    def test_alpha_vantage_key_is_loaded_from_profile_env_for_child_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".env").write_text(
                "COPILOT_GITHUB_TOKEN=test-copilot\n"
                "ALPHA_VANTAGE_API_KEY=test-alpha-secret\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"HERMES_HOME": str(home)}, clear=True):
                child = build_env(home, "copilot", COPILOT_DEFAULTS)

        self.assertEqual(child["ALPHA_VANTAGE_API_KEY"], "test-alpha-secret")
        self.assertNotIn("ALPHA_VANTAGE_API_KEY", os.environ)

    def test_missing_alpha_vantage_key_is_not_added_to_child_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".env").write_text("COPILOT_GITHUB_TOKEN=test-copilot\n", encoding="utf-8")
            with patch.dict(os.environ, {"HERMES_HOME": str(home)}, clear=True):
                child = build_env(home, "copilot", COPILOT_DEFAULTS)
        self.assertNotIn("ALPHA_VANTAGE_API_KEY", child)

    def test_copilot_profile_forwards_copilot_token_as_openai_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".env").write_text("COPILOT_GITHUB_TOKEN=test-copilot\n", encoding="utf-8")
            with patch.dict(os.environ, {"HERMES_HOME": str(home)}, clear=True):
                child = build_env(home, "copilot", COPILOT_DEFAULTS)
        self.assertEqual(child["OPENAI_API_KEY"], "test-copilot")
        self.assertNotIn("COPILOT_GITHUB_TOKEN", child)

    def test_lmstudio_profile_does_not_require_or_forward_copilot_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)  # empty profile: no Copilot credential at all
            with patch.dict(os.environ, {"HERMES_HOME": str(home)}, clear=True):
                child = build_env(home, "lmstudio", LMSTUDIO_DEFAULTS)
        self.assertNotIn("OPENAI_API_KEY", child)
        self.assertEqual(child["TRADINGAGENTS_LLM_BACKEND_URL"], "http://127.0.0.1:1234/v1")

    def test_checkpoint_enabled_is_a_wrapper_default_but_overridable(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".env").write_text("", encoding="utf-8")
            with patch.dict(os.environ, {"HERMES_HOME": str(home)}, clear=True):
                child = build_env(home, "lmstudio", LMSTUDIO_DEFAULTS)
        self.assertEqual(child["TRADINGAGENTS_CHECKPOINT_ENABLED"], "true")
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".env").write_text("", encoding="utf-8")
            with patch.dict(os.environ, {"HERMES_HOME": str(home),
                                         "TRADINGAGENTS_CHECKPOINT_ENABLED": "false"}, clear=True):
                child = build_env(home, "lmstudio", LMSTUDIO_DEFAULTS)
        self.assertEqual(child["TRADINGAGENTS_CHECKPOINT_ENABLED"], "false")


class LLMProfileTests(unittest.TestCase):
    def _home_with(self, env_text: str) -> Path:
        tmp = Path(tempfile.mkdtemp())
        (tmp / ".env").write_text(env_text, encoding="utf-8")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        return tmp

    def test_auto_prefers_copilot_when_token_present(self):
        home = self._home_with("COPILOT_GITHUB_TOKEN=test-copilot\n")
        # Copilot is gated behind an explicit consent flag; the chain preference
        # itself is what this test asserts.
        env = {"HERMES_HOME": str(home), run_analysis.ALLOW_COPILOT_ENV: "1"}
        with patch.dict(os.environ, env, clear=True), \
                patch.object(run_analysis, "_endpoint_reachable", return_value=True):
            name, defaults = run_analysis.resolve_llm_profile("auto")
        self.assertEqual(name, "copilot")
        self.assertEqual(defaults["TRADINGAGENTS_LLM_BACKEND_URL"], "https://api.githubcopilot.com")

    def test_auto_falls_back_to_lmstudio_when_no_token(self):
        home = self._home_with("")
        with patch.dict(os.environ, {"HERMES_HOME": str(home)}, clear=True), \
                patch.object(run_analysis, "_endpoint_reachable", return_value=True):
            name, defaults = run_analysis.resolve_llm_profile("auto")
        self.assertEqual(name, "lmstudio")
        self.assertEqual(defaults["TRADINGAGENTS_LLM_PROVIDER"], "openai_compatible")
        self.assertEqual(defaults["TRADINGAGENTS_QUICK_THINK_LLM"], "unsloth/qwen3.8-27b")

    def test_explicit_lmstudio_gates_on_endpoint_reachability(self):
        with patch.dict(os.environ, clear=True), \
                patch.object(run_analysis, "_endpoint_reachable", return_value=False) as reach:
            with self.assertRaises(SystemExit) as ctx:
                run_analysis.resolve_llm_profile("lmstudio")
        self.assertEqual(ctx.exception.code, 2)
        self.assertTrue(reach.called)

    def test_copilot_profile_fails_loudly_without_token(self):
        home = self._home_with("")
        with patch.dict(os.environ, {"HERMES_HOME": str(home)}, clear=True):
            with self.assertRaises(SystemExit) as ctx:
                run_analysis.resolve_llm_profile("copilot")
        self.assertEqual(ctx.exception.code, 2)

    def test_lmstudio_url_and_model_overrides(self):
        with patch.dict(os.environ, {
            "LMSTUDIO_BASE_URL": "http://10.0.0.5:1234/v1",
            "LMSTUDIO_MODEL": "qwen/qwen3.6-27b",
        }, clear=True), patch.object(run_analysis, "_endpoint_reachable", return_value=True):
            _, defaults = run_analysis.resolve_llm_profile("lmstudio")
        self.assertEqual(defaults["TRADINGAGENTS_LLM_BACKEND_URL"], "http://10.0.0.5:1234/v1")
        self.assertEqual(defaults["TRADINGAGENTS_QUICK_THINK_LLM"], "qwen/qwen3.6-27b")


class OrchestrationTests(unittest.TestCase):
    """The layer absorbed from ta.sh: quick profile, ticker metadata, output layout."""

    def test_quick_profile_is_swift_with_low_effort(self):
        self.assertEqual(run_analysis.QUICK_LLM_MODEL, "swift-1.5-qwen3.8-27b")
        self.assertEqual(run_analysis.QUICK_REASONING_EFFORT, "low")

    def test_load_ticker_meta_reads_yaml_names_and_isin(self):
        names, isin = run_analysis.load_ticker_meta("RHM.DE")
        self.assertTrue(names)
        self.assertIsInstance(isin, str)

    def test_load_ticker_meta_fails_for_unknown_ticker(self):
        with self.assertRaises(SystemExit) as ctx:
            run_analysis.load_ticker_meta("NOPE.XYZ")
        self.assertEqual(ctx.exception.code, 2)

    def test_default_output_dir_layout_with_suffix(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(Path, "home", return_value=Path(tmp)):
                out = run_analysis.default_output_dir("RHM.DE", "2026-10-20", "ab")
            self.assertEqual(
                str(out),
                f"{tmp}/.tradingagents/reports/trading-scenarios/RHM.DE/2026-10-20-ab")
            self.assertTrue(out.is_dir())

    def test_default_output_dir_without_suffix(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(Path, "home", return_value=Path(tmp)):
                out = run_analysis.default_output_dir("RHM.DE", "2026-10-20", None)
        self.assertEqual(
            str(out),
            f"{tmp}/.tradingagents/reports/trading-scenarios/RHM.DE/2026-10-20")


if __name__ == "__main__":
    unittest.main()
