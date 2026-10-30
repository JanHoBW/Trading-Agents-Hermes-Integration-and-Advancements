"""Fund baskets and listed option contracts, kept in the data layer.

The exposure code asks "what is inside this fund" and "what does this contract
look like"; the vendor library is imported here, so an outage is a vendor fact
rather than a claim about the instrument.
"""

from __future__ import annotations

from typing import Any


def top_holdings(symbol: str) -> list[dict[str, Any]]:
    """Top holdings of a fund as ``[{symbol, name, weight}]``; empty when unpublished."""
    import yfinance as yf

    frame = yf.Ticker(symbol).funds_data.top_holdings
    if frame is None or not hasattr(frame, "iterrows") or len(frame) == 0:
        return []
    holdings: list[dict[str, Any]] = []
    for index, row in frame.iterrows():
        try:
            weight = float(row.get("Holding Percent"))
        except (TypeError, ValueError):
            continue
        if weight <= 0:
            continue
        holdings.append({"symbol": str(index).strip().upper(),
                         "name": str(row.get("Name") or "").strip(),
                         "weight": round(weight, 6)})
    return holdings


def search_symbol(name: str, quote_types: tuple[str, ...] = ("ETF", "MUTUALFUND")) -> str | None:
    """First vendor symbol matching an instrument name, restricted to quote types."""
    import yfinance as yf

    for quote in yf.Search(name).quotes or []:
        if quote.get("quoteType") in quote_types and quote.get("symbol"):
            return str(quote["symbol"]).upper()
    return None


def option_contract(symbol: str, expiry: str, option_type: str, strike: float) -> dict[str, Any] | None:
    """Spot and implied volatility for one listed contract, or ``None``."""
    import yfinance as yf

    ticker = yf.Ticker(symbol)
    chain = ticker.option_chain(expiry)
    frame = chain.calls if option_type == "call" else chain.puts
    row = frame[frame["strike"].astype(float) == float(strike)]
    if row.empty:
        return None
    history = ticker.history(period="5d")
    if not len(history):
        return None
    quote = row.iloc[0]
    return {"underlying_price": float(history["Close"].iloc[-1]),
            "implied_volatility": float(quote["impliedVolatility"]),
            "bid": float(quote.get("bid") or 0.0),
            "ask": float(quote.get("ask") or 0.0),
            "last_price": float(quote.get("lastPrice") or 0.0),
            "source": f"yahoo/{symbol}/option_chain"}
