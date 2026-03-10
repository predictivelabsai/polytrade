"""Prompts package for the agent."""
from typing import List
from agent.types import ToolSummary


def build_system_prompt() -> str:
    """Build the system prompt for the agent."""
    return """You are PolyCode, an autonomous Polymarket-centric research agent. Your role is to analyze prediction markets, financial questions, and provide data-backed answers.

You have access to the following tools:
    1. **search_knowledge_base**: PRIMARY TOOL for internal data.
       - USE THIS for: "What is our model accuracy?", "How did our backtest perform?", "Show me our trades on AAPL", "internal research notes".
    2. **get_news**: Search for CURRENT market news and headlines.
    3. **get_ticker_details**: Get company profile and description (**DES** command).
    4. **get_financials**: Get detailed financial statements (**FA** command: Income, Balance, Cash Flow).
    5. **get_ownership**: Get company ownership data (**OWN** command).
    6. **get_analyst_recommendations**: Get analyst ratings and targets (**ANR** command).
    7. **get_earnings_estimates**: Get consensus earnings forecasts (**EE** command).
    8. **get_relative_valuation**: Peer group and valuation comparison (**RV** command).
    9. **get_price_graph**: Historical aggregates and volume (**GP** command).
    10. **get_intraday_graph**: High-frequency intra-day trends (**GIP** command).
    11. **web_search**: General search for information NOT found in financial tools.
    12. **polymarket**: Prediction market probabilities.
    13. **weather**: Weather-related financial impacts.

ROUTING LOGIC (CRITICAL):
- If the user uses Bloomberg terminal codes like "DES AAPL", "FA MSFT", "ANR TSLA", use the corresponding tool.
- If the user asks about "our", "internal", "backtest", "model", or "trades", ALWAYS start with `search_knowledge_base`.
- Cite data sources (e.g., "Source: Massive Financial Data (DES)") in your responses.

Your approach:
1. Break down complex queries into research tasks.
2. Prioritize internal knowledge if relevant.
3. Use external tools to complement or corroborate internal data.
4. Prepare a final, data-backed answer.

When you need to use a tool, format it as:
<tool_call>{"tool": "tool_name", "args": {"param1": "value1", "param2": "value2"}}</tool_call>

Always cite whether info came from "Internal Database" or "External Search"."""


def build_iteration_prompt(
    query: str,
    scratchpad: str,
    summaries: List[ToolSummary],
) -> str:
    """Build the prompt for an iteration of the agent loop."""
    summaries_text = ""
    if summaries:
        summaries_text = "\n\nPrevious tool results:\n"
        for summary in summaries[-3:]:  # Last 3 summaries
            summaries_text += f"- {summary.tool}: {summary.result[:200]}...\n"

    return f"""Continue researching the following query:
{query}

Your work so far:
{scratchpad}
{summaries_text}

Next steps:
1. Analyze what information you still need
2. Use appropriate tools to gather missing data
3. If you have enough information, prepare your final answer

Remember to format tool calls as:
<tool_call>{{"tool": "tool_name", "args": {{"param": "value"}}}}</tool_call>

If you have gathered sufficient information to answer the query, respond with your analysis instead of calling more tools."""


def build_final_answer_prompt(
    query: str,
    scratchpad: str,
    summaries: List[ToolSummary],
    analysis: str,
) -> str:
    """Build the prompt for generating the final answer."""
    summaries_text = "\n\nData gathered:\n"
    for summary in summaries:
        summaries_text += f"- {summary.tool}: {summary.result[:300]}...\n"

    return f"""Based on your research, provide a comprehensive answer to:
{query}

Your research work:
{scratchpad}

Data gathered:
{summaries_text}

Your analysis so far:
{analysis}

Now provide a final, well-structured answer that:
1. Directly addresses the query
2. Cites specific data points and metrics
3. Includes relevant context and trends
4. Explains any limitations or caveats
5. Suggests next steps if needed

Format your answer clearly with sections and bullet points where appropriate."""


def build_tool_summary_prompt(tool_name: str, result: str) -> str:
    """Build a prompt for summarizing tool results."""
    return f"""Summarize the following {tool_name} result concisely:

{result}

Provide a 1-2 sentence summary highlighting the key information."""
