"""FastHTML web shell for PolyTrade — browser-based CLI."""
import asyncio
import collections
import io
import json
import logging
import os
import sys
import time
import uuid as _uuid
from pathlib import Path
from typing import Dict, Optional

# Ensure project root is importable
sys.path.insert(0, str(Path(__file__).parent.absolute()))

from dotenv import load_dotenv
load_dotenv()

from fasthtml.common import *

from agent.agent import Agent
from agent.types import (
    AgentConfig,
    AnswerChunkEvent,
    DoneEvent,
    LogEvent,
    StreamResetEvent,
    ToolEndEvent,
    ToolErrorEvent,
    ToolStartEvent,
)
from components.command_processor import CommandProcessor

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Rich console output capture — redirect Rich prints to string
# ---------------------------------------------------------------------------

class RichCapture:
    """Capture Rich Console output to a string buffer."""

    def __init__(self):
        from rich.console import Console
        self._buf = io.StringIO()
        self.console = Console(file=self._buf, force_terminal=False, width=120, no_color=True)

    def get_output(self) -> str:
        return self._buf.getvalue()

    def clear(self):
        self._buf.truncate(0)
        self._buf.seek(0)


# ---------------------------------------------------------------------------
# Per-user session state
# ---------------------------------------------------------------------------

class UserSession:
    """Holds all per-user mutable state for the web UI."""

    SESSION_TTL = 7200  # 2 hours

    def __init__(self):
        self.agent: Optional[Agent] = None
        self.cmd_processor: Optional[CommandProcessor] = None
        self.chat_history = []
        self.current_ticker: Optional[str] = None
        # Streaming agent state
        self._chat_events = collections.deque(maxlen=200)
        self._chat_task = None
        self._chat_done = False
        self._chat_final = ""
        self._chat_286_html = None
        # Backtest streaming state
        self._bt_task = None
        self._bt_done = False
        self._bt_result = ""
        self._bt_286_html = None
        self._log_lines = collections.deque(maxlen=500)
        self.last_accessed = time.time()

    async def ensure_agent(self):
        """Lazily initialize agent + command processor."""
        if self.agent is None:
            model = os.getenv("MODEL")
            provider = os.getenv("MODEL_PROVIDER")
            config = AgentConfig(model=model, model_provider=provider)
            self.agent = Agent.create(config)
            self.cmd_processor = CommandProcessor(self.agent)


_sessions: Dict[str, UserSession] = {}


def _get_session(session) -> UserSession:
    """Get or create a UserSession for this browser session."""
    sid = session.get("session_id")
    if not sid:
        sid = str(_uuid.uuid4())
        session["session_id"] = sid
    if sid not in _sessions:
        _sessions[sid] = UserSession()
    uss = _sessions[sid]
    uss.last_accessed = time.time()
    return uss


def _evict_stale():
    """Remove sessions older than TTL."""
    now = time.time()
    stale = [sid for sid, u in _sessions.items() if now - u.last_accessed > UserSession.SESSION_TTL]
    for sid in stale:
        del _sessions[sid]


# ---------------------------------------------------------------------------
# Structured command detection
# ---------------------------------------------------------------------------

_STRUCTURED_PREFIXES = {
    "load", "news", "financials", "quote", "des", "fa", "anr", "ee", "rv",
    "own", "gp", "gip", "scan", "reset", "r",
    "help", "h", "?", "cls", "clear", "exit", "quit", "q",
}

# Long-running poly: commands that need streaming
_STREAMING_POLY = {"poly:backtest", "poly:backtestv2", "poly:backtest2", "poly:predict"}


def _is_structured(cmd_lower: str) -> bool:
    """Return True if input is a known structured command."""
    first = cmd_lower.split()[0] if cmd_lower.split() else ""
    if first in _STRUCTURED_PREFIXES:
        return True
    if first.startswith("poly:"):
        return True
    return False


# ---------------------------------------------------------------------------
# CSS & JS
# ---------------------------------------------------------------------------

_theme = Script("document.documentElement.dataset.theme='dark';")

