"""Deterministic post-run gate over the saved report tree.

The report is written by an LLM; before anyone treats it as a deliverable this
module checks what the run *actually* produced on disk. It is deliberately not a
keyword contest over the prose: every check is tied to an artifact or to a
machine-readable record, so a passing gate means "the run is structurally
complete and its gaps are recorded", not "the text mentions RSI".

Result shape (also written to ``quality_gate.json`` next to the report)::

    {"status": "APPROVED" | "NEEDS_REVIEW",
     "checks": [{"name", "passed", "blocking", "detail"}],
     "gaps": [...],              # coverage entries that were not `ok`
     "generated_at": "...", "report_dir": "..."}

Blocking checks decide the status; warnings are reported but never flip it.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

GATE_FILENAME = "quality_gate.json"

#: The PM must commit to exactly one of these (see the portfolio manager prompt).
RATINGS = ("Buy", "Overweight", "Hold", "Underweight", "Sell")

#: Markers of an unfinished report. Case-sensitive on purpose: "todo" can be prose.
PLACEHOLDERS = ("TODO", "TBD", "XXX", "[insert", "<insert", "lorem ipsum")

#: A full run's report is never this short; below it something is missing.
MIN_REPORT_CHARS = 2000

#: Sections a complete run writes: (label, relative path).
EXPECTED_SECTIONS = (
    ("market analyst", "1_analysts/market.md"),
    ("sentiment analyst", "1_analysts/sentiment.md"),
    ("news analyst", "1_analysts/news.md"),
    ("fundamentals analyst", "1_analysts/fundamentals.md"),
    ("research manager", "2_research/manager.md"),
    ("trader", "3_trading/trader.md"),
    ("portfolio manager", "5_portfolio/decision.md"),
)

OK_STATUSES = ("ok",)


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _coverage_gaps(report_dir: Path) -> tuple[dict | None, list[dict]]:
    """The coverage snapshot and every entry that is not `ok`."""
    path = report_dir / "data_coverage.json"
    if not path.is_file():
        return None, []
    try:
        snapshot = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, []
    if not isinstance(snapshot, dict):
        return None, []
    # ``methods`` is the per-method registry (method -> {status, served_by, skips});
    # ``gaps`` additionally lists every skipped vendor with its reason.
    methods = snapshot.get("methods") or {}
    entries = ([{"method": name, **rec} for name, rec in methods.items()]
               if isinstance(methods, dict) else list(methods))
    gaps = []
    for entry in entries:
        if entry.get("status") in OK_STATUSES:
            continue
        skips = entry.get("skips") or []
        reason = skips[0].get("reason") if skips and isinstance(skips[0], dict) else ""
        gaps.append({
            "method": entry.get("method"),
            "status": entry.get("status"),
            "served_by": entry.get("served_by") or [],
            "reason": (reason or "")[:160],
        })
    return snapshot, gaps


def _rating(decision: str) -> str | None:
    """The rating the decision committed to, if it is unambiguous.

    An explicit ``**Rating**: Buy`` line wins — the PM prompt asks for exactly
    that, and prose may legitimately mention alternatives ("Buy rather than
    Hold"). Without such a line the decision has to name exactly one rating;
    naming two is itself the defect.
    """
    import re

    explicit = re.search(
        r"\*{0,2}rating\*{0,2}\s*[:=]\s*\*{0,2}\s*(buy|overweight|hold|underweight|sell)\b",
        decision, flags=re.IGNORECASE,
    )
    if explicit:
        match = explicit.group(1).lower()
        return next(r for r in RATINGS if r.lower() == match)

    found = [r for r in RATINGS if re.search(rf"\b{r}\b", decision, flags=re.IGNORECASE)]
    return found[0] if len(found) == 1 else None


def run_quality_gate(report_dir) -> dict:
    """Check a finished run's report tree; write and return the verdict."""
    report_dir = Path(report_dir)
    checks: list[dict] = []

    def check(name: str, passed: bool, detail: str, blocking: bool = True) -> None:
        checks.append({"name": name, "passed": bool(passed),
                       "blocking": blocking, "detail": detail})

    report = _read(report_dir / "complete_report.md")
    if report:
        check("report_present", True, "complete_report.md exists and is not empty")
        check("report_substantial", len(report) >= MIN_REPORT_CHARS,
              f"{len(report)} chars (minimum {MIN_REPORT_CHARS})")
    else:
        check("report_present", False, "complete_report.md missing")

    decision = _read(report_dir / "5_portfolio/decision.md")
    check("decision_present", bool(decision.strip()),
          "5_portfolio/decision.md present" if decision.strip()
          else "5_portfolio/decision.md missing or empty")
    if decision.strip():
        rating = _rating(decision)
        check("decision_rating", rating is not None,
              f"rating found: {rating}" if rating else
              "no single Buy/Overweight/Hold/Underweight/Sell in the decision "
              "(missing, or several named without committing)")

    placeholders = [marker for marker in PLACEHOLDERS if report and marker in report]
    check("no_placeholders", not placeholders,
          "none" if not placeholders else f"found: {', '.join(placeholders)}")

    snapshot, gaps = _coverage_gaps(report_dir)
    methods = (snapshot or {}).get("methods") or []
    check("coverage_recorded", bool(methods),
          f"{len(methods)} vendor call(s) recorded" if methods else
          "data_coverage.json missing or empty — the run's data provenance was not written")

    # Warnings: worth surfacing to the reader, not worth failing the run.
    missing = [label for label, rel in EXPECTED_SECTIONS
               if not _read(report_dir / rel).strip()]
    check("sections_complete", not missing,
          "all expected sections written" if not missing else f"missing: {', '.join(missing)}",
          blocking=False)

    coverage_md = _read(report_dir / "6_coverage/coverage.md")
    has_windows = "Analysis windows" in coverage_md
    check("coverage_windows_named", has_windows or not coverage_md,
          "both windows printed" if has_windows else
          ("no coverage section written" if not coverage_md else
           "no 'Analysis windows' line in the coverage section (no trade_date?)"),
          blocking=False)

    check("no_data_gaps", not gaps,
          "every requested source answered" if not gaps else
          f"{len(gaps)} gap(s): " + ", ".join(f"{g['method']}={g['status']}" for g in gaps[:6]),
          blocking=False)

    blocking_failures = [c for c in checks if c["blocking"] and not c["passed"]]
    verdict = {
        "status": "NEEDS_REVIEW" if blocking_failures else "APPROVED",
        "checks": checks,
        "gaps": gaps,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "report_dir": str(report_dir),
    }
    try:
        (report_dir / GATE_FILENAME).write_text(
            json.dumps(verdict, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    except OSError:
        # A read-only results dir must not turn a finished run into a crash.
        pass
    return verdict
