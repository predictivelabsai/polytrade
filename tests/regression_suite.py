#!/usr/bin/env python
"""
PolyTrade Regression Suite — tests every backend component.

Run:  python -m pytest tests/regression_suite.py -v
      python -m pytest tests/regression_suite.py -v -k "stock"     # just stock tools
      python -m pytest tests/regression_suite.py -v -k "weather"   # just weather
      python -m pytest tests/regression_suite.py -v -k "agent"     # just agent
      python -m pytest tests/regression_suite.py -v -k "db"        # just DB ops
"""

import os
import sys
import uuid
import asyncio
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dotenv import load_dotenv
load_dotenv()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
def agent():
    from agent.agent import Agent, AgentConfig
    config = AgentConfig(
        model=os.getenv("MODEL"),
        model_provider=os.getenv("MODEL_PROVIDER"),
    )
    return Agent.create(config)


@pytest.fixture(scope="session")
def command_processor(agent):
    from components.command_processor import CommandProcessor
    return CommandProcessor(agent)


# ===========================================================================
# 1. STOCK TOOLS
# ===========================================================================

class TestStockTools:
    """Test all stock research tool calls."""

    @pytest.mark.asyncio
    async def test_get_ticker_details(self, agent):
        result = agent.tool_map["get_ticker_details"].func(ticker="AAPL")
        assert result, "get_ticker_details returned empty"
        assert "AAPL" in result or "Apple" in result

    @pytest.mark.asyncio
    async def test_get_financials(self, agent):
        result = agent.tool_map["get_financials"].func(ticker="AAPL")
        assert result, "get_financials returned empty"

    @pytest.mark.asyncio
    async def test_get_analyst_recommendations(self, agent):
        result = agent.tool_map["get_analyst_recommendations"].func(ticker="MSFT")
        assert result, "get_analyst_recommendations returned empty"

    @pytest.mark.asyncio
    async def test_get_earnings_estimates(self, agent):
        result = agent.tool_map["get_earnings_estimates"].func(ticker="TSLA")
        assert result, "get_earnings_estimates returned empty"

    @pytest.mark.asyncio
    async def test_get_relative_valuation(self, agent):
        result = agent.tool_map["get_relative_valuation"].func(ticker="GOOG")
        assert result, "get_relative_valuation returned empty"

    @pytest.mark.asyncio
    async def test_get_ownership(self, agent):
        result = agent.tool_map["get_ownership"].func(ticker="AAPL")
        assert result, "get_ownership returned empty"

    @pytest.mark.asyncio
    async def test_get_price_graph(self, agent):
        result = agent.tool_map["get_price_graph"].func(ticker="AAPL")
        assert result, "get_price_graph returned empty"

    @pytest.mark.asyncio
    async def test_get_intraday_graph(self, agent):
        result = agent.tool_map["get_intraday_graph"].func(ticker="AAPL")
        assert result, "get_intraday_graph returned empty"

    @pytest.mark.asyncio
    async def test_get_news(self, agent):
        result = agent.tool_map["get_news"].func(query="TSLA earnings")
        assert result, "get_news returned empty"


# ===========================================================================
# 2. WEATHER & POLYMARKET TOOLS
# ===========================================================================

