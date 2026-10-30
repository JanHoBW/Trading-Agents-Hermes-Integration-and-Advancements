"""Currency rates for a book that holds more than one currency.

An exposure in USD is not comparable with a EUR cap. Converting it requires a
rate, and a rate is a fact with a date -- so this module resolves pairs from the
price vendor, stamps them with ``as_of``, caches them, and returns ``None``
rather than a default of 1.0 when no rate is available.

Only the pair the caller asks for is fetched; a missing quote is reported as
missing, never bridged through a third currency that nobody verified.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Iterable, Mapping

SCHEMA = "tradingagents.fx.v1"


@dataclass(frozen=True)
class Rate:
    pair: str
    base: str
    quote: str
    rate: float
    as_of: str
    source: str

    def to_dict(self) -> dict:
        return asdict(self)


def default_cache_path() -> Path:
    return Path.home() / ".tradingagents" / "fx.json"


def pair_name(base: str, quote: str) -> str:
    return f"{base.strip().upper()}{quote.strip().upper()}"


def yahoo_symbol(base: str, quote: str) -> str:
    return f"{pair_name(base, quote)}=X"


def load_cache(path: str | Path | None = None) -> dict[str, dict]:
    path = Path(path) if path else default_cache_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    rates = payload.get("rates") if isinstance(payload, dict) else None
    if not isinstance(rates, dict):
        return {}
    return {str(k).upper(): v for k, v in rates.items() if isinstance(v, dict)}


def save_cache(rates: Mapping[str, Mapping], path: str | Path | None = None) -> Path:
    path = Path(path) if path else default_cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "schema": SCHEMA,
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "rates": dict(rates),
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def fetch_rate(base: str, quote: str, as_of: str) -> dict | None:
    """Close of ``{BASE}{QUOTE}=X`` on or before ``as_of``, from the price vendor."""
    symbol = yahoo_symbol(base, quote)
    try:
        from tradingagents.dataflows.vendors.yahoo.ohlcv import load_ohlcv

        frame = load_ohlcv(symbol, as_of, fill_gaps=False)
    except Exception:  # noqa: BLE001 -- a rate miss must not fail a run
        return None
    if frame is None or frame.empty:
        return None
    try:
        close = float(frame["Close"].iloc[-1])
    except (KeyError, TypeError, ValueError):
        return None
    if not (close > 0):
        return None
    stamp = str(as_of)
    if "Date" in frame.columns:
        try:
            stamp = str(frame["Date"].iloc[-1])[:10]
        except Exception:  # noqa: BLE001 -- the requested date stays the label
            pass
    return Rate(pair=pair_name(base, quote), base=base.upper(), quote=quote.upper(),
                rate=close, as_of=stamp, source=f"yahoo/{symbol}").to_dict()


def _fresh(entry: Mapping, as_of: str, max_age_days: int) -> bool:
    try:
        age = (date.fromisoformat(as_of) - date.fromisoformat(str(entry.get("as_of")))).days
    except (TypeError, ValueError):
        return False
    return 0 <= age <= max_age_days


def resolve(
    pairs: Iterable[tuple[str, str]],
    *,
    as_of: str,
    cache_path: str | Path | None = None,
    max_age_days: int = 1,
    fetch: Callable[[str, str, str], dict | None] | None = None,
    write: bool = True,
) -> dict[str, dict]:
    """Resolve pairs against the cache, fetching only what is stale or absent."""
    cache = load_cache(cache_path)
    fetch = fetch or fetch_rate
    changed = False
    for base, quote in pairs:
        base, quote = base.strip().upper(), quote.strip().upper()
        if not base or not quote or base == quote:
            continue
        name = pair_name(base, quote)
        cached = cache.get(name)
        if cached and _fresh(cached, as_of, max_age_days):
            continue
        fresh = fetch(base, quote, as_of)
        if fresh:
            cache[name] = fresh
            changed = True
    if changed and write:
        try:
            save_cache(cache, cache_path)
        except OSError:
            pass
    return cache


def rate_for(
    base: str,
    quote: str,
    rates: Mapping[str, Mapping] | None,
) -> dict | None:
    """The rate for ``base -> quote``, inverting the opposite pair when that is all we have."""
    base, quote = (base or "").strip().upper(), (quote or "").strip().upper()
    if not base or not quote:
        return None
    if base == quote:
        return {"pair": pair_name(base, quote), "base": base, "quote": quote,
                "rate": 1.0, "as_of": None, "source": "identity"}
    rates = rates or {}
    direct = rates.get(pair_name(base, quote))
    if direct and float(direct.get("rate") or 0) > 0:
        return dict(direct)
    inverse = rates.get(pair_name(quote, base))
    if inverse and float(inverse.get("rate") or 0) > 0:
        return {"pair": pair_name(base, quote), "base": base, "quote": quote,
                "rate": 1.0 / float(inverse["rate"]), "as_of": inverse.get("as_of"),
                "source": f"inverse of {inverse.get('pair')}"}
    return None


def convert(amount: float, rate: Mapping | None) -> float | None:
    if rate is None or amount is None:
        return None
    try:
        return float(amount) * float(rate["rate"])
    except (TypeError, ValueError, KeyError):
        return None


def needed_pairs(currencies: Iterable[str], account_currency: str | None) -> list[tuple[str, str]]:
    """The pairs a book needs, excluding identity pairs."""
    target = (account_currency or "").strip().upper()
    if not target:
        return []
    seen: set[str] = set()
    pairs: list[tuple[str, str]] = []
    for currency in currencies:
        base = str(currency).strip().upper()
        if not base or base == target or base in seen:
            continue
        seen.add(base)
        pairs.append((base, target))
    return pairs


def cached_rates(
    currencies: Iterable[str],
    account_currency: str | None,
    *,
    cache_path: str | Path | None = None,
) -> dict[str, dict]:
    """Rates from the cache only -- what a run may use without touching the network."""
    cache = load_cache(cache_path)
    wanted = {pair_name(b, q) for b, q in needed_pairs(currencies, account_currency)}
    return {name: record for name, record in cache.items() if name in wanted}


def refresh(
    currencies: Iterable[str],
    account_currency: str | None,
    *,
    as_of: str | None = None,
    cache_path: str | Path | None = None,
    fetch: Callable[[str, str, str], dict | None] | None = None,
    max_age_days: int = 1,
) -> dict[str, dict]:
    """Bring the cache up to date before a run (doctor does this once)."""
    as_of = as_of or date.today().isoformat()
    pairs = needed_pairs(currencies, account_currency)
    if not pairs:
        return load_cache(cache_path)
    return resolve(pairs, as_of=as_of, cache_path=cache_path, fetch=fetch,
                   max_age_days=max_age_days)
