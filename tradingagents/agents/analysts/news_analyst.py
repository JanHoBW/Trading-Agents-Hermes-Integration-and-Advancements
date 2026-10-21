from datetime import date

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from tradingagents.dataflows import windows
from tradingagents.agents.analysts.turn import take_turn
from tradingagents.agents.context import get_instrument_context_from_state, get_language_instruction
from tradingagents.dataflows.event_context import build_event_context
from tradingagents.agents.tools import (
    get_capital_market_disclosures,
    get_global_news,
    get_macro_indicators,
    get_news,
    get_prediction_markets,
)

# The tools this analyst is offered; its tool node is built from the same tuple.
TOOLS = (
    get_news,
    get_global_news,
    get_macro_indicators,
    get_prediction_markets,
    get_capital_market_disclosures,
)


def create_news_analyst(llm):
    def news_analyst_node(state):
        current_date = state["trade_date"]
        asset_type = state.get("asset_type", "stock")
        asset_label = "company" if asset_type == "stock" else "asset"
        instrument_context = get_instrument_context_from_state(state)
        # Two horizons: headlines answer "what is happening now", the injected
        # event context answers "what shaped this thesis" (first-party, 180 d).
        start_date, end_date = windows.headline_window(current_date)
        headline_days = (date.fromisoformat(end_date) - date.fromisoformat(start_date)).days
        event_block = build_event_context(state["company_of_interest"], current_date)

        system_message = (
            f"You are a news researcher tasked with analyzing recent news and trends in the headline window {start_date} to {end_date} ({headline_days} days). Please write a comprehensive report of the current state of the world that is relevant for trading and macroeconomics. Use the available tools: get_news(start_date='{start_date}', end_date='{end_date}') — call it with EXACTLY these dates for news about the {asset_label} under analysis (this is the configured headline window; do not widen it), get_global_news(curr_date, look_back_days, limit) for broader macroeconomic news, get_macro_indicators(indicator, curr_date, look_back_days) to ground macro commentary in actual data from FRED (e.g. 'cpi', 'core_pce', 'unemployment', 'fed_funds_rate', '10y_treasury', 'yield_curve'), and get_prediction_markets(topic, limit) for live market-implied probabilities of forward-looking events (e.g. 'Fed rate cut', 'recession 2026', geopolitical or sector events), and get_capital_market_disclosures(ticker, trade_date) for OFFICIAL German issuer disclosures (ad-hoc Art. 17 MAR, voting-rights notifications WpHG §40/41, capital-market information such as buy-backs/AGM/capital measures, §50 WpHG) — use it for German listings instead of inferring corporate events from news articles; it self-selects, so for other markets it reports that no configured source covers the instrument. Do not describe an absent disclosure as an absent event. Provide specific, actionable insights with supporting evidence to help traders make informed decisions."
            + """ Make sure to append a Markdown table at the end of the report to organize key points in the report, organized and easy to read."""
            + get_language_instruction()
            + (f"\n\n{event_block}" if event_block else "")
        )

        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are a helpful AI assistant, collaborating with other assistants."
                    " Use the provided tools to progress towards answering the question."
                    " If you are unable to fully answer, that's OK; another assistant with different tools"
                    " will help where you left off. Execute what you can to make progress."
                    " Report what your tools support; another agent decides the trade."
                    " You have access to the following tools: {tool_names}."
                    " Today's date is {current_date}; treat it as 'now' for all analysis and tool-call date ranges. {instrument_context}\n"
                    "{system_message}",
                ),
                MessagesPlaceholder(variable_name="messages"),
            ]
        )

        prompt = prompt.partial(system_message=system_message)
        prompt = prompt.partial(tool_names=", ".join([tool.name for tool in TOOLS]))
        prompt = prompt.partial(current_date=current_date)
        prompt = prompt.partial(instrument_context=instrument_context)

        result, report = take_turn(prompt, llm, TOOLS, state["messages"])

        return {
            "messages": [result],
            "news_report": report,
        }

    return news_analyst_node
