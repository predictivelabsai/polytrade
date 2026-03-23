"""Core agent implementation with LangGraph architecture."""
import json
import asyncio
import re
import os
from typing import AsyncGenerator, Optional, Any, Dict, List, Annotated, TypedDict, Union
from datetime import datetime
from operator import add

from langchain_core.tools import StructuredTool
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, BaseMessage
from langgraph.graph import StateGraph, END

from agent.types import (
    AgentConfig,
    AgentEvent,
    ToolStartEvent,
    ToolEndEvent,
    ToolErrorEvent,
    AnswerStartEvent,
    AnswerChunkEvent,
    DoneEvent,
    LogEvent,
    StreamResetEvent,
    ToolSummary,
)
from model.llm import LLMProvider
from agent.tools import (
    FinancialsTool, 
    TickerTool, 
    StockAnalysisTool,
    StockGraphTool,
    NewsTool, 
    WebSearchTool,
    PolymarketCLOBClient,
    PolymarketWrapper,
    WeatherSearchTool
)
from agent.tools.polymarket_tool import PolymarketClient
from agent.tools.weather_tool import WeatherClient
from agent.prompts import (
    build_system_prompt,
    build_iteration_prompt,
    build_final_answer_prompt,
)


class AgentState(TypedDict):
    """State management for the LangGraph agent."""
    messages: Annotated[List[BaseMessage], add]
    query: str
    scratchpad: str
    summaries: Annotated[List[ToolSummary], add]
    iteration: int
    final_answer: Optional[str]
    events: List[AgentEvent] # For internal event passing if needed


