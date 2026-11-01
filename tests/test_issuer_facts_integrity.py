"""Point-in-time IR evidence and faithful guidance wording; all HTTP is isolated."""
from datetime import date

import pytest

from tradingagents.dataflows.vendors import issuer_facts as ir

pytestmark = pytest.mark.unit


def test_historical_collector_filters_future_publications_and_current_only_sources(monkeypatch):
    instrument = {"issuer": "Example", "ir_facts": {
        "enabled": True, "terms_checked": "checked",
        "facts_json": "https://example.com/current.json",
        "quote_json": "https://example.com/quote.json",
        "reference_date": "2026-12-31", "calendar_verified": "2026-10-01",
        "calendar": [{"date": "2026-12-01", "event": "results"}],
        "release_index": {"url": "https://example.com/index", "entry_regex":
                          r"(?P<title>Results) (?P<date>\d{4}-\d{2}-\d{2})"},
        "prose_facts": [{"label": "Group revenue", "regex": r"Group revenue (?P<v>\d+)"}],
    }}
    monkeypatch.setattr(ir, "load_instrument", lambda *a: instrument)
    monkeypatch.setattr(ir, "_fetch_json", lambda *a: pytest.fail("current-only endpoint fetched"))
    monkeypatch.setattr(ir, "_fetch_text", lambda *a:
                        "Results 2026-09-01 Group revenue 999 Results 2026-05-01 Group revenue 20")
    facts = ir.collect_ir_facts("XYZ", as_of="2026-06-01")
    assert facts["as_of"] == "2026-06-01"
    assert facts["reference_date"] == "2026-05-01"
    assert facts["facts"] == {"Group revenue": "20"}
    assert [e["date"] for e in facts["latest_publications"]] == ["2026-05-01"]
    assert facts["quote"] == {} and facts["calendar"] == []
    assert "current-only" in " ".join(facts["historical_unavailable"])


def test_historical_public_api_neither_reads_nor_writes_current_delta(monkeypatch, tmp_path):
    instrument = {"ir_facts": {"enabled": True, "terms_checked": "checked",
                               "facts_json": "https://example.com/current"}}
    monkeypatch.setattr(ir, "load_instrument", lambda *a: instrument)
    monkeypatch.setattr(ir, "_fetch_json", lambda *a: pytest.fail("current-only fetch"))
    monkeypatch.setattr(ir, "get_config", lambda: {"data_cache_dir": str(tmp_path)})
    ir.save_state("XYZ", {"as_of": "2026-10-01", "facts": {"Future": "999"}})
    path = ir._state_path("XYZ")
    before = (path.read_bytes(), path.stat().st_mtime_ns)
    monkeypatch.setattr(ir, "load_state", lambda *a: pytest.fail("historical delta read"))
    block = ir.get_ir_facts("XYZ", "2025-01-01")
    assert "current-only" in block
    assert "999" not in block and "CHANGES" not in block and "No change" not in block
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before


@pytest.mark.parametrize("helper", ["_guidance_from_releases", "_latest_release_text"])
def test_sitemap_selection_defensively_filters_future_sources(monkeypatch, helper):
    instrument = {"ir": {"enabled": True, "sitemap": "https://example.com/map",
                          "url_patterns": ["/results/"]}}
    entries = [{"date": "2026-09-01", "url": "https://example.com/de/results/future"},
               {"date": "2026-05-01", "url": "https://example.com/de/results/old"}]
    monkeypatch.setattr(ir.issuer_ir, "_sitemap_urls", lambda *a: [])
    monkeypatch.setattr(ir.issuer_ir, "dated_entries", lambda *a: entries)
    monkeypatch.setattr(ir.issuer_ir, "_allowed", lambda *a: True)
    def fetch(url):
        assert "future" not in url
        return "Results", "Der Konzern erwartet eine Marge von 19 %.", []
    monkeypatch.setattr(ir.issuer_ir, "fetch_article", fetch)
    result = getattr(ir, helper)(instrument, as_of="2026-06-01")
    assert "2026-05-01" in result


RHM_CUT = (
    "The naval business is affected by the cancellation of the F126 frigate programme. "
    "As a result, the sales forecast for the naval business – and consequently for the Group "
    "as a whole – is being reduced by €300 million in the current fiscal year. "
    "The Group now expects sales for the 2026 fiscal year to be in the range of €13.7 billion "
    "to €14.2 billion, with organic sales growth remaining unchanged at between 28% and 31%."
)
ENR_CONFIRM = (
    "Siemens Energy confirms its fiscal year 2026 outlook, which was raised after the end "
    "of the first half-year, and expects its Profit margin before Special Items to be "
    "towards the upper end of the guided range."
)


