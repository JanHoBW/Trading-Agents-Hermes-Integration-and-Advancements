"""Issuer-site news via sitemap discovery (pilot: Rheinmetall).

The vendor exists because German issuer sites are JS apps whose news list holds no
links in the HTML, while their sitemaps list every release with a dated slug and
the release pages are server-rendered. These tests pin the parts that must not
regress: discovery/filtering, the translation pairing (DE/EN twins), the gate on a
recorded terms check, robots handling, and the honest empty-window wording.
"""
from __future__ import annotations

import json
from datetime import date
from unittest import mock

import pytest

from tradingagents.dataflows import router
from tradingagents.dataflows.errors import VendorNotConfiguredError, VendorUnavailableError
from tradingagents.dataflows.vendors import issuer_ir

URLSET = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.com/de/media/news-watch/news/2026/10/2026-10-08-firma-verdoppelt-kreditlinie</loc><lastmod>2026-10-08T07:04:27+00:00</lastmod></url>
  <url><loc>https://example.com/en/media/news-watch/news/2026/10/2026-10-08-firm-doubles-credit-facility</loc><lastmod>2026-10-08T07:04:34+00:00</lastmod></url>
  <url><loc>https://example.com/de/media/news-watch/news/2026/10/2026-10-16-neue-fertigung</loc></url>
  <url><loc>https://example.com/de/jobs/2026-10-08-stellenangebot</loc></url>
  <url><loc>https://example.com/de/media/news-watch/news/ohne-datum</loc></url>
