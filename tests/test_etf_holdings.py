"""ETF top holdings in the fundamentals report.

Yahoo exposes ``funds_data.top_holdings`` token-free; the block must appear for
funds, stay out of plain equities, and never cost the rest of the report when the
extra request fails.
"""
from __future__ import annotations

from unittest import TestCase, mock

import pandas as pd

from tradingagents.dataflows import date_window
from tradingagents.dataflows.vendors.yahoo import fundamentals


def _info(nav=146.58, legal_type="Exchange Traded Fund"):
    return {
        "longName": "iShares Core MSCI World UCITS ETF",
        "currency": "EUR",
        "navPrice": nav,
        "legalType": legal_type,
        "netExpenseRatio": 0.2,
        "totalAssets": 70_000_000_000,
    }


def _holdings():
    return pd.DataFrame(
        {"Holding Percent": [0.0579, 0.0539, 0.0399], "Holding Name": ["NVIDIA", "Apple", "Microsoft"]},
        index=pd.Index(["NVDA", "AAPL", "MSFT"], name="Symbol"),
    )


class _FakeTicker:
    def __init__(self, info, holdings=None, error=None):
        self.info = info
        self.funds_data = mock.Mock()
        if error is not None:
            type(self.funds_data).top_holdings = mock.PropertyMock(side_effect=error)
        else:
            self.funds_data.top_holdings = holdings


def _run(info, holdings=None, error=None):
    fake = _FakeTicker(info, holdings, error)
    with mock.patch.object(date_window, "get_current_date", return_value="2026-10-21"), \
         mock.patch.object(fundamentals.yf, "Ticker", return_value=fake):
        return fundamentals.get_fundamentals("EUNL.DE", "2026-10-21")


class EtfHoldingsTests(TestCase):
    def test_fund_gets_ranked_holdings_without_a_fake_as_of_date(self):
        out = _run(_info(), _holdings())
        self.assertIn("ETF top holdings (3 shown", out)
        self.assertIn("- NVIDIA (NVDA): 5.79%", out)
        self.assertIn("- Apple (AAPL): 5.39%", out)
        self.assertIn("Yahoo supplies no as-of date", out)
        # ranked: the largest weight comes first
        self.assertLess(out.index("NVDA"), out.index("AAPL"))

    def test_plain_equity_gets_no_holdings_block(self):
        out = _run({"longName": "Rheinmetall AG", "currency": "EUR"}, None)
        self.assertNotIn("top holdings", out)

    def test_failed_holdings_lookup_keeps_the_rest_of_the_report(self):
        out = _run(_info(), error=RuntimeError("funds endpoint down"))
        self.assertIn("ETF NAV", out)
        self.assertNotIn("top holdings", out)

    def test_empty_holdings_frame_is_omitted(self):
        out = _run(_info(), pd.DataFrame())
        self.assertNotIn("top holdings", out)

    def test_missing_weight_column_is_omitted(self):
        out = _run(_info(), pd.DataFrame({"Something": [1.0]}, index=pd.Index(["X"])))
        self.assertNotIn("top holdings", out)

    def test_holdings_are_capped_and_truncation_is_stated(self):
        many = pd.DataFrame(
            {"Holding Percent": [0.01] * 14},
            index=pd.Index([f"S{i}" for i in range(14)], name="Symbol"),
        )
        out = _run(_info(), many)
        self.assertIn("14 shown", out)
        self.assertIn("4 further holding(s) not shown", out)
        self.assertEqual(out.count("- S"), 10)
