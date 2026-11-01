"""Public IR pilot wording: exercise the generic, registry-driven seam only."""
from tradingagents.dataflows.vendors.issuer_facts import (
    extract_guidance,
    extract_release_facts,
    parse_release_index,
)

RHM_BACKLOG = {
    "label": "Rheinmetall Backlog Konzern (inklusive erwarteter Rahmenabrufe)",
    "regex": r"Der Rheinmetall Backlog zum 30\. Juni 2026 erhöhte sich deutlich auf (?P<v>[\d,]+) MrdEUR[^.]{0,90}\. Der Backlog umfasst neben dem Auftragsbestand auch die erwarteten Abrufe aus bestehenden Rahmenverträgen\.",
    "suffix": " Mrd EUR",
    "reference_date": "2026-06-30",
    "perimeter_note": "Konzern inklusive erwarteter Rahmenabrufe; keine ausschliesslich festen Auftraege",
}
RHM_TEXT = (
    "Der Rheinmetall Backlog zum 30. Juni 2026 erhöhte sich deutlich auf 80,5 MrdEUR "
    "(Vorjahr: 56,0 MrdEUR). Der Backlog umfasst neben dem Auftragsbestand auch die "
    "erwarteten Abrufe aus bestehenden Rahmenverträgen."
)
ENR_CONFIRMATION = (
    "Siemens Energy confirms its fiscal year 2026 outlook, which was raised after "
    "the end of the first half-year, and expects its Profit margin before Special "
    "Items to be towards the upper end of the guided range."
)


def test_backlog_has_measurement_and_publication_dates_and_basis():
    evidence = extract_release_facts(
        RHM_TEXT, [RHM_BACKLOG], publication_date="2026-08-06",
        source_url="https://www.rheinmetall.com/de/media/news-watch/news/2026/08/2026-08-06-rheinmetall-pressemitteilung-halbjahresfinanzbericht-h1",
        as_of="2026-11-01",
    )
    assert len(evidence) == 1
    assert evidence[0]["value"] == "80,5 Mrd EUR"
    assert evidence[0]["reference_date"] == "2026-06-30"
    assert evidence[0]["publication_date"] == "2026-08-06"
    assert "Rahmen" in evidence[0]["raw_sentence"]
    assert "Konzern" in evidence[0]["perimeter_note"]


def test_measurement_date_does_not_backdate_release_availability():
    assert extract_release_facts(
        RHM_TEXT, [RHM_BACKLOG], publication_date="2026-08-06",
        source_url="https://www.rheinmetall.com/", as_of="2026-06-30",
    ) == []


def test_confirmation_without_numbers_is_not_a_new_raise():
    guidance = extract_guidance(ENR_CONFIRMATION)
    assert guidance.get("guidance_direction") == "confirm"
    assert guidance.get("guidance_raw_sentence") == ENR_CONFIRMATION
    assert "guidance_range" not in guidance
    assert "upper end" in (guidance.get("guidance_sentence") or "")


def test_index_entry_body_stops_before_next_publication():
    html = (
        '<entry title="Q3 Earnings Release" date="2026-08-05">'
        + ENR_CONFIRMATION
        + '<entry title="Unrelated release" date="2026-08-25">Wrong figure 999'
    )
    entries = parse_release_index(html, {
        "entry_regex": r'<entry title="(?P<title>[^"]+)" date="(?P<date>\d{4}-\d{2}-\d{2})">',
        "date_format": "%Y-%m-%d", "block_chars": 15000,
    }, as_of="2026-08-05")
    assert len(entries) == 1
    assert entries[0]["date"] == "2026-08-05"
    assert "999" not in entries[0]["text"]
    assert extract_guidance(entries[0]["text"]).get("guidance_direction") == "confirm"