def test_rhm_now_range_preserves_explicit_300m_reduction_and_reason():
    guidance = ir.extract_guidance(RHM_CUT)
    assert guidance["guidance_direction"] == "cut"
    assert guidance["guidance_numeric_range"] == {"low": 13.7, "high": 14.2,
                                                  "unit": "billion", "currency": "EUR"}
    assert "F126" in guidance["guidance_reason"]
    assert "€300 million" in guidance["guidance_raw_sentence"]
    rendered = ir.render_ir_facts({"ticker": "RHM.DE", "guidance": guidance})
    assert "guidance_direction: cut" in rendered and "F126" in rendered


def test_enr_confirmation_is_evidence_without_a_repeated_numeric_range():
    guidance = ir.extract_guidance(ENR_CONFIRM)
    assert guidance["guidance_direction"] == "confirm"
    assert "guidance_numeric_range" not in guidance
    assert guidance["guidance_raw_sentence"] == ENR_CONFIRM
    assert "upper end" in guidance["guidance_sentence"]


@pytest.mark.parametrize("sentence,direction", [
    ("The Group now expects revenue in the range of €13.7 billion to €14.2 billion.", "unspecified"),
    ("Der Konzern erhöht seine Prognose auf 14,0 bis 14,5 Mrd EUR.", "raise"),
    ("Der Konzern bestätigt seine Prognose für das Geschäftsjahr.", "confirm"),
])
def test_guidance_direction_requires_explicit_wording(sentence, direction):
    assert ir.extract_guidance(sentence)["guidance_direction"] == direction


def test_configured_release_facts_keep_date_perimeter_and_raw_evidence(monkeypatch):
    patterns = [
        {"label": "Group backlog", "regex": r"Group backlog at (?P<date>\d{4}-\d{2}-\d{2}) was EUR(?P<v>\d+)bn",
         "suffix": " billion EUR", "perimeter_note": "group; includes framework call-offs"},
        {"label": "Group FCF pre tax", "regex": r"Group free cash flow pre tax was EUR(?P<v>\d+)m",
         "suffix": " million EUR", "perimeter_note": "group; pre tax", "reference_date": "2026-06-30"},
        {"label": "Future backlog", "regex": r"Future backlog (?P<v>\d+)", "reference_date": "2027-01-01"},
    ]
    instrument = {"ir_facts": {"enabled": True, "terms_checked": "checked",
                               "facts_json": "https://example.com/annual", "prose_facts": patterns},
                  "ir": {"enabled": True, "sitemap": "https://example.com/map",
                         "url_patterns": ["/news/"]}}
    monkeypatch.setattr(ir, "load_instrument", lambda *a: instrument)
    monkeypatch.setattr(ir, "_fetch_json", lambda *a: (
        [{"title": "Backlog FY2025", "value": "10"}], 'Stand: 31.12.2025'))
    entries = [{"date": "2026-08-01", "url": "https://example.com/de/news/2026-08-01-results"},
               {"date": "2026-05-01", "url": "https://example.com/de/news/2026-05-01-results"}]
    monkeypatch.setattr(ir.issuer_ir, "_sitemap_urls", lambda *a: [])
    monkeypatch.setattr(ir.issuer_ir, "dated_entries", lambda *a: entries)
    monkeypatch.setattr(ir.issuer_ir, "_allowed", lambda *a: True)
    def fetch(url):
        if "08-01" in url:
            return "Results", ("Segment backlog was EUR999bn. Group backlog at 2026-06-30 was EUR20bn. "
                               "Group free cash flow pre tax was EUR2319m. Future backlog 666."), []
        return "Results", "The Group confirms its outlook. Group backlog at 2026-03-31 was EUR15bn.", []
    monkeypatch.setattr(ir.issuer_ir, "fetch_article", fetch)
    facts = ir.collect_ir_facts("XYZ", as_of=date.today().isoformat())
    assert facts["facts"] == {"Backlog FY2025": "10"}
    assert facts["reference_date"] == "31.12.2025"
    assert facts["release_facts"] == {"Group backlog": "20 billion EUR", "Group FCF pre tax": "2319 million EUR"}
    evidence = facts["release_fact_evidence"]
    assert evidence[0]["reference_date"] == "2026-06-30"
    assert evidence[0]["publication_date"] == "2026-08-01"
    assert "framework" in evidence[0]["perimeter_note"]
    assert "Group backlog" in evidence[0]["raw_sentence"]
    assert "999" not in str(evidence) and "666" not in str(evidence)
    assert facts["guidance_date"] == "2026-05-01"
    block = ir.render_ir_facts(facts)
    assert "2026-06-30" in block and "group; pre tax" in block