class TestWeatherTools:
    """Test weather and Polymarket tool calls."""

    @pytest.mark.asyncio
    async def test_search_weather_markets(self):
        from agent.tools.polymarket_search_tool import WeatherSearchTool
        from agent.tools.polymarket_tool import PolymarketClient
        pm = PolymarketClient()
        st = WeatherSearchTool(pm)
        result = await st.search(query="temperature", city="London", limit=5)
        assert isinstance(result, list), "search_weather_markets should return a list"
        assert len(result) > 0, "No weather markets found for London"
        assert "question" in result[0]

    @pytest.mark.asyncio
    async def test_search_weather_markets_seoul(self):
        from agent.tools.polymarket_search_tool import WeatherSearchTool
        from agent.tools.polymarket_tool import PolymarketClient
        pm = PolymarketClient()
        st = WeatherSearchTool(pm)
        result = await st.search(query="temperature", city="Seoul", limit=5)
        assert isinstance(result, list)
        assert len(result) > 0, "No weather markets found for Seoul"

    @pytest.mark.asyncio
    async def test_search_weather_markets_new_york(self):
        from agent.tools.polymarket_search_tool import WeatherSearchTool
        from agent.tools.polymarket_tool import PolymarketClient
        pm = PolymarketClient()
        st = WeatherSearchTool(pm)
        result = await st.search(query="temperature", city="New York", limit=5)
        assert isinstance(result, list)
        assert len(result) > 0, "No weather markets found for New York"

    @pytest.mark.asyncio
    async def test_scan_weather_opportunities(self, agent):
        if "scan_weather_opportunities" not in agent.tool_map:
            pytest.skip("scan_weather_opportunities not available")
        tool = agent.tool_map["scan_weather_opportunities"]
        result = await tool.coroutine()
        assert isinstance(result, list), "scan should return a list"

    @pytest.mark.asyncio
    async def test_simulate_trade(self):
        from agent.tools.polymarket_search_tool import WeatherSearchTool
        from agent.tools.polymarket_tool import PolymarketClient
        pm = PolymarketClient()
        st = WeatherSearchTool(pm)
        markets = await st.search(query="temperature", city="London", limit=3)
        assert len(markets) > 0, "Need markets to test simulation"
        token_id = markets[0].get("yes_book", {}).get("token_id")
        if not token_id:
            pytest.skip("No token_id available for simulation")
        from agent.tools.polymarket_wrapper import PolymarketWrapper
        from agent.tools.polymarket_clob_api import PolymarketCLOBClient
        from agent.tools.weather_tool import WeatherClient
        import os
        clob = PolymarketCLOBClient()
        wc = WeatherClient(api_key=os.getenv("TOMORROWIO_API_KEY", ""))
        wrapper = PolymarketWrapper(pm, clob, wc)
        result = await wrapper.simulate_polymarket_trade(amount=10.0, market_id=token_id)
        assert isinstance(result, dict), "simulate should return a dict"

    @pytest.mark.asyncio
    async def test_web_search(self, agent):
        if "web_search" not in agent.tool_map:
            pytest.skip("web_search not available (no TAVILY_API_KEY)")
        result = agent.tool_map["web_search"].func(query="weather forecast London")
        assert result, "web_search returned empty"


# ===========================================================================
# 3. WEATHER CLIENT DIRECT
# ===========================================================================

class TestWeatherClient:
    """Test the Tomorrow.io weather client directly."""

    @pytest.mark.asyncio
    async def test_forecast_london(self):
        from agent.tools.weather_tool import WeatherClient
        api_key = os.getenv("TOMORROWIO_API_KEY")
        if not api_key:
            pytest.skip("No TOMORROWIO_API_KEY")
        client = WeatherClient(api_key=api_key)
        forecast = await client.get_forecast("London")
        assert forecast is not None, "Forecast for London is None"
        assert forecast.city == "London"
        assert forecast.high_temp is not None
        await client.close()

    @pytest.mark.asyncio
    async def test_forecast_seoul(self):
        from agent.tools.weather_tool import WeatherClient
        api_key = os.getenv("TOMORROWIO_API_KEY")
        if not api_key:
            pytest.skip("No TOMORROWIO_API_KEY")
        client = WeatherClient(api_key=api_key)
        forecast = await client.get_forecast("Seoul")
        assert forecast is not None
        await client.close()

    @pytest.mark.asyncio
    async def test_probability_calculation(self):
        from agent.tools.weather_tool import WeatherClient
        api_key = os.getenv("TOMORROWIO_API_KEY", "dummy")
        client = WeatherClient(api_key=api_key)
        # If actual == target, probability should be high
        prob = client.calculate_probability(actual_temp=10.0, target_temp=10.0)
        assert prob > 0.8, f"Same temp should have high probability, got {prob}"
        # If actual is far from target, probability should be low
        prob_low = client.calculate_probability(actual_temp=30.0, target_temp=10.0)
        assert prob_low < 0.2, f"Far temp should have low probability, got {prob_low}"


# ===========================================================================
# 4. AGENT CORE
# ===========================================================================

