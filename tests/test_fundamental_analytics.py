"""Offline period-aware analytics; all values and instrument identities are synthetic."""

import json

import pytest

from tradingagents.dataflows.fundamental_analytics import analyze_fundamentals
from tradingagents.dataflows.vendors import sec_edgar

pytestmark = pytest.mark.unit


def fact(start, end, value, filed, accession="filing-1", **extra):
    return {
        "start": start,
        "end": end,
        "val": value,
        "filed": filed,
        "accn": accession,
        "form": "10-Q",
        "fy": 2026,
        "fp": "Q3",
        **extra,
    }


def sec_payload(monkeypatch, series):
    monkeypatch.setattr(sec_edgar, "cik_for", lambda ticker: "0000000001")
    monkeypatch.setattr(
        sec_edgar,
        "_cached_json",
        lambda *args: {
            "facts": {
                "us-gaap": {tag: {"units": {"USD": values}} for tag, values in series.items()}
            }
        },
    )


def block(text, marker="FUNDAMENTAL_ANALYTICS"):
    prefix = f"# {marker}: "
    lines = [line[len(prefix) :] for line in text.splitlines() if line.startswith(prefix)]
    assert len(lines) == 1, f"missing unique {marker} block"
    return json.loads(lines[0])


def test_sec_cashflow_ytd_becomes_comparable_quarters_in_real_output(monkeypatch):
    ends = ["2026-03-31", "2026-06-30", "2026-09-30"]
    cfo = [
        fact("2026-01-01", end, val, "2026-10-20")
        for end, val in zip(ends, [30_000_000, 80_000_000, 150_000_000], strict=True)
    ]
    capex = [
        fact("2026-01-01", end, val, "2026-10-20")
        for end, val in zip(ends, [10_000_000, 30_000_000, 60_000_000], strict=True)
    ]
    sec_payload(
        monkeypatch,
        {
            "NetCashProvidedByUsedInOperatingActivities": cfo,
            "PaymentsToAcquirePropertyPlantAndEquipment": capex,
        },
    )
    out = sec_edgar.get_cashflow("SYNTH", "quarterly", "2026-10-30")
    analytics = block(out)
    assert [
        (r["start"], r["end"], r["cfo"], r["capex"], r["fcf"]) for r in analytics["periods"]
    ] == [
        ("2026-01-01", ends[0], 30_000_000, 10_000_000, 20_000_000),
        ("2026-04-01", ends[1], 50_000_000, 20_000_000, 30_000_000),
        ("2026-07-01", ends[2], 70_000_000, 30_000_000, 40_000_000),
    ]
    records = block(out, "PERIOD_FACTS")
    assert all(r["filed"] == "2026-10-20" and r["accession"] == "filing-1" for r in records)
    assert analytics["as_of"] == "2026-10-30"
    assert analytics["ttm"]["status"] == "unavailable"


@pytest.mark.parametrize(
    "starts,ends,expected",
    [
        (
            ["2025-10-01", "2026-01-01", "2026-04-01", "2026-07-01"],
            ["2025-12-31", "2026-03-31", "2026-06-30", "2026-09-30"],
            "ok",
        ),
        (
            ["2024-10-01", "2026-01-01", "2026-04-01", "2026-07-01"],
            ["2024-12-31", "2026-03-31", "2026-06-30", "2026-09-30"],
            "unavailable",
        ),
    ],
)
def test_income_ttm_requires_four_exactly_consecutive_quarters(monkeypatch, starts, ends, expected):
    sec_payload(
        monkeypatch,
        {
            "Revenues": [
                fact(start, end, 10_000_000, "2026-10-20")
                for start, end in zip(starts, ends, strict=True)
            ]
        },
    )
    out = sec_edgar.get_income_statement("SYNTH", "quarterly", "2026-10-30")
    ttm = block(out)["ttm"]
    assert ttm["status"] == expected
    if expected == "ok":
        assert ttm["metrics"]["Revenue"]["value"] == 40_000_000
        assert ttm["start"] == starts[0] and ttm["end"] == ends[-1]
        assert block(out)["status"] == "ok"
    else:
        assert not ttm.get("metrics")


def record(metric, value, **changes):
    return {
        "metric": metric,
        "value": value,
        "start": "2026-04-01",
        "end": "2026-06-30",
        "unit": "USD",
        "filed": "2026-07-20",
        "accession": "filing-1",
        "perimeter": "total_group",
        "tax_basis": "after_tax",
        "basis": "reported",
        "capex_sign": "outflow_positive",
        **changes,
    }


