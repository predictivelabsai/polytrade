"""
PolyTrade AG-UI — 3-pane chat interface powered by LangGraph + astream_events.

Left pane:  Navigation / help expanders
Center:     Chat (WebSocket streaming)
Right:      Thinking trace / artifacts (toggled)

Launch:  python agui_app.py          # port 4003
         uvicorn agui_app:app --port 4003 --reload
"""

import os
import sys
import uuid as _uuid
import logging
from pathlib import Path
from typing import Dict, Optional

sys.path.insert(0, str(Path(__file__).parent.absolute()))

from dotenv import load_dotenv

load_dotenv()

from fasthtml.common import *

logger = logging.getLogger(__name__)

from utils.agui import setup_agui, get_chat_styles, StreamingCommand

# ---------------------------------------------------------------------------
# LangGraph Agent with StructuredTool wrappers
# ---------------------------------------------------------------------------

from langchain_openai import ChatOpenAI
from langchain_core.tools import StructuredTool
from langgraph.prebuilt import create_react_agent

SYSTEM_PROMPT = (
    "You are PolyTrade, an AI financial research and Polymarket weather trading assistant. "
    "You have tools to look up stock data, news, analyst ratings, and Polymarket weather markets. "
    "Use your tools when users ask about specific stocks or market data. "
    "Be concise and use markdown formatting with tables where appropriate. "
    "Users can type CLI commands directly in chat (e.g. load AAPL, fa NVDA, "
    "poly:weather London, poly:backtest Seoul 7) and they will be executed automatically. "
    "For stock queries, always use the appropriate tool to get real data."
)


# --- Tool wrappers ---

def get_stock_financials(ticker: str) -> str:
    """Get financial data (revenue, earnings, margins) for a stock ticker."""
    try:
        from agent.agent import Agent
        from agent.types import AgentConfig
        agent = Agent.create(AgentConfig())
        tool = agent.tool_map.get("get_financials")
        if tool:
            return tool.func(ticker=ticker.upper())
        return f"Tool not available for {ticker}"
    except Exception as e:
        return f"Error: {e}"


def get_ticker_info(ticker: str) -> str:
    """Get company profile and current quote for a stock ticker."""
    try:
        from agent.agent import Agent
        from agent.types import AgentConfig
        agent = Agent.create(AgentConfig())
        tool = agent.tool_map.get("get_ticker_details")
        if tool:
            return tool.func(ticker=ticker.upper())
        return f"Tool not available for {ticker}"
    except Exception as e:
        return f"Error: {e}"


def get_analyst_ratings(ticker: str) -> str:
    """Get analyst recommendations and price targets for a stock."""
    try:
        from agent.agent import Agent
        from agent.types import AgentConfig
        agent = Agent.create(AgentConfig())
        tool = agent.tool_map.get("get_analyst_recommendations")
        if tool:
            return tool.func(ticker=ticker.upper())
        return f"Tool not available for {ticker}"
    except Exception as e:
        return f"Error: {e}"


def get_stock_news(ticker: str) -> str:
    """Get latest news headlines for a stock ticker."""
    try:
        from agent.agent import Agent
        from agent.types import AgentConfig
        agent = Agent.create(AgentConfig())
        tool = agent.tool_map.get("get_news")
        if tool:
            return tool.func(ticker=ticker.upper())
        return f"Tool not available for {ticker}"
    except Exception as e:
        return f"Error: {e}"


def get_earnings_estimates(ticker: str) -> str:
    """Get earnings estimates for a stock ticker."""
    try:
        from agent.agent import Agent
        from agent.types import AgentConfig
        agent = Agent.create(AgentConfig())
        tool = agent.tool_map.get("get_earnings_estimates")
        if tool:
            return tool.func(ticker=ticker.upper())
        return f"Tool not available for {ticker}"
    except Exception as e:
        return f"Error: {e}"


def search_weather_markets(city: str = "London") -> str:
    """Search Polymarket weather prediction markets for a city."""
    try:
        from agent.agent import Agent
        from agent.types import AgentConfig
        agent = Agent.create(AgentConfig())
        tool = agent.tool_map.get("search_weather_markets")
        if tool:
            return tool.func(query="temperature", city=city)
        return "Tool not available"
    except Exception as e:
        return f"Error: {e}"


