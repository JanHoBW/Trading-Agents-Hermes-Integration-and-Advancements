"""IR snapshot transport: mocked public collector, real JSON and consumer."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tradingagents.dataflows.vendors import news_supplement as supplement

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "ir_snapshot_transport", ROOT / "hermes/skills/hermes-trading-scenarios/scripts/news_sources/ir_facts_snapshot.py"
)
snapshot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(snapshot)
URL = "https://issuer.example/results"


def consume(monkeypatch, path, as_of="2026-11-01"):
    monkeypatch.setattr(supplement, "get_config", lambda: {"news_supplement_path": str(path)})
    return supplement.get_news_supplement("XYZ.DE", "2026-10-25", as_of)


def test_cli_passes_historical_as_of_to_collector(monkeypatch, tmp_path):
    seen = []

    def collect(ticker, *, as_of=None):
        seen.append((ticker, as_of))
        return {"facts": {"Revenue FY2025": "10 bn EUR"}, "reference_date": "2025-12-31", "source": URL}

    monkeypatch.setattr(snapshot, "_repo_import", lambda: SimpleNamespace(collect_ir_facts=collect))
    out = tmp_path / "snapshot.json"
    assert snapshot.main(["build", "--ticker", "XYZ.DE", "--date", "2026-02-01", "--out", str(out)]) == 0
    assert seen == [("XYZ.DE", "2026-02-01")]
    assert "Revenue FY2025: 10 bn EUR" in consume(monkeypatch, out, "2026-02-01")


def release_fixture():
    return {
        "facts": {"Revenue FY2025": "10 bn EUR"}, "reference_date": "31.12.2025",
        "source": URL, "perimeter_note": "annual continuing operations",
        "release_facts": {"Margin H1": "17.1 %"},
        "release_fact_evidence": [{"label": "Margin H1", "value": "17.1 %",
            "reference_date": "2026-06-30", "publication_date": "2026-08-06",
            "source_url": URL, "perimeter_note": "H1 group adjusted",
            "raw_sentence": "The H1 adjusted margin was 17.1 %."}],
        "release_facts_date": "2026-08-06", "release_facts_source": URL,
    }


def test_release_evidence_survives_serialization_to_public_consumer(monkeypatch, tmp_path):
    raw = release_fixture()
    monkeypatch.setattr(snapshot, "_repo_import", lambda: SimpleNamespace(collect_ir_facts=lambda *a, **k: raw))
    out = tmp_path / "snapshot.json"
    assert snapshot.main(["build", "--ticker", "XYZ.DE", "--date", "2026-11-01", "--out", str(out)]) == 0
    figures = json.loads(out.read_text())["facts"]["figures"]
    assert figures[0]["reference_date"] == "31.12.2025"
    assert figures[1] == raw["release_fact_evidence"][0]
    text = consume(monkeypatch, out)
    assert "Margin H1: 17.1 %" in text
    assert "reference 2026-06-30" in text
    assert "publication 2026-08-06" in text
    assert "H1 group adjusted" in text
    assert "The H1 adjusted margin was 17.1 %." in text


def test_actual_guidance_object_reaches_consumer_with_numeric_range_and_basis(monkeypatch, tmp_path):
    raw = release_fixture()
    raw.update(guidance={"guidance_range": "13.7 to 14.2 bn EUR",
        "guidance_numeric_range": {"low": 13.7, "high": 14.2, "unit": "bn", "currency": "EUR"},
        "guidance_margin": "19 %", "guidance_direction": "cut",
        "guidance_sentence": "We reduce FY2026 revenue guidance.",
        "guidance_raw_sentence": "We reduce FY2026 revenue guidance to 13.7 to 14.2 bn EUR.",
        "guidance_reason": "cancelled project"},
        guidance_date="2026-08-06", guidance_source=URL, guidance_perimeter="FY2026 group")
    out = tmp_path / "snapshot.json"
    monkeypatch.setattr(snapshot, "_repo_import", lambda: SimpleNamespace(collect_ir_facts=lambda *a, **k: raw))
    assert snapshot.main(["build", "--ticker", "XYZ.DE", "--date", "2026-11-01", "--out", str(out)]) == 0
    transported = supplement._validate_facts(json.loads(out.read_text())["facts"])
    assert transported["guidance"] == raw["guidance"]
    assert transported["guidance_date"] == raw["guidance_date"]
    text = consume(monkeypatch, out)
    for expected in ("13.7 to 14.2 bn EUR", "19 %", "cut", "cancelled project", "2026-08-06", URL, "FY2026 group"):
        assert expected in text


@pytest.mark.parametrize("kind", ["release", "evidence", "confirmation"])
def test_build_accepts_dated_release_or_confirmation_without_annual_data(kind, monkeypatch, tmp_path):
    fixture = release_fixture()
    raw = {"reference_date": "", "guidance": {}, "facts": {}}
    if kind == "release":
        raw.update({key: value for key, value in fixture.items() if key.startswith("release_")})
    elif kind == "evidence":
        raw = {"release_fact_evidence": fixture["release_fact_evidence"]}
    else:
        raw.pop("facts")
        raw.update(guidance={"guidance_direction": "confirm", "guidance_raw_sentence": "We confirm the outlook."},
                   guidance_date="2026-08-06", guidance_source=URL)
    monkeypatch.setattr(snapshot, "_repo_import", lambda: SimpleNamespace(collect_ir_facts=lambda *a, **k: raw))
    out = tmp_path / "snapshot.json"
    assert snapshot.main(["build", "--ticker", "XYZ.DE", "--date", "2026-11-01", "--out", str(out)]) == 0
    text = consume(monkeypatch, out)
    assert ("confirm" if kind == "confirmation" else "Margin H1: 17.1 %") in text
    assert "2026-08-06" in text
    assert "Revenue FY2025" not in text


@pytest.mark.parametrize("field", ["reference_date", "publication_date", "guidance_date", "root_publication_date", "root_reference_date"])
@pytest.mark.parametrize("route", ["build", "import", "consumer"])
def test_future_evidence_is_rejected_at_every_transport_boundary(field, route, monkeypatch, tmp_path, capsys):
    raw = {"issuer": "Example", "reference_date": "2025-12-31", "figures": [
        {"label": "Margin", "value": "17.1 %", "reference_date": "2026-06-30",
         "publication_date": "2026-08-06", "source_url": URL}],
        "guidance": "Outlook confirmed", "guidance_date": "2026-08-06", "guidance_source": URL}
    if field.startswith("root_"):
        raw[field.removeprefix("root_")] = "2026-12-01"
    elif field == "guidance_date":
        raw[field] = "2026-12-01"
    else:
        raw["figures"][0][field] = "2026-12-01"
    out = tmp_path / "snapshot.json"
    if route == "consumer":
        snapshot._write("XYZ.DE", "2026-11-01", raw, out)
        with pytest.raises(supplement.VendorUnavailableError, match="future"):
            consume(monkeypatch, out)
    else:
        if route == "build":
            vendor = {**raw, "facts": {}, "release_fact_evidence": raw["figures"]}
            monkeypatch.setattr(snapshot, "_repo_import", lambda: SimpleNamespace(collect_ir_facts=lambda *a, **k: vendor))
            args = ["build"]
        else:
            infile = tmp_path / "raw.json"
            infile.write_text(json.dumps(raw))
            args = ["import", "--in", str(infile)]
        assert snapshot.main(args + ["--ticker", "XYZ.DE", "--date", "2026-11-01", "--out", str(out)]) == 2
        assert "future" in capsys.readouterr().err
        assert not out.exists()


@pytest.mark.parametrize("guidance", [
    {"unexpected": "injected"}, {"guidance_direction": "buy"},
    {"guidance_numeric_range": {"low": "13", "high": 14}},
    {"guidance_numeric_range": {"low": float("nan"), "high": 14}},
    {"guidance_numeric_range": {"low": 15, "high": 14}},
    {"guidance_numeric_range": {"low": True, "high": 14}},
    {"guidance_numeric_range": {"low": 13, "high": 14, "secret": "x"}},
    {"guidance_sentence": "x" * 201}, {"guidance_raw_sentence": "x" * 2001},
])
def test_guidance_object_is_allowlisted_typed_and_bounded(guidance):
    raw = {"reference_date": "2025-12-31", "figures": [], "guidance": guidance,
           "guidance_date": "2026-08-06", "guidance_source": URL}
    with pytest.raises(ValueError, match="guidance"):
        supplement._validate_facts(raw, as_of="2026-11-01")


@pytest.mark.parametrize("where", ["figure", "guidance"])
@pytest.mark.parametrize("url", ["http://issuer.example/x", "https://u:p@issuer.example/x",
    "https://127.0.0.1/x", "https://[::1]/x", "https://10.0.0.1/x", "https://localhost/x",
    "https://metadata.internal/x", "https://issuer.example:bad/x"])
def test_evidence_sources_require_public_https(where, url):
    raw = {"reference_date": "2025-12-31", "figures": [{"label": "Margin", "value": "17 %", "source_url": URL}],
           "guidance": {"guidance_direction": "confirm"}, "guidance_date": "2026-08-06", "guidance_source": URL}
    if where == "figure":
        raw["figures"][0]["source_url"] = url
    else:
        raw["guidance_source"] = url
    with pytest.raises(ValueError, match="public HTTPS"):
        supplement._validate_facts(raw)


def test_import_preserves_typed_per_figure_evidence(monkeypatch, tmp_path):
    figure = {"label": "Net cash", "value": "0 to 1 bn EUR", "source_url": URL,
        "reference_date": "2026-06-30", "publication_date": "2026-08-06",
        "perimeter_note": "group", "numeric_value": 0,
        "numeric_range": {"low": 0, "high": 1, "unit": "bn", "currency": "EUR"},
        "direction": "confirm", "raw_sentence": "x" * 600}
    raw = {"figures": [figure], "reference_date": "", "guidance": ""}
    infile, out = tmp_path / "raw.json", tmp_path / "snapshot.json"
    infile.write_text(json.dumps(raw))
    assert snapshot.main(["import", "--ticker", "XYZ.DE", "--date", "2026-11-01", "--in", str(infile), "--out", str(out)]) == 0
    actual = supplement._validate_facts(json.loads(out.read_text())["facts"])["figures"][0]
    assert actual == figure
    assert type(actual["numeric_value"]) is int
    assert "0 to 1 bn EUR" in consume(monkeypatch, out)


@pytest.mark.parametrize("scope,key,value", [
    ("root", "issuer", "x" * 201), ("root", "perimeter", "x" * 201),
    ("root", "guidance_perimeter", "x" * 201), ("root", "guidance", "x" * 201),
    ("root", "guidance", ["x" * 201]), ("root", "guidance", ["ok"] * 41),
    ("figure", "perimeter_note", "x" * 201), ("figure", "raw_sentence", "x" * 2001),
    ("figure", "raw_sentence", {}), ("figure", "direction", "buy"),
    ("figure", "reference_date", 20260630),
])
def test_extended_evidence_fields_keep_ordinary_and_raw_budgets(scope, key, value):
    raw = {"reference_date": "2025-12-31", "figures": [{"label": "Cash", "value": "0", "source_url": URL}]}
    (raw if scope == "root" else raw["figures"][0])[key] = value
    with pytest.raises(ValueError):
        supplement._validate_facts(raw)


def test_vendor_evidence_overrides_publication_dated_display_mapping(monkeypatch, tmp_path):
    raw = release_fixture()
    raw["facts"] = {"Margin H1": "17.1 %"}
    raw["reference_date"] = "2026-08-06"
    raw["release_facts"] = {}
    monkeypatch.setattr(snapshot, "_repo_import", lambda: SimpleNamespace(collect_ir_facts=lambda *a, **k: raw))
    out = tmp_path / "snapshot.json"
    assert snapshot.main(["build", "--ticker", "XYZ.DE", "--date", "2026-11-01", "--out", str(out)]) == 0
    figures = json.loads(out.read_text())["facts"]["figures"]
    assert figures == raw["release_fact_evidence"]
    assert "reference 2026-06-30" in consume(monkeypatch, out)


def test_vendor_long_display_wording_is_bounded_without_losing_raw_guidance(monkeypatch, tmp_path):
    raw = release_fixture()
    raw["release_fact_evidence"][0]["value"] = "v" * 208
    raw["release_fact_evidence"][0]["raw_sentence"] = "v" * 208
    raw.update(guidance={"guidance_direction": "confirm", "guidance_sentence": "s" * 400,
        "guidance_raw_sentence": "s" * 600}, guidance_date="2026-08-06", guidance_source=URL)
    monkeypatch.setattr(snapshot, "_repo_import", lambda: SimpleNamespace(collect_ir_facts=lambda *a, **k: raw))
    out = tmp_path / "snapshot.json"
    assert snapshot.main(["build", "--ticker", "XYZ.DE", "--date", "2026-11-01", "--out", str(out)]) == 0
    facts = json.loads(out.read_text())["facts"]
    assert facts["figures"][1]["value"] == "v" * 200
    assert facts["guidance"]["guidance_sentence"] == "s" * 200
    assert facts["guidance"]["guidance_raw_sentence"] == "s" * 600
    assert "s" * 600 in consume(monkeypatch, out)


@pytest.mark.parametrize("missing", ["guidance_date", "guidance_source"])
def test_new_guidance_objects_require_dated_public_provenance(missing):
    raw = {"reference_date": "2025-12-31", "figures": [], "guidance": {"guidance_direction": "confirm"},
           "guidance_date": "2026-08-06", "guidance_source": URL}
    del raw[missing]
    with pytest.raises(ValueError, match="guidance"):
        supplement._validate_facts(raw)


@pytest.mark.parametrize("guidance", ["Outlook confirmed", ["Revenue 10 bn EUR", "Margin 19 %"]])
def test_schema_one_legacy_guidance_remains_accepted(guidance, monkeypatch, tmp_path):
    raw = {"reference_date": "31.12.2025", "figures": [{"label": "Revenue", "value": "10 bn EUR", "source_url": URL}],
           "guidance": guidance}
    out = tmp_path / "snapshot.json"
    snapshot._write("XYZ.DE", "2026-11-01", raw, out)
    assert supplement._validate_facts(raw)["guidance"] == guidance
    assert "Revenue: 10 bn EUR" in consume(monkeypatch, out)


def test_figure_limit_remains_forty(monkeypatch, tmp_path):
    raw = {"facts": {f"Metric {i}": str(i) for i in range(41)}, "reference_date": "2025-12-31", "source": URL}
    monkeypatch.setattr(snapshot, "_repo_import", lambda: SimpleNamespace(collect_ir_facts=lambda *a, **k: raw))
    out = tmp_path / "snapshot.json"
    assert snapshot.main(["build", "--ticker", "XYZ.DE", "--date", "2026-11-01", "--out", str(out)]) == 0
    facts = json.loads(out.read_text())["facts"]
    assert len(facts["figures"]) == 40
    assert "Metric 39: 39" in consume(monkeypatch, out)
    facts["figures"].append(dict(facts["figures"][0]))
    with pytest.raises(ValueError, match="at most 40"):
        supplement._validate_facts(facts)


def test_imported_block_publication_does_not_replace_annual_reference(monkeypatch, tmp_path):
    raw = {"reference_date": "2025-12-31", "publication_date": "2026-03-01",
           "figures": [{"label": "Annual revenue", "value": "10 bn EUR", "source_url": URL}]}
    infile, out = tmp_path / "raw.json", tmp_path / "snapshot.json"
    infile.write_text(json.dumps(raw))
    assert snapshot.main(["import", "--ticker", "XYZ.DE", "--date", "2026-11-01", "--in", str(infile), "--out", str(out)]) == 0
    facts = json.loads(out.read_text())["facts"]
    assert facts["publication_date"] == "2026-03-01"
    assert facts["figures"][0]["reference_date"] == "2025-12-31"
    assert facts["figures"][0]["publication_date"] == "2026-03-01"
    text = consume(monkeypatch, out)
    assert "reference 2025-12-31, publication 2026-03-01" in text


@pytest.mark.parametrize("route", ["build", "import"])
def test_future_release_publication_metadata_cannot_be_hidden_by_older_evidence(route, monkeypatch, tmp_path, capsys):
    raw = release_fixture()
    raw["release_facts_date"] = "2026-12-01"
    out = tmp_path / "snapshot.json"
    if route == "build":
        monkeypatch.setattr(snapshot, "_repo_import", lambda: SimpleNamespace(collect_ir_facts=lambda *a, **k: raw))
        args = ["build"]
    else:
        infile = tmp_path / "raw.json"
        infile.write_text(json.dumps(raw))
        args = ["import", "--in", str(infile)]
    assert snapshot.main(args + ["--ticker", "XYZ.DE", "--date", "2026-11-01", "--out", str(out)]) == 2
    assert "future" in capsys.readouterr().err
    assert not out.exists()