class TestAgent:
    """Test the LangGraph agent end-to-end."""

    @pytest.mark.asyncio
    async def test_agent_simple_query(self, agent):
        """Agent should answer a simple stock question."""
        events = []
        async for event in agent.run("What is AAPL?"):
            events.append(event)
        assert len(events) > 0, "Agent produced no events"
        # Should have at least one DoneEvent or AnswerChunkEvent
        event_types = [type(e).__name__ for e in events]
        assert any("Done" in t or "Answer" in t for t in event_types), \
            f"No Done/Answer event found. Types: {event_types}"

    @pytest.mark.asyncio
    async def test_agent_tool_usage(self, agent):
        """Agent should use tools for stock queries."""
        events = []
        async for event in agent.run("Get financial analysis for NVDA"):
            events.append(event)
        event_types = [type(e).__name__ for e in events]
        assert any("ToolStart" in t for t in event_types), \
            f"Agent didn't use any tools. Types: {event_types}"

    def test_agent_has_all_tools(self, agent):
        """Verify all expected tools are registered."""
        expected = [
            "get_financials", "get_ticker_details", "get_ownership",
            "get_analyst_recommendations", "get_earnings_estimates",
            "get_relative_valuation", "get_price_graph", "get_intraday_graph",
            "get_news",
        ]
        for tool_name in expected:
            assert tool_name in agent.tool_map, f"Missing tool: {tool_name}"

    def test_agent_has_weather_tools(self, agent):
        """Verify Polymarket weather tools are registered."""
        if not os.getenv("TOMORROWIO_API_KEY"):
            pytest.skip("No TOMORROWIO_API_KEY")
        weather_tools = [
            "scan_weather_opportunities", "search_weather_markets",
            "simulate_polymarket_trade",
        ]
        for tool_name in weather_tools:
            assert tool_name in agent.tool_map, f"Missing weather tool: {tool_name}"

    def test_agent_tool_coroutines(self, agent):
        """Verify async tools have coroutine parameter set."""
        async_tools = [
            "scan_weather_opportunities", "search_weather_markets",
            "simulate_polymarket_trade", "place_real_order",
        ]
        for name in async_tools:
            if name in agent.tool_map:
                tool = agent.tool_map[name]
                assert hasattr(tool, "coroutine") and tool.coroutine is not None, \
                    f"Tool {name} missing coroutine parameter"


# ===========================================================================
# 5. COMMAND PROCESSOR
# ===========================================================================

class TestCommandProcessor:
    """Test Bloomberg-style command routing."""

    @pytest.mark.asyncio
    async def test_help_command(self, command_processor):
        handled, query = await command_processor.process_command("help")
        assert handled is True
        assert query is None

    @pytest.mark.asyncio
    async def test_load_command(self, command_processor):
        handled, query = await command_processor.process_command("load AAPL")
        assert handled is True

    @pytest.mark.asyncio
    async def test_fa_command(self, command_processor):
        handled, query = await command_processor.process_command("fa AAPL")
        assert handled is True

    @pytest.mark.asyncio
    async def test_anr_command(self, command_processor):
        handled, query = await command_processor.process_command("anr MSFT")
        assert handled is True

    @pytest.mark.asyncio
    async def test_ee_command(self, command_processor):
        handled, query = await command_processor.process_command("ee TSLA")
        assert handled is True

    @pytest.mark.asyncio
    async def test_poly_weather_command(self, command_processor):
        handled, query = await command_processor.process_command("poly:weather London")
        assert handled is True

    @pytest.mark.asyncio
    async def test_unknown_goes_to_agent(self, command_processor):
        handled, query = await command_processor.process_command("What is the meaning of life?")
        assert handled is False
        assert query is not None

    @pytest.mark.asyncio
    async def test_poly_simbuy_needs_args(self, command_processor):
        handled, query = await command_processor.process_command("poly:simbuy")
        assert handled is True  # Handled (shows error)

    @pytest.mark.asyncio
    async def test_poly_buy_needs_args(self, command_processor):
        handled, query = await command_processor.process_command("poly:buy")
        assert handled is True  # Handled (shows error)


# ===========================================================================
# 6. DATABASE OPERATIONS
# ===========================================================================

