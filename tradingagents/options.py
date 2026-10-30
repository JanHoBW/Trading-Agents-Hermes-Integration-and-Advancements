"""Delta for listed options, so an option position has an exposure too.

An option is not its premium: two contracts of a call are exposure to a hundred
shares each, scaled by delta. Delta is computed from the contract the vendor
quotes (strike, expiry, implied volatility) with Black-Scholes -- no model
estimates a number here either; if the chain states no usable volatility the
delta stays ``None`` and the position is reported as unknown.

Only listed, standard contracts are supported: an OCC symbol such as
``ORCL261218C00060000`` resolves; a European warrant (``kind: knock_out``) keeps
using its ratio instead.
"""

from __future__ import annotations

import math
import re
from datetime import date
from typing import Callable

OCC_PATTERN = re.compile(r"^(?P<root>[A-Z]{1,6})(?P<date>\d{6})(?P<side>[CP])(?P<strike>\d{8})$")
DEFAULT_MULTIPLIER = 100.0

#: Implied volatility outside this band is a vendor artefact, not a price.
IV_BAND = (0.005, 5.0)


def normal_cdf(value: float) -> float:
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


def parse_occ(symbol: str) -> dict | None:
    """Split an OCC option symbol into underlying, expiry, side and strike."""
    match = OCC_PATTERN.match(str(symbol or "").strip().upper())
    if not match:
        return None
    raw = match.group("date")
    try:
        expiry = date(2000 + int(raw[:2]), int(raw[2:4]), int(raw[4:6]))
    except ValueError:
        return None
    return {
        "underlying": match.group("root"),
        "expiry": expiry.isoformat(),
        "option_type": "call" if match.group("side") == "C" else "put",
        "strike": int(match.group("strike")) / 1000.0,
    }


def black_scholes_delta(
    spot: float,
    strike: float,
    expiry: str,
    as_of: str,
    volatility: float,
    option_type: str = "call",
    rate: float = 0.0,
) -> float | None:
    """Raw delta of one contract (0..1 call, -1..0 put), or ``None``.

    The contract multiplier is not applied here: it belongs to the position, so
    a reader can compare a delta with the vendor's own greeks.
    """
    try:
        spot, strike = float(spot), float(strike)
        volatility = float(volatility)
    except (TypeError, ValueError):
        return None
    if spot <= 0 or strike <= 0 or not (IV_BAND[0] <= volatility <= IV_BAND[1]):
        return None
    try:
        days = (date.fromisoformat(expiry) - date.fromisoformat(as_of)).days
    except (TypeError, ValueError):
        return None
    if days <= 0:
        return None
    years = days / 365.0
    vol_sqrt_t = volatility * math.sqrt(years)
    if vol_sqrt_t <= 0:
        return None
    d1 = (math.log(spot / strike) + (rate + 0.5 * volatility ** 2) * years) / vol_sqrt_t
    delta = normal_cdf(d1)
    if option_type.strip().lower() == "put":
        delta -= 1.0
    return round(delta, 4)


def black_scholes_price(
    spot: float,
    strike: float,
    expiry: str,
    as_of: str,
    volatility: float,
    option_type: str = "call",
    rate: float = 0.0,
) -> float | None:
    """Theoretical premium of one contract, used to invert an implied volatility."""
    try:
        spot, strike, volatility = float(spot), float(strike), float(volatility)
        days = (date.fromisoformat(expiry) - date.fromisoformat(as_of)).days
    except (TypeError, ValueError):
        return None
    if spot <= 0 or strike <= 0 or volatility <= 0 or days <= 0:
        return None
    years = days / 365.0
    vol_sqrt_t = volatility * math.sqrt(years)
    d1 = (math.log(spot / strike) + (rate + 0.5 * volatility ** 2) * years) / vol_sqrt_t
    d2 = d1 - vol_sqrt_t
    discount = math.exp(-rate * years)
    if option_type.strip().lower() == "put":
        return strike * discount * normal_cdf(-d2) - spot * normal_cdf(-d1)
    return spot * normal_cdf(d1) - strike * discount * normal_cdf(d2)


