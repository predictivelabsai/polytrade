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

# ---------------------------------------------------------------------------
# Google OAuth setup (optional — gracefully skip if no creds)
# ---------------------------------------------------------------------------

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
_oauth_enabled = bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)

_authlib_oauth = None
if _oauth_enabled:
    from authlib.integrations.starlette_client import OAuth as AuthlibOAuth
    _authlib_oauth = AuthlibOAuth()
    _authlib_oauth.register(
        name="google",
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"},
    )

_GOOGLE_SVG = """<svg width="18" height="18" viewBox="0 0 18 18" xmlns="http://www.w3.org/2000/svg">
<path d="M17.64 9.2c0-.637-.057-1.251-.164-1.84H9v3.481h4.844c-.209 1.125-.843 2.078-1.796 2.717v2.258h2.908c1.702-1.567 2.684-3.874 2.684-6.615z" fill="#4285F4"/>
<path d="M9 18c2.43 0 4.467-.806 5.956-2.18l-2.908-2.259c-.806.54-1.837.86-3.048.86-2.344 0-4.328-1.584-5.036-3.711H.957v2.332A8.997 8.997 0 009 18z" fill="#34A853"/>
<path d="M3.964 10.71A5.41 5.41 0 013.682 9c0-.593.102-1.17.282-1.71V4.958H.957A8.996 8.996 0 000 9s.38 1.572.957 3.042l3.007-2.332z" fill="#FBBC05"/>
<path d="M9 3.58c1.321 0 2.508.454 3.44 1.345l2.582-2.58C13.463.891 11.426 0 9 0A8.997 8.997 0 00.957 4.958L3.964 7.29C4.672 5.163 6.656 3.58 9 3.58z" fill="#EA4335"/>
</svg>"""

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
    secret_key=os.getenv("JWT_SECRET", "polytrade-dev-secret-change-me"),
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
        # Extract user_id from session
        user_id = None
        if session:
            user = session.get("user") if isinstance(session, dict) else getattr(session, "get", lambda k: None)("user")
            if user:
                user_id = user.get("user_id") if isinstance(user, dict) else None
        cp = CommandProcessor(agent, user_id=user_id)

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
                return f"```\n{output}\n```"  # Stripped by core.py and rendered as <pre>
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

## Weather Markets
- `poly:weather London` — Search markets + token IDs
- `poly:weather Seoul` — Seoul weather markets
- `poly:weather New York` — NYC weather markets
- `scan` — Scan all weather opportunities

## Backtest & Predict
- `poly:backtest London 7` — 7-day London backtest
- `poly:backtestv2 Seoul 7` — Cross-sectional YES/NO backtest
- `poly:predict London 2` — Forward-looking prediction

## Trading
- `poly:simbuy 50 <token_id>` — Simulate trade (get token ID from poly:weather)
- `poly:buy 50 <token_id>` — Real USDC buy order
- `poly:sell 50 <token_id>` — Real USDC sell order
- `poly:portfolio` — On-chain USDC portfolio
- `poly:paperportfolio` — Paper trading portfolio

## Reports & PnL
- `poly:report weather` — Weather trades report (backtest)
- `poly:report weather paper` — Weather paper trades
- `poly:trades weather` — Weather trades table
- `poly:trades weather paper` — Weather paper trades
- `poly:pnl weather` — Weather PnL summary
- `poly:pnl weather paper` — Weather paper PnL

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

/* === Sidebar Auth === */
.auth-section {
  padding-bottom: 0.75rem;
  border-bottom: 1px solid #1e2a3a;
}

.sidebar-auth { display: flex; flex-direction: column; gap: 0.5rem; }