class TestDatabase:
    """Test DB CRUD operations."""

    @pytest.mark.asyncio
    async def test_create_and_get_run(self):
        from db.repository import create_run, get_run, finish_run
        run_id = await create_run("test query", "test-model", "test-provider")
        assert run_id, "create_run returned empty"
        run = await get_run(run_id)
        assert run is not None
        assert run["query"] == "test query"
        # Finish it
        await finish_run(run_id, iterations=1, tool_calls=[{"tool": "test"}])
        run = await get_run(run_id)
        assert run["status"] == "completed"

    @pytest.mark.asyncio
    async def test_list_runs(self):
        from db.repository import get_runs
        runs = await get_runs(limit=5)
        assert isinstance(runs, list)

    @pytest.mark.asyncio
    async def test_upsert_and_get_trade(self):
        from db.repository import upsert_trade, get_trade
        trade_id = f"test-{uuid.uuid4().hex[:8]}"
        trade = {
            "trade_id": trade_id,
            "market_id": "test-market-123",
            "amount": 50.0,
            "entry_price": 0.05,
            "trade_type": "paper",
            "status": "OPEN",
        }
        result = await upsert_trade(trade)
        assert result is not None
        fetched = await get_trade(trade_id)
        assert fetched is not None
        assert float(fetched["amount"]) == 50.0

    @pytest.mark.asyncio
    async def test_pnl_summary(self):
        from db.repository import get_pnl_summary
        summary = await get_pnl_summary()
        assert isinstance(summary, dict)
        assert "total_invested" in summary

    @pytest.mark.asyncio
    async def test_pnl_snapshot(self):
        from db.repository import save_pnl_snapshot
        snap = await save_pnl_snapshot()
        assert isinstance(snap, dict)


# ===========================================================================
# 7. CHAT STORE
# ===========================================================================

class TestChatStore:
    """Test chat persistence layer."""

    @pytest.mark.asyncio
    async def test_save_and_load_conversation(self):
        from utils.agui.chat_store import (
            save_conversation, save_message,
            load_conversation_messages, delete_conversation,
        )
        tid = str(uuid.uuid4())
        await save_conversation(tid, title="Regression test chat")
        await save_message(tid, "user", "Hello from regression test")
        await save_message(tid, "assistant", "Hi! This is a test response.")

        msgs = await load_conversation_messages(tid)
        assert len(msgs) == 2
        assert msgs[0]["role"] == "user"
        assert msgs[1]["role"] == "assistant"
        assert "regression test" in msgs[0]["content"]

        # Cleanup
        await delete_conversation(tid)
        msgs_after = await load_conversation_messages(tid)
        assert len(msgs_after) == 0

    @pytest.mark.asyncio
    async def test_list_conversations(self):
        from utils.agui.chat_store import (
            save_conversation, save_message,
            list_conversations, delete_conversation,
        )
        tid = str(uuid.uuid4())
        await save_conversation(tid, title="List test")
        await save_message(tid, "user", "First message for listing")

        convs = await list_conversations(limit=5)
        assert isinstance(convs, list)
        assert any(c["thread_id"] == tid for c in convs)

        # Cleanup
        await delete_conversation(tid)

    @pytest.mark.asyncio
    async def test_conversation_title_from_first_message(self):
        from utils.agui.chat_store import (
            save_conversation, save_message,
            list_conversations, delete_conversation,
        )
        tid = str(uuid.uuid4())
        msg_text = "What is the weather in London?"
        await save_conversation(tid, title=msg_text[:80])
        await save_message(tid, "user", msg_text)

        convs = await list_conversations(limit=5)
        match = [c for c in convs if c["thread_id"] == tid]
        assert len(match) == 1
        assert match[0]["first_msg"] == msg_text

        await delete_conversation(tid)


# ===========================================================================
# 8. BACKTEST ENGINE
# ===========================================================================

class TestBacktestEngine:
    """Test the backtest engine."""

    @pytest.mark.asyncio
    async def test_backtest_london(self):
        from agent.tools.polymarket_tool import PolymarketClient
        from agent.tools.visual_crossing_client import VisualCrossingClient
        from utils.backtest_engine import BacktestEngine

        vc_key = os.getenv("VISUAL_CROSSING_API_KEY")
        if not vc_key:
            pytest.skip("No VISUAL_CROSSING_API_KEY")

        pm = PolymarketClient()
        vc = VisualCrossingClient(api_key=vc_key)
        engine = BacktestEngine(polymarket_client=pm, weather_client=vc)

        from datetime import date
        today = date.today().isoformat()
        result = await engine.run_backtest("London", today, lookback_days=3)
        assert isinstance(result, dict)
        assert "trades" in result
        assert "total_invested" in result
        await pm.close()
        await vc.close()


# ===========================================================================
# 9. TRADING STRATEGY
# ===========================================================================

