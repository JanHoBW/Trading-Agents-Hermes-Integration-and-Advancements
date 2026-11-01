"""Guidance action regressions from a dated public issuer release."""
import pytest

from tradingagents.dataflows.vendors.issuer_facts import extract_guidance

pytestmark = pytest.mark.unit


def test_german_revenue_expectation_cut_keeps_reason_and_current_band():
    text = (
        "Aufgrund der Stornierung des F126-Projekts sinkt die Umsatzbasis. "
        "Die Umsatzerwartung des Marinebereichs und somit auch des Konzerns "
        "reduziert sich daher im laufenden Geschäftsjahr um 300 MioEUR. "
        "Der Konzern rechnet nun für das Geschäftsjahr 2026 mit einem Umsatz "
        "in der Spanne von 13,7 MrdEUR bis 14,2 MrdEUR und unverändert mit "
        "einem organischen Umsatzwachstum von 28% bis 31%."
    )
    result = extract_guidance(text)
    assert result['guidance_direction'] == 'cut'
    assert '300 MioEUR' in result['guidance_raw_sentence']
    assert 'F126' in result['guidance_reason']
    assert result['guidance_numeric_range']['low'] == 13.7
    assert result['guidance_numeric_range']['high'] == 14.2


def test_now_alone_still_does_not_establish_raise():
    result = extract_guidance(
        "The company now expects sales in the range of EUR 13.7 to EUR 14.2 billion."
    )
    assert result['guidance_direction'] == 'unspecified'
