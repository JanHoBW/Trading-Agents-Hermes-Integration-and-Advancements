"""The ``run_dir`` results layout (TRADINGAGENTS_RESULTS_LAYOUT=run_dir).

In this layout the results directory already belongs to a single run, so every
writer puts its artifacts directly there instead of adding ticker/date layers:
the live message log and the full-states log sit at the top level, all report
files in its ``reports/`` subdirectory. The wrapper (ta.sh) sets this for
unattended runs; the default ``shared`` layout is unchanged.
"""

import pytest

from cli import run as cli_run
from tradingagents.graph.trading_graph import TradingAgentsGraph


def _config(tmp_path):
    return {"results_dir": str(tmp_path), "results_layout": "run_dir"}


@pytest.mark.unit
def test_run_directory_is_the_results_directory_itself(tmp_path):
    ok = cli_run._run_directory(_config(tmp_path), "NVDA", "2026-09-01")
    assert ok == tmp_path


@pytest.mark.unit
def test_run_directory_still_validates_the_ticker(tmp_path):
    with pytest.raises(ValueError):
        cli_run._run_directory(_config(tmp_path), "..", "2026-09-01")


def _graph_with_config(cfg):
    """A graph instance carrying only a config — enough for path helpers."""
    graph = object.__new__(TradingAgentsGraph)
    graph.config = cfg
    return graph


@pytest.mark.unit
def test_default_report_path_has_no_stamp_in_run_dir_layout(tmp_path):
    path = _graph_with_config(_config(tmp_path)).default_report_path("NVDA")
    assert path == tmp_path / "reports"


@pytest.mark.unit
def test_shared_layout_paths_are_unchanged(tmp_path):
    # No results_layout key: the default shared layout must behave exactly as before.
    cfg = {"results_dir": str(tmp_path)}

    ok = cli_run._run_directory(cfg, "NVDA", "2026-09-01")
    assert ok == tmp_path / "NVDA" / "2026-09-01"

    path = _graph_with_config(cfg).default_report_path("NVDA")
    assert path.parent == tmp_path / "reports"
    assert path.name.startswith("NVDA_")