.auth-input {
  width: 100%; padding: 0.45rem 0.6rem;
  background: #0f1117; border: 1px solid #2a3040; border-radius: 0.375rem;
  color: #e2e8f0; font-family: inherit; font-size: 0.8rem;
  box-sizing: border-box;
}
.auth-input::placeholder { color: #475569; }
.auth-input:focus { outline: none; border-color: #10b981; }

.auth-btn {
  width: 100%; padding: 0.45rem;
  background: #059669; color: #d1fae5; border: none; border-radius: 0.375rem;
  cursor: pointer; font-family: inherit; font-weight: 600; font-size: 0.8rem;
}
.auth-btn:hover { background: #047857; }

.auth-alt { font-size: 0.75rem; color: #64748b; text-align: center; }
.auth-link { color: #10b981; text-decoration: none; font-size: 0.75rem; }
.auth-link:hover { text-decoration: underline; }
.auth-error { color: #f87171; font-size: 0.75rem; padding: 0.25rem 0; }

.user-info {
  display: flex; align-items: center; gap: 0.5rem; padding: 0.25rem 0;
}
.user-avatar {
  width: 28px; height: 28px; background: #064e3b; color: #10b981;
  border-radius: 50%; display: flex; align-items: center; justify-content: center;
  font-size: 0.75rem; font-weight: 700;
}
.user-info-text { flex: 1; min-width: 0; }
.user-name { font-size: 0.8rem; color: #e2e8f0; font-weight: 500; }
.user-email { font-size: 0.7rem; color: #64748b; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.logout-btn {
  display: block; text-align: center; margin-top: 0.5rem;
  padding: 0.3rem; background: transparent; border: 1px solid #2a3040;
  border-radius: 0.375rem; color: #64748b; font-size: 0.75rem;
  text-decoration: none; font-family: inherit;
}
.logout-btn:hover { border-color: #f87171; color: #f87171; }

.google-btn {
  display: flex; align-items: center; justify-content: center; gap: 0.5rem;
  width: 100%; padding: 0.5rem; background: #1a1f2e; border: 1px solid #2a3040;
  border-radius: 0.375rem; color: #e2e8f0; text-decoration: none;
  font-size: 0.8rem; font-family: inherit; cursor: pointer; transition: all 0.2s;
}
.google-btn:hover { background: #2a3040; border-color: #4285F4; }
.google-btn svg { flex-shrink: 0; }

.auth-divider {
  text-align: center; color: #475569; font-size: 0.7rem;
  margin: 0.25rem 0; position: relative;
}

/* === Sidebar Nav === */
.sidebar-nav {
  display: flex; flex-direction: column; gap: 0.15rem;
  padding: 0.5rem 0; border-top: 1px solid #1e2a3a;
}
.nav-link {
  display: block; padding: 0.4rem 0.5rem; border-radius: 0.375rem;
  color: #94a3b8; font-size: 0.8rem; text-decoration: none; transition: all 0.15s;
}
.nav-link:hover { background: #141821; color: #e2e8f0; }

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
        ("gip AAPL", "Intraday price graph"),
        ("news TSLA", "Latest news"),
    ]),
    ("Weather Markets", [
        ("poly:weather London", "Search London markets + token IDs"),
        ("poly:weather Seoul", "Seoul weather markets"),
        ("poly:weather New York", "New York weather markets"),
        ("scan", "Scan all weather opportunities"),
    ]),
    ("Backtest & Predict", [
        ("poly:backtest London 7", "7-day London backtest"),
        ("poly:backtestv2 Seoul 7", "Cross-sectional YES/NO backtest"),
        ("poly:predict London 2", "Forward-looking prediction"),
    ]),
    ("Trading", [
        ("poly:simbuy 50 <token_id>", "Simulate buy $50 (needs token ID)"),
        ("poly:buy 50 <token_id>", "Real USDC buy order"),
        ("poly:sell 50 <token_id>", "Real USDC sell order"),
        ("poly:portfolio", "On-chain USDC portfolio"),
        ("poly:paperportfolio", "Paper trading portfolio"),
    ]),
    ("Reports & PnL", [
        ("poly:report weather", "Weather trades report (backtest)"),
        ("poly:report weather paper", "Weather paper trades"),
        ("poly:trades weather", "Weather trades table"),
        ("poly:trades weather paper", "Weather paper trades"),
        ("poly:pnl weather", "Weather PnL summary"),
        ("poly:pnl weather paper", "Weather paper PnL"),
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

def _auth_section(session):
    """Build auth section: login/signup forms or user info."""
    user = session.get("user")
    if user:
        # Logged in — show user info + logout
        name = user.get("display_name") or user.get("email", "User")
        return Div(
            Div(
                Span(name[:1].upper(), cls="user-avatar"),
                Div(
                    Div(name, cls="user-name"),
                    Div(user.get("email", ""), cls="user-email"),
                    cls="user-info-text",
                ),
                cls="user-info",
            ),
            A("Logout", href="/logout", cls="logout-btn"),
            cls="auth-section",
        )
    # Not logged in — show login form with toggle to signup
    return Div(
        Div(id="auth-forms", hx_get="/agui-auth/login-form", hx_trigger="load", hx_swap="innerHTML"),
        cls="auth-section",
    )


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

    # Auth section (login/signup or user info)
    parts.append(_auth_section(session))

    user = session.get("user")

    # New Chat button
    parts.append(
        Button("+ New Chat", cls="new-chat-btn", onclick="window.location.href='/?new=1'")
    )

    # Recent conversations (filtered by user)
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

    # Navigation links — Dashboard with SSO token if logged in
    if user:
        from utils.auth import create_cross_app_token
        sso_token = create_cross_app_token(user["user_id"], user["email"])
        # Use localhost for dev, prod domain for deployed
        import os
        web_host = os.getenv("WEB_APP_URL", "http://localhost:4002")
        dashboard_url = f"{web_host}/sso?token={sso_token}"
    else:
        dashboard_url = "http://localhost:4002"
    nav_links = [
        A("Dashboard", href=dashboard_url, target="_blank", cls="nav-link"),
    ]
    if user:
        nav_links.append(A("Profile", href="/profile", cls="nav-link"))
    nav = Div(*nav_links, cls="sidebar-nav")
    parts.append(nav)

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
    """Return the conversation list for the sidebar (DB-backed, filtered by user)."""
    from utils.agui.chat_store import list_conversations
    current_tid = session.get("thread_id", "")
    user = session.get("user")
    if not user:
        return Div(Div("Login to see your chats", cls="conv-empty"))
    user_id = user.get("user_id")
    # Claim current thread if it has no user_id
    if current_tid and user_id:
        try:
            from db.connection import get_pool
            from uuid import UUID
            pool = await get_pool()
            await pool.execute("""
                UPDATE polycode.chat_conversations
                SET user_id = $1 WHERE thread_id = $2 AND user_id IS NULL
            """, UUID(user_id), UUID(current_tid))
        except Exception:
            pass
    convs = await list_conversations(user_id=user_id, limit=20)
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


# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------

@rt("/agui-auth/login-form")
def login_form_fragment():
    """Return the login form for the sidebar."""
    parts = []
    if _oauth_enabled:
        parts.append(A(NotStr(_GOOGLE_SVG), " Sign in with Google", href="/login", cls="google-btn"))
        parts.append(Div("or", cls="auth-divider"))
    parts.extend([
        Form(
            Input(type="email", name="email", placeholder="Email", required=True, cls="auth-input"),
            Input(type="password", name="password", placeholder="Password", required=True, cls="auth-input"),
            Button("Login", type="submit", cls="auth-btn"),
            hx_post="/agui-auth/login",
            hx_target="#auth-forms",
            hx_swap="innerHTML",
            cls="sidebar-auth",
        ),
        Div(
            A("Forgot password?", href="/forgot", cls="auth-link"),
            cls="auth-alt",
        ),
        Div(
            "No account? ",
            A("Sign up", href="#", hx_get="/agui-auth/register-form",
              hx_target="#auth-forms", hx_swap="innerHTML", cls="auth-link"),
            cls="auth-alt",
        ),
    ])
    return Div(*parts, cls="sidebar-auth")


@rt("/agui-auth/register-form")
def register_form_fragment():
    """Return the signup form for the sidebar."""
    parts = []
    if _oauth_enabled:
        parts.append(A(NotStr(_GOOGLE_SVG), " Sign up with Google", href="/login", cls="google-btn"))
        parts.append(Div("or", cls="auth-divider"))
    parts.extend([
        Form(
            Input(type="text", name="display_name", placeholder="Name", cls="auth-input"),
            Input(type="email", name="email", placeholder="Email", required=True, cls="auth-input"),
            Input(type="password", name="password", placeholder="Password (min 6 chars)", required=True, cls="auth-input"),
            Button("Sign Up", type="submit", cls="auth-btn"),
            hx_post="/agui-auth/register",
            hx_target="#auth-forms",
            hx_swap="innerHTML",
            cls="sidebar-auth",
        ),
        Div(
            "Have an account? ",
            A("Login", href="#", hx_get="/agui-auth/login-form",
              hx_target="#auth-forms", hx_swap="innerHTML", cls="auth-link"),
            cls="auth-alt",
        ),
    ])
    return Div(*parts, cls="sidebar-auth")


@rt("/agui-auth/login", methods=["POST"])
async def agui_login(session, email: str = "", password: str = ""):
    """Process sidebar login."""
    from utils.auth import authenticate, session_login
    if not email or not password:
        return Div(Div("Email and password required.", cls="auth-error"), login_form_fragment())
    user = await authenticate(email, password)
    if not user:
        return Div(Div("Invalid email or password.", cls="auth-error"), login_form_fragment())
    session_login(session, user)
    # Use HX-Redirect header for HTMX to do a full page reload
    from starlette.responses import Response
    resp = Response(status_code=200, headers={"HX-Redirect": "/"})
    return resp


@rt("/agui-auth/register", methods=["POST"])
async def agui_register(session, email: str = "", password: str = "", display_name: str = ""):
    """Process sidebar signup."""
    from utils.auth import create_user, session_login
    if not email or not password:
        return Div(Div("Email and password required.", cls="auth-error"), register_form_fragment())
    if len(password) < 6:
        return Div(Div("Password must be at least 6 characters.", cls="auth-error"), register_form_fragment())
    user = await create_user(email, password, display_name or None)
    if not user:
        return Div(Div("Email already registered.", cls="auth-error"), register_form_fragment())
    session_login(session, user)
    from starlette.responses import Response
    return Response(status_code=200, headers={"HX-Redirect": "/"})


@rt("/register")
async def register_page(request, session, msg: str = ""):
    """Full-page register form (for CLI signup redirect)."""
    if request.method == "POST":
        form = await request.form()
        email = form.get("email", "")
        password = form.get("password", "")
        display_name = form.get("display_name", "")
        if not email or not password:
            msg = "Email and password required."
        elif len(password) < 6:
            msg = "Password must be at least 6 characters."
        else:
            from utils.auth import create_user, session_login
            user = await create_user(email, password, display_name or None)
            if user:
                session_login(session, user)
                from starlette.responses import RedirectResponse
                return RedirectResponse("/", status_code=303)
            msg = "Email already registered."
    parts = [H2("Create Account")]
    if msg:
        parts.append(P(msg, cls="auth-error"))
    if _oauth_enabled:
        parts.append(A(NotStr(_GOOGLE_SVG), " Sign up with Google", href="/login",
                       cls="google-btn", style="margin-bottom:0.75rem;"))
        parts.append(Div("or", style="text-align:center;color:#475569;font-size:0.8rem;margin-bottom:0.75rem;"))
    parts.append(Form(
        Input(type="text", name="display_name", placeholder="Name (optional)", cls="auth-input"),
        Input(type="email", name="email", placeholder="Email", required=True, cls="auth-input"),
        Input(type="password", name="password", placeholder="Password (min 6 chars)", required=True, cls="auth-input"),
        Button("Sign Up", type="submit", cls="auth-btn"),
        method="post", action="/register",
    ))
    parts.append(Div("Have an account? ", A("Login", href="/signin"), cls="auth-alt", style="margin-top:1rem;"))
    return (
        Title("Sign Up — PolyTrade"),
        Style(LAYOUT_CSS),
        Style(_AUTH_PAGE_CSS),
        Div(Div(*parts, cls="auth-card"), cls="auth-page"),
    )


@rt("/signin")
async def signin_page(request, session, msg: str = ""):
    """Full-page sign-in form."""
    if request.method == "POST":
        form = await request.form()
        email = form.get("email", "")
        password = form.get("password", "")
        from utils.auth import authenticate, session_login
        user = await authenticate(email, password)
        if user:
            session_login(session, user)
            from starlette.responses import RedirectResponse
            return RedirectResponse("/", status_code=303)
        msg = "Invalid email or password."
    parts = [H2("PolyTrade Login")]
    if msg:
        parts.append(P(msg, cls="auth-error"))
    if _oauth_enabled:
        parts.append(A(NotStr(_GOOGLE_SVG), " Sign in with Google", href="/login",
                       cls="google-btn", style="margin-bottom:0.75rem;"))
        parts.append(Div("or", style="text-align:center;color:#475569;font-size:0.8rem;margin-bottom:0.75rem;"))
    parts.append(Form(
        Input(type="email", name="email", placeholder="Email", required=True, cls="auth-input"),
        Input(type="password", name="password", placeholder="Password", required=True, cls="auth-input"),
        Button("Login", type="submit", cls="auth-btn"),
        method="post", action="/signin",
    ))
    parts.append(Div(A("Forgot password?", href="/forgot"), cls="auth-alt", style="margin-top:0.75rem;"))
    parts.append(Div("No account? ", A("Sign up", href="/register"), cls="auth-alt"))
    return (
        Title("Sign In — PolyTrade"),
        Style(LAYOUT_CSS),
        Style(_AUTH_PAGE_CSS),
        Div(Div(*parts, cls="auth-card"), cls="auth-page"),
    )


@rt("/logout")
def logout(session):
    """Sign out — clear session."""
    session.pop("user", None)
    from starlette.responses import RedirectResponse
    return RedirectResponse("/", status_code=303)


@rt("/forgot")
async def forgot_password(request, session, email: str = "", msg: str = ""):
    """Forgot password page — show form or process email."""
    if request.method == "POST" and email:
        from utils.auth import create_password_reset_token
        from utils.email_util import send_email_to
        token = await create_password_reset_token(email)
        if token:
            scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
            host = request.headers.get("host", request.url.netloc)
            reset_url = f"{scheme}://{host}/reset?token={token}"
            body_html = f"""
            <div style="font-family: -apple-system, sans-serif; max-width: 500px; margin: 0 auto;">
              <h2>Reset Your Password</h2>
              <p>You requested a password reset for your PolyTrade account.</p>
              <p><a href="{reset_url}"
                    style="display:inline-block; padding:12px 24px; background:#059669;
                           color:#fff; text-decoration:none; border-radius:6px;">
                Reset Password
              </a></p>
              <p style="color:#6c757d; font-size:13px;">
                This link expires in 1 hour. If you didn't request this, ignore this email.
              </p>
            </div>
            """
            send_email_to(email, "PolyTrade — Password Reset", body_html)
        msg = "If that email is registered, you will receive a reset link."
    return (
        Title("Forgot Password — PolyTrade"),
        Style(LAYOUT_CSS),
        Style(_AUTH_PAGE_CSS),
        Div(
            Div(
                H2("Forgot Password"),
                P(msg, cls="auth-success") if msg else None,
                Form(
                    Input(type="email", name="email", placeholder="Your email address",
                          required=True, cls="auth-input", value=email),
                    Button("Send Reset Link", type="submit", cls="auth-btn"),
                    method="post", action="/forgot",
                ),
                A("Back to chat", href="/", cls="auth-link",
                  style="display:block;text-align:center;margin-top:1rem;"),
                cls="auth-card",
            ),
            cls="auth-page",
        ),
    )


@rt("/reset")
async def reset_password(request, session, token: str = "", msg: str = ""):
    """Reset password page — verify token and update password."""
    if request.method == "POST":
        password = (request.query_params.get("password") or "")
        # Read form data
        form = await request.form()
        password = form.get("password", "")
        token = form.get("token", token)
        if len(password) < 6:
            msg = "Password must be at least 6 characters."
        else:
            from utils.auth import verify_and_consume_reset_token, update_password
            user = await verify_and_consume_reset_token(token)
            if user:
                await update_password(user["user_id"], password)
                from starlette.responses import RedirectResponse
                return RedirectResponse("/?msg=Password+reset+successfully.+Please+login.", status_code=303)
            else:
                msg = "Invalid or expired reset link."
    if not token:
        msg = "Missing reset token."
    return (
        Title("Reset Password — PolyTrade"),
        Style(LAYOUT_CSS),
        Style(_AUTH_PAGE_CSS),
        Div(
            Div(
                H2("Reset Password"),
                P(msg, cls="auth-error") if msg else None,
                Form(
                    Hidden(name="token", value=token),
                    Input(type="password", name="password", placeholder="New password (min 6 chars)",
                          required=True, cls="auth-input"),
                    Button("Reset Password", type="submit", cls="auth-btn"),
                    method="post", action=f"/reset?token={token}",
                ),
                A("Back to chat", href="/", cls="auth-link",
                  style="display:block;text-align:center;margin-top:1rem;"),
                cls="auth-card",
            ),
            cls="auth-page",
        ),
    )


_AUTH_PAGE_CSS = """
.auth-page {
  display: flex; align-items: center; justify-content: center;
  min-height: 100vh; background: #0a0d14;
  font-family: 'SF Mono', 'Fira Code', ui-monospace, monospace;
}
.auth-card {
  background: #0f1117; border: 1px solid #1e2a3a; border-radius: 12px;
  padding: 2rem; width: 100%; max-width: 400px;
}
.auth-card h2 { color: #10b981; margin-bottom: 1.5rem; text-align: center; }
.auth-card .auth-input {
  width: 100%; padding: 0.6rem 0.8rem; margin-bottom: 0.75rem;
  background: #141821; border: 1px solid #2a3040; border-radius: 0.375rem;
  color: #e2e8f0; font-family: inherit; font-size: 0.85rem;
}
.auth-card .auth-input:focus { outline: none; border-color: #10b981; }
.auth-card .auth-btn {
  width: 100%; padding: 0.6rem; background: #059669; color: #d1fae5;
  border: none; border-radius: 0.375rem; cursor: pointer; font-family: inherit;
  font-weight: 600; font-size: 0.85rem; margin-top: 0.5rem;
}
.auth-card .auth-btn:hover { background: #047857; }
.auth-error { color: #f87171; font-size: 0.8rem; margin-bottom: 0.75rem; }
.auth-success { color: #10b981; font-size: 0.8rem; margin-bottom: 0.75rem; }
.auth-link { color: #10b981; font-size: 0.8rem; }
"""


# ---------------------------------------------------------------------------
# Profile page
# ---------------------------------------------------------------------------

@rt("/profile")
async def profile_page(session, msg: str = ""):
    user = session.get("user")
    if not user:
        from starlette.responses import RedirectResponse
        return RedirectResponse("/")

    parts = [H2("Profile")]
    if msg:
        is_err = "fail" in msg.lower() or "error" in msg.lower() or "cannot" in msg.lower()
        parts.append(P(msg, cls="auth-error" if is_err else "auth-success"))

    # Account info
    parts.extend([
        H3("Account Info"),
        Form(
            Label("Email"),
            Input(type="email", value=user.get("email", ""), disabled=True, cls="auth-input",
                  style="opacity:0.6;cursor:not-allowed;"),
            Label("Display Name"),
            Input(type="text", name="display_name", value=user.get("display_name", ""),
                  placeholder="Your name", required=True, cls="auth-input"),
            Button("Update Name", type="submit", cls="auth-btn"),
            method="post", action="/profile/name", cls="profile-form",
        ),
    ])

    # Change password — check if user has a password set
    from utils.auth import get_user_by_id
    db_user = await get_user_by_id(user["user_id"])
    has_pw = db_user and db_user.get("password_hash")

    if has_pw:
        parts.extend([
            H3("Change Password"),
            Form(
                Input(type="password", name="current_password", placeholder="Current password",
                      required=True, cls="auth-input"),
                Input(type="password", name="new_password", placeholder="New password (min 6 chars)",
                      required=True, cls="auth-input"),
                Input(type="password", name="confirm_password", placeholder="Confirm new password",
                      required=True, cls="auth-input"),
                Button("Change Password", type="submit", cls="auth-btn"),
                method="post", action="/profile/password", cls="profile-form",
            ),
        ])
    else:
        parts.extend([
            H3("Set Password"),
            P("You signed in with Google. Set a password to also log in with email + CLI.",
              style="color:#64748b;font-size:0.8rem;margin-bottom:0.5rem;"),
            Form(
                Input(type="password", name="new_password", placeholder="New password (min 6 chars)",
                      required=True, cls="auth-input"),
                Input(type="password", name="confirm_password", placeholder="Confirm new password",
                      required=True, cls="auth-input"),
                Button("Set Password", type="submit", cls="auth-btn"),
                method="post", action="/profile/password", cls="profile-form",
            ),
        ])

    parts.append(Div(A("Back to chat", href="/", cls="auth-link"), cls="auth-alt",
                      style="margin-top:1.5rem;"))

    return (
        Title("Profile — PolyTrade"),
        Style(LAYOUT_CSS),
        Style(_AUTH_PAGE_CSS),
        Style("""
            .profile-page { max-width:500px; margin:2rem auto; padding:2rem; }
            .profile-page h2 { color:#10b981; margin-bottom:1rem; }
            .profile-page h3 { color:#94a3b8; font-size:0.9rem; margin:1.5rem 0 0.75rem; }
            .profile-page label { color:#64748b; font-size:0.8rem; display:block; margin-bottom:0.25rem; }
            .profile-form { display:flex; flex-direction:column; gap:0.5rem; }
            body { background:#0a0d14; color:#e2e8f0; font-family:'SF Mono',ui-monospace,monospace; }
        """),
        Div(*parts, cls="profile-page"),
    )


@rt("/profile/name", methods=["POST"])
async def profile_update_name(session, display_name: str = ""):
    user = session.get("user")
    if not user:
        from starlette.responses import RedirectResponse
        return RedirectResponse("/")
    if not display_name.strip():
        from starlette.responses import RedirectResponse
        return RedirectResponse("/profile?msg=Display+name+cannot+be+empty", status_code=303)
    from utils.auth import update_display_name
    ok = await update_display_name(user["user_id"], display_name)
    if ok:
        session["user"]["display_name"] = display_name.strip()
    from starlette.responses import RedirectResponse
    return RedirectResponse(f"/profile?msg={'Name+updated' if ok else 'Failed+to+update'}", status_code=303)


@rt("/profile/password", methods=["POST"])
async def profile_change_password(session, current_password: str = "", new_password: str = "", confirm_password: str = ""):
    from starlette.responses import RedirectResponse
    from utils.auth import authenticate, update_password, get_user_by_id
    user = session.get("user")
    if not user:
        return RedirectResponse("/")
    if not new_password:
        return RedirectResponse("/profile?msg=New+password+required", status_code=303)
    if new_password != confirm_password:
        return RedirectResponse("/profile?msg=Passwords+do+not+match", status_code=303)
    if len(new_password) < 6:
        return RedirectResponse("/profile?msg=Password+must+be+6%2B+characters", status_code=303)
    # Check if user has existing password (Google-only users don't)
    db_user = await get_user_by_id(user["user_id"])
    has_pw = db_user and db_user.get("password_hash")
    if has_pw:
        # Must verify current password
        if not current_password:
            return RedirectResponse("/profile?msg=Current+password+required", status_code=303)
        auth = await authenticate(user["email"], current_password)
        if not auth:
            return RedirectResponse("/profile?msg=Current+password+incorrect", status_code=303)
    # Set/update password
    await update_password(user["user_id"], new_password)
    return RedirectResponse("/profile?msg=Password+updated+successfully", status_code=303)


# ---------------------------------------------------------------------------
# Google OAuth routes
# ---------------------------------------------------------------------------

if _oauth_enabled:
    @rt("/login")
    async def login_get(request):
        scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
        host = request.headers.get("host", request.url.netloc)
        redirect_uri = f"{scheme}://{host}/auth/callback"
        return await _authlib_oauth.google.authorize_redirect(request, redirect_uri)

    @rt("/auth/callback")
    async def auth_callback(request, session):
        try:
            token = await _authlib_oauth.google.authorize_access_token(request)
        except Exception as e:
            logger.error(f"OAuth token exchange failed: {e}")
            from starlette.responses import RedirectResponse
            return RedirectResponse("/?error=Google+login+failed")

        userinfo = token.get("userinfo", {})
        if not userinfo:
            userinfo = await _authlib_oauth.google.userinfo(token=token)

        email = userinfo.get("email", "")
        name = userinfo.get("name", "")

        if not email:
            from starlette.responses import RedirectResponse
            return RedirectResponse("/?error=Google+did+not+provide+email")

        from utils.auth import get_user_by_email, create_user, session_login

        user = await get_user_by_email(email)
        if not user:
            user = await create_user(email=email, display_name=name)

        if user:
            session_login(session, user)
        else:
            from starlette.responses import RedirectResponse
            return RedirectResponse("/?error=Could+not+create+account")

        from starlette.responses import RedirectResponse
        return RedirectResponse("/")

if not _oauth_enabled:
    @rt("/login")
    def login_get():
        from starlette.responses import RedirectResponse
        return RedirectResponse("/")


@rt("/download-csv")
def download_csv(path: str = ""):
    """Serve a CSV file from test-results/ directory."""
    import os
    from starlette.responses import FileResponse
    if not path or ".." in path or not path.startswith("test-results/"):
        return {"error": "invalid path"}
    full_path = os.path.join(os.path.dirname(__file__), path)
    if not os.path.isfile(full_path):
        return {"error": "file not found"}
    filename = os.path.basename(full_path)
    return FileResponse(full_path, media_type="text/csv", filename=filename)


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