_css = Style("""
body { font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace; }
main { max-width: 960px; margin: 0 auto; padding: 1rem; display: flex; flex-direction: column; height: 95vh; }
#output { flex: 1; overflow-y: auto; }
.cmd-entry { border-bottom: 1px solid var(--pico-muted-border-color); padding: 0.75rem 0; }
.cmd-echo { color: var(--pico-muted-color); font-size: 0.85em; margin-bottom: 0.25rem; }
.cmd-echo b { color: var(--pico-primary); }
#cmd-form { display: flex; gap: 0.5rem; padding-top: 0.5rem; border-top: 1px solid var(--pico-muted-border-color); }
#cmd-form input { flex: 1; margin-bottom: 0; }
#cmd-form button { width: auto; margin-bottom: 0; }
.help-grid { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 1.5rem; font-size: 0.85em; }
@media (max-width: 768px) { .help-grid { grid-template-columns: 1fr; } }
.help-grid h4 { color: var(--pico-primary); margin: 0.8rem 0 0.3rem; font-size: 0.95em; }
.help-grid h4:first-child { margin-top: 0; }
.help-grid dl { margin: 0; }
.help-grid dt { color: #e2c07b; font-size: 0.9em; margin-top: 0.3rem; }
.help-grid dd { color: var(--pico-muted-color); margin: 0 0 0 0.5rem; font-size: 0.85em; }
.htmx-request .htmx-indicator { display: inline; }
.htmx-indicator { display: none; }
nav.top-nav { display: flex; align-items: center; justify-content: space-between;
              padding: 0.5rem 0; margin-bottom: 0.5rem;
              border-bottom: 1px solid var(--pico-muted-border-color); }
nav.top-nav .nav-brand { font-weight: bold; font-size: 1.1em; color: var(--pico-primary); text-decoration: none; }
nav.top-nav .nav-links { display: flex; gap: 1rem; align-items: center; font-size: 0.85em; }
nav.top-nav .nav-links a { color: var(--pico-muted-color); text-decoration: none; }
nav.top-nav .nav-links a:hover { color: var(--pico-primary); }
.log-console { max-height: 400px; overflow-y: auto; background: #1a1a2e;
               border-radius: 0.5rem; padding: 0.5rem; margin-top: 0.5rem; }
.log-pre { color: #8b949e; font-size: 0.8em; margin: 0; white-space: pre-wrap; word-break: break-word; }
.data-pre { background: #1a1a2e; padding: 0.75rem; border-radius: 0.5rem;
            color: #c9d1d9; font-size: 0.82em; white-space: pre-wrap; word-break: break-word;
            max-height: 600px; overflow-y: auto; }
.status-bar { font-size: 0.75em; color: var(--pico-muted-color); text-align: right; margin-bottom: 0.3rem; }
""")

_js = Script("""
document.addEventListener('htmx:afterSettle', function() {
    var out = document.getElementById('output');
    if (out) out.scrollTop = out.scrollHeight;
});
document.addEventListener('htmx:afterRequest', function(evt) {
    if (evt.detail.elt && evt.detail.elt.id === 'cmd-form') {
        evt.detail.elt.reset();
        evt.detail.elt.querySelector('input').focus();
    }
});
document.addEventListener('htmx:configRequest', function(evt) {
    evt.detail.timeout = 300000;  // 5 minutes
});
document.addEventListener('htmx:afterSwap', function(evt) {
    var lc = document.getElementById('log-console');
    if (lc) lc.scrollTop = lc.scrollHeight;
    var cc = document.getElementById('chat-console');
    if (cc) cc.scrollTop = cc.scrollHeight;
});
""")

app, rt = fast_app(hdrs=[_theme, MarkdownJS(), _css, _js])


# ---------------------------------------------------------------------------
# Help grid (mirrors CLI help)
# ---------------------------------------------------------------------------

