"""IR facts from the issuer's own overview page (pilot: Rheinmetall).

The vendor exists because two failure modes keep recurring on IR sites: the key
figures are animated client-side (so the DOM yields scrambled digits), and the
figures carry a reference date and a perimeter that news scraping drops. These
tests pin the parts that must not regress: the generic JSON walk, unit/label
normalisation, guidance extraction, the delta against the stored state, the
config gate, and the routing contract (optional category, degrades instead of
failing a run).
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from unittest import mock

import pytest

from tradingagents.dataflows import router
from tradingagents.dataflows.errors import VendorNotConfiguredError, VendorUnavailableError
from tradingagents.dataflows.vendors import issuer_facts, news_supplement

# Shaped like the Rheinmetall CMS snippet: facts nested under frame/areablock
# layers, footnotes in a wysiwyg field, no flat "figures" key anywhere.
SNIPPET = {
    "id": 53834,
    "locale": "de",
    "elements": {
        "areablock": [
            {"type": "frame", "data": {"frameElements": [
                {"type": "facts", "data": {"facts": [
                    {"data": {"title": "MrdEUR Auftragsbestand*", "value": "63,8"}},
                    {"data": {"title": "MrdEUR Umsatz*", "value": "9,9"}},
                    {"data": {"title": "Operative Marge*", "value": "18,5%"}},
                ]}},
                {"type": "facts", "data": {"facts": [
                    {"data": {"title": "EUR Dividende", "value": "11,50"}},
                    {"data": {"title": "Beschäftigte**", "value": "~34.000"}},
                ]}},
                {"type": "wysiwyg", "data": {
                    "wysiwyg": "<p>* Stand: 31.12.2025 (Fortgeführte Aktivitäten)</p>",
                }},
            ]}},
        ],
    },
}

QUOTE = {"id": 1, "price": 1036.6, "currency": "EUR", "changeAbsolute": "24.60",
         "changeRelative": "2.43%", "date": "2026-10-29 09:28:00", "exchange": "Xetra"}

RELEASE = (
    "Die Gesellschaft rechnet für das Geschäftsjahr 2026 mit einem Umsatz in der Spanne "
    "von 13,7 MrdEUR bis 14,2 MrdEUR und einer operativen Ergebnismarge von rund 19%. "
    "Der Auftragsbestand lag bei 63.800 MioEUR."
)


def _facts_block(**over):
    block = {
        "enabled": True,
        "terms_checked": "2026-10-29: robots allows the IR pages",
        "facts_json": "https://example.com/api/kennzahlen",
        "quote_json": "https://example.com/api/stock-teaser",
        "overview_url": "https://example.com/de/investor-relations/ueberblick",
        "perimeter_note": "fortgeführte Aktivitäten",
        "calendar": [{"date": "2026-11-05", "event": "Q3-Bericht", "place": "Online"}],
        "calendar_verified": date.today().isoformat(),
    }
    block.update(over)
    return block


@pytest.fixture
def instrument():
    return {"issuer": "Rheinmetall AG", "ticker": "RHM.DE", "ir_facts": _facts_block()}


@pytest.fixture
def patched(monkeypatch, instrument):
    """Registry lookup and HTTP replaced; state lives in the test cache dir."""
    monkeypatch.setattr(issuer_facts, "load_instrument", lambda ticker, config=None: instrument)
    calls: dict[str, object] = {}

    def fake_fetch(url):
        calls.setdefault("urls", []).append(url)
        if url.endswith("kennzahlen"):
            return SNIPPET, json.dumps(SNIPPET, ensure_ascii=False)
        return QUOTE, json.dumps(QUOTE)

    monkeypatch.setattr(issuer_facts, "_fetch_json", fake_fetch)
    return calls


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_walk_facts_finds_nested_title_value_pairs():
    pairs = issuer_facts.walk_facts(SNIPPET)
    assert [p["label"] for p in pairs] == [
        "MrdEUR Auftragsbestand*", "MrdEUR Umsatz*", "Operative Marge*",
        "EUR Dividende", "Beschäftigte**",
    ]


@pytest.mark.unit
@pytest.mark.parametrize("title, value, label, expected", [
    ("MrdEUR Auftragsbestand*", "63,8", "Auftragsbestand", "63,8 Mrd EUR"),
    ("EUR Dividende", "11,50", "Dividende", "11,50 EUR"),
    ("Operative Marge*", "18,5%", "Operative Marge", "18,5%"),
    ("Beschäftigte**", "~34.000", "Beschäftigte", "~34.000"),
    ("Standorte", "161", "Standorte", "161"),
])
def test_normalize_fact_moves_the_unit_into_the_value(title, value, label, expected):
    assert issuer_facts.normalize_fact(title, value) == (label, expected)


@pytest.mark.unit
def test_extract_quote_matches_by_shape():
    quote = issuer_facts.extract_quote({"wrapper": [QUOTE]})
    assert quote["price"] == 1036.6
    assert quote["exchange"] == "Xetra"
    assert issuer_facts.extract_quote({"nothing": 1}) == {}


@pytest.mark.unit
def test_extract_guidance_reads_range_margin_and_backlog():
    guidance = issuer_facts.extract_guidance(RELEASE)
    assert guidance["guidance_range"] == "13,7 bis 14,2 Mrd"
    assert guidance["guidance_margin"] == "19 %"
    assert guidance["backlog"] == "63.800 Mio"
    assert "Spanne" in guidance["guidance_sentence"]


@pytest.mark.unit
def test_extract_guidance_returns_nothing_when_the_text_states_nothing():
    assert issuer_facts.extract_guidance("Wir haben eine neue Halle eröffnet.") == {}


@pytest.mark.unit
def test_guess_reference_date_reads_the_german_footnote():
    assert issuer_facts._guess_reference_date(json.dumps(SNIPPET)) == "31.12.2025"


# ---------------------------------------------------------------------------
# Delta
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_diff_reports_only_changes_and_names_both_sides():
    old = {"facts": {"Umsatz": "9,9 Mrd EUR", "Dividende": "11,50 EUR"},
           "guidance": {"guidance_range": "14,0 bis 14,5 Mrd"},
           "calendar": [{"date": "2026-11-05", "event": "Q3-Bericht"}],
           "quote": {"price": 1021.0}}
    new = {"facts": {"Umsatz": "9,9 Mrd EUR", "Dividende": "12,50 EUR", "Standorte": "161"},
           "guidance": {"guidance_range": "13,7 bis 14,2 Mrd"},
           "calendar": [{"date": "2026-11-12", "event": "Q3-Bericht"}],
           "quote": {"price": 1036.6}}
    changes = issuer_facts.diff_facts(old, new)
    joined = "\n".join(changes)
    assert "GEAENDERT: Dividende: 11,50 EUR -> 12,50 EUR" in joined
    assert "NEU: Standorte = 161" in joined
    assert "GEAENDERT: guidance_range: 14,0 bis 14,5 Mrd -> 13,7 bis 14,2 Mrd" in joined
    assert "KALENDER NEU: 2026-11-12 — Q3-Bericht" in joined
    assert "KALENDER ENTFERNT: 2026-11-05 — Q3-Bericht" in joined
    assert "KURS: 1,021.00 -> 1,036.60" in joined
    assert not any("Umsatz" in c for c in changes)   # unchanged facts stay silent


@pytest.mark.unit
def test_diff_is_silent_without_a_previous_state():
    assert issuer_facts.diff_facts(None, {"facts": {"Umsatz": "9,9"}}) == []


# ---------------------------------------------------------------------------
# Gate
# ---------------------------------------------------------------------------

@pytest.mark.unit
@pytest.mark.parametrize("block, message", [
    (None, "no IR facts source enabled"),
    ({"enabled": False}, "no IR facts source enabled"),
    (_facts_block(terms_checked="  "), "no recorded terms check"),
])
def test_config_gate_refuses_an_unchecked_source(block, message):
    with pytest.raises(VendorNotConfiguredError, match=message):
        issuer_facts._facts_config({"ir_facts": block} if block else {}, "TEST.DE")


@pytest.mark.unit
def test_config_gate_accepts_the_release_text_fallback_without_a_facts_endpoint():
    block = _facts_block(facts_json="")
    instrument = {"ir_facts": block,
                  "ir": {"enabled": True, "sitemap": "https://example.com/sitemap.xml",
                         "url_patterns": ["/news/"]}}
    cfg = issuer_facts._facts_config(instrument, "TEST.DE")
    assert cfg["facts_json"] == ""


@pytest.mark.unit
def test_config_gate_refuses_http_endpoints():
    with pytest.raises(VendorNotConfiguredError, match="HTTPS"):
        issuer_facts._fetch_json("http://example.com/api.json")


# ---------------------------------------------------------------------------
# Collection and rendering
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_collect_builds_labels_quote_and_reference_date(patched):
    facts = issuer_facts.collect_ir_facts("RHM.DE")
    assert facts["facts"] == {
        "Auftragsbestand": "63,8 Mrd EUR",
        "Umsatz": "9,9 Mrd EUR",
        "Operative Marge": "18,5%",
        "Dividende": "11,50 EUR",
        "Beschäftigte": "~34.000",
    }
    assert facts["reference_date"] == "31.12.2025"   # read from the footnote
    assert facts["quote"]["price"] == 1036.6
    assert facts["calendar"][0]["event"] == "Q3-Bericht"


@pytest.mark.unit
def test_render_carries_reference_date_perimeter_and_the_no_quarter_warning(patched):
    block = issuer_facts.get_ir_facts("RHM.DE")
    assert "Reference date: 31.12.2025" in block
    assert "fortgeführte Aktivitäten" in block
    assert "Do not treat the reference date as the current quarter" in block
    assert "- Auftragsbestand: 63,8 Mrd EUR" in block
    assert "2026-11-05: Q3-Bericht (Online)" in block


@pytest.mark.unit
def test_second_read_reports_no_change_and_a_third_reports_the_delta(patched, monkeypatch):
    issuer_facts.get_ir_facts("RHM.DE")            # seeds the stored state
    assert "No change since the last stored state" in issuer_facts.get_ir_facts("RHM.DE")

    changed = json.loads(json.dumps(SNIPPET))
    changed["elements"]["areablock"][0]["data"]["frameElements"][0]["data"]["facts"][1]["data"]["value"] = "8,8"
    monkeypatch.setattr(issuer_facts, "_fetch_json",
                        lambda url: (changed, json.dumps(changed)) if url.endswith("kennzahlen")
                        else (QUOTE, json.dumps(QUOTE)))
    late = issuer_facts.get_ir_facts("RHM.DE")
    assert "GEAENDERT: Umsatz: 9,9 Mrd EUR -> 8,8 Mrd EUR" in late


@pytest.mark.unit
def test_historical_run_does_not_overwrite_the_delta_basis(patched):
    issuer_facts.get_ir_facts("RHM.DE")
    before = issuer_facts.load_state("RHM.DE")
    issuer_facts.get_ir_facts("RHM.DE", as_of="2025-01-02")
    assert issuer_facts.load_state("RHM.DE") == before


@pytest.mark.unit
def test_release_text_fallback_extracts_guidance(monkeypatch, instrument):
    instrument["ir_facts"] = _facts_block(facts_json="")
    instrument["ir"] = {"enabled": True, "sitemap": "https://example.com/sitemap.xml",
                        "url_patterns": ["/news/"]}
    monkeypatch.setattr(issuer_facts, "load_instrument", lambda t, config=None: instrument)
    monkeypatch.setattr(issuer_facts, "_latest_release_text",
                        lambda inst: ("2026-08-06", "https://example.com/news/release", RELEASE))
    facts = issuer_facts.collect_ir_facts("RHM.DE")
    assert facts["facts"] == {}
    assert facts["guidance"]["guidance_range"] == "13,7 bis 14,2 Mrd"
    assert facts["guidance_date"] == "2026-08-06"
    block = issuer_facts.render_ir_facts(facts)
    assert "Guidance (extracted from release text dated 2026-08-06)" in block


@pytest.mark.unit
def test_unreadable_facts_endpoint_is_a_gap_not_a_crash(monkeypatch, instrument):
    monkeypatch.setattr(issuer_facts, "load_instrument", lambda t, config=None: instrument)
    monkeypatch.setattr(issuer_facts, "_fetch_json",
                        mock.Mock(side_effect=VendorUnavailableError("boom")))
    with pytest.raises(VendorUnavailableError):
        issuer_facts.collect_ir_facts("RHM.DE")


# ---------------------------------------------------------------------------
# Routing contract
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_router_serves_the_facts_as_an_optional_category(patched):
    text = router.route_to_vendor("get_ir_facts", "RHM.DE", None)
    assert "IR facts: Rheinmetall AG" in text


@pytest.mark.unit
def test_router_degrades_instead_of_failing_for_an_unconfigured_ticker(monkeypatch):
    monkeypatch.setattr(issuer_facts, "load_instrument",
                        mock.Mock(side_effect=VendorNotConfiguredError("no issuer identity")))
    text = router.route_to_vendor("get_ir_facts", "XXXX.DE", None)
    assert text.startswith("DATA_UNAVAILABLE: no configured vendor covers get_ir_facts")


# ---------------------------------------------------------------------------
# Facts travel through the news-supplement channel too
# ---------------------------------------------------------------------------

def _snapshot(tmp_path: Path, facts: dict | None = None, items: list | None = None) -> Path:
    path = tmp_path / "supp.json"
    path.write_text(json.dumps({
        "schema_version": 1, "ticker": "RHM.DE", "as_of": "2026-11-03",
        "items": items or [], "facts": facts,
    }), encoding="utf-8")
    return path


@pytest.mark.unit
def test_facts_only_snapshot_is_served_without_a_headline_window(tmp_path, monkeypatch):
    facts = {
        "issuer": "Rheinmetall AG", "reference_date": "31.12.2025",
        "perimeter": "fortgeführte Aktivitäten",
        "figures": [{"label": "Auftragsbestand", "value": "63,8 Mrd EUR",
                     "source_url": "https://www.rheinmetall.com/api/kennzahlen"}],
        "guidance": "Umsatz 13,7 bis 14,2 Mrd EUR", "calendar": [],
    }
    path = _snapshot(tmp_path, facts=facts)
    monkeypatch.setattr("tradingagents.dataflows.vendors.news_supplement.get_config",
                        lambda: {"news_supplement_path": str(path)})
    text = news_supplement.get_news_supplement("RHM.DE", "2026-10-27", "2026-11-03")
    assert "Issuer facts supplied with this snapshot" in text
    assert "- Auftragsbestand: 63,8 Mrd EUR" in text
    assert "never present a prior-year reference date as the current quarter" in text
    assert "only issuer facts" in text


@pytest.mark.unit
def test_facts_without_a_reference_date_are_rejected(tmp_path, monkeypatch):
    path = _snapshot(tmp_path, facts={"figures": []})
    monkeypatch.setattr("tradingagents.dataflows.vendors.news_supplement.get_config",
                        lambda: {"news_supplement_path": str(path)})
    with pytest.raises(VendorUnavailableError, match="reference_date"):
        news_supplement.get_news_supplement("RHM.DE", "2026-10-27", "2026-11-03")
