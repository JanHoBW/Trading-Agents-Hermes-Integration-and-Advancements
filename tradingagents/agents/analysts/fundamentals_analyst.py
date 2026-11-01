import json

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from tradingagents.agents.analysts.turn import take_turn
from tradingagents.agents.context import (
    UNTRUSTED_DATA_PREAMBLE,
    get_instrument_context_from_state,
    get_language_instruction,
    wrap_untrusted,
)
from tradingagents.agents.tools import (
    get_balance_sheet,
    get_cashflow,
    get_fundamentals,
    get_income_statement,
    get_insider_transactions,
    get_ir_facts,
)
from tradingagents.dataflows.fundamental_analytics import (
    analyze_fundamentals,
    records_from_messages,
)

# The tools this analyst is offered; its tool node is built from the same tuple.
TOOLS = (
    get_fundamentals,
    get_balance_sheet,
    get_cashflow,
    get_income_statement,
    get_insider_transactions,
    get_ir_facts,
)


def create_fundamentals_analyst(llm):
    def fundamentals_analyst_node(state):
        current_date = state["trade_date"]
        instrument_context = get_instrument_context_from_state(state)
        analytics = analyze_fundamentals(records_from_messages(state["messages"], as_of=current_date), current_date,
                                         asset_type=state.get("asset_type", "stock"))
        analytics_block = "# FUNDAMENTAL_ANALYTICS: " + json.dumps(analytics, sort_keys=True, allow_nan=False)

        system_message = (
            "You are a researcher tasked with analyzing fundamental information over the past week about a company. Please write a comprehensive report of the company's fundamental information such as financial documents, company profile, basic company financials, and company financial history to gain a full view of the company's fundamental information to inform traders. Make sure to include as much detail as possible. Provide specific, actionable insights with supporting evidence to help traders make informed decisions."
            + " Make sure to append a Markdown table at the end of the report to organize key points in the report, organized and easy to read."
            + " Use the available tools: `get_fundamentals` for comprehensive company analysis, `get_balance_sheet`, `get_cashflow`, and `get_income_statement` for specific financial statements, and `get_insider_transactions` for recent insider buying and selling, and `get_ir_facts` for the issuer's OWN key figures, guidance and calendar with their reference dates."
            + " Where `get_ir_facts` and a vendor statement disagree, name both, say which perimeter each uses (continued vs total-group operations, fiscal year vs quarter), and prefer the issuer's own figure for what the issuer reported — vendor fundamentals frequently mix perimeters."
            + " Never present a prior-year reference date as the current quarter."
            + " Use the deterministic FUNDAMENTAL_ANALYTICS block for derived arithmetic; unavailable means do not fill it in. The SEC CSV preserves RAW filed amounts: 6/9-month columns are YTD, not discrete quarters. Never sum nonconsecutive quarters or sum YTD columns as TTM."
            + " Compute CFO, capex, FCF or conversion only from identical periods and units with reconciled reporting perimeters and tax bases. Missing, zero or negative denominators are unavailable. Tax/perimeter or adjusted-vs-reported differences remain unreconciled. Never invent causes of a numerical divergence."
            + " Forward estimate year is UNKNOWN unless explicitly supplied by the provider. Never assign undated forward EPS to a fiscal year, including from a fiscal year-end date or today's date; UNKNOWN is not NTM. ETF company cash-flow analytics are not_applicable."
            + " " + UNTRUSTED_DATA_PREAMBLE
            + "\n" + wrap_untrusted("deterministic fundamental analytics", analytics_block)
            + get_language_instruction()
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
        if report:
            report += "\n\n## Deterministic fundamental analytics (source units)\n" + analytics_block + "\n"

        return {
            "messages": [result],
            "fundamentals_report": report,
        }

    return fundamentals_analyst_node