def _help_html():
    """3-column help grid matching CLI commands (same layout as AlpaTrade)."""

    def _section(title, items):
        dl_items = []
        for cmd, desc in items:
            dl_items.append(Dt(cmd))
            dl_items.append(Dd(desc))
        return (H4(title), Dl(*dl_items))

    # Column 1: Stock Research
    col1 = Div(
        *_section("Stock Research", [
            ("load AAPL", "Company profile & quote"),
            ("fa NVDA", "Financial analysis"),
            ("anr MSFT", "Analyst recommendations"),
            ("ee TSLA", "Earnings estimates"),
            ("rv GOOG", "Relative valuation"),
            ("own AAPL", "Institutional ownership"),
            ("gp AAPL", "Price graph"),
            ("gip AAPL", "Intraday price graph"),
            ("news TSLA", "Latest news"),
            ("quote AAPL", "Real-time quote"),
            ("scan", "Scan weather opportunities"),
        ]),
    )

    # Column 2: Polymarket Weather + Backtest
    col2 = Div(
        *_section("Weather Markets", [
            ("poly:weather London", "Search weather markets"),
            ("poly:weather Seoul", "Seoul markets"),
            ("poly:weather New York", "New York markets"),
        ]),
        *_section("Backtest & Predict", [
            ("poly:backtest London 7", "7-day backtest"),
            ("poly:backtestv2 Seoul 7", "Cross-sectional YES/NO"),
            ("poly:predict London 2", "Forward prediction"),
        ]),
        *_section("Simulate", [
            ("poly:simbuy 50 <id>", "Simulate trade (no risk)"),
        ]),
    )

    # Column 3: Trading + Reports + General
    col3 = Div(
        *_section("Paper Trading", [
            ("poly:paperbuy 50 <id>", "Paper buy"),
            ("poly:papersell <id>", "Paper sell"),
            ("poly:paperportfolio", "Paper portfolio"),
        ]),
        *_section("Real Trading", [
            ("poly:buy 50 <id>", "Real USDC buy order"),
            ("poly:sell 50 <id>", "Real sell order"),
            ("poly:portfolio", "On-chain portfolio"),
        ]),
        *_section("Reports & PnL", [
            ("poly:report weather", "Weather trades report (backtest)"),
            ("poly:report weather paper", "Weather paper trades"),
            ("poly:trades weather", "Weather trades table"),
            ("poly:trades weather paper", "Weather paper trades"),
            ("poly:pnl weather", "Weather PnL summary"),
            ("poly:pnl weather paper", "Weather paper PnL"),
        ]),
        *_section("General", [
            ("help / h / ?", "This help screen"),
            ("clear / cls", "Clear output"),
            ("<any question>", "Ask the AI research agent"),
        ]),
    )

    return Div(
        H3("PolyTrade — Command Reference"),
        Div(col1, col2, col3, cls="help-grid"),
    )


# ---------------------------------------------------------------------------
# Nav bar
# ---------------------------------------------------------------------------

def _nav():
    """Top navigation bar (same layout as AlpaTrade)."""
    model = os.getenv("MODEL", "?")
    provider = os.getenv("MODEL_PROVIDER", "?")
    links = [
        A("Home", href="/"),
        A("Guide", href="/guide"),
        A("Dashboard", href="/"),
        Span(f"{provider}/{model}", style="color: var(--pico-muted-color); font-size: 0.8em;"),
    ]
    return Nav(
        A("PolyTrade", href="/", cls="nav-brand"),
        Div(*links, cls="nav-links"),
        cls="top-nav",
    )


# ---------------------------------------------------------------------------
# Guide page
# ---------------------------------------------------------------------------

_GUIDE_MD = """
# PolyTrade User Guide

## Stock Research Commands

| Command | Description |
|---------|-------------|
| `load AAPL` | Company profile & real-time quote |
| `fa NVDA` | Full financial analysis (income, balance sheet, cash flow) |
| `anr MSFT` | Analyst recommendations & price targets |
| `ee TSLA` | Earnings estimates (EPS, revenue forecasts) |
| `rv GOOG` | Relative valuation vs peers |
| `own AAPL` | Institutional ownership breakdown |
| `gp AAPL` | Price graph (historical) |
| `gip AAPL` | Intraday price graph |
| `news TSLA` | Latest company news |
| `quote AAPL` | Real-time quote |
| `scan` | Scan all weather market opportunities |

## Polymarket Weather Commands

| Command | Description |
|---------|-------------|
| `poly:weather London` | Search weather prediction markets for a city |
| `poly:backtest Seoul 7` | Run a 7-day backtest for Seoul |
| `poly:backtestv2 Seoul 7` | Cross-sectional YES/NO backtest |
| `poly:predict London 2` | Forward-looking prediction (2 days) |

## Trading Commands

| Command | Description |
|---------|-------------|
| `poly:simbuy 50 <id>` | Simulate a $50 trade (no real money) |
| `poly:buy 50 <id>` | Place a real USDC buy order |
| `poly:sell 50 <id>` | Place a real sell order |
| `poly:portfolio` | View on-chain portfolio |
| `poly:paperportfolio` | View paper trading portfolio |

## Reports & PnL

| Command | Description |
|---------|-------------|
| `poly:report weather` | Weather trades report (backtest) |
| `poly:report weather paper` | Weather paper trades |
| `poly:trades weather` | Weather trades table |
| `poly:trades weather paper` | Weather paper trades |
| `poly:pnl weather` | Weather PnL summary |
| `poly:pnl weather paper` | Weather paper PnL |

## AI Chat
Type any free-form question to chat with the AI research agent.
The agent has access to all tools above and will use them automatically.
"""


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@rt("/")
def get(session):
    model = os.getenv("MODEL", "?")
    provider = os.getenv("MODEL_PROVIDER", "?")
    return (
        Title("PolyTrade"),
        Main(
            _nav(),
            Div(
                Span(f"{provider}/{model}", cls="status-bar"),
                style="text-align: right; margin-bottom: 0.5rem;",
            ),
            Div(_help_html(), id="output"),
            Form(
                Input(type="text", name="command",
                      placeholder="load AAPL — poly:weather London — or ask anything",
                      autofocus=True, autocomplete="off"),
                Button("Run", type="submit"),
                Span(" Running...", cls="htmx-indicator",
                     style="color: var(--pico-muted-color); font-size: 0.85em;"),
                id="cmd-form",
                hx_post="/cmd", hx_target="#output", hx_swap="beforeend",
                hx_indicator=".htmx-indicator",
            ),
        ),
    )


