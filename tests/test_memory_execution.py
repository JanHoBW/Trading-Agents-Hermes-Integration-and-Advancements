"""Execution tracking (what the human did with a call) and unscorable entries.

Two gaps this closes: the log could say what the model decided and what the
human thought of it, but not whether the trade happened; and a decision on a
delisted instrument or with a missing benchmark stayed pending forever.
"""
from __future__ import annotations

import json

import pytest

from tradingagents.memory import TradingMemoryLog

_SEP = TradingMemoryLog._SEPARATOR


def _log(tmp_path, name="mem.md"):
    return TradingMemoryLog({"memory_log_path": str(tmp_path / name)})


@pytest.mark.unit
def test_execution_is_stored_parsed_and_reported(tmp_path):
    log = _log(tmp_path)
    log.store_decision("RHM.DE", "2026-10-01", "Rating: Buy\nEnter now.")

    assert log.add_execution("RHM.DE", "2026-10-01", "executed", reason="50 shares at 940", execution_date="2026-10-02")
    entry = log.load_entries()[0]
    assert entry["execution"]["status"] == "executed"
    assert entry["execution"]["grund"] == "50 shares at 940"
    assert entry["human_feedback"] is None

    # Re-recording the identical decision is a no-op; a changed one replaces it.
    assert log.add_execution("RHM.DE", "2026-10-01", "executed", reason="50 shares at 940", execution_date="2026-10-02") is False
    assert log.add_execution("RHM.DE", "2026-10-01", "refused", reason="too hot after the gap") is True
    entry = log.load_entries()[0]
    assert entry["execution"]["status"] == "refused"
    assert entry["execution"]["grund"] == "too hot after the gap"


@pytest.mark.unit
def test_rejects_unknown_status_and_missing_entry(tmp_path):
    log = _log(tmp_path)
    log.store_decision("RHM.DE", "2026-10-01", "Rating: Buy")
    with pytest.raises(ValueError):
        log.add_execution("RHM.DE", "2026-10-01", "maybe")
    assert log.add_execution("RHM.DE", "2026-09-01", "executed") is False


@pytest.mark.unit
def test_pending_entry_with_execution_reaches_the_pm_context(tmp_path):
    log = _log(tmp_path)
    log.store_decision("RHM.DE", "2026-10-01", "Rating: Buy\nEnter now.")
    log.add_execution("RHM.DE", "2026-10-01", "refused", reason="position already at cap")

    context = log.get_past_context("RHM.DE")
    assert "EXECUTION" in context
    assert "refused" in context and "position already at cap" in context


@pytest.mark.unit
def test_execution_survives_settlement_rewrite(tmp_path):
    log = _log(tmp_path)
    log.store_decision("RHM.DE", "2026-10-01", "Rating: Buy\nEnter now.")
    log.add_execution("RHM.DE", "2026-10-01", "executed", reason="bought the dip")

    assert log.merge_horizons(
        "RHM.DE", "2026-10-01",
        {5: (0.031, 0.012, 5, "2026-10-08")},
        reflection="Lesson text.", anchor_days=5, resolution_date="2026-10-08",
        reflection_covers=[5],
    )
    entries = log.load_entries()
    assert entries[0]["execution"]["grund"] == "bought the dip"
    assert "+3.1%" in entries[0]["raw"]


@pytest.mark.unit
def test_mark_unscorable_is_terminal_and_auditable(tmp_path):
    log = _log(tmp_path)
    log.store_decision("DEAD.DE", "2026-01-05", "Rating: Hold")
    assert log.mark_unscorable("DEAD.DE", "2026-01-05", "delisted",
                               "no price bars for DEAD.DE on or after 2026-01-05")

    entry = log.load_entries()[0]
    assert entry["unscorable"]["status"] == "delisted"
    assert "no price bars" in entry["unscorable"]["grund"]
    assert entry["pending"] is False and entry["raw"] is None
    # No longer offered to settlement, so it cannot be retried forever.
    assert log.get_entries_needing_settlement([5, 21, 63, 126]) == []
    # The reader sees why it has no outcome.
    assert "NOT SCORABLE" in log.get_past_context("DEAD.DE")


@pytest.mark.unit
def test_unscorable_rejects_unknown_status_and_non_pending_entries(tmp_path):
    log = _log(tmp_path)
    log.store_decision("DEAD.DE", "2026-01-05", "Rating: Hold")
    with pytest.raises(ValueError):
        log.mark_unscorable("DEAD.DE", "2026-01-05", "stale")
    log.merge_horizons("DEAD.DE", "2026-01-05", {5: (0.01, 0.0, 5, "2026-01-12")},
                       reflection="x", anchor_days=5)
    assert log.mark_unscorable("DEAD.DE", "2026-01-05", "delisted") is False


@pytest.mark.unit
def test_unscorable_entries_count_as_resolved_for_rotation(tmp_path):
    log = TradingMemoryLog({"memory_log_path": str(tmp_path / "mem.md"),
                            "memory_log_max_entries": 1})
    for day in ("2026-01-01", "2026-01-02"):
        log.store_decision("DEAD.DE", day, "Rating: Hold")
    log.store_decision("LIVE.DE", "2026-01-03", "Rating: Buy")
    log.mark_unscorable("DEAD.DE", "2026-01-01", "benchmark_missing", "no ^GDAXI bars")
    log.mark_unscorable("DEAD.DE", "2026-01-02", "benchmark_missing", "no ^GDAXI bars")
    # Rotation runs on a settlement rewrite, and must treat unscorable entries
    # as resolved (they are finished, not waiting).
    log.merge_horizons("LIVE.DE", "2026-01-03", {5: (0.01, 0.0, 5, "2026-01-12")},
                       reflection="x", anchor_days=5, resolution_date="2026-01-12")
    dates = {(e["ticker"], e["date"]) for e in log.load_entries()}
    assert ("DEAD.DE", "2026-01-01") not in dates
    assert ("DEAD.DE", "2026-01-02") not in dates
    assert ("LIVE.DE", "2026-01-03") in dates


@pytest.mark.unit
def test_annotations_and_execution_coexist_in_one_entry(tmp_path):
    log = _log(tmp_path)
    log.store_decision("RHM.DE", "2026-10-01", "Rating: Buy\nEnter now.")
    log.add_human_feedback("RHM.DE", "2026-10-01", "call was right", rating="gut")
    log.add_execution("RHM.DE", "2026-10-01", "executed")

    blob = (tmp_path / "mem.md").read_text()
    assert blob.count("HUMAN_FEEDBACK:") == 1
    assert blob.count("EXECUTION:") == 1
    entry = log.load_entries()[0]
    assert entry["human_feedback"]["bewertung"] == "gut"
    assert entry["execution"]["status"] == "executed"
    json.loads(entry["execution"] and "{}")  # execution parsed as a dict, not a string
    assert isinstance(entry["execution"], dict)