class Agent:
    """LangGraph-based agent for financial research."""

    DEFAULT_MAX_ITERATIONS = 3

    def __init__(
        self,
        config: AgentConfig,
        tools: List[StructuredTool],
        system_prompt: str,
    ):
        """Initialize the agent."""
        self.model = config.model or os.getenv("MODEL")
        self.model_provider = config.model_provider or os.getenv("MODEL_PROVIDER")
        self.max_iterations = config.max_iterations or self.DEFAULT_MAX_ITERATIONS
        self.tools = tools
        self.tool_map = {tool.name: tool for tool in tools}
        self.system_prompt = system_prompt
        self.signal = config.signal
        self.llm = LLMProvider.get_model(self.model, self.model_provider)
        self.graph = self._build_graph()

    def _build_graph(self) -> StateGraph:
        """Build the LangGraph state machine."""
        builder = StateGraph(AgentState)

        # Define nodes
        builder.add_node("call_model", self._call_model)
        builder.add_node("execute_tools", self._execute_tools)
        builder.add_node("generate_final_answer", self._generate_final_answer)

        # Define edges
        builder.set_entry_point("call_model")
        
        builder.add_conditional_edges(
            "call_model",
            self._should_continue,
            {
                "tools": "execute_tools",
                "final": "generate_final_answer",
                "end": END
            }
        )
        
        builder.add_edge("execute_tools", "call_model")
        builder.add_edge("generate_final_answer", END)

        return builder.compile()

    async def _call_model(self, state: AgentState) -> Dict[str, Any]:
        """Node: Call LLM to decide next step."""
        iteration = state["iteration"] + 1
        query = state["query"]
        messages = state["messages"]
        scratchpad = state["scratchpad"]
        summaries = state["summaries"]

        # Build iteration prompt
        iteration_prompt = build_iteration_prompt(query, scratchpad, summaries)
        
        # Build actual messages for this call
        # We always include the system prompt and the current message history
        actual_messages = [SystemMessage(content=self.system_prompt)] + messages + [HumanMessage(content=iteration_prompt)]

        # Call LLM (native async — enables streaming via astream_events)
        response = await self.llm.ainvoke(actual_messages)
        response_text = response.content if hasattr(response, "content") else str(response)

        return {
            "messages": [AIMessage(content=response_text)],
            "iteration": iteration
        }

    async def _execute_tools(self, state: AgentState) -> Dict[str, Any]:
        """Node: Parse and execute tool calls."""
        last_message = state["messages"][-1]
        response_text = last_message.content
        tool_calls = self._parse_tool_calls(response_text)
        
        new_summaries = []
        new_scratchpadLines = []

        for tool_call in tool_calls:
            tool_name = tool_call.get("tool", "")
            tool_args = tool_call.get("args", {})

            if tool_name not in self.tool_map:
                error_msg = f"Unknown tool: {tool_name}"
                new_scratchpadLines.append(f"Tool Error ({tool_name}): {error_msg}")
                continue

            try:
                tool = self.tool_map[tool_name]
                result = await asyncio.to_thread(tool.func, **tool_args)

                summary = ToolSummary(
                    tool=tool_name,
                    args=tool_args,
                    result=str(result)[:1000],
                    timestamp=datetime.now().isoformat(),
                )
                new_summaries.append(summary)
                new_scratchpadLines.append(f"Tool ({tool_name}): {json.dumps(tool_args)}\nResult: {str(result)[:500]}")

            except Exception as e:
                error_msg = f"Tool execution failed: {str(e)}"
                new_scratchpadLines.append(f"Tool Error ({tool_name}): {error_msg}")

        return {
            "summaries": new_summaries,
            "scratchpad": state["scratchpad"] + "\n" + "\n".join(new_scratchpadLines)
        }

    async def _generate_final_answer(self, state: AgentState) -> Dict[str, Any]:
        """Node: Synthesize final answer."""
        query = state["query"]
        scratchpad = state["scratchpad"]
        summaries = state["summaries"]
        last_thought = state["messages"][-1].content

        final_prompt = build_final_answer_prompt(query, scratchpad, summaries, last_thought)
        actual_messages = [SystemMessage(content=self.system_prompt)] + state["messages"] + [HumanMessage(content=final_prompt)]

        response = await self.llm.ainvoke(actual_messages)
        final_answer = response.content if hasattr(response, "content") else str(response)

        return {"final_answer": final_answer}

    def _should_continue(self, state: AgentState) -> str:
        """Edge logic: Check if we need more tools or if we are done."""
        if state["iteration"] >= self.max_iterations:
            return "end"

        last_message = state["messages"][-1]
        response_text = last_message.content
        tool_calls = self._parse_tool_calls(response_text)

        if tool_calls:
            return "tools"
        # Skip generate_final_answer (extra LLM call) — the model's
        # response IS the answer. run() fallback uses last_model_response.
        return "end"

    @staticmethod
    def create(config: AgentConfig = None) -> "Agent":
        """Create a new Agent instance with tools."""
        if config is None:
            config = AgentConfig()

        financial_tool = FinancialsTool()
        ticker_tool = TickerTool()
        analysis_tool = StockAnalysisTool()
        graph_tool = StockGraphTool()
        news_tool = NewsTool()
        web_tool = WebSearchTool()

        tools = [
            StructuredTool(
                name="get_financials",
                description="Get financial statements for a company (FA command). Parameters: ticker (stock symbol, e.g. 'AAPL'), statement_type ('income', 'balance', 'cash_flow', or 'all'), and period ('annual' or 'quarterly').",
                func=financial_tool.get_financials,
                args_schema=None,
            ),
            StructuredTool(
                name="get_ticker_details",
                description="Get detailed information about a stock ticker symbol (DES command).",
                func=ticker_tool.get_ticker_details,
                args_schema=None,
            ),
            StructuredTool(
                name="get_ownership",
                description="Get ownership summary for a security (OWN command).",
                func=ticker_tool.get_ownership,
                args_schema=None,
            ),
            StructuredTool(
                name="get_analyst_recommendations",
                description="Get analyst buy/sell/hold ratings (ANR command).",
                func=analysis_tool.get_analyst_recommendations,
                args_schema=None,
            ),
            StructuredTool(
                name="get_earnings_estimates",
                description="Get earnings estimates and consensus forecasts (EE command).",
                func=analysis_tool.get_earnings_estimates,
                args_schema=None,
            ),
            StructuredTool(
                name="get_relative_valuation",
                description="Compares a security against its peers (RV command).",
                func=analysis_tool.get_relative_valuation,
                args_schema=None,
            ),
            StructuredTool(
                name="get_price_graph",
                description="Get a standard price graph data with volume (GP command).",
                func=graph_tool.get_price_graph,
                args_schema=None,
            ),
            StructuredTool(
                name="get_intraday_graph",
                description="Intraday price graph for analyzing trends within a single day (GIP command).",
                func=graph_tool.get_intraday_graph,
                args_schema=None,
            ),
            StructuredTool(
                name="get_news",
                description="Get latest financial news for a company or topic.",
                func=news_tool.get_news,
                args_schema=None,
            ),
            # Polymarket Weather Analysis Tool
        ]

        # Initialize Polymarket Clients
        pm_api_key = os.getenv("POLYMARKET_API_KEY")
        tomorrow_api_key = os.getenv("TOMORROWIO_API_KEY")
        pm_private_key = os.getenv("POLYMARKET_PRIVATE_KEY")

        if tomorrow_api_key:
            pm_client = PolymarketClient(api_key=pm_api_key)
            clob_client = PolymarketCLOBClient(key=pm_private_key)
            weather_client = WeatherClient(api_key=tomorrow_api_key)
            pm_wrapper = PolymarketWrapper(pm_client, clob_client, weather_client)

            def _make_sync(async_fn):
                """Create a sync wrapper for an async function (fallback for non-async callers)."""
                import asyncio, functools
                @functools.wraps(async_fn)
                def wrapper(*args, **kwargs):
                    return asyncio.run(async_fn(*args, **kwargs))
                return wrapper

            tools.append(
                StructuredTool(
                    name="scan_weather_opportunities",
                    description="Scan Polymarket for weather-related trading opportunities. Returns a list of markets with calculated edge and confidence.",
                    func=_make_sync(pm_wrapper.scan_weather_opportunities),
                    coroutine=pm_wrapper.scan_weather_opportunities,
                    args_schema=None,
                )
            )
            tools.append(
                StructuredTool(
                    name="place_real_order",
                    description="Place a REAL order on Polymarket. Parameters: amount (USDC), token_id (CLOB Token ID), side (optional, 'BUY' or 'SELL').",
                    func=_make_sync(pm_client.create_order),
                    coroutine=pm_client.create_order,
                    args_schema=None,
                )
            )
            tools.append(
                StructuredTool(
                    name="simulate_polymarket_trade",
                    description="Simulate a trade on Polymarket by walking the CLOB order book for a specific amount. Parameters: amount (USDC), market_id (Token ID).",
                    func=_make_sync(pm_wrapper.simulate_polymarket_trade),
                    coroutine=pm_wrapper.simulate_polymarket_trade,
                    args_schema=None,
                )
            )

            search_tool = WeatherSearchTool(pm_client)
            tools.append(
                StructuredTool(
                    name="search_weather_markets",
                    description="Search for weather-related markets on Polymarket by city or keyword. Parameters: query (optional), city (optional).",
                    func=_make_sync(search_tool.search),
                    coroutine=search_tool.search,
                    args_schema=None,
                )
            )

        if os.getenv("TAVILY_API_KEY"):
            tools.append(
                StructuredTool(
                    name="web_search",
                    description="Search the web for general financial information",
                    func=web_tool.search,
                    args_schema=None,
                )
            )

        system_prompt = build_system_prompt()
        return Agent(config, tools, system_prompt)

    async def run(
        self,
        query: str,
        chat_history: Optional[List[Dict[str, str]]] = None,
    ) -> AsyncGenerator[AgentEvent, None]:
        """Run the agent and stream events via LangGraph.

        Streams LLM tokens in real-time using on_chat_model_stream events.
        If the model decides to call tools, a StreamResetEvent is emitted
        so the UI can clear any optimistically-streamed text.
        """

        # Convert chat history to messages (skip empty content to avoid API errors)
        messages = []
        if chat_history:
            for item in chat_history:
                content = (item.get("content") or "").strip()
                if not content:
                    continue
                if item.get("role") == "user":
                    messages.append(HumanMessage(content=content))
                elif item.get("role") == "assistant":
                    messages.append(AIMessage(content=content))

        initial_state = {
            "messages": messages,
            "query": query,
            "scratchpad": f"Query: {query}\n",
            "summaries": [],
            "iteration": 0,
            "final_answer": None,
        }

        last_iteration = 0
        all_tool_calls = []
        last_model_response = ""
        streaming_buffer: list[str] = []
        is_streaming = False  # currently streaming tokens to UI

        try:
            async for event in self.graph.astream_events(initial_state, version="v2"):
                kind = event["event"]

                # ── Real-time LLM token streaming ──────────────────────────
                if kind == "on_chat_model_stream":
                    chunk = event.get("data", {}).get("chunk")
                    if chunk and hasattr(chunk, "content") and chunk.content:
                        token = chunk.content
                        streaming_buffer.append(token)
                        # Stop streaming if we detect tool-call XML
                        if "<tool_call>" in "".join(streaming_buffer):
                            continue
                        # Optimistically stream token to UI
                        if not is_streaming:
                            is_streaming = True
                        yield AnswerChunkEvent(chunk=token)
                    continue

                if kind != "on_chain_end":
                    continue

                # ── Node completion handling ────────────────────────────────
                node_name = event.get("metadata", {}).get("langgraph_node")
                if not node_name:
                    continue

                data = event.get("data", {}).get("output")
                if not data or not isinstance(data, dict):
                    continue

                if node_name == "call_model":
                    last_iteration = data.get("iteration", last_iteration)
                    msgs = data.get("messages", [])
                    if msgs:
                        last_model_response = msgs[-1].content

                    # Check if model requested tool calls
                    tool_calls = self._parse_tool_calls(last_model_response)
                    if tool_calls:
                        # Model is calling tools — reset any streamed text
                        if is_streaming:
                            yield StreamResetEvent()
                            is_streaming = False
                        yield LogEvent(
                            message=f"Agent Thinking (Iteration {last_iteration})...",
                            level="thought",
                        )
                    # Reset buffer for next iteration
                    streaming_buffer = []

                elif node_name == "execute_tools":
                    for summary in data.get("summaries", []):
                        yield ToolStartEvent(tool=summary.tool, args=summary.args)
                        yield ToolEndEvent(tool=summary.tool, result=summary.result[:500])
                    # Keepalive: signal that agent is generating the answer
                    # (prevents WebSocket idle timeout during second LLM call)
                    yield LogEvent(message="Generating answer...", level="thought")

                elif node_name == "generate_final_answer":
                    final_answer = data.get("final_answer", "")
                    if final_answer:
                        last_model_response = final_answer

            # ── Graph completed — emit DoneEvent with final answer ─────────
            answer = last_model_response or "No answer was generated."
            answer = re.sub(r"<tool_call>.*?</tool_call>", "", answer, flags=re.DOTALL).strip()
            if not answer:
                answer = "No answer was generated."
            yield DoneEvent(
                answer=answer,
                iterations=last_iteration,
                tool_calls=all_tool_calls,
            )
        except Exception as e:
            yield ToolErrorEvent(tool="agent", error=str(e))
            yield DoneEvent(answer=f"An error occurred: {str(e)}", iterations=last_iteration)
        
    def _parse_tool_calls(self, response_text: str) -> List[Dict[str, Any]]:
        """Parse tool calls from LLM response."""
        tool_calls = []
        pattern = r"<tool_call>(.*?)</tool_call>"
        matches = re.findall(pattern, response_text, re.DOTALL)
        for match in matches:
            try:
                tool_call = json.loads(match)
                tool_calls.append(tool_call)
            except json.JSONDecodeError:
                continue
        return tool_calls