@pytest.mark.parametrize(
    "denominator,changes,status",
    [
        (20, {}, "ok"),
        (0, {}, "unavailable"),
        (-20, {}, "unavailable"),
        (None, {}, "unavailable"),
        (20, {"start": "2026-01-01"}, "unavailable"),
        (20, {"unit": "EUR"}, "unavailable"),
        (20, {"perimeter": "continuing_operations"}, "unreconciled"),
        (20, {"tax_basis": "before_tax"}, "unreconciled"),
        (20, {"basis": "adjusted"}, "unreconciled"),
    ],
)
def test_conversion_uses_only_same_period_unit_positive_matching_denominator(
    denominator, changes, status
):
    records = [record("Operating Cash Flow", 50), record("Capital Expenditure", 10)]
    if denominator is not None:
        records.append(record("Net Income", denominator, **changes))
    result = analyze_fundamentals(records, "2026-08-01")["periods"][0]
    assert result["fcf"] == 40
    assert result["conversion_status"].startswith(status)
    if status == "ok":
        assert result["cfo_conversion"] == 2.5 and result["fcf_conversion"] == 2
        assert result["conversion_denominator"] == "Net Income"
    else:
        assert result["cfo_conversion"] is None and result["fcf_conversion"] is None


@pytest.mark.parametrize(
    "changes",
    [
        {"start": "2026-01-01"},
        {"unit": "EUR"},
        {"perimeter": "continuing_operations"},
        {"capex_sign": "unknown"},
    ],
)
def test_fcf_refuses_nonmatching_or_ambiguous_capex(changes):
    out = analyze_fundamentals(
        [record("Operating Cash Flow", 50), record("Capital Expenditure", 10, **changes)],
        "2026-08-01",
    )
    assert all(p["fcf"] is None for p in out["periods"])


def test_sec_cashflow_derivation_is_cutoff_safe_and_keeps_input_vintages(monkeypatch):
    prior = fact("2026-01-01", "2026-03-31", 30_000_000, "2026-04-20", "q1")
    current = fact("2026-01-01", "2026-06-30", 80_000_000, "2026-07-20", "q2")
    future = fact(
        "2026-01-01", "2026-06-30", 800_000_000, "2026-09-20", "q2-amended", form="10-Q/A"
    )
    sec_payload(
        monkeypatch, {"NetCashProvidedByUsedInOperatingActivities": [prior, current, future]}
    )
    out = sec_edgar.get_cashflow("SYNTH", "quarterly", "2026-08-01")
    assert "800000000" not in out
    assert block(out)["periods"][-1]["cfo"] == 50_000_000
    assert (
        block(out)["periods"][-1]["provenance"]["Operating Cash Flow"]["inputs"][0]["accession"]
        == "q1"
    )
    # A lone amendment cannot reconcile its cumulative total with an old Q1.
    after = sec_edgar.get_cashflow("SYNTH", "quarterly", "2026-10-01")
    assert not any(p["start"] == "2026-04-01" for p in block(after)["periods"])
    assert "unreconciled" in block(after)["limitations"]


def test_analytics_rejects_future_inputs_without_vendor_guard():
    out = analyze_fundamentals(
        [record("Operating Cash Flow", 50, filed="2027-01-01")], "2026-08-01"
    )
    assert not out["periods"]


def test_sec_annual_cashflow_completes_fourth_quarter_not_a_rolling_10q_total(monkeypatch):
    rows = [
        fact("2026-01-01", end, value, "2027-02-20", "annual", form="10-K")
        for end, value in [
            ("2026-03-31", 10_000_000),
            ("2026-06-30", 30_000_000),
            ("2026-09-30", 60_000_000),
            ("2026-12-31", 100_000_000),
        ]
    ]
    sec_payload(monkeypatch, {"NetCashProvidedByUsedInOperatingActivities": rows})
    analytics = block(sec_edgar.get_cashflow("SYNTH", "quarterly", "2027-03-01"))
    assert [p["cfo"] for p in analytics["periods"]] == [
        10_000_000,
        20_000_000,
        30_000_000,
        40_000_000,
    ]
    assert analytics["ttm"]["metrics"]["Operating Cash Flow"]["value"] == 100_000_000


