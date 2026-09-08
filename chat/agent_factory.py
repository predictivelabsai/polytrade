"""Build the one canonical LangGraph research agent used by every chat UI."""

from __future__ import annotations

import os
import threading
from functools import lru_cache
from types import SimpleNamespace
from typing import Any

from .prompts import SYSTEM_PROMPT


class ResearchToolRegistry:
    """Lazily create and reuse the existing research tool implementations."""

    def __init__(self) -> None:
        self._agent = None
        self._safe_command_agent = None
        self._lock = threading.Lock()

    def _get_agent(self):
        if self._agent is None:
            with self._lock:
                if self._agent is None:
                    from agent.agent import Agent
                    from agent.types import AgentConfig

                    self._agent = Agent.create(
                        AgentConfig(
                            model=os.getenv("MODEL"),
                            model_provider=os.getenv("MODEL_PROVIDER"),
                            allow_real_trading=False,
                        )
                    )
        return self._agent

    def invoke(self, tool_name: str, **kwargs: Any) -> str:
        if tool_name == "place_real_order":
            return "Tool unavailable: place_real_order"
        tool = self._get_agent().tool_map.get(tool_name)
        if not tool:
            return f"Tool unavailable: {tool_name}"
        return tool.func(**kwargs)

    @property
    def command_agent(self):
        """Agent instance used by the legacy direct-command processor."""
        if self._safe_command_agent is None:
            agent = self._get_agent()
            safe_tools = [
                tool for tool in agent.tools if tool.name != "place_real_order"
            ]
            self._safe_command_agent = SimpleNamespace(
                tools=safe_tools,
                tool_map={tool.name: tool for tool in safe_tools},
                allow_real_trading=False,
            )
        return self._safe_command_agent


@lru_cache(maxsize=1)
def get_tool_registry() -> ResearchToolRegistry:
    return ResearchToolRegistry()


def _build_tools(registry: ResearchToolRegistry):
    from langchain_core.tools import StructuredTool

    def get_stock_financials(ticker: str) -> str:
        """Get financial data including revenue, earnings, and margins."""
        return registry.invoke("get_financials", ticker=ticker.upper())

    def get_ticker_info(ticker: str) -> str:
        """Get a company profile and current quote."""
        return registry.invoke("get_ticker_details", ticker=ticker.upper())

    def get_analyst_ratings(ticker: str) -> str:
        """Get analyst recommendations and price targets."""
        return registry.invoke("get_analyst_recommendations", ticker=ticker.upper())

    def get_stock_news(ticker: str) -> str:
        """Get recent news for a stock ticker."""
        return registry.invoke("get_news", query=ticker.upper())

    def get_earnings_estimates(ticker: str) -> str:
        """Get earnings estimates for a stock ticker."""
        return registry.invoke("get_earnings_estimates", ticker=ticker.upper())

    def search_weather_markets(city: str = "London") -> str:
        """Search Polymarket weather prediction markets for a city."""
        return registry.invoke("search_weather_markets", query="temperature", city=city)

    def scan_opportunities() -> str:
        """Scan for high-edge weather market opportunities."""
        return registry.invoke("scan_weather_opportunities")

    return [
        StructuredTool.from_function(
            get_stock_financials,
            name="get_stock_financials",
            description="Get financial data (revenue, earnings, margins) for a stock.",
        ),
        StructuredTool.from_function(
            get_ticker_info,
            name="get_ticker_info",
            description="Get a company profile and current quote for a stock.",
        ),
        StructuredTool.from_function(
            get_analyst_ratings,
            name="get_analyst_ratings",
            description="Get analyst recommendations and price targets for a stock.",
        ),
        StructuredTool.from_function(
            get_stock_news,
            name="get_stock_news",
            description="Get recent news headlines for a stock ticker.",
        ),
        StructuredTool.from_function(
            get_earnings_estimates,
            name="get_earnings_estimates",
            description="Get earnings estimates for a stock ticker.",
        ),
        StructuredTool.from_function(
            search_weather_markets,
            name="search_weather_markets",
            description="Search Polymarket weather prediction markets for a city.",
        ),
        StructuredTool.from_function(
            scan_opportunities,
            name="scan_opportunities",
            description="Scan for high-edge weather market opportunities.",
        ),
    ]


@lru_cache(maxsize=1)
def get_chat_tools():
    """Return the exact research-only tool set exposed to the chat model."""
    return tuple(_build_tools(get_tool_registry()))


@lru_cache(maxsize=1)
def get_chat_agent():
    """Return the process-wide canonical DeepAgents research harness."""
    from deepagents import create_deep_agent

    from model.llm import LLMProvider

    model = os.getenv("MODEL") or "gpt-4.1-mini"
    provider = os.getenv("MODEL_PROVIDER") or "openai"
    llm = LLMProvider.get_model(model, provider, temperature=0.5)
    tools = get_chat_tools()
    return create_deep_agent(model=llm, tools=list(tools), system_prompt=SYSTEM_PROMPT)