def implied_volatility(
    price: float,
    spot: float,
    strike: float,
    expiry: str,
    as_of: str,
    option_type: str = "call",
    rate: float = 0.0,
    low: float = 0.01,
    high: float = 3.0,
    tolerance: float = 1e-4,
) -> float | None:
    """Volatility whose model price matches ``price``, by bisection.

    Vendors routinely publish 0.0 for a thin contract; the traded premium is
    then the only price there is, and inverting it is arithmetic rather than an
    assumption. A price outside the model's range yields ``None``.
    """
    try:
        price = float(price)
    except (TypeError, ValueError):
        return None
    if price <= 0:
        return None
    for _ in range(80):
        middle = (low + high) / 2.0
        model = black_scholes_price(spot, strike, expiry, as_of, middle, option_type, rate)
        if model is None:
            return None
        if abs(model - price) < tolerance:
            return round(middle, 6)
        if model < price:
            low = middle
        else:
            high = middle
    return None


def fetch_contract(occ_symbol: str, as_of: str) -> dict | None:
    """Spot, implied volatility and multiplier for a listed contract."""
    parsed = parse_occ(occ_symbol)
    if parsed is None:
        return None
    try:
        from tradingagents.dataflows.vendors.yahoo import funds as yahoo_funds

        contract = yahoo_funds.option_contract(
            parsed["underlying"], parsed["expiry"], parsed["option_type"], parsed["strike"])
    except Exception:  # noqa: BLE001 -- a chain miss is reported, not guessed
        return None
    if not contract:
        return None
    return {**parsed, "underlying_price": contract["underlying_price"],
            "implied_volatility": contract.get("implied_volatility"),
            "bid": contract.get("bid"), "ask": contract.get("ask"),
            "last_price": contract.get("last_price"),
            "multiplier": DEFAULT_MULTIPLIER, "source": contract.get("source")}


def resolve_option(
    occ_symbol: str,
    *,
    as_of: str,
    fetch: Callable[[str, str], dict | None] | None = None,
) -> dict | None:
    """The instrument record for a listed option, delta included."""
    fetch = fetch or fetch_contract
    contract = None
    try:
        contract = fetch(occ_symbol, as_of)
    except Exception:  # noqa: BLE001 -- keep the record without a delta
        contract = None
    if not contract:
        return None
    spot = contract.get("underlying_price")
    volatility = contract.get("implied_volatility")
    iv_source = "vendor"
    option_price = None
    if volatility is None or not (IV_BAND[0] <= float(volatility) <= IV_BAND[1]):
        # A vendor that publishes 0.0 for a thin contract still publishes a
        # premium; invert that instead of dropping the position.
        bid = float(contract.get("bid") or 0.0)
        ask = float(contract.get("ask") or 0.0)
        option_price = (bid + ask) / 2.0 if bid > 0 and ask > 0 else float(
            contract.get("last_price") or 0.0)
        volatility = implied_volatility(
            option_price, spot, contract["strike"], contract["expiry"], as_of,
            contract["option_type"])
        iv_source = "derived_from_price"
    if volatility is None or not (IV_BAND[0] <= float(volatility) <= IV_BAND[1]):
        return None
    delta = black_scholes_delta(
        spot, contract["strike"], contract["expiry"], as_of,
        volatility, contract["option_type"])
    return {
        "key": str(occ_symbol).strip().upper(),
        "kind": "option",
        "name": f"{contract['underlying']} {contract['expiry']} {contract['strike']:g} "
                f"{contract['option_type']}",
        "underlying": contract["underlying"],
        "underlying_price": contract["underlying_price"],
        "underlying_currency": contract.get("underlying_currency") or "USD",
        "option_type": contract["option_type"],
        "barrier": contract["strike"],
        "delta": delta,
        "multiplier": contract.get("multiplier") or DEFAULT_MULTIPLIER,
        "implied_volatility": round(float(volatility), 6),
        "iv_source": iv_source,
        "option_price": option_price,
        "source": contract.get("source"),
        "as_of": as_of,
    }
