"""UReg capital-market disclosures: category filter, label mapping, DE/EN merge.

The category ids in ``CAPITAL_MARKET_CATEGORIES`` were observed live against
three issuers; these tests pin the behaviour that does not depend on the
network — filtering, merging, point-in-time, and the honest empty answer.
"""
from __future__ import annotations

from unittest import TestCase, mock

import pytest

from tradingagents.dataflows import router
from tradingagents.dataflows.vendors import ureg_pdmr

AD_HOC = ureg_pdmr.CAPITAL_MARKET_CATEGORIES[79][0]
VOTING = ureg_pdmr.CAPITAL_MARKET_CATEGORIES[81][0]


def _row(date, cat, lang, type_id, company="Rheinmetall Aktiengesellschaft", payload="p"):
    return {
        "disclosure_date": date, "company": company, "language": lang,
        "payload": payload, "category": cat, "type": type_id,
        "title": f"{cat} title {lang}",
    }


class MergeTranslationsTests(TestCase):
    def test_german_twin_wins_and_merge_is_counted(self):
        rows = [_row("2026-09-25", 81, "en", 907), _row("2026-09-25", 81, "de", 907)]
        kept, merged = ureg_pdmr._merge_translations(rows)
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["language"], "de")
        self.assertEqual(merged, 1)

    def test_distinct_types_on_one_day_stay_separate(self):
        rows = [_row("2026-07-16", 81, "de", 907), _row("2026-07-16", 81, "de", 908)]
        kept, merged = ureg_pdmr._merge_translations(rows)
        self.assertEqual(len(kept), 2)
        self.assertEqual(merged, 0)

    def test_newest_first(self):
        rows = [_row("2026-01-02", 79, "de", 902), _row("2026-05-04", 79, "de", 902)]
        kept, _ = ureg_pdmr._merge_translations(rows)
        self.assertEqual([r["disclosure_date"] for r in kept], ["2026-05-04", "2026-01-02"])


class CapitalMarketVendorTests(TestCase):
    def _call(self, rows, **kwargs):
        kwargs.setdefault("ticker", "RHM.DE")
        with mock.patch.object(ureg_pdmr, "_load_instrument", return_value={
                "ticker": "RHM.DE", "ureg_query_name": "Rheinmetall AG",
                "ureg_entity_name": "Rheinmetall Aktiengesellschaft"}), \
             mock.patch.object(ureg_pdmr, "_search", return_value=rows) as search, \
             mock.patch.object(ureg_pdmr, "fetch_detail_text", return_value="excerpt"):
            out = ureg_pdmr.get_ureg_capital_market_disclosures(**kwargs)
        return out, search

    def test_all_categories_by_default(self):
        _out, search = self._call([_row("2026-08-05", 81, "de", 907)])
        self.assertEqual(search.call_args.kwargs["wanted"], set(ureg_pdmr.CAPITAL_MARKET_CATEGORIES))

    def test_category_name_filter_selects_the_right_id(self):
        _out, search = self._call([_row("2026-08-05", 81, "de", 907)], categories=[AD_HOC])
        self.assertEqual(search.call_args.kwargs["wanted"], {79})

    def test_unknown_category_name_is_reported_not_ignored(self):
        out, _search = self._call([_row("2026-08-05", 79, "de", 902)], categories=["dividends"])
        self.assertIn("ignored unknown category name(s): dividends", out)

    def test_entity_validation_drops_other_companies(self):
        rows = [_row("2026-08-05", 81, "de", 907),
                _row("2026-08-05", 81, "de", 907, company="Rheinmetall MAN Military Vehicles GmbH")]
        out, _search = self._call(rows)
        self.assertIn("1 row(s) of other matched companies excluded", out)

    def test_point_in_time_filter(self):
        rows = [_row("2026-08-05", 81, "de", 907), _row("2026-10-15", 81, "de", 908)]
        out, _search = self._call(rows, as_of_date="2026-09-01")
        self.assertIn("2026-08-05", out)
        self.assertNotIn("2026-10-15", out)

    def test_empty_window_is_an_absence_in_this_source_only(self):
        out, _search = self._call([], as_of_date="2026-09-01")
        self.assertIn("Result: none on record", out)
        self.assertIn("absence in this source only", out)

    def test_detail_failure_does_not_lose_the_disclosure(self):
        with mock.patch.object(ureg_pdmr, "_load_instrument", return_value={
                "ticker": "RHM.DE", "ureg_query_name": "Rheinmetall AG",
                "ureg_entity_name": "Rheinmetall Aktiengesellschaft"}), \
             mock.patch.object(ureg_pdmr, "_search", return_value=[_row("2026-08-05", 81, "de", 907)]), \
             mock.patch.object(ureg_pdmr, "fetch_detail_text",
                               side_effect=ureg_pdmr.VendorUnavailableError("timeout")):
            out = ureg_pdmr.get_ureg_capital_market_disclosures("RHM.DE")
        self.assertIn("## 2026-08-05 — voting_rights", out)
        self.assertIn("detail fetch failed: timeout", out)

    def test_labels_come_from_the_live_category_map(self):
        out, _search = self._call([_row("2026-08-05", 81, "de", 907)])
        self.assertIn("voting_rights — voting-rights notification (WpHG §40/41)", out)


class RouterWiringTests(TestCase):
    def test_method_belongs_to_the_optional_disclosure_category(self):
        self.assertEqual(router.get_category_for_method("get_capital_market_disclosures"),
                         "disclosure_data")
        self.assertIn("disclosure_data", router.OPTIONAL_CATEGORIES)

    def test_default_config_routes_it_to_ureg_capital(self):
        from tradingagents.default_config import DEFAULT_CONFIG
        vendors = DEFAULT_CONFIG["data_vendors"]
        self.assertEqual(vendors["disclosure_data"], "ureg_capital")
        self.assertIn("ureg_capital", router.VENDOR_METHODS["get_capital_market_disclosures"])

    def test_uncovered_instrument_degrades_with_a_scope_note(self):
        def not_configured(*_a, **_k):
            raise router.VendorNotConfiguredError("AAPL is not a German-equity identity")

        with mock.patch.dict(router.VENDOR_METHODS,
                             {"get_capital_market_disclosures": {"ureg_capital": not_configured}}), \
             mock.patch.object(router, "get_vendor", return_value="ureg_capital"):
            out = router.route_to_vendor("get_capital_market_disclosures", "AAPL", None)
        self.assertTrue(out.startswith("DATA_UNAVAILABLE:"))
        self.assertIn("coverage limit", out)
        self.assertIn("not evidence about the instrument", out)


@pytest.mark.unit
def test_news_analyst_is_offered_the_tool():
    from tradingagents.agents.analysts import news_analyst
    names = {tool.name for tool in news_analyst.TOOLS}
    assert "get_capital_market_disclosures" in names
