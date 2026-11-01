"""Run-local Yahoo OHLCV: immutable artifacts, copied reads, context propagation.

The compatibility dispatchers contain no market data. They preserve legacy
vendor behavior outside a scope; the ContextVar owns the run's locked store.
"""
import hashlib
import json
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from datetime import UTC, datetime
from functools import wraps
from threading import RLock

import pandas as pd

from tradingagents.dataflows.errors import NoMarketDataError
from tradingagents.dataflows.symbols import normalize_symbol

_active = ContextVar("tradingagents_market_snapshot", default=None)
_adapter_lock = RLock()


class RunSnapshot:
    def __init__(self, symbol, as_of, artifact=None):
        self.symbol = normalize_symbol(symbol)
        self.as_of = str(as_of)
        self.lock = RLock()
        self.artifact = deepcopy(artifact)
        if self.artifact is not None:
            metadata = self.artifact.get("metadata") or {}
            if metadata.get("symbol") != self.symbol or metadata.get("requested_as_of") != self.as_of:
                raise ValueError("snapshot identity does not match ticker/date")
            if self.artifact.get("available") is not False:
                digest = hashlib.sha256(json.dumps(self.artifact.get("ohlcv"), sort_keys=True,
                    separators=(",", ":")).encode()).hexdigest()
                if digest != metadata.get("sha256"):
                    raise ValueError("snapshot OHLCV digest does not match artifact")
                if self.artifact.get("factor_risk") is not None:
                    factor_digest = hashlib.sha256(json.dumps(self.artifact["factor_risk"],
                        sort_keys=True, separators=(",", ":")).encode()).hexdigest()
                    if factor_digest != self.artifact.get("factor_risk_sha256"):
                        raise ValueError("snapshot factor risk digest does not match artifact")
        self.error = None

    def rows(self, symbol, as_of):
        if normalize_symbol(symbol) != self.symbol:
            return None
        if str(as_of) > self.as_of:
            raise ValueError("market data request exceeds run snapshot cutoff")
        with self.lock:
            if self.error is not None:
                raise self.error
            if self.artifact is None:
                try:
                    # Resolve through the snapshot seam so existing offline
                    # vendor injection remains supported.
                    from tradingagents.dataflows.vendors.yahoo import snapshot
                    frame = snapshot.load_ohlcv(symbol, self.as_of, fill_gaps=False)
                    frame = frame.copy()
                    frame["Date"] = frame["Date"].map(
                        lambda x: pd.Timestamp(x).tz_localize(None).normalize())
                    frame = frame[frame["Date"] <= pd.Timestamp(self.as_of)].sort_values("Date")
                    if frame.empty:
                        raise NoMarketDataError(symbol, self.symbol, "no snapshot rows")
                    records = []
                    for row in frame.to_dict(orient="records"):
                        records.append({key: (value.isoformat() if isinstance(value, pd.Timestamp)
                                               else None if pd.isna(value) else value)
                                        for key, value in row.items()})
                    attrs = frame.attrs
                    metadata = {
                        "symbol": self.symbol, "requested_as_of": self.as_of,
                        "observed_at": datetime.now(UTC).isoformat(),
                        "source": "yfinance", "price_adjustment": "adjusted",
                        "bar_timestamp": str(frame["Date"].iloc[-1].date()),
                        "timestamp_kind": "session_date",
                        "quote_timestamp": attrs.get("quote_timestamp"),
                        "venue_timezone": attrs.get("venue_timezone"),
                        "venue": attrs.get("venue"),
                        "bar_status": attrs.get("bar_status") if attrs.get("bar_status")
                            in {"final", "partial"} else "unknown",
                        "sha256": hashlib.sha256(json.dumps(records, sort_keys=True,
                            separators=(",", ":")).encode()).hexdigest(),
                    }
                    self.artifact = {"schema": "tradingagents.market_snapshot.v1",
                                     "metadata": metadata, "ohlcv": records}
                    try:
                        factor_risk = _capture_factors(frame, self.symbol, self.as_of, metadata["observed_at"])
                    except Exception as exc:  # Optional analytics never invalidates valid prices.
                        metadata["factor_risk_error"] = f"{type(exc).__name__}: {exc}"[:200]
                        factor_risk = None
                    if factor_risk is not None:
                        self.artifact["factor_risk"] = factor_risk
                        self.artifact["factor_risk_sha256"] = hashlib.sha256(json.dumps(factor_risk,
                            sort_keys=True, separators=(",", ":")).encode()).hexdigest()
                except Exception as exc:
                    self.error = exc
                    self.artifact = {"schema": "tradingagents.market_snapshot.v1",
                                     "metadata": {"symbol": self.symbol,
                                                  "requested_as_of": self.as_of},
                                     "available": False, "reason": str(exc)[:200], "ohlcv": []}
                    raise
            if self.artifact.get("available") is False:
                raise NoMarketDataError(symbol, self.symbol, self.artifact.get("reason", "snapshot unavailable"))
            result = pd.DataFrame(deepcopy(self.artifact["ohlcv"]))
            result["Date"] = pd.to_datetime(result["Date"])
            result = result[result["Date"] <= pd.Timestamp(as_of)].copy()
            result.attrs.update(self.artifact["metadata"])
            return result


