"""Provenance: prompt hashes and a replay id.

"Which prompts produced this report?" used to be an assumption about the
checkout. Now it is a fact in provenance.json, and the replay id ties the run's
inputs, rounds, backend and prompt hashes to one digest.
"""
import importlib.util
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_analysis.py"
SPEC = importlib.util.spec_from_file_location("run_analysis_prompts_under_test", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("could not load run_analysis.py for tests")
run_analysis = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(run_analysis)


def _args(**kw):
    base = dict(ticker="RHM.DE", date="2026-10-21", analysts="market,news,fundamentals,social",
                debate_rounds=3, risk_rounds=2, llm="lmstudio")
    base.update(kw)
    return Namespace(**base)


class PromptVersionTests(unittest.TestCase):
    def test_hashes_every_agent_module_and_nothing_else(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agents = root / "tradingagents" / "agents"
            (agents / "analysts").mkdir(parents=True)
            (agents / "analysts" / "news_analyst.py").write_text("# prompt", encoding="utf-8")
            (agents / "state.py").write_text("# state", encoding="utf-8")
            (root / "tradingagents" / "graph").mkdir()
            (root / "tradingagents" / "graph" / "graph.py").write_text("# not a prompt", encoding="utf-8")

            versions = run_analysis.prompt_versions(root)

        self.assertEqual(sorted(versions), [
            "tradingagents/agents/analysts/news_analyst.py",
            "tradingagents/agents/state.py",
        ])
        self.assertTrue(all(len(v) == 64 for v in versions.values()))

    def test_missing_tree_is_empty_not_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(run_analysis.prompt_versions(Path(tmp)), {})

    def test_hash_tracks_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agents = root / "tradingagents" / "agents"
            agents.mkdir(parents=True)
            f = agents / "p.py"
            f.write_text("v1", encoding="utf-8")
            first = run_analysis.prompt_versions(root)["tradingagents/agents/p.py"]
            f.write_text("v2", encoding="utf-8")
            second = run_analysis.prompt_versions(root)["tradingagents/agents/p.py"]
        self.assertNotEqual(first, second)


class ReplayIdTests(unittest.TestCase):
    def test_same_inputs_same_id(self):
        prompts = {"tradingagents/agents/p.py": "a" * 64}
        a = run_analysis.replay_id(_args(), "lmstudio", prompts)
        b = run_analysis.replay_id(_args(), "lmstudio", dict(prompts))
        self.assertEqual(a, b)
        self.assertEqual(len(a), 16)

    def test_prompt_change_changes_the_id(self):
        a = run_analysis.replay_id(_args(), "lmstudio", {"p": "a" * 64})
        b = run_analysis.replay_id(_args(), "lmstudio", {"p": "b" * 64})
        self.assertNotEqual(a, b)

    def test_backend_rounds_and_date_all_matter(self):
        prompts = {"p": "a" * 64}
        base = run_analysis.replay_id(_args(), "lmstudio", prompts)
        self.assertNotEqual(base, run_analysis.replay_id(_args(), "webercloud", prompts))
        self.assertNotEqual(base, run_analysis.replay_id(_args(risk_rounds=1), "lmstudio", prompts))
        self.assertNotEqual(base, run_analysis.replay_id(_args(date="2026-10-20"), "lmstudio", prompts))

    def test_reasoning_effort_is_part_of_the_id(self):
        # Two runs that differ only in effort must not look identical afterwards.
        prompts = {"p": "a" * 64}
        base = run_analysis.replay_id(_args(), "custom", prompts)
        self.assertNotEqual(base, run_analysis.replay_id(_args(), "custom", prompts, "xhigh"))
        self.assertNotEqual(base, run_analysis.replay_id(_args(), "custom", prompts, "low"))


class EffectiveEffortTests(unittest.TestCase):
    def test_reads_the_value_the_child_receives(self):
        self.assertEqual(run_analysis.effective_effort(
            {"TRADINGAGENTS_OPENAI_REASONING_EFFORT": "xhigh"}), "xhigh")

    def test_unset_or_blank_is_provider_default(self):
        self.assertEqual(run_analysis.effective_effort({}), "provider-default")
        self.assertEqual(run_analysis.effective_effort(
            {"TRADINGAGENTS_OPENAI_REASONING_EFFORT": "   "}), "provider-default")


if __name__ == "__main__":
    unittest.main()
