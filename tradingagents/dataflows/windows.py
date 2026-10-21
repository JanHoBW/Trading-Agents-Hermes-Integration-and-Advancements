"""Analysis windows, in one place.

Two horizons with different jobs:

* **Headline window** (``news_lookback_days``, default 7 d) — what is happening
  now. Drives ticker news, the sentiment analyst and the news supplement.
* **Event window** (``event_context_lookback_days``, default 180 d) — what shaped
  the thesis. Drives the first-party event context (issuer releases, capital-market
  disclosures, insider filings). 180 calendar days is ~126 trading days, i.e. the
  longest horizon the run is later settled against.

Everything is anchored on the run's ``trade_date``, never on wall-clock time, so a
historical run sees the same windows it would have seen then.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from tradingagents.dataflows.config import get_config

HEADLINE_KEY = "news_lookback_days"
EVENT_KEY = "event_context_lookback_days"


def _parse(day: str) -> date:
    try:
        return datetime.strptime(str(day).strip()[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError) as exc:
        raise ValueError(f"not a YYYY-MM-DD date: {day!r}") from exc


def window(trade_date: str, days: int) -> tuple[str, str]:
    """``(start, end)`` as ISO strings, ``days`` calendar days before ``trade_date``."""
    end = _parse(trade_date)
    if int(days) < 1:
        raise ValueError(f"lookback must be at least 1 day, got {days!r}")
    return (end - timedelta(days=int(days))).isoformat(), end.isoformat()


def _days(key: str, default: int, config: dict | None) -> int:
    cfg = config if config is not None else get_config()
    try:
        return int(cfg.get(key, default))
    except (TypeError, ValueError):
        return default


def headline_window(trade_date: str, config: dict | None = None) -> tuple[str, str]:
    """The news/sentiment window for this run."""
    return window(trade_date, _days(HEADLINE_KEY, 7, config))


def event_window(trade_date: str, config: dict | None = None) -> tuple[str, str]:
    """The first-party event-context window for this run."""
    return window(trade_date, _days(EVENT_KEY, 180, config))


def event_context_enabled(config: dict | None = None) -> bool:
    cfg = config if config is not None else get_config()
    return bool(cfg.get("event_context_enabled", True))
