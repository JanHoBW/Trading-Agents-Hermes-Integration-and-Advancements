"""CLI memory commands (backlog #8): the human feedback loop.

``tradingagents memory feedback`` annotates a decision by hand; the note is
stored on the entry itself (HUMAN_FEEDBACK block) and flows into the portfolio
manager context on the next run of the same ticker. ``tradingagents memory
show`` reads recent entries (outcomes, lessons, feedback) back.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

import cli.main as cli_main
from tradingagents.memory import TradingMemoryLog

DECISION_BUY = "Rating: Buy\n\nBuy NVDA on strength."

runner = CliRunner()


@pytest.fixture
def log_path(tmp_path, monkeypatch):
    path = tmp_path / "trading_memory.md"
    monkeypatch.setitem(cli_main.DEFAULT_CONFIG, "memory_log_path", str(path))
    return path


def _log(path):
    return TradingMemoryLog({"memory_log_path": str(path)})


def _seed(log, ticker="NVDA", date="2026-01-05"):
    log.store_decision(ticker, date, DECISION_BUY)


def _run_memory(args):
    return runner.invoke(cli_main.app, ["memory"] + args)


@pytest.mark.unit
def test_feedback_stores_annotation_on_pending_entry(log_path):
    log = _log(log_path)
    _seed(log)
    result = _run_memory(["feedback", "NVDA", "--note", "Order flow was thin.",
                          "--rating", "teilweise", "--date", "2026-01-05"])
    assert result.exit_code == 0, result.output
    entry = log.load_entries()[0]
    assert entry["human_feedback"]["vermerk"] == "Order flow was thin."
    assert entry["human_feedback"]["bewertung"] == "teilweise"


@pytest.mark.unit
def test_feedback_default_date_is_most_recent_entry(log_path):
    log = _log(log_path)
    log.store_decision("NVDA", "2026-01-05", DECISION_BUY)
    log.store_decision("NVDA", "2026-02-01", DECISION_BUY)
    result = _run_memory(["feedback", "NVDA", "--note", "Latest one."])
    assert result.exit_code == 0, result.output
    entries = {e["date"]: e for e in log.load_entries()}
    assert entries["2026-02-01"]["human_feedback"]["vermerk"] == "Latest one."
    assert entries["2026-01-05"]["human_feedback"] is None


@pytest.mark.unit
def test_feedback_replaces_previous_annotation(log_path):
    log = _log(log_path)
    _seed(log)
    assert _run_memory(["feedback", "NVDA", "--note", "first"]).exit_code == 0
    result = _run_memory(["feedback", "NVDA", "--note", "second",
                          "--date", "2026-01-05"])
    assert result.exit_code == 0, result.output
    entry = log.load_entries()[0]
    assert entry["human_feedback"]["vermerk"] == "second"
    assert "first" not in log_path.read_text(encoding="utf-8")


@pytest.mark.unit
def test_feedback_rejects_unknown_ticker_and_date(log_path):
    log = _log(log_path)
    _seed(log)
    result = _run_memory(["feedback", "AAPL", "--note", "x"])
    assert result.exit_code == 1
    assert "NVDA" in result.output  # lists available tickers
    result = _run_memory(["feedback", "NVDA", "--note", "x",
                          "--date", "2026-12-25"])
    assert result.exit_code == 1


@pytest.mark.unit
def test_feedback_rejects_bad_rating(log_path):
    result = _run_memory(["feedback", "NVDA", "--note", "x", "--rating", "mega"])
    assert result.exit_code == 1


@pytest.mark.unit
def test_feedback_requires_note():
    result = _run_memory(["feedback", "NVDA"])
    assert result.exit_code != 0


@pytest.mark.unit
def test_show_renders_entries_with_feedback(log_path):
    log = _log(log_path)
    _seed(log)
    log.add_human_feedback("NVDA", "2026-01-05", "Watched the program wins.",
                           rating="gut", feedback_date="2026-01-06")
    result = _run_memory(["show", "NVDA"])
    assert result.exit_code == 0, result.output
    assert "NVDA" in result.output
    assert "Buy" in result.output
    assert "Watched the program wins." in result.output
    assert "gut" in result.output


@pytest.mark.unit
def test_show_empty_log_fails_cleanly(log_path):
    result = _run_memory(["show", "NVDA"])
    assert result.exit_code == 1
    assert "No memory log" in result.output