def test_historical_sitemap_collector_uses_results_not_newer_order_news(monkeypatch):
    instrument = {"ir_facts": {"enabled": True, "terms_checked": "checked",
                               "facts_json": "https://example.com/current"},
                  "ir": {"enabled": True, "sitemap": "https://example.com/map",
                         "url_patterns": ["/news/"]}}
    monkeypatch.setattr(ir, "load_instrument", lambda *a: instrument)
    monkeypatch.setattr(ir, "_fetch_json", lambda *a: pytest.fail("current-only"))
    monkeypatch.setattr(ir.issuer_ir, "_sitemap_urls", lambda *a: [])
    monkeypatch.setattr(ir.issuer_ir, "dated_entries", lambda *a: [
        {"date": "2026-05-30", "url": "https://example.com/de/news/order"},
        {"date": "2026-05-01", "url": "https://example.com/de/news/results"}])
    monkeypatch.setattr(ir.issuer_ir, "_allowed", lambda *a: True)
    monkeypatch.setattr(ir.issuer_ir, "fetch_article", lambda url: (
        "News", ENR_CONFIRM if "results" in url else "New order signed.", []))
    facts = ir.collect_ir_facts("XYZ", as_of="2026-06-01")
    assert facts["guidance"].get("guidance_direction") == "confirm"
    assert facts["guidance_date"] == "2026-05-01"


def test_historical_index_filters_before_the_entry_cap(monkeypatch):
    instrument = {"ir_facts": {"enabled": True, "terms_checked": "checked",
        "release_index": {"url": "https://example.com/index", "entry_regex":
                          r"(?P<title>Results) (?P<date>\d{4}-\d{2}-\d{2})"}}}
    monkeypatch.setattr(ir, "load_instrument", lambda *a: instrument)
    page = " ".join(f"Results 2026-09-{day:02d} Future." for day in range(1, 26))
    page += " Results 2026-05-01 " + ENR_CONFIRM
    monkeypatch.setattr(ir, "_fetch_text", lambda *a: page)
    assert ir.collect_ir_facts("XYZ", as_of="2026-06-01")["guidance_date"] == "2026-05-01"


def test_current_endpoint_future_reference_is_not_presented_as_known(monkeypatch):
    instrument = {"ir_facts": {"enabled": True, "terms_checked": "checked",
                               "facts_json": "https://example.com/current",
                               "reference_date": "2999-12-31"}}
    monkeypatch.setattr(ir, "load_instrument", lambda *a: instrument)
    monkeypatch.setattr(ir, "_fetch_json", lambda *a: ([{"title": "Future", "value": "999"}], ""))
    facts = ir.collect_ir_facts("XYZ")
    assert facts["facts"] == {} and facts["reference_date"] == ""
    assert "after as_of" in facts["facts_error"]


def test_guidance_numeric_fields_accept_german_thousands_without_crashing():
    guidance = ir.extract_guidance("Der Konzern erwartet Umsatz in der Spanne von 1.234,5 bis 1.345,5 Mio EUR.")
    assert guidance["guidance_numeric_range"]["low"] == 1234.5


def test_delta_surfaces_confirmation_without_a_numeric_range():
    old = {"guidance": {"guidance_direction": "raise"}}
    new = {"guidance": ir.extract_guidance(ENR_CONFIRM)}
    assert "GEAENDERT: guidance_direction: raise -> confirm" in ir.diff_facts(old, new)


def test_unparseable_range_preserves_wording_without_inventing_numeric_fields():
    sentence = "Der Konzern erwartet Umsatz in der Spanne von 1.2.3 bis 4.5.6 Mio EUR."
    guidance = ir.extract_guidance(sentence)
    assert "guidance_numeric_range" not in guidance
    assert guidance["guidance_raw_sentence"] == sentence