</urlset>"""

INDEX = """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://example.com/sitemap-pages.xml</loc></sitemap>
</sitemapindex>"""

ARTICLE = """<html><head><title>Firma verdoppelt Kreditlinie</title>
<link rel="alternate" hreflang="de" href="https://example.com/de/media/news-watch/news/2026/10/2026-10-08-firma-verdoppelt-kreditlinie">
<link rel="alternate" hreflang="en" href="https://example.com/en/media/news-watch/news/2026/10/2026-10-08-firm-doubles-credit-facility">
</head><body><nav>jumpToMain Kontakt</nav>
<p>08/10/2026 | Pressemitteilung Die syndizierte Kreditlinie wurde auf 1,5 Mrd. Euro verdoppelt.</p>
<script>var x = 1;</script></body></html>"""

PATTERNS = ["/media/news-watch/news/\\d{4}/\\d{2}/\\d{4}-\\d{2}-\\d{2}-"]


def _ir(**over):
    cfg = {"enabled": True, "terms_checked": "2026-10-21: robots allows it",
           "sitemap": "https://example.com/sitemap.xml", "url_patterns": PATTERNS}
    cfg.update(over)
    return cfg


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_parse_sitemap_returns_locs_and_detects_index():
    urls = issuer_ir.parse_sitemap(URLSET)
    assert len(urls) == 5
    assert issuer_ir.is_sitemap_index(INDEX) is True
    assert issuer_ir.is_sitemap_index(URLSET) is False


@pytest.mark.unit
def test_oversized_sitemap_is_rejected():
    with pytest.raises(ValueError, match="8 MB"):
        issuer_ir.parse_sitemap("<urlset>" + ("x" * (issuer_ir.MAX_SITEMAP_BYTES + 1)) + "</urlset>")


@pytest.mark.unit
def test_dated_entries_filter_by_pattern_window_and_slug_date():
    entries = issuer_ir.dated_entries(issuer_ir.parse_sitemap(URLSET), PATTERNS,
                                      date(2026, 10, 1), date(2026, 10, 31))
    urls = [e["url"] for e in entries]
    assert len(urls) == 3                     # job page + undated slug excluded
    assert all("/media/news-watch/news/" in u for u in urls)
    assert entries[0]["date"] >= entries[-1]["date"]   # newest first

    narrow = issuer_ir.dated_entries(issuer_ir.parse_sitemap(URLSET), PATTERNS,
                                     date(2026, 10, 10), date(2026, 10, 20))
    assert [e["date"] for e in narrow] == ["2026-10-16"]


@pytest.mark.unit
def test_parse_alternates_pairs_the_translations():
    alts = issuer_ir.parse_alternates(ARTICLE)
    assert "https://example.com/en/media/news-watch/news/2026/10/2026-10-08-firm-doubles-credit-facility" in alts
    assert len(alts) == 2


@pytest.mark.unit
def test_clean_article_strips_markup_and_site_chrome():
    title, text = issuer_ir.clean_article(ARTICLE)
    assert title == "Firma verdoppelt Kreditlinie"
    assert "Kreditlinie wurde auf 1,5 Mrd" in text
    assert "jumpToMain" not in text and "var x" not in text


@pytest.mark.unit
def test_excerpt_cuts_on_a_word_boundary():
    assert issuer_ir.excerpt("a" * 10, 5) == "aaaaa …"
    assert issuer_ir.excerpt("short", 50) == "short"


# ---------------------------------------------------------------------------
# Gate: only a checked, enabled, https-configured issuer is read
# ---------------------------------------------------------------------------

@pytest.mark.unit
@pytest.mark.parametrize("block, message", [
    (None, "no issuer-site news source enabled"),
    ({"enabled": False}, "no issuer-site news source enabled"),
    (_ir(terms_checked="  "), "no recorded terms check"),
    (_ir(sitemap="http://example.com/sitemap.xml"), "must be an HTTPS URL"),
    (_ir(url_patterns=[]), "no url_patterns"),
])
def test_ir_config_gate(block, message):
    with pytest.raises(VendorNotConfiguredError, match=message):
        issuer_ir._ir_config({"ir": block} if block else {}, "TEST.DE")


# ---------------------------------------------------------------------------
# End to end with the network mocked out
# ---------------------------------------------------------------------------

def _run(monkeypatch, ir=None, articles=None, allowed=True, start="2026-10-01", end="2026-10-31"):
    monkeypatch.setattr(issuer_ir, "load_instrument",
                        lambda ticker: {"issuer": "Firma AG", "ticker": ticker, "ir": ir or _ir()})
    monkeypatch.setattr(issuer_ir, "_sitemap_urls", lambda sitemap: issuer_ir.parse_sitemap(URLSET))
    monkeypatch.setattr(issuer_ir, "_allowed", lambda url: allowed)
    payload = articles or (lambda url: (f"Titel {url[-30:]}", "Text der Meldung.", 
                                        ["https://example.com/en/media/news-watch/news/2026/10/2026-10-08-firm-doubles-credit-facility"]))
    monkeypatch.setattr(issuer_ir, "fetch_article", payload)
    return issuer_ir.get_news_issuer_ir("TEST.DE", start, end)


@pytest.mark.unit
def test_output_reports_source_window_and_first_party_caveat(monkeypatch):
    out = _run(monkeypatch)
    assert "Official issuer site: Firma AG (TEST.DE)" in out
    assert "Window: 2026-10-01 to 2026-10-31" in out
    assert "not independent corroboration" in out
    assert "### 2026-10-08" in out


@pytest.mark.unit
def test_german_release_wins_and_the_english_twin_is_skipped(monkeypatch):
    seen = []

    def fetch(url):
        seen.append(url)
        return "Titel", "Text", [
            "https://example.com/en/media/news-watch/news/2026/10/2026-10-08-firm-doubles-credit-facility"]

    out = _run(monkeypatch, articles=fetch)
    assert any("/de/" in u for u in seen)
    assert not any("/en/" in u for u in seen)          # twin skipped before fetching
    assert "translation twin" in out


@pytest.mark.unit
def test_empty_window_is_an_absence_in_this_source_only(monkeypatch):
    out = _run(monkeypatch, start="2026-11-01", end="2026-11-30")
    assert "no dated release matched" in out
    assert "absence in this source" in out


@pytest.mark.unit
def test_robots_blocking_every_match_is_an_unavailable_source(monkeypatch):
    with pytest.raises(VendorUnavailableError, match="robots.txt"):
        _run(monkeypatch, allowed=False)


@pytest.mark.unit
def test_a_failed_article_page_does_not_lose_the_entry(monkeypatch):
    def fetch(url):
        raise VendorUnavailableError("timeout")

    out = _run(monkeypatch, articles=fetch)
    assert "(page unavailable)" in out and "Fetch failed: timeout" in out


@pytest.mark.unit
def test_limit_caps_article_fetches(monkeypatch):
    calls = []

    def fetch(url):
        calls.append(url)
        return "T", "x", []

    with mock.patch.object(issuer_ir, "get_config", return_value={"news_article_limit": 1}):
        out = _run(monkeypatch, articles=fetch)
    assert len(calls) == 1
    assert "showing 1 of" in out


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_router_leads_the_news_chain_with_the_issuer_site():
    from tradingagents.default_config import DEFAULT_CONFIG
    chain = DEFAULT_CONFIG["data_vendors"]["news_data"].split(",")
    assert chain[0] == "issuer_ir"
    assert "issuer_ir" in router.VENDOR_METHODS["get_news"]
    assert "issuer_ir" not in router.VENDOR_METHODS["get_insider_transactions"]


@pytest.mark.unit
def test_registry_block_is_picked_up_from_a_file(tmp_path):
    from tradingagents.dataflows import issuer_registry

    path = tmp_path / "registry.json"
    path.write_text(json.dumps({"schema_version": 1, "instruments": {
        "RHM.DE": {"issuer": "Rheinmetall AG", "ir": _ir()}}}), encoding="utf-8")
    record = issuer_registry.load_instrument("RHM.DE", {"issuer_registry_path": str(path)})
    assert record["ir"]["sitemap"] == "https://example.com/sitemap.xml"
    with pytest.raises(VendorNotConfiguredError):
        issuer_registry.load_instrument("NVDA", {"issuer_registry_path": str(path)})