def _capture_factors(frame, symbol, as_of, observed_at):
    """One optional factor batch, attached to the already frozen price input."""
    from tradingagents.dataflows.config import get_config
    from tradingagents.dataflows.factor_risk import fit_factor_risk, load_factor_inputs
    from tradingagents.memory.settlement import resolve_benchmark
    config = get_config()
    if not config.get("factor_risk_enabled", False):
        return None
    specs = deepcopy(config.get("factor_risk_inputs") or {})
    specs.setdefault("benchmark", {
        "symbol": resolve_benchmark(symbol, config),
        "source": "yfinance; configured benchmark policy, not portfolio look-through",
    })
    # FX pairs and rate units must be explicitly configured; never infer them
    # from a listing suffix or turn a bond price into a yield series.
    import yfinance as yf
    inputs = load_factor_inputs(as_of, specs, batch_loader=yf.download, current_time=observed_at)
    asset = frame.copy()
    asset.attrs["auto_adjust"] = True  # Same Yahoo auto_adjust=True input contract.
    result = fit_factor_risk(symbol, as_of, asset, inputs["factors"],
                             factor_metadata=inputs["factor_metadata"], current_time=observed_at)
    result["input_coverage"] = inputs["coverage"]
    result["batch_calls"] = inputs["batch_calls"]
    result["observed_at"] = observed_at
    result["factor_input_sha256"] = {
        name: hashlib.sha256(data.to_json(date_format="iso", double_precision=15).encode()).hexdigest()
        for name, data in inputs["factors"].items()
    }
    for name, fit in result["factors"].items():
        if fit["reason"] == "missing_input":
            fit["reason"] = inputs["coverage"].get(name, "not_configured")
    return result


def frozen_rows(symbol, as_of, loader=None):
    store = _active.get()
    if store is not None:
        result = store.rows(symbol, as_of)
        if result is not None:
            return result
    if loader is None:
        from tradingagents.dataflows.vendors.yahoo.ohlcv import load_ohlcv
        loader = load_ohlcv
    return loader(symbol, as_of, fill_gaps=False)


def snapshot_artifact():
    store = _active.get()
    return deepcopy(store.artifact) if store is not None else None


def _install_dispatchers():
    # Existing vendor functions imported load_ohlcv by value. Bind one stable
    # dispatcher at that seam, never monkeypatch/unpatch for individual runs.
    from tradingagents.dataflows import router
    from tradingagents.dataflows.vendors.yahoo import market
    with _adapter_lock:
        if not getattr(market.load_ohlcv, "_run_snapshot_dispatch", False):
            original_load = market.load_ohlcv
            @wraps(original_load)
            def load(symbol, as_of_date, fill_gaps=True):
                if _active.get() is None:
                    return original_load(symbol, as_of_date, fill_gaps=fill_gaps)
                return frozen_rows(symbol, as_of_date, loader=original_load)
            load._run_snapshot_dispatch = True
            market.load_ohlcv = load
        original = router.VENDOR_METHODS["get_stock_data"]["yfinance"]
        if (not getattr(original, "_run_snapshot_dispatch", False)
                and getattr(original, "__module__", None) == market.__name__):
            @wraps(original)
            def stock(symbol, start_date, end_date):
                store = _active.get()
                if store is None or normalize_symbol(symbol) != store.symbol:
                    return original(symbol, start_date, end_date)
                frame = frozen_rows(symbol, min(str(end_date), store.as_of))
                frame = frame[frame["Date"] >= pd.Timestamp(start_date)].set_index("Date")
                if frame.empty:
                    raise NoMarketDataError(symbol, store.symbol, "no snapshot rows in window")
                return (f"# Stock data for {store.symbol} from {start_date} to {end_date}\n"
                        f"# Snapshot sha256: {store.artifact['metadata']['sha256']}\n"
                        f"# Total records: {len(frame)}\n\n" + frame.to_csv())
            stock._run_snapshot_dispatch = True
            router.VENDOR_METHODS["get_stock_data"]["yfinance"] = stock


@contextmanager
def market_snapshot_scope(symbol, as_of, artifact=None):
    """Freeze once; LangGraph copies this context to parallel tool workers."""
    _install_dispatchers()
    store = RunSnapshot(symbol, as_of, artifact)
    token = _active.set(store)
    try:
        yield store
    finally:
        _active.reset(token)