@rt("/guide")
def get_guide(session):
    return (
        Title("PolyTrade — Guide"),
        Main(
            _nav(),
            Div(_GUIDE_MD, cls="marked"),
        ),
    )


@rt("/cmd")
async def post(command: str, session):
    cmd_lower = command.strip().lower()
    if not command.strip():
        return ""

    # Web-only overrides
    if cmd_lower in ("exit", "quit", "q"):
        return Div(
            P(B(f"> {command}"), cls="cmd-echo"),
            P("Close the browser tab to end this session."),
            cls="cmd-entry",
        )
    if cmd_lower in ("clear", "cls"):
        return Div(id="output", hx_swap_oob="innerHTML")
    if cmd_lower in ("help", "h", "?"):
        return Div(
            P(B(f"> {command}"), cls="cmd-echo"),
            _help_html(),
            cls="cmd-entry",
        )

    uss = _get_session(session)
    await uss.ensure_agent()

    # Check for long-running poly: commands → streaming
    first_word = cmd_lower.split()[0] if cmd_lower.split() else ""
    if first_word in _STREAMING_POLY:
        return _start_streaming_cmd(command, uss)

    # Structured commands → execute directly, capture Rich output
    if _is_structured(cmd_lower):
        return await _exec_structured(command, uss)

    # Free-form → stream through agent
    return _start_chat_stream(command, uss)


async def _exec_structured(command: str, uss: UserSession):
    """Execute a structured command and capture Rich console output."""
    # Create a capture console and swap it into the command processor
    cap = RichCapture()
    original_console = uss.cmd_processor.console
    uss.cmd_processor.console = cap.console

    try:
        is_handled, agent_query = await uss.cmd_processor.process_command(command)

        output = cap.get_output().strip()

        if not is_handled and agent_query:
            # Command processor said "not handled, ask agent" — stream it
            uss.cmd_processor.console = original_console
            return _start_chat_stream(agent_query, uss)

        if output:
            return Div(
                P(B(f"> {command}"), cls="cmd-echo"),
                Pre(output, cls="data-pre"),
                cls="cmd-entry",
            )
        return Div(
            P(B(f"> {command}"), cls="cmd-echo"),
            P("Done.", style="color: var(--pico-muted-color);"),
            cls="cmd-entry",
        )
    except Exception as e:
        return Div(
            P(B(f"> {command}"), cls="cmd-echo"),
            P(f"Error: {e}", style="color: #e06c75;"),
            cls="cmd-entry",
        )
    finally:
        uss.cmd_processor.console = original_console


# ---------------------------------------------------------------------------
# Streaming: long-running commands (backtest/predict)
# ---------------------------------------------------------------------------

def _start_streaming_cmd(command: str, uss: UserSession):
    """Launch a long-running poly: command as a background task."""
    # Cancel existing
    if uss._bt_task and not uss._bt_task.done():
        uss._bt_task.cancel()

    uss._bt_done = False
    uss._bt_result = ""
    uss._bt_286_html = None
    uss._log_lines.clear()

    cap = RichCapture()
    original_console = uss.cmd_processor.console

    async def _run():
        uss.cmd_processor.console = cap.console
        try:
            await uss.cmd_processor.process_command(command)
            uss._bt_result = cap.get_output().strip()
        except Exception as e:
            uss._bt_result = f"Error: {e}"
        finally:
            uss.cmd_processor.console = original_console
            uss._bt_done = True

    uss._bt_task = asyncio.create_task(_run())

    return Div(
        P(B(f"> {command}"), cls="cmd-echo"),
        Div(
            Pre("Starting...", cls="log-pre"),
            cls="log-console", id="log-console",
            hx_get="/stream_bt", hx_trigger="every 1s", hx_swap="innerHTML",
        ),
        cls="cmd-entry",
    )