def scan_opportunities() -> str:
    """Scan for high-edge weather market opportunities across all cities."""
    try:
        from agent.agent import Agent
        from agent.types import AgentConfig
        agent = Agent.create(AgentConfig())
        tool = agent.tool_map.get("scan_weather_opportunities")
        if tool:
            return tool.func()
        return "Tool not available"
    except Exception as e:
        return f"Error: {e}"


# ---------------------------------------------------------------------------
# Build LangGraph agent from tool functions
# ---------------------------------------------------------------------------

_provider = os.getenv("MODEL_PROVIDER", "xai")
_model = os.getenv("MODEL", "grok-3-mini")

_llm_kwargs = {
    "model": _model,
    "temperature": 0.5,
    "max_tokens": 3000,
    "streaming": True,
}

if _provider == "xai":
    _llm_kwargs["api_key"] = os.getenv("XAI_API_KEY")
    _llm_kwargs["base_url"] = "https://api.x.ai/v1"
elif _provider == "openai":
    _llm_kwargs["api_key"] = os.getenv("OPENAI_API_KEY")
elif _provider == "anthropic":
    from langchain_anthropic import ChatAnthropic
    _llm_kwargs.pop("max_tokens", None)

TOOLS = [
    StructuredTool.from_function(get_stock_financials, name="get_stock_financials",
        description="Get financial data (revenue, earnings, margins) for a stock."),
    StructuredTool.from_function(get_ticker_info, name="get_ticker_info",
        description="Get company profile and current quote for a stock."),
    StructuredTool.from_function(get_analyst_ratings, name="get_analyst_ratings",
        description="Get analyst recommendations and price targets for a stock."),
    StructuredTool.from_function(get_stock_news, name="get_stock_news",
        description="Get latest news headlines for a stock ticker."),
    StructuredTool.from_function(get_earnings_estimates, name="get_earnings_estimates",
        description="Get earnings estimates for a stock."),
    StructuredTool.from_function(search_weather_markets, name="search_weather_markets",
        description="Search Polymarket weather prediction markets for a city."),
    StructuredTool.from_function(scan_opportunities, name="scan_opportunities",
        description="Scan for high-edge weather market opportunities across all cities."),
]

if _provider == "anthropic":
    llm = ChatAnthropic(**_llm_kwargs)
else:
    llm = ChatOpenAI(**_llm_kwargs)

langgraph_agent = create_react_agent(model=llm, tools=TOOLS, prompt=SYSTEM_PROMPT)


# ---------------------------------------------------------------------------
# FastHTML app
# ---------------------------------------------------------------------------