@pytest.mark.parametrize(
    "quote_type,expected", [("EQUITY", "unavailable"), ("ETF", "not_applicable")]
)
def test_yahoo_overview_never_assigns_undated_forward_eps_to_fy(monkeypatch, quote_type, expected):
    from types import SimpleNamespace

    from tradingagents.dataflows.vendors.yahoo import fundamentals as yahoo

    monkeypatch.setattr(yahoo, "withhold_live_profile", lambda *args: None)
    monkeypatch.setattr(
        yahoo.yf,
        "Ticker",
        lambda *args: SimpleNamespace(
            info={
                "longName": "Synthetic",
                "currency": "USD",
                "financialCurrency": "USD",
                "quoteType": quote_type,
                "forwardEps": 9.5,
                "forwardPE": 20,
                "operatingCashflow": 50,
                "freeCashflow": 40,
                "netIncomeToCommon": 20,
            }
        ),
    )
    monkeypatch.setattr(yahoo, "_etf_top_holdings", lambda *args: [])
    out = yahoo.get_fundamentals("SYNTH", "2026-08-01")
    assert "Forward estimate year: UNKNOWN" in out
    assert "undated EPS must not be assigned to FY" in out
    assert block(out)["status"] == expected
    assert not block(out)["periods"]


def test_yahoo_statement_preserves_unknown_start_and_publication_without_derivation(monkeypatch):
    from types import SimpleNamespace

    import pandas as pd

    from tradingagents.dataflows.vendors.yahoo import fundamentals as yahoo

    data = pd.DataFrame(
        {pd.Timestamp("2026-06-30"): [50, -10]},
        index=["Operating Cash Flow", "Capital Expenditure"],
    )
    monkeypatch.setattr(yahoo, "withhold_undated_statements", lambda *args: None)
    monkeypatch.setattr(yahoo.yf, "Ticker", lambda *args: SimpleNamespace(quarterly_cashflow=data))
    out = yahoo.get_cashflow("SYNTH", "quarterly", "2026-08-01")
    assert "Capital Expenditure,-10" in out
    records = block(out, "PERIOD_FACTS")
    assert all(
        r["start"] is None and r["filed"] is None and r["duration_days"] is None for r in records
    )
    assert all(r["end"] == "2026-06-30" for r in records)
    assert block(out)["status"] == "unavailable"


def test_fundamentals_consumer_merges_cashflow_and_income_and_appends_code_block(monkeypatch):
    from langchain_core.messages import AIMessage, ToolMessage

    from tradingagents.agents.analysts import fundamentals_analyst as analyst

    sec_payload(
        monkeypatch,
        {
            "NetCashProvidedByUsedInOperatingActivities": [
                fact("2026-04-01", "2026-06-30", 50, "2026-07-20")
            ],
            "PaymentsToAcquirePropertyPlantAndEquipment": [
                fact("2026-04-01", "2026-06-30", 10, "2026-07-20")
            ],
            "NetIncomeLoss": [fact("2026-04-01", "2026-06-30", 20, "2026-07-20")],
        },
    )
    messages = [
        ToolMessage(content=fn("SYNTH", "quarterly", "2026-08-01"), name=name, tool_call_id=name)
        for fn, name in [
            (sec_edgar.get_cashflow, "get_cashflow"),
            (sec_edgar.get_income_statement, "get_income_statement"),
        ]
    ]
    captured = {}

    def turn(prompt, llm, tools, messages):
        captured["system"] = prompt.format_messages(messages=messages)[0].content
        return AIMessage(content="Synthetic report"), "Synthetic report"

    monkeypatch.setattr(analyst, "take_turn", turn)
    result = analyst.create_fundamentals_analyst(None)(
        {
            "trade_date": "2026-08-01",
            "company_of_interest": "SYNTH",
            "asset_type": "stock",
            "messages": messages,
        }
    )
    assert block(result["fundamentals_report"])["periods"][0]["fcf_conversion"] == 2
    assert 'source="deterministic fundamental analytics"' in captured["system"]
    assert "Forward estimate year is UNKNOWN" in captured["system"]
    assert "Never sum nonconsecutive quarters" in captured["system"]


def test_fundamentals_consumer_etf_block_is_not_applicable(monkeypatch):
    from langchain_core.messages import AIMessage

    from tradingagents.agents.analysts import fundamentals_analyst as analyst

    monkeypatch.setattr(analyst, "take_turn", lambda *args: (AIMessage(content="Fund"), "Fund"))
    result = analyst.create_fundamentals_analyst(None)(
        {
            "trade_date": "2026-08-01",
            "company_of_interest": "SYNTH",
            "asset_type": "etf",
            "messages": [],
        }
    )
    assert block(result["fundamentals_report"])["status"] == "not_applicable"


