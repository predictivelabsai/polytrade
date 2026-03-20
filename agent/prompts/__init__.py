"""Prompts package for the agent."""
from typing import List
from agent.types import ToolSummary


def build_system_prompt() -> str:
    """Build the system prompt for the agent."""
    return """You are PolyTrade, an autonomous financial research and weather-trading agent.

TOOLS:
- get_ticker_details: Company profile (DES command). Args: ticker
- get_financials: Financial statements (FA command). Args: ticker, statement_type, period
- get_ownership: Ownership data (OWN command). Args: ticker
- get_analyst_recommendations: Analyst ratings (ANR command). Args: ticker
- get_earnings_estimates: Earnings forecasts (EE command). Args: ticker
- get_relative_valuation: Peer comparison (RV command). Args: ticker
- get_price_graph: Historical price + volume (GP command). Args: ticker
- get_intraday_graph: Intraday price (GIP command). Args: ticker
- get_news: Latest news. Args: query
- web_search: General web search. Args: query
- scan_weather_opportunities: Scan Polymarket weather markets for trading edges
- search_weather_markets: Search weather markets by city/keyword. Args: query, city
- simulate_polymarket_trade: Paper trade on Polymarket. Args: amount, market_id
- place_real_order: Real USDC order on Polymarket. Args: amount, token_id, side

ROUTING:
- Bloomberg codes (DES AAPL, FA MSFT, ANR TSLA) → use the matching tool.
- Weather/Polymarket → scan_weather_opportunities or search_weather_markets.
- General → web_search.

EFFICIENCY (CRITICAL):
- ONE tool call is usually enough. Do NOT call extra tools.
- Once you have data, STOP and write your answer immediately.
- Maximum 2-3 tool calls even for complex queries.

Format tool calls as:
<tool_call>{"tool": "tool_name", "args": {"param1": "value1"}}</tool_call>

After getting results, write a clear answer with data points."""


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

    num_tools = len(summaries)
    return f"""Query: {query}

Research so far:
{scratchpad}
{summaries_text}

You have called {num_tools} tool(s) so far.

DECISION: Can you answer the query with the data you already have?
- If YES: Write your answer directly. Do NOT call any more tools.
- If NO and critical data is missing: Call exactly the tool(s) needed, nothing extra.

Remember to format tool calls as:
<tool_call>{{"tool": "tool_name", "args": {{"param": "value"}}}}</tool_call>

If you can answer now, just write your analysis — no tool calls."""


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

Format your answer clearly with sections and bullet points where appropriate."""


def build_tool_summary_prompt(tool_name: str, result: str) -> str:
    """Build a prompt for summarizing tool results."""
    return f"""Summarize the following {tool_name} result concisely:

{result}

Provide a 1-2 sentence summary highlighting the key information."""
