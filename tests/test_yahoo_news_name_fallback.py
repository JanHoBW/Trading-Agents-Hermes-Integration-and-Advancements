"""Yahoo news: a symbol query is not the same as a name query.

Verified live 2026-10-21: ``Search("RHM.DE")`` returns 0 articles while
``Search("Rheinmetall")`` returns 10, nine of them tagged ``RHM.DE`` — and
``Ticker(...).get_news()`` returns nothing even for NVDA. The vendor therefore
reported a data gap that did not exist. The fallback asks again with the quote's
own company name and keeps the strict symbol tag filter.
"""
from __future__ import annotations

from datetime import datetime
from unittest import mock

import pytest

from tradingagents.dataflows.vendors.yahoo import news as ynews


def _epoch(day: str) -> int:
    return int(datetime.fromisoformat(day).timestamp())


def _article(title: str, day: str, tickers=("RHM.DE",)) -> dict:
    return {"title": title, "publisher": "P", "link": "l",
            "providerPublishTime": _epoch(day), "relatedTickers": list(tickers)}


class _Quote:
    def __init__(self, symbol, longname=None, shortname=None):
        self._d = {"symbol": symbol}
        if longname:
            self._d["longname"] = longname
        if shortname:
            self._d["shortname"] = shortname

    def get(self, key, default=None):
        return self._d.get(key, default)


def _install(monkeypatch, answers: dict, ticker_news=()):
    """FakeTicker with an empty quote feed, and a Search answering per query."""
    searched = []

    class FakeTicker:
        def __init__(self, *a, **k):
            pass

        def get_news(self, count=20):
            return list(ticker_news)

    class FakeSearch:
        def __init__(self, query, **k):
            searched.append(query)
            body = answers.get(query, {})
            self.news = list(body.get("news", []))
            self.quotes = list(body.get("quotes", []))

    monkeypatch.setattr(ynews.yf, "Ticker", FakeTicker)
    monkeypatch.setattr(ynews.yf, "Search", FakeSearch)
    return searched


@pytest.mark.unit
def test_symbol_query_without_news_falls_back_to_the_company_name(monkeypatch):
    searched = _install(monkeypatch, {
        "RHM.DE": {"news": [], "quotes": [_Quote("RHM.DE", longname="Rheinmetall AG")]},
        "Rheinmetall AG": {"news": [_article("Rheinmetall Fell While Profit Rose", "2026-10-20")]},
    })

    out = ynews.get_news_yfinance("RHM.DE", "2026-10-14", "2026-10-21")

    assert searched == ["RHM.DE", "Rheinmetall AG"]
    assert "Rheinmetall Fell While Profit Rose" in out
    assert "unavailable" not in out


@pytest.mark.unit
def test_fallback_keeps_the_symbol_tag_filter(monkeypatch):
    # A name match that is not tagged with the symbol is not about the listing.
    _install(monkeypatch, {
        "RHM.DE": {"news": [], "quotes": [_Quote("RHM.DE", longname="Rheinmetall AG")]},
        "Rheinmetall AG": {"news": [_article("Defence sector roundup", "2026-10-20", tickers=("LDO.MI",))]},
    })

    from tradingagents.dataflows.errors import VendorUnavailableError
    with pytest.raises(VendorUnavailableError, match="no news articles tagged RHM.DE"):
        ynews.get_news_yfinance("RHM.DE", "2026-10-14", "2026-10-21")


@pytest.mark.unit
def test_shortname_is_used_when_longname_is_missing(monkeypatch):
    searched = _install(monkeypatch, {
        "RHM.DE": {"news": [], "quotes": [_Quote("RHM.DE", shortname="RHEINMETALL AG I")]},
        "RHEINMETALL AG I": {"news": [_article("News", "2026-10-20")]},
    })
    ynews.get_news_yfinance("RHM.DE", "2026-10-14", "2026-10-21")
    assert searched == ["RHM.DE", "RHEINMETALL AG I"]


@pytest.mark.unit
def test_no_quote_name_means_no_second_query(monkeypatch):
    from tradingagents.dataflows.errors import VendorUnavailableError

    searched = _install(monkeypatch, {"RHM.DE": {"news": [], "quotes": []}})
    with pytest.raises(VendorUnavailableError):
        ynews.get_news_yfinance("RHM.DE", "2026-10-14", "2026-10-21")
    assert searched == ["RHM.DE"]


@pytest.mark.unit
def test_symbol_query_that_already_has_tagged_news_does_not_ask_twice(monkeypatch):
    searched = _install(monkeypatch, {
        "RHM.DE": {"news": [_article("Direct hit", "2026-10-20")],
                   "quotes": [_Quote("RHM.DE", longname="Rheinmetall AG")]},
    })
    out = ynews.get_news_yfinance("RHM.DE", "2026-10-14", "2026-10-21")
    assert "Direct hit" in out
    assert searched == ["RHM.DE"]


@pytest.mark.unit
def test_quote_news_feed_still_wins_when_it_has_articles(monkeypatch):
    # The name fallback must not run at all when the quote feed answered.
    searched = _install(monkeypatch, {}, ticker_news=[_article("Feed item", "2026-10-20")])
    out = ynews.get_news_yfinance("RHM.DE", "2026-10-14", "2026-10-21")
    assert "Feed item" in out
    assert searched == []