def test_consumer_ignores_malformed_nonfinite_and_non_tool_period_payloads():
    from langchain_core.messages import AIMessage, ToolMessage

    from tradingagents.dataflows.fundamental_analytics import records_from_messages

    valid = record("Operating Cash Flow", 50)
    text = (
        "# PERIOD_FACTS: "
        + json.dumps(
            [
                valid,
                {},
                record("Operating Cash Flow", float("nan")),
                record("Operating Cash Flow", 70, start="bad-date"),
                record("Operating Cash Flow", True),
            ]
        )
        + "\n"
    )
    messages = [
        AIMessage(content=text),
        ToolMessage(content=text, name="get_cashflow", tool_call_id="cf"),
        ToolMessage(content="# PERIOD_FACTS: bad-json", name="get_cashflow", tool_call_id="bad"),
    ]
    records = records_from_messages(messages)
    assert len(records) == 1
    assert analyze_fundamentals(records, "2026-08-01")["periods"][0]["cfo"] == 50


def test_repeated_tool_payloads_do_not_disable_ttm():
    records = [
        record("Revenue", 10, start=start, end=end)
        for start, end in [
            ("2025-10-01", "2025-12-31"),
            ("2026-01-01", "2026-03-31"),
            ("2026-04-01", "2026-06-30"),
            ("2026-07-01", "2026-09-30"),
        ]
    ]
    assert (
        analyze_fundamentals(records * 2, "2026-10-01")["ttm"]["metrics"]["Revenue"]["value"] == 40
    )


@pytest.mark.parametrize(
    "metric,changes", [("Revenue", {"perimeter": "continuing_operations"}), ("Diluted EPS", {})]
)
def test_ttm_refuses_perimeter_changes_or_per_share_summing(metric, changes):
    records = [
        record(metric, 10, start=start, end=end)
        for start, end in [
            ("2025-10-01", "2025-12-31"),
            ("2026-01-01", "2026-03-31"),
            ("2026-04-01", "2026-06-30"),
            ("2026-07-01", "2026-09-30"),
        ]
    ]
    records[-1].update(changes)
    assert analyze_fundamentals(records, "2026-10-01")["ttm"]["status"] == "unavailable"


def test_period_metadata_is_bounded_but_retains_latest_derivation_baseline(monkeypatch):
    rows = [
        fact(f"{year}-01-01", f"{year}-{month}-{day}", value, "2026-10-20")
        for year in range(2010, 2027)
        for month, day, value in [
            ("03", "31", 10_000_000),
            ("06", "30", 30_000_000),
            ("09", "30", 60_000_000),
        ]
    ]
    sec_payload(monkeypatch, {"NetCashProvidedByUsedInOperatingActivities": rows})
    out = sec_edgar.get_cashflow("SYNTH", "quarterly", "2026-10-30")
    assert len(block(out, "PERIOD_FACTS")) <= 16
    assert len(block(out)["periods"]) <= 4
    assert block(out)["periods"][-1]["cfo"] == 30_000_000
    assert "RAW filed amounts" in out


def test_unknown_units_or_conflicting_vintages_are_not_arithmetic_inputs():
    unknown = analyze_fundamentals(
        [
            record("Operating Cash Flow", 50, unit="UNKNOWN"),
            record("Capital Expenditure", 10, unit="UNKNOWN"),
        ],
        "2026-08-01",
    )
    assert not unknown["periods"]
    conflict = analyze_fundamentals(
        [record("Operating Cash Flow", 50), record("Operating Cash Flow", 500)], "2026-08-01"
    )
    assert not conflict["periods"]


def test_unmatched_ytd_cannot_be_subtracted_across_fiscal_years():
    rows = [
        record("Operating Cash Flow", 30, start="2025-01-01", end="2025-03-31"),
        record("Operating Cash Flow", 100, start="2026-01-01", end="2026-06-30"),
    ]
    out = analyze_fundamentals(rows, "2026-08-01")
    assert not any(p["end"] == "2026-06-30" for p in out["periods"])
    assert out["limitations"] == "unreconciled"


def test_message_cutoff_precedes_vintage_selection():
    from langchain_core.messages import ToolMessage

    from tradingagents.dataflows.fundamental_analytics import records_from_messages

    records = [
        record("Operating Cash Flow", 50),
        record("Operating Cash Flow", 500, filed="2026-09-01"),
    ]
    message = ToolMessage(
        content="# PERIOD_FACTS: " + json.dumps(records), name="get_cashflow", tool_call_id="cash"
    )
    known = records_from_messages([message], as_of="2026-08-01")
    assert known[0]["value"] == 50


def test_unknown_perimeter_is_unreconciled_even_when_both_fields_are_unknown():
    out = analyze_fundamentals(
        [
            record("Operating Cash Flow", 50, perimeter="unknown"),
            record("Capital Expenditure", 10, perimeter="unknown"),
        ],
        "2026-08-01",
    )
    assert out["periods"][0]["fcf"] is None
    assert out["periods"][0]["fcf_status"].startswith("unreconciled")