app, rt = fast_app(
    exts="ws",
    secret_key=os.urandom(32).hex(),
    hdrs=[
        Script(src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"),
    ],
)


# ---------------------------------------------------------------------------
# CLI command interceptor — routes poly:*, load, fa, etc. to CommandProcessor
# ---------------------------------------------------------------------------

_STRUCTURED_PREFIXES = {
    "load", "news", "fa", "anr", "ee", "rv", "own", "gp", "gip",
    "quote", "scan", "help", "h", "?",
}

_STREAMING_COMMANDS = {"poly:backtest", "poly:backtestv2", "poly:backtest2", "poly:predict", "scan"}


async def _command_interceptor(msg: str, session):
    """Detect CLI commands and route to CommandProcessor. Returns markdown or None."""
    cmd_lower = msg.strip().lower()
    first_word = cmd_lower.split()[0] if cmd_lower.split() else ""

    is_command = (
        first_word in _STRUCTURED_PREFIXES or
        first_word.startswith("poly:")
    )

    if not is_command:
        return None

    # Help
    if cmd_lower in ("help", "h", "?"):
        return _AGUI_HELP

    # Long-running commands → StreamingCommand
    if first_word in _STREAMING_COMMANDS:
        return StreamingCommand(msg, session)

    # Execute via CommandProcessor
    try:
        from agent.agent import Agent
        from agent.types import AgentConfig
        from components.command_processor import CommandProcessor
        import io
        from rich.console import Console

        config = AgentConfig(
            model=os.getenv("MODEL"),
            model_provider=os.getenv("MODEL_PROVIDER"),
        )
        agent = Agent.create(config)
        cp = CommandProcessor(agent)

        # Capture Rich output
        buf = io.StringIO()
        original_console = cp.console
        cp.console = Console(file=buf, force_terminal=False, width=120, no_color=True)

        try:
            is_handled, agent_query = await cp.process_command(msg)
            output = buf.getvalue().strip()

            if not is_handled and agent_query:
                return None  # Let AI handle it

            if output:
                return f"```\n{output}\n```"
            return "Command executed."
        finally:
            cp.console = original_console
    except Exception as e:
        return f"# Error\n\n```\n{e}\n```"


_AGUI_HELP = """# PolyTrade Commands

## Stock Research
- `load AAPL` — Company profile & quote
- `fa NVDA` — Financial analysis
- `anr MSFT` — Analyst recommendations
- `ee TSLA` — Earnings estimates
- `rv GOOG` — Relative valuation
- `own AAPL` — Institutional ownership
- `gp AAPL` — Price graph
- `gip AAPL` — Intraday price graph
- `news TSLA` — Latest news
- `quote AAPL` — Real-time quote
- `scan` — Scan weather opportunities

## Polymarket Weather
- `poly:weather London` — Search weather markets
- `poly:backtest Seoul 7` — Multi-day backtest
- `poly:backtestv2 Seoul 7` — Cross-sectional YES/NO backtest
- `poly:predict London 2` — Forward-looking prediction

## Trading
- `poly:simbuy 50 <id>` — Simulate trade
- `poly:buy 50 <id>` — Real USDC buy order
- `poly:sell 50 <id>` — Real sell order
- `poly:portfolio` — On-chain portfolio
- `poly:paperportfolio` — Paper portfolio

## AI Chat
Type any question to chat with AI about stocks & weather markets.
"""

agui = setup_agui(app, langgraph_agent, command_interceptor=_command_interceptor)


# ---------------------------------------------------------------------------
# CSS — 3-pane layout
# ---------------------------------------------------------------------------

LAYOUT_CSS = """
/* === Layout — Dark Trading Terminal Theme === */

* { box-sizing: border-box; margin: 0; padding: 0; }

body {
  font-family: 'SF Mono', 'Fira Code', ui-monospace, monospace;
  background: #0a0d14;
  color: #e2e8f0;
  height: 100vh;
  overflow: hidden;
}

/* === 3-Pane Grid === */
.app-layout {
  display: grid;
  grid-template-columns: 260px 1fr;
  height: 100vh;
  transition: grid-template-columns 0.3s ease;
}

.app-layout .right-pane { display: none; }

.app-layout.right-open {
  grid-template-columns: 260px 1fr 380px;
}

.app-layout.right-open .right-pane { display: flex; }

/* === Left Pane (Sidebar) === */
.left-pane {
  background: #0f1117;
  border-right: 1px solid #1e2a3a;
  display: flex;
  flex-direction: column;
  overflow-y: auto;
  padding: 1rem;
  gap: 1.25rem;
}

.brand {
  font-size: 1.25rem;
  font-weight: 700;
  color: #10b981;
  text-decoration: none;
  padding-bottom: 0.75rem;
  border-bottom: 1px solid #1e2a3a;
}

.brand:hover { color: #34d399; }

.sidebar-header {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding-bottom: 0.75rem;
  border-bottom: 1px solid #1e2a3a;
}

.sidebar-header .brand { border-bottom: none; padding-bottom: 0; }

.chat-badge {
  font-size: 0.6rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  background: #059669;
  color: #d1fae5;
  padding: 0.15rem 0.4rem;
  border-radius: 0.25rem;
}

/* === New Chat Button === */
.new-chat-btn {
  width: 100%;
  padding: 0.5rem;
  background: transparent;
  border: 1px dashed #2a3040;
  border-radius: 0.5rem;
  color: #10b981;
  font-family: inherit;
  font-size: 0.8rem;
  cursor: pointer;
  transition: all 0.2s;
}

.new-chat-btn:hover { background: #0d2818; border-color: #10b981; }

/* === Help Expanders === */
.help-section {
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
  padding-bottom: 0.5rem;
  border-bottom: 1px solid #1e2a3a;
}

.help-toggle {
  display: flex;
  align-items: center;
  width: 100%;
  padding: 0.35rem 0.5rem;
  background: none;
  border: none;
  border-radius: 0.375rem;
  color: #94a3b8;
  font-family: inherit;
  font-size: 0.8rem;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.15s;
  text-align: left;
}

.help-toggle:hover { background: #141821; color: #e2e8f0; }

.help-cnt {
  margin-left: auto;
  margin-right: 0.35rem;
  font-size: 0.65rem;
  color: #64748b;
  background: #1a1f2e;
  padding: 0.1rem 0.4rem;
  border-radius: 1rem;
}

.help-arrow { color: #64748b; font-size: 0.65rem; transition: transform 0.2s; }
.help-toggle.open .help-arrow { transform: rotate(90deg); }

.help-list {
  display: none;
  flex-direction: column;
  gap: 0.1rem;
  padding-left: 0.5rem;
}

.help-list.open { display: flex; }

.help-item {
  display: block;
  width: 100%;
  padding: 0.3rem 0.5rem;
  background: none;
  border: none;
  border-radius: 0.25rem;
  color: #10b981;
  font-family: inherit;
  font-size: 0.7rem;
  cursor: pointer;
  text-align: left;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  transition: all 0.15s;
}

.help-item:hover { background: #0d2818; color: #34d399; }

/* === Sidebar Footer === */
.sidebar-footer {
  font-size: 0.7rem;
  color: #475569;
  text-align: center;
  padding-top: 0.5rem;
  margin-top: auto;
}

/* === Center Pane (Chat) === */
.center-pane {
  display: flex;
  flex-direction: column;
  height: 100vh;
  background: #0f1117;
  overflow: hidden;
}

.center-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 0.75rem 1rem;
  background: #141821;
  border-bottom: 1px solid #1e2a3a;
  min-height: 3rem;
}

.center-header h2 {
  font-size: 0.95rem;
  font-weight: 600;
  color: #10b981;
}

.toggle-trace-btn {
  padding: 0.3rem 0.7rem;
  background: transparent;
  color: #64748b;
  border: 1px solid #2a3040;
  border-radius: 0.375rem;
  font-family: inherit;
  font-size: 0.75rem;
  cursor: pointer;
  transition: all 0.2s;
}

.toggle-trace-btn:hover {
  background: #1a1f2e;
  color: #10b981;
  border-color: #10b981;
}

.center-chat {
  flex: 1;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.center-chat > div { flex: 1; display: flex; flex-direction: column; height: 100%; }

.center-chat .chat-container {
  height: 100%; flex: 1; border: none; border-radius: 0;
  background: #0f1117; display: flex; flex-direction: column;
}

.center-chat .chat-messages { background: #0f1117; flex: 1; }
.center-chat .chat-input { background: #141821; border-top: 1px solid #1e2a3a; }
.center-chat .chat-input-field { background: #0f1117; border-color: #2a3040; color: #e2e8f0; }
.center-chat .chat-input-field:focus { border-color: #10b981; box-shadow: 0 0 0 3px rgba(16, 185, 129, 0.1); }
.center-chat .chat-message.chat-assistant .chat-message-content { background: #1a1f2e; color: #e2e8f0; }
.center-chat .chat-message.chat-user .chat-message-content { background: #064e3b; color: #d1fae5; }
.center-chat .chat-message.chat-tool .chat-message-content { background: #1a1f2e; color: #64748b; }

/* === Right Pane (Trace) === */
.right-pane {
  background: #0f1117;
  border-left: 1px solid #1e2a3a;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.right-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 0.75rem 1rem;
  border-bottom: 1px solid #1e2a3a;
}

.right-header h3 { font-size: 0.85rem; font-weight: 600; color: #10b981; }

.close-trace-btn {
  background: none; border: none; color: #64748b; cursor: pointer;
  font-size: 1.1rem; padding: 0.2rem;
}
.close-trace-btn:hover { color: #e2e8f0; }

.right-tabs { display: flex; border-bottom: 1px solid #1e2a3a; }

.right-tab {
  flex: 1; padding: 0.5rem; text-align: center; font-size: 0.75rem;
  color: #64748b; cursor: pointer; border: none; background: none; font-family: inherit;
}

.right-tab:hover { color: #94a3b8; }
.right-tab.active { color: #10b981; border-bottom: 2px solid #10b981; }

.right-content {
  flex: 1; overflow-y: auto; padding: 1rem;
  display: flex; flex-direction: column;
}

/* === Trace Entries === */
.trace-entry {
  display: flex; flex-direction: column; gap: 0.25rem;
  padding: 0.5rem 0.75rem; margin-bottom: 0.5rem;
  border-left: 3px solid #2a3040; border-radius: 0 0.25rem 0.25rem 0;
  background: #141821; font-size: 0.8rem;
  animation: trace-in 0.2s ease-out;
}

@keyframes trace-in {
  from { opacity: 0; transform: translateX(-0.5rem); }
  to { opacity: 1; transform: translateX(0); }
}

.trace-label { color: #94a3b8; font-weight: 500; }
.trace-detail { color: #64748b; font-size: 0.75rem; font-family: inherit; word-break: break-all; }

.trace-run-start { border-left-color: #10b981; }
.trace-run-start .trace-label { color: #10b981; }

.trace-run-end { border-left-color: #34d399; }
.trace-run-end .trace-label { color: #34d399; }

.trace-streaming { border-left-color: #a78bfa; }
.trace-streaming .trace-label { color: #a78bfa; }

.trace-tool-active { border-left-color: #fbbf24; }
.trace-tool-active .trace-label { color: #fbbf24; }

.trace-tool-done { border-left-color: #34d399; }
.trace-tool-done .trace-label { color: #34d399; }

.trace-done { border-left-color: #34d399; }
.trace-done .trace-label { color: #34d399; }

.trace-error { border-left-color: #f87171; }
.trace-error .trace-label { color: #f87171; }

#trace-content { font-size: 0.8rem; color: #94a3b8; overflow-y: auto; flex: 1; }
#artifact-content { display: none; }
#detail-content { display: none; }

/* === Scrollbars (dark) === */
.left-pane, .right-content, #trace-content {
  scrollbar-width: thin;
  scrollbar-color: #2a3040 transparent;
}
.left-pane::-webkit-scrollbar, .right-content::-webkit-scrollbar { width: 5px; }
.left-pane::-webkit-scrollbar-thumb, .right-content::-webkit-scrollbar-thumb { background: #2a3040; border-radius: 3px; }

/* === Conversation List === */
.conv-section {
  display: flex;
  flex-direction: column;
  max-height: 35vh;
  overflow-y: auto;
  padding-bottom: 0.5rem;
  border-bottom: 1px solid #1e2a3a;
  scrollbar-width: thin;
  scrollbar-color: #2a3040 transparent;
}

.conv-section::-webkit-scrollbar { width: 5px; }
.conv-section::-webkit-scrollbar-thumb { background: #2a3040; border-radius: 3px; }

.conv-item {
  display: block;
  padding: 0.5rem 0.6rem;
  color: #94a3b8;
  font-size: 0.8rem;
  text-decoration: none;
  border-radius: 0.375rem;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  transition: all 0.15s;
}

.conv-item:hover { background: #141821; color: #e2e8f0; }

.conv-active {
  background: #0d2818;
  color: #10b981;
  border-left: 2px solid #10b981;
}

.conv-empty {
  font-size: 0.75rem;
  color: #475569;
  font-style: italic;
  padding: 0.5rem;
}

/* === Responsive === */
@media (max-width: 768px) {
  .app-layout { grid-template-columns: 1fr !important; }
  .left-pane { display: none; }
  .right-pane { display: none; }
}
"""


# ---------------------------------------------------------------------------
# Help expanders — collapsible command reference in sidebar
# ---------------------------------------------------------------------------

_HELP_CATEGORIES = [
    ("Stock Research", [
        ("load AAPL", "Company profile & quote"),
        ("fa NVDA", "Financial analysis"),
        ("anr MSFT", "Analyst recommendations"),
        ("ee TSLA", "Earnings estimates"),
        ("rv GOOG", "Relative valuation"),
        ("own AAPL", "Institutional ownership"),
        ("gp AAPL", "Price graph"),
        ("news TSLA", "Latest news"),
    ]),
    ("Weather Markets", [
        ("poly:weather London", "Search weather markets"),
        ("poly:weather Seoul", "Seoul weather markets"),
        ("poly:weather New York", "New York weather markets"),
        ("scan", "Scan all weather opportunities"),
    ]),
    ("Backtest & Predict", [
        ("poly:backtest London 7", "7-day backtest"),
        ("poly:backtestv2 Seoul 7", "Cross-sectional backtest"),
        ("poly:predict London 2", "Forward prediction"),
    ]),
    ("Trading", [
        ("poly:simbuy 50", "Simulate buy $50"),
        ("poly:buy 50", "Real USDC buy"),
        ("poly:sell 50", "Real USDC sell"),
        ("poly:portfolio", "On-chain portfolio"),
        ("poly:paperportfolio", "Paper portfolio"),
    ]),
]


def _help_expanders():
    """Build collapsible help category groups for the sidebar."""
    groups = []
    for cat_name, items in _HELP_CATEGORIES:
        cat_id = f"help-{cat_name.lower().replace(' ', '-').replace('&', '')}"
        toggle_btn = Button(
            cat_name,
            Span(f"{len(items)}", cls="help-cnt"),
            Span(">", cls="help-arrow"),
            cls="help-toggle",
            onclick=f"toggleGroup('{cat_id}')",
        )
        tool_items = []
        for cmd, desc in items:
            tool_items.append(
                Button(
                    cmd,
                    cls="help-item",
                    onclick=f"fillChat({repr(cmd)})",
                    title=desc,
                )
            )
        tool_list = Div(*tool_items, cls="help-list", id=cat_id)
        groups.append(toggle_btn)
        groups.append(tool_list)

    return Div(*groups, cls="help-section")


# ---------------------------------------------------------------------------
# Left pane builder
# ---------------------------------------------------------------------------

def _left_pane(session):
    """Build the left sidebar."""
    parts = []

    # Header: Brand + CHAT badge
    parts.append(
        Div(
            A("PolyTrade", href="/", cls="brand"),
            Span("CHAT", cls="chat-badge"),
            cls="sidebar-header",
        )
    )

    # New Chat button
    parts.append(
        Button("+ New Chat", cls="new-chat-btn", onclick="window.location.href='/?new=1'")
    )

    # Recent conversations
    parts.append(
        Div(
            H4("Recent", style="font-size:0.8rem;font-weight:600;color:#64748b;margin-bottom:0.5rem;"),
            Div(
                id="conv-list",
                hx_get="/agui-conv/list",
                hx_trigger="load",
                hx_swap="innerHTML",
            ),
            cls="conv-section",
        )
    )

    # Help expanders
    parts.append(_help_expanders())

    # Footer
    parts.append(Div("Powered by PolyTrade", cls="sidebar-footer"))

    return Div(*parts, cls="left-pane", id="left-pane")


# ---------------------------------------------------------------------------
# Right pane builder
# ---------------------------------------------------------------------------

def _right_pane():
    """Build the right pane: thinking trace."""
    return Div(
        Div(
            H3("Trace"),
            Div(
                Button(
                    "Clear",
                    cls="close-trace-btn",
                    onclick="document.getElementById('trace-content').innerHTML="
                    "'<div style=\"color:#475569;font-style:italic\">"
                    "Tool calls and reasoning will appear here.</div>';",
                    style="margin-right: 0.5rem; font-size: 0.7rem;",
                ),
                Button("x", cls="close-trace-btn", onclick="toggleRightPane()"),
                style="display: flex; align-items: center;",
            ),
            cls="right-header",
        ),
        Div(
            Button("Thinking", cls="right-tab active", onclick="showTab('trace')"),
            Button("Artifacts", cls="right-tab", onclick="showTab('artifact')"),
            cls="right-tabs",
        ),
        Div(
            Div(
                Div("Tool calls and reasoning will appear here during agent runs.",
                    style="color: #475569; font-style: italic;"),
                id="trace-content",
            ),
            Div(
                Div("Charts and data will appear here when tools generate visual output.",
                    style="color: #475569; font-style: italic;"),
                id="artifact-content",
            ),
            Div(id="detail-content"),
            cls="right-content",
        ),
        cls="right-pane",
    )


# ---------------------------------------------------------------------------
# Layout JS
# ---------------------------------------------------------------------------

LAYOUT_JS = """
function toggleRightPane() {
    var layout = document.querySelector('.app-layout');
    layout.classList.toggle('right-open');
}

function toggleGroup(catId) {
    var list = document.getElementById(catId);
    if (!list) return;
    list.classList.toggle('open');
    var btn = list.previousElementSibling;
    if (btn) btn.classList.toggle('open');
}

function fillChat(cmd) {
    if (window._aguiProcessing) return;
    var ta = document.getElementById('chat-input');
    var fm = document.getElementById('chat-form');
    if (ta && fm) {
        ta.value = cmd;
        ta.focus();
    }
}

function showTab(tab) {
    var trace = document.getElementById('trace-content');
    var artifact = document.getElementById('artifact-content');
    var detail = document.getElementById('detail-content');
    var tabs = document.querySelectorAll('.right-tab');

    tabs.forEach(function(t) { t.classList.remove('active'); });
    if (trace) trace.style.display = 'none';
    if (artifact) artifact.style.display = 'none';
    if (detail) detail.style.display = 'none';

    if (tab === 'trace') {
        if (trace) trace.style.display = 'flex';
        tabs[0].classList.add('active');
    } else if (tab === 'artifact') {
        if (artifact) artifact.style.display = 'block';
        tabs[1].classList.add('active');
    }
}
"""


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@rt("/")
def get(session, new: str = "", thread: str = ""):
    if new == "1":
        thread_id = str(_uuid.uuid4())
        session["thread_id"] = thread_id
    elif thread:
        thread_id = thread
        session["thread_id"] = thread_id
    else:
        thread_id = session.get("thread_id")
        if not thread_id:
            thread_id = str(_uuid.uuid4())
            session["thread_id"] = thread_id

    return (
        Title("PolyTrade Chat"),
        Style(LAYOUT_CSS),
        Div(
            _left_pane(session),
            Div(
                Div(
                    H2("PolyTrade Chat"),
                    Button("Trace", cls="toggle-trace-btn", onclick="toggleRightPane()"),
                    cls="center-header",
                ),
                Div(agui.chat(thread_id), cls="center-chat"),
                cls="center-pane",
            ),
            _right_pane(),
            cls="app-layout",
        ),
        Script(LAYOUT_JS),
    )


@rt("/agui-conv/list")
async def conv_list(session):
    """Return the conversation list for the sidebar (DB-backed)."""
    from utils.agui.chat_store import list_conversations
    current_tid = session.get("thread_id", "")
    convs = await list_conversations(user_id=None, limit=20)
    items = []
    for c in convs:
        tid = c["thread_id"]
        title = c.get("first_msg") or c.get("title") or "New chat"
        if len(title) > 40:
            title = title[:40] + "..."
        cls = "conv-item conv-active" if tid == current_tid else "conv-item"
        items.append(A(title, href=f"/?thread={tid}", cls=cls))
    if not items:
        items.append(Div("No conversations yet", cls="conv-empty"))
    return Div(*items)


@rt("/health")
def health():
    return {"status": "ok", "service": "agui"}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.getenv("AGUI_PORT", "4003"))
    reload_flag = os.getenv("AGUI_RELOAD", "true").lower() == "true"
    print(f"PolyTrade AG-UI starting on http://localhost:{port}")
    serve(port=port, reload=reload_flag)
