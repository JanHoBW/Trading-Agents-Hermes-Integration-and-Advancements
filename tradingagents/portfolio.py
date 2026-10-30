"""The caller's book, as the decision agents see it.

Optional input to a run: what is held, at what average price, and how much cash
is free. Without it the agents cannot tell adding to a full position from
opening a new one. Three states are distinct and must stay so: a position, a
flat book, and no context at all, since treating "not provided" as "flat" would
invent a fact about the caller's account.

Broker-neutral by construction: quantities are generic units and the currency is
whatever label the caller passes, so nothing here implies a venue or an
execution path.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError


class Position(BaseModel):
    ticker: str = Field(description="Instrument symbol, e.g. AAPL")
    quantity: float = Field(description="Signed units held; negative is short")
    average_price: float | None = Field(default=None, description="Average entry price per unit")
    market_value: float | None = Field(
        default=None,
        description="Current market value in the account currency, as the broker states it",
    )
    acquired_on: str | None = Field(
        default=None,
        description="Date the position was opened (ISO); a daily-reset note's risk depends on it",
    )
    analysis_ticker: str | None = Field(
        default=None,
        description=(
            "Symbol this line is analyzed and sized under when it differs from the booked "
            "one (depot WKN, ADR line); exposure is grouped and netted under it"
        ),
    )


class PortfolioContext(BaseModel):
    cash: float | None = Field(default=None, description="Free cash available")
    currency: str | None = Field(default=None, description="Currency label for cash and prices")
    equity: float | None = Field(
        default=None,
        description="Total account value in `currency`; required for risk-based sizing",
    )
    risk_per_trade_pct: float | None = Field(
        default=None, ge=0, le=100,
        description="Maximum loss per trade as a percentage of equity; overrides the run config",
    )
    max_position_pct: float | None = Field(
        default=None, gt=0, le=100,
        description="Maximum single-position notional as a percentage of equity",
    )
    positions: list[Position] = Field(default_factory=list)

    def position_in(self, ticker: str) -> Position | None:
        symbol = ticker.strip().upper()
        return next((p for p in self.positions
                     if p.ticker.upper() == symbol
                     or (p.analysis_ticker or "").strip().upper() == symbol), None)

    def render(self, ticker: str) -> str:
        """The portfolio block for the decision agents, led by the analyzed instrument."""
        symbol = ticker.strip().upper()
        held = self.position_in(symbol)
        if held is None:
            lines = [f"- No current position in {symbol}"]
        else:
            price = f", average price {held.average_price:,.2f}" if held.average_price is not None else ""
            lines = [f"- Current position in {symbol}: {held.quantity:,.4g} units{price}"]
        if self.cash is not None:
            lines.append(f"- Cash available: {self.cash:,.2f}{' ' + self.currency if self.currency else ''}")
        if self.equity is not None:
            lines.append(f"- Account equity: {self.equity:,.2f}"
                         f"{' ' + self.currency if self.currency else ''}")
        if self.risk_per_trade_pct is not None:
            lines.append(f"- Risk budget per trade: {self.risk_per_trade_pct:g} % of equity")
        if self.max_position_pct is not None:
            lines.append(f"- Maximum position size: {self.max_position_pct:g} % of equity")
        others = [p for p in self.positions if p is not held]
        if others:
            lines.append("- Other positions: " + ", ".join(f"{p.ticker.upper()} {p.quantity:,.4g}" for p in others))
        return "Portfolio at the analysis date:\n" + "\n".join(lines)

    def fingerprint(self) -> str:
        """Stable digest of the book, so a changed one cannot resume a stale run."""
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()[:12]


def load_portfolio(path: str | Path) -> PortfolioContext:
    """Read a portfolio JSON file, failing before the run rather than mid-graph."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return PortfolioContext.model_validate(data)
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        raise ValueError(f"portfolio file {path} is not usable: {exc}") from exc
