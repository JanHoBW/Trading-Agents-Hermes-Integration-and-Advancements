"""Risk debators and the Portfolio Manager must quantify, not just argue.

The debate was prose-only: a stance could be taken without ever translating it
into a stop distance or a position size. The prompt now requires numbers that are
already in the inputs, fixes the scenario definitions, and forbids inventing
sensitivities the reports never provided.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from tradingagents.agents.context import RISK_QUANTIFICATION
from tradingagents.agents.managers.portfolio_manager import create_portfolio_manager
from tradingagents.agents.risk_mgmt.aggressive_debator import create_aggressive_debator
from tradingagents.agents.risk_mgmt.conservative_debator import create_conservative_debator
from tradingagents.agents.risk_mgmt.neutral_debator import create_neutral_debator

_RISK_STATE = {
    "history": "h", "aggressive_history": "a", "conservative_history": "c",
    "neutral_history": "n", "current_aggressive_response": "", "current_conservative_response": "",
    "current_neutral_response": "", "latest_speaker": "Neutral", "count": 1,
}


def _state():
    return {
        "company_of_interest": "RHM.DE", "trade_date": "2026-10-21", "asset_type": "stock",
        "messages": [], "risk_debate_state": dict(_RISK_STATE),
        "investment_plan": "plan", "trader_investment_plan": "trader plan",
        "market_report": "MKT", "sentiment_report": "S", "news_report": "N",
        "fundamentals_report": "F",
    }


def _capturing_llm():
    captured = {}
    response = MagicMock()
    response.content = "argument"
    llm = MagicMock()

    def invoke(prompt):
        captured["prompt"] = prompt
        return response

    llm.invoke.side_effect = invoke
    return llm, captured


@pytest.mark.unit
@pytest.mark.parametrize("factory", [create_aggressive_debator, create_conservative_debator,
                                     create_neutral_debator])
def test_each_risk_debator_is_required_to_quantify(factory):
    llm, captured = _capturing_llm()
    factory(llm)(_state())
    prompt = captured["prompt"]
    assert RISK_QUANTIFICATION in prompt
    assert "broad index -10 %" in prompt
    assert "never invent" in prompt


@pytest.mark.unit
def test_portfolio_manager_prompt_carries_the_quantification_rule():
    from tradingagents.agents.schemas import PortfolioDecision, PortfolioRating

    captured = {}
    structured = MagicMock()
    structured.invoke.side_effect = lambda prompt: (
        captured.__setitem__("prompt", prompt)
        or PortfolioDecision(rating=PortfolioRating.HOLD, executive_summary="x", investment_thesis="y")
    )
    llm = MagicMock()
    llm.with_structured_output.return_value = structured

    create_portfolio_manager(llm)(_state())
    assert RISK_QUANTIFICATION in captured["prompt"]


def _sized_state():
    """A state whose book, proposal and market levels can be sized."""
    return {
        **_state(),
        "portfolio_account": {"equity": 25000.0, "cash": 5000.0, "currency": "EUR"},
        "trader_proposal": {"action": "Buy", "entry_price": 100.0, "stop_loss": 90.0},
        "market_levels": {"available": True, "last_close": 100.0, "atr": 5.0,
                          "adv_value": 10_000_000.0},
    }


@pytest.mark.unit
@pytest.mark.parametrize("factory", [create_aggressive_debator, create_conservative_debator,
                                     create_neutral_debator])
def test_each_risk_debator_sees_the_deterministic_size(factory):
    """The debate argues about a size the code computed, not one it guessed."""
    llm, captured = _capturing_llm()
    factory(llm)(_sized_state())
    prompt = captured["prompt"]
    assert "Deterministic sizing of the Trader's proposal" in prompt
    assert "25 units" in prompt
    assert "risk budget 1.00 %" in prompt


@pytest.mark.unit
def test_the_pm_prompt_carries_the_deterministic_size():
    from tradingagents.agents.schemas import PortfolioDecision, PortfolioRating

    captured = {}
    structured = MagicMock()
    structured.invoke.side_effect = lambda prompt: (
        captured.__setitem__("prompt", prompt)
        or PortfolioDecision(rating=PortfolioRating.BUY, executive_summary="x",
                             investment_thesis="y")
    )
    llm = MagicMock()
    llm.with_structured_output.return_value = structured

    create_portfolio_manager(llm)(_sized_state())
    assert "25 units" in captured["prompt"]
    assert "Use these figures verbatim" in captured["prompt"]


@pytest.mark.unit
def test_without_a_book_the_prompt_forbids_a_share_count():
    llm, captured = _capturing_llm()
    create_conservative_debator(llm)(_state())
    assert "Deterministic sizing: not available" in captured["prompt"]
    assert "Do not state a share count" in captured["prompt"]


@pytest.mark.unit
def test_constant_names_the_required_scenarios_and_the_missing_data_rule():
    assert "broad index -10 %" in RISK_QUANTIFICATION
    assert "rates +100 bp" in RISK_QUANTIFICATION
    assert "oil +20 %" in RISK_QUANTIFICATION
    assert "EUR/USD +5 %" in RISK_QUANTIFICATION
    assert "not available" in RISK_QUANTIFICATION
