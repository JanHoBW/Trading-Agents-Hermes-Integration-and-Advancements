"""Instrument master data for a book that is not only shares.

A position in a knock-out or a leveraged ETC is not a number of shares: it is
an exposure to an underlying, scaled by a contractual ratio. Without that ratio
the exposure is unknowable, and a gate that counted one unit as one share would
wave a levered position through as if it were small.

Resolution is deliberately conservative. Fields are parsed from the public
security page (ariva) and stamped with ``source`` and ``as_of``; a field the
page does not state stays ``None`` and is reported as unknown rather than
defaulted to 1.0. The parsed record is cached on disk, because term sheets
change slowly while a run needs the numbers quickly.
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

SCHEMA = "tradingagents.instruments.v1"
ARIVA_URL = "https://www.ariva.de/{key}"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0 Safari/537.36"
)
YAHOO_SEARCH = (
    "https://query1.finance.yahoo.com/v1/finance/search"
    "?q={query}&quotesCount=5&newsCount=0"
)

KINDS = ("share", "etf", "etc", "knock_out", "option", "fund", "unknown")


@dataclass(frozen=True)
class Instrument:
    """What is contractually true about one security, as far as the page states it."""

    key: str
    name: str | None = None
    kind: str = "unknown"
    underlying: str | None = None
    underlying_name: str | None = None
    underlying_price: float | None = None
    underlying_currency: str | None = None
    ratio: float | None = None
    direction: str | None = None
    barrier: float | None = None
    leverage: float | None = None
    reset: str | None = None
    source: str | None = None
    as_of: str | None = None
    fetched_on: str | None = None

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}

    def is_derivative(self) -> bool:
        return self.kind in {"knock_out", "option"}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Instrument":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in dict(data).items() if k in known})


def default_cache_path() -> Path:
    return Path.home() / ".tradingagents" / "instruments.json"


# --- parsing ---------------------------------------------------------------

def _cells(html_text: str) -> list[str]:
    """Flatten the page into text cells; ariva renders facts as adjacent cells."""
    text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html_text, flags=re.S)
    text = re.sub(r"<[^>]+>", "|", text)
    text = text.replace("&nbsp;", " ").replace("&auml;", "ä").replace("&euro;", "€")
    text = text.replace("&#8364;", "€").replace("&amp;", "&")
    return [c.strip() for c in re.split(r"\|+", text) if c.strip()]


def parse_number(text: Any) -> float | None:
    """German or plain numbers: '1.048,2 €' -> 1048.2, '0,10' -> 0.1."""
    if text is None:
        return None
    raw = str(text).replace("\xa0", " ").strip()
    cleaned = re.sub(r"[^0-9,.\-+]", "", raw)
    if not cleaned or not re.search(r"\d", cleaned):
        return None
    if "," in cleaned and "." in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    elif "," in cleaned:
        cleaned = cleaned.replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


def _currency_of(text: str | None) -> str | None:
    raw = str(text or "")
    if "€" in raw or "EUR" in raw:
        return "EUR"
    if "$" in raw or "USD" in raw:
        return "USD"
    if "£" in raw or "GBP" in raw:
        return "GBP"
    if "CHF" in raw:
        return "CHF"
    return None


#: Convenience aliases for issuer names whose Yahoo lookup is rate limited or
#: ambiguous. An unknown name stays ``None`` -- a wrong symbol would net the
#: exposure against the wrong underlying, which is worse than not netting it.
UNDERLYING_ALIASES = {
    "rheinmetall ag": "RHM.DE",
    "oracle corporation": "ORCL",
}


def _kind_of(title: str, cells: list[str]) -> str:
    """The product type the page states, not the product types it advertises.

    The page carries links to unrelated knock-outs further down, so scanning the
    whole text would classify a gold ETC as a derivative.
    """
    product = (_value_after(cells, "Produkttyp") or "").lower()
    if "knock-out" in product:
        return "knock_out"
    if "optionsschein" in product or product.startswith("option"):
        return "option"
    head = (title or "").lower()
    if re.search(r"\d+\s*x\s+daily", head):
        return "etc"
    if "etf" in head:
        return "etf"
    asset = (_value_after(cells, "Typ") or "").lower()
    if "edelmetall" in asset:
        return "etc"
    if "aktie" in asset or re.search(r"\baktie\b", head):
        return "share"
    return "unknown"


def _value_after(cells: list[str], label: str, offset: int = 1) -> str | None:
    try:
        index = cells.index(label)
    except ValueError:
        return None
    if index + offset >= len(cells):
        return None
    return cells[index + offset]


def parse_ariva(
    html_text: str,
    key: str,
    *,
    as_of: str | None = None,
    source: str | None = None,
    symbol_lookup: Callable[[str], str | None] | None = None,
    fetched_on: str | None = None,
) -> Instrument:
    """Parse the security page into an :class:`Instrument`.

    Only what the page states is filled in; every other field stays ``None`` so
    that a caller can tell "no ratio published" from "ratio is 1".
    """
    cells = _cells(html_text)
    title = cells[0] if cells else ""
    title = re.sub(r"\s+-\s+Kurs\s+-\s+ARIVA.*$", "", title, flags=re.I)
    kind = _kind_of(title, cells)
    as_of = as_of or date.today().isoformat()
    source = source or f"ariva/{key}"

    name: str | None = None
    underlying = underlying_name = None
    underlying_price = underlying_currency = None
    ratio = barrier = leverage = direction = reset = None

    if kind == "share":
        # The page title carries the venue symbol: "Equinor ASA Aktie (ADR)
        # Aktie (A2JLT6, EQNR) - Kurs ..." -> EQNR.
        name = re.split(r"\s+Aktie\b", title)[0].strip() or None
        symbol_match = re.search(r"\(([A-Z0-9]{4,12}),\s*([A-Z0-9.\-]{1,12})\)", title)
        if symbol_match:
            underlying = symbol_match.group(2)

    if kind in {"etf", "etc", "fund"}:
        name = re.split(r"\s+-\s+", title)[0].strip() or None
        match = re.search(r"(\d+(?:[.,]\d+)?)\s*x\s*daily", title, re.I)
        if match:
            leverage = parse_number(match.group(1))
            reset = "daily"

    if kind == "knock_out" and "Basiswert" in cells:
        index = cells.index("Basiswert")
        values = cells[index + 4:index + 10]
        if values:
            underlying_name = values[0]
        if len(values) > 1:
            underlying_price = parse_number(values[1])
            underlying_currency = _currency_of(values[1])
        # The cells after the price are change (percent) then ratio; skip anything
        # carrying a percent sign so a flat day does not read as the ratio.
        for candidate in values[2:]:
            if "%" in candidate:
                continue
            number = parse_number(candidate)
            if number is not None:
                ratio = number
                break
        strike_cell = _value_after(cells, "Strike")
        barrier = parse_number(strike_cell) if strike_cell else None
        if barrier is not None and underlying_price is not None:
            direction = "long" if barrier < underlying_price else "short"

    if underlying_name:
        if symbol_lookup is not None:
            try:
                underlying = symbol_lookup(underlying_name)
            except Exception:  # noqa: BLE001 -- a symbol miss must not lose the record
                underlying = None
        if underlying is None:
            underlying = UNDERLYING_ALIASES.get(underlying_name.strip().lower())

    return Instrument(
        key=key.strip().upper(),
        name=name,
        kind=kind,
        underlying=underlying,
        underlying_name=underlying_name,
        underlying_price=underlying_price,
        underlying_currency=underlying_currency,
        ratio=ratio,
        direction=direction,
        barrier=barrier,
        leverage=leverage,
        reset=reset,
        source=source,
        as_of=as_of,
        fetched_on=fetched_on or datetime.now().astimezone().date().isoformat(),
    )


# --- fetching --------------------------------------------------------------

def fetch_ariva(key: str, *, timeout: float = 20.0) -> str:
    request = urllib.request.Request(
        ARIVA_URL.format(key=key.strip().upper()),
        headers={"User-Agent": USER_AGENT},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        return response.read().decode("utf-8", "replace")


def yahoo_symbol(name: str, *, timeout: float = 10.0) -> str | None:
    """Resolve an issuer or product name to a Yahoo ticker; ``None`` when unsure."""
    query = urllib.parse.quote(str(name).strip())
    request = urllib.request.Request(
        YAHOO_SEARCH.format(query=query), headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            payload = json.loads(response.read().decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001 -- lookup is a convenience, not a requirement
        return None
    for quote in payload.get("quotes") or []:
        symbol = quote.get("symbol")
        if symbol and quote.get("quoteType") in {"EQUITY", "ETF", "MUTUALFUND", "INDEX"}:
            return str(symbol).upper()
    return None


# --- cache -----------------------------------------------------------------

def load_cache(path: str | Path | None = None) -> dict[str, dict]:
    path = Path(path) if path else default_cache_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    instruments = payload.get("instruments") if isinstance(payload, dict) else None
    if not isinstance(instruments, dict):
        return {}
    return {str(k).upper(): v for k, v in instruments.items() if isinstance(v, dict)}


def save_cache(cache: Mapping[str, Mapping], path: str | Path | None = None) -> Path:
    path = Path(path) if path else default_cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": SCHEMA,
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "instruments": dict(cache),
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def _fresh(entry: Mapping, max_age_days: int, today: str) -> bool:
    stamp = entry.get("fetched_on")
    if not stamp:
        return False
    try:
        age = (date.fromisoformat(today) - date.fromisoformat(str(stamp))).days
    except ValueError:
        return False
    return age <= max_age_days


def resolve(
    keys: Iterable[str],
    *,
    cache_path: str | Path | None = None,
    max_age_days: int = 1,
    force: bool = False,
    fetch: Callable[[str], str] | None = None,
    symbol_lookup: Callable[[str], str | None] | None = None,
    today: str | None = None,
    write: bool = True,
) -> dict[str, dict]:
    """Resolve keys against the cache, fetching only what is stale or missing.

    A failed fetch keeps any older cached entry and never invents one: the
    caller sees the record it had, with its original ``as_of``.
    """
    cache = load_cache(cache_path)
    today = today or date.today().isoformat()
    fetch = fetch or fetch_ariva
    changed = False
    for key in keys:
        symbol = str(key).strip().upper()
        if not symbol:
            continue
        cached = cache.get(symbol)
        if cached and not force and _fresh(cached, max_age_days, today):
            continue
        try:
            html_text = fetch(symbol)
        except Exception:  # noqa: BLE001 -- keep whatever the cache had
            continue
        record = parse_ariva(
            html_text, symbol, as_of=today, symbol_lookup=symbol_lookup, fetched_on=today)
        if record.kind == "unknown" and cached:
            continue
        cache[symbol] = record.to_dict()
        changed = True
    if changed and write:
        try:
            save_cache(cache, cache_path)
        except OSError:
            pass
    return {k.upper(): v for k, v in cache.items()}


def _main(argv: list[str] | None = None) -> int:
    """``python -m tradingagents.instruments resolve MK0L4Q [--force]``."""
    import argparse

    parser = argparse.ArgumentParser(prog="tradingagents.instruments")
    parser.add_argument("keys", nargs="+", help="WKN/ISIN of the instruments to resolve")
    parser.add_argument("--force", action="store_true", help="ignore the cache age")
    parser.add_argument("--cache", default=None, help="cache file (default ~/.tradingagents/instruments.json)")
    args = parser.parse_args(argv)

    records = resolve(args.keys, cache_path=args.cache, force=args.force,
                      symbol_lookup=yahoo_symbol)
    for key in args.keys:
        record = records.get(str(key).strip().upper())
        if record is None:
            print(f"{key}: unresolved")
            continue
        print(json.dumps(record, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())


def for_book(
    account: Mapping[str, Any] | None,
    *,
    cache_path: str | Path | None = None,
) -> dict[str, dict]:
    """The cached records for the instruments a book actually holds."""
    keys = {
        str(p.get("ticker", "")).strip().upper()
        for p in (account or {}).get("positions") or []
        if p.get("ticker")
    }
    cache = load_cache(cache_path)
    return {key: record for key, record in cache.items() if key in keys}
