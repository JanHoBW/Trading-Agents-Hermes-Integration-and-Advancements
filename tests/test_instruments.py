"""The instrument record: what a security page states, and nothing more."""

from __future__ import annotations

import json

import pytest

from tradingagents import instruments


def page(*cells: str) -> str:
    return "<html><body>" + "".join(f"<td>{c}</td>" for c in cells) + "</body></html>"


KNOCK_OUT = page(
    "Hebelprodukt - WKN MK0L4Q, ISIN DE000MK0L4Q3",
    "Produkttyp", "Knock-Out Produkte",
    "Basiswert", "Kurs", "Änderung", "Ratio",
    "Rheinmetall AG", "1.048,2 €", "+3,58%", "0,10",
    "Schwelle", "Niveau", "Strike", "831,317 €", "223,68 €",
)

PUT = page(
    "Hebelprodukt - WKN FG40N8, ISIN DE000FG40N87",
    "Produkttyp", "Knock-Out Produkte",
    "Basiswert", "Kurs", "Änderung", "Ratio",
    "Oracle Corporation", "134,74 $", "0,00%", "0,10",
    "Schwelle", "Niveau", "Strike", "161,9208 $", "27,18 $",
)

LEVERAGED = page("WisdomTree Aluminium 2x Daily Leveraged", "Typ", "ETC")
ETF = page("WisdomTree Europe Defence UCITS ETF - EUR Hedged", "Währung", "Euro")
PLAIN = page("Irgendein Produkt ohne Angaben", "Typ", "Sonstiges")

UNKNOWN_ISSUER = page(
    "Hebelprodukt - WKN XX0000, ISIN DE000XX00000",
    "Produkttyp", "Knock-Out Produkte",
    "Basiswert", "Kurs", "Änderung", "Ratio",
    "Unbekannte Firma AG", "10,0 €", "+0,00%", "0,10",
    "Schwelle", "Niveau", "Strike", "8,00 €", "2,00 €",
)


@pytest.mark.unit
@pytest.mark.parametrize("raw,expected", [
    ("1.048,2 €", 1048.2),
    ("0,10", 0.1),
    ("831,317  €", 831.317),
    ("-1.257,01", -1257.01),
    ("+3,58%", 3.58),
    ("n/a", None),
    ("", None),
])
def test_number_parsing(raw, expected):
    assert instruments.parse_number(raw) == expected


@pytest.mark.unit
def test_a_knock_out_keeps_underlying_ratio_and_strike():
    record = instruments.parse_ariva(KNOCK_OUT, "MK0L4Q", as_of="2026-10-30")
    assert record.kind == "knock_out"
    assert record.underlying_name == "Rheinmetall AG"
    assert record.underlying_price == 1048.2
    assert record.underlying_currency == "EUR"
    assert record.ratio == 0.1
    assert record.barrier == 831.317
    assert record.direction == "long"


@pytest.mark.unit
def test_a_strike_above_spot_is_a_short():
    record = instruments.parse_ariva(PUT, "FG40N8", as_of="2026-10-30")
    assert record.direction == "short"
    assert record.underlying_currency == "USD"
    assert record.ratio == 0.1


@pytest.mark.unit
def test_a_daily_leveraged_etc_carries_its_leverage():
    record = instruments.parse_ariva(LEVERAGED, "A0V6ZV", as_of="2026-10-30")
    assert record.kind == "etc"
    assert record.leverage == 2.0
    assert record.reset == "daily"


@pytest.mark.unit
def test_an_etf_is_named_without_its_share_class_suffix():
    record = instruments.parse_ariva(ETF, "A40Y9K", as_of="2026-10-30")
    assert record.kind == "etf"
    assert record.name == "WisdomTree Europe Defence UCITS ETF"


@pytest.mark.unit
def test_an_unknown_page_states_no_numbers():
    record = instruments.parse_ariva(PLAIN, "XXXXXX", as_of="2026-10-30")
    assert record.kind == "unknown"
    assert record.ratio is None and record.leverage is None


@pytest.mark.unit
def test_the_underlying_symbol_comes_from_the_lookup_not_from_guessing():
    record = instruments.parse_ariva(
        KNOCK_OUT, "MK0L4Q", symbol_lookup=lambda name: "RHM.DE" if "Rheinmetall" in name else None)
    assert record.underlying == "RHM.DE"
    broken = instruments.parse_ariva(
        UNKNOWN_ISSUER, "XX0000",
        symbol_lookup=lambda name: (_ for _ in ()).throw(RuntimeError("boom")))
    assert broken.underlying is None and broken.ratio == 0.1


@pytest.mark.unit
def test_the_alias_table_fills_a_name_the_lookup_cannot_resolve():
    record = instruments.parse_ariva(KNOCK_OUT, "MK0L4Q", symbol_lookup=lambda name: None)
    assert record.underlying == "RHM.DE"


@pytest.mark.unit
def test_resolve_fetches_once_then_serves_the_cache(tmp_path):
    cache = tmp_path / "instruments.json"
    calls = []

    def fetch(key):
        calls.append(key)
        return KNOCK_OUT

    first = instruments.resolve(["mk0l4q"], cache_path=cache, fetch=fetch, today="2026-10-30")
    assert calls == ["MK0L4Q"]
    assert first["MK0L4Q"]["ratio"] == 0.1
    assert json.loads(cache.read_text())["instruments"]["MK0L4Q"]["as_of"] == "2026-10-30"

    second = instruments.resolve(["MK0L4Q"], cache_path=cache, fetch=fetch, today="2026-10-30")
    assert calls == ["MK0L4Q"], "a fresh cache entry must not be refetched"
    assert second["MK0L4Q"]["ratio"] == 0.1

    instruments.resolve(["MK0L4Q"], cache_path=cache, fetch=fetch, today="2026-10-30", force=True)
    assert calls == ["MK0L4Q", "MK0L4Q"]


@pytest.mark.unit
def test_a_stale_entry_is_refreshed_and_a_failed_fetch_keeps_the_old_one(tmp_path):
    cache = tmp_path / "instruments.json"
    instruments.resolve(["MK0L4Q"], cache_path=cache, fetch=lambda key: KNOCK_OUT, today="2026-10-30")

    def broken(key):
        raise OSError("page gone")

    kept = instruments.resolve(["MK0L4Q"], cache_path=cache, fetch=broken, today="2026-11-05")
    assert kept["MK0L4Q"]["ratio"] == 0.1
    assert kept["MK0L4Q"]["as_of"] == "2026-10-30", "a failed refresh must not restamp the record"


@pytest.mark.unit
def test_for_book_only_returns_held_instruments(tmp_path):
    cache = tmp_path / "instruments.json"
    instruments.resolve(["MK0L4Q"], cache_path=cache, fetch=lambda key: KNOCK_OUT, today="2026-10-30")
    book = {"positions": [{"ticker": "MK0L4Q", "quantity": 305}, {"ticker": "RHM.DE", "quantity": 4}]}
    records = instruments.for_book(book, cache_path=cache)
    assert set(records) == {"MK0L4Q"}