@rt("/stream_bt")
def stream_bt_get(session):
    """Poll backtest progress; HTTP 286 stops HTMX polling when done."""
    uss = _get_session(session)

    if uss._bt_done and uss._bt_result is not None:
        result_html = Div(Pre(uss._bt_result, cls="data-pre"))
        uss._bt_286_html = to_xml(result_html)
        uss._bt_result = None
        return Response(uss._bt_286_html, status_code=286, headers={"Content-Type": "text/html"})

    # HTMX race replay
    if uss._bt_done and uss._bt_286_html is not None:
        html = uss._bt_286_html
        uss._bt_286_html = None
        return Response(html, status_code=286, headers={"Content-Type": "text/html"})

    return Pre("Running backtest... please wait", cls="log-pre")


# ---------------------------------------------------------------------------
# Streaming: free-form agent chat
# ---------------------------------------------------------------------------

def _start_chat_stream(command: str, uss: UserSession):
    """Launch an agent query with streaming trace console."""
    if uss._chat_task and not uss._chat_task.done():
        uss._chat_task.cancel()

    uss._chat_events.clear()
    uss._chat_done = False
    uss._chat_final = ""
    uss._chat_286_html = None

    async def _run():
        try:
            uss.chat_history.append({"role": "user", "content": command})
            final_answer = ""

            async for event in uss.agent.run(command, uss.chat_history):
                if isinstance(event, ToolStartEvent):
                    uss._chat_events.append({"type": "tool_call", "tool": event.tool})
                elif isinstance(event, ToolEndEvent):
                    uss._chat_events.append({"type": "tool_result", "tool": event.tool})
                elif isinstance(event, ToolErrorEvent):
                    uss._chat_events.append({"type": "error", "content": f"{event.tool}: {event.error[:120]}"})
                elif isinstance(event, LogEvent):
                    if event.level == "thought":
                        uss._chat_events.append({"type": "thought", "content": event.message.strip()})
                elif isinstance(event, DoneEvent):
                    final_answer = event.answer
                    uss.chat_history.append({"role": "assistant", "content": final_answer})

                    # Save run to DB
                    try:
                        from db.repository import create_run, finish_run, save_pnl_snapshot
                        run_id = await create_run(command, os.getenv("MODEL"), os.getenv("MODEL_PROVIDER"))
                        if run_id:
                            await finish_run(run_id, event.iterations, event.tool_calls)
                            await save_pnl_snapshot(run_id=run_id)
                    except Exception:
                        pass

            uss._chat_final = final_answer
        except Exception as e:
            uss._chat_events.append({"type": "error", "content": str(e)})
            uss._chat_final = f"Error: {e}"
        finally:
            uss._chat_done = True

    uss._chat_task = asyncio.create_task(_run())

    return Div(
        P(B(f"> {command}"), cls="cmd-echo"),
        Div(
            Pre("Thinking...", cls="log-pre"),
            id="chat-console", cls="log-console",
            hx_get="/chat-stream", hx_trigger="every 500ms", hx_swap="innerHTML",
        ),
        cls="cmd-entry",
    )


@rt("/chat-stream")
def chat_stream_get(session):
    """Stream agent trace; HTTP 286 stops HTMX polling when done."""
    uss = _get_session(session)
    events = list(uss._chat_events)
    lines = []
    for ev in events:
        if ev["type"] == "tool_call":
            lines.append(f">> Calling {ev['tool']}...")
        elif ev["type"] == "tool_result":
            lines.append(f"<< {ev['tool']} returned data")
        elif ev["type"] == "thought":
            lines.append(f"   {ev['content']}")
        elif ev["type"] == "error":
            lines.append(f"!! {ev['content']}")

    trace_text = "\n".join(lines) if lines else "Thinking..."

    if uss._chat_done:
        parts = []
        if lines:
            parts.append(Pre("\n".join(lines), cls="log-pre"))
            parts.append(Hr())
        parts.append(Div(uss._chat_final, cls="marked"))
        result_html = Div(*parts)
        uss._chat_286_html = to_xml(result_html)
        uss._chat_done = False
        uss._chat_final = ""
        return Response(uss._chat_286_html, status_code=286, headers={"Content-Type": "text/html"})

    # HTMX race replay
    if uss._chat_286_html is not None:
        html = uss._chat_286_html
        uss._chat_286_html = None
        return Response(html, status_code=286, headers={"Content-Type": "text/html"})

    return Pre(trace_text, cls="log-pre")