class TestTradingStrategy:
    """Test trading signal generation."""

    def test_analyze_market_buy_signal(self):
        from agent.tools.trading_strategy import TradingStrategy
        strategy = TradingStrategy(min_edge=0.10, min_confidence=0.50)
        opp = strategy.analyze_market(
            market_id="test-001",
            city="London",
            market_question="Will it be above 10C?",
            market_price=0.05,
            fair_price=0.80,
            liquidity=500.0,
        )
        assert opp.signal.value in ("BUY", "SELL", "HOLD", "SKIP")
        assert opp.edge_percentage > 0

    def test_analyze_market_low_liquidity_skip(self):
        from agent.tools.trading_strategy import TradingStrategy
        strategy = TradingStrategy(min_liquidity=100.0)
        opp = strategy.analyze_market(
            market_id="test-002",
            city="Seoul",
            market_question="Will it snow?",
            market_price=0.05,
            fair_price=0.80,
            liquidity=10.0,  # Below min
        )
        assert opp.signal.value == "SKIP"

    def test_portfolio_simulator(self):
        from agent.tools.trading_strategy import TradingStrategy, PortfolioSimulator
        strategy = TradingStrategy()
        sim = PortfolioSimulator(initial_capital=100.0)
        opp = strategy.analyze_market(
            market_id="test-003",
            city="London",
            market_question="Temperature above 5C?",
            market_price=0.05,
            fair_price=0.90,
            liquidity=500.0,
        )
        trade_result = sim.execute_trade(opp, amount=10.0)
        assert isinstance(trade_result, dict)
        summary = sim.get_summary()
        assert any(k in summary for k in ("capital", "remaining_capital", "current_capital", "initial_capital"))


# ===========================================================================
# 10. LLM PROVIDER
# ===========================================================================

class TestLLMProvider:
    """Test LLM model instantiation."""

    def test_get_model_xai(self):
        from model.llm import LLMProvider
        if not os.getenv("XAI_API_KEY"):
            pytest.skip("No XAI_API_KEY")
        model = LLMProvider.get_model("grok-3-mini", "xai")
        assert model is not None

    def test_get_model_openai(self):
        from model.llm import LLMProvider
        if not os.getenv("OPENAI_API_KEY"):
            pytest.skip("No OPENAI_API_KEY")
        model = LLMProvider.get_model("gpt-4o-mini", "openai")
        assert model is not None

    def test_get_model_anthropic(self):
        from model.llm import LLMProvider
        if not os.getenv("ANTHROPIC_API_KEY"):
            pytest.skip("No ANTHROPIC_API_KEY")
        model = LLMProvider.get_model("claude-sonnet-4-20250514", "anthropic")
        assert model is not None


# ===========================================================================
# 11. VISUAL CROSSING CLIENT
# ===========================================================================

class TestVisualCrossing:
    """Test historical weather data."""

    @pytest.mark.asyncio
    async def test_historical_weather(self):
        from agent.tools.visual_crossing_client import VisualCrossingClient
        api_key = os.getenv("VISUAL_CROSSING_API_KEY")
        if not api_key:
            pytest.skip("No VISUAL_CROSSING_API_KEY")
        client = VisualCrossingClient(api_key=api_key)
        data = await client.get_historical_weather_range("London", "2025-03-01", days=3)
        # Returns list or dict with 'days' key
        if isinstance(data, dict):
            assert "days" in data
        else:
            assert isinstance(data, list) and len(data) > 0
        await client.close()


# ===========================================================================
# 12. POLYMARKET CLOB API
# ===========================================================================

class TestPolymarketCLOB:
    """Test Polymarket CLOB order book."""

    @pytest.mark.asyncio
    async def test_get_order_book(self):
        from agent.tools.polymarket_clob_api import PolymarketCLOBClient
        client = PolymarketCLOBClient()
        # Use a known active token ID
        from agent.tools.polymarket_tool import PolymarketClient
        from agent.tools.polymarket_search_tool import WeatherSearchTool
        pm = PolymarketClient()
        st = WeatherSearchTool(pm)
        markets = await st.search(query="temperature", city="London", limit=3)
        if not markets:
            pytest.skip("No markets available")
        token_id = markets[0].get("yes_book", {}).get("token_id")
        if not token_id:
            pytest.skip("No token_id in first market")
        book = await client.get_order_book(token_id)
        assert book is not None
        assert book.best_bid is not None or book.best_ask is not None


# ===========================================================================
# MAIN
# ===========================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short", "-x"])