# ---------------------------------------------------------------------------
# Periodic session cleanup
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Auth routes (web shell)
# ---------------------------------------------------------------------------

_WEB_AUTH_CSS = """
.auth-page { display:flex; align-items:center; justify-content:center; min-height:100vh; background:#0a0d14; font-family:'SF Mono',ui-monospace,monospace; }
.auth-card { background:#0f1117; border:1px solid #1e2a3a; border-radius:12px; padding:2rem; width:100%; max-width:400px; }
.auth-card h2 { color:#10b981; margin-bottom:1.5rem; text-align:center; }
.auth-card input { width:100%; padding:0.6rem 0.8rem; margin-bottom:0.75rem; background:#141821; border:1px solid #2a3040; border-radius:0.375rem; color:#e2e8f0; font-family:inherit; font-size:0.85rem; box-sizing:border-box; }
.auth-card input:focus { outline:none; border-color:#10b981; }
.auth-card button { width:100%; padding:0.6rem; background:#059669; color:#d1fae5; border:none; border-radius:0.375rem; cursor:pointer; font-family:inherit; font-weight:600; font-size:0.85rem; margin-top:0.5rem; }
.auth-card button:hover { background:#047857; }
.auth-error { color:#f87171; font-size:0.8rem; margin-bottom:0.75rem; }
.auth-success { color:#10b981; font-size:0.8rem; margin-bottom:0.75rem; }
.auth-alt { text-align:center; margin-top:1rem; font-size:0.8rem; color:#64748b; }
.auth-alt a { color:#10b981; text-decoration:none; }
.auth-alt a:hover { text-decoration:underline; }
"""

@rt("/signin")
async def signin(request, session, email: str = "", msg: str = ""):
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
    return (
        Title("Sign In — PolyTrade"),
        Style(_WEB_AUTH_CSS),
        Div(Div(
            H2("PolyTrade Login"),
            P(msg, cls="auth-error") if msg else None,
            Form(
                Input(type="email", name="email", placeholder="Email", required=True, value=email),
                Input(type="password", name="password", placeholder="Password", required=True),
                Button("Login", type="submit"),
                method="post", action="/signin",
            ),
            Div(A("Forgot password?", href="/forgot"), cls="auth-alt"),
            Div("No account? ", A("Sign up", href="/register"), cls="auth-alt"),
            cls="auth-card",
        ), cls="auth-page"),
    )

@rt("/register")
async def register(request, session, msg: str = ""):
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
    return (
        Title("Sign Up — PolyTrade"),
        Style(_WEB_AUTH_CSS),
        Div(Div(
            H2("Create Account"),
            P(msg, cls="auth-error") if msg else None,
            Form(
                Input(type="text", name="display_name", placeholder="Name (optional)"),
                Input(type="email", name="email", placeholder="Email", required=True),
                Input(type="password", name="password", placeholder="Password (min 6 chars)", required=True),
                Button("Sign Up", type="submit"),
                method="post", action="/register",
            ),
            Div("Have an account? ", A("Login", href="/signin"), cls="auth-alt"),
            cls="auth-card",
        ), cls="auth-page"),
    )

@rt("/web-logout")
def web_logout(session):
    session.pop("user", None)
    from starlette.responses import RedirectResponse
    return RedirectResponse("/signin", status_code=303)


@rt("/health")
def health():
    _evict_stale()
    return {"status": "ok", "sessions": len(_sessions)}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.getenv("WEB_PORT", "4002"))
    print(f"\n  PolyTrade Web Shell -> http://localhost:{port}")
    print(f"  Model: {os.getenv('MODEL', '?')} via {os.getenv('MODEL_PROVIDER', '?')}")
    print()
    print("  Commands:")
    print("    load AAPL              Company profile & quote")
    print("    fa NVDA                Financial analysis")
    print("    anr MSFT               Analyst recommendations")
    print("    ee TSLA                Earnings estimates")
    print("    rv GOOG                Relative valuation")
    print("    news TSLA              Latest news")
    print("    scan                   Scan weather opportunities")
    print("    poly:weather London    Search weather markets")
    print("    poly:backtest Seoul 7  Multi-day backtest")
    print("    poly:predict London 2  Forward prediction")
    print("    <any question>         Ask the AI agent")
    print()
    serve(port=port)
