"""
AG-UI core: WebSocket-based chat with LangGraph agents.

Streaming via LangGraph astream_events(v2).
Adapted for PolyTrade — in-memory conversation store (no SQLAlchemy dependency).
"""

from typing import Dict, List, Optional, Any
from fasthtml.common import (
    Div, Form, Hidden, Textarea, Button, Span, Script, Style, Pre, NotStr,
)
import asyncio
import collections
import logging
import re
import threading
import uuid

from .styles import get_chat_styles


# ---------------------------------------------------------------------------
# Follow-up suggestions — contextual pills shown after command results
# ---------------------------------------------------------------------------

def _get_followup_suggestions(msg: str, result: str = None) -> list:
    """Return contextual follow-up suggestions based on the command."""
    cmd = msg.strip().lower()
    first = cmd.split()[0] if cmd.split() else ""

    if first.startswith("poly:backtest"):
        return ["poly:backtestv2 London 7", "poly:weather London", "poly:predict London 2"]
    if first.startswith("poly:weather"):
        return ["poly:backtest London 7", "poly:predict London 2", "poly:simbuy 50"]
    if first.startswith("poly:predict"):
        return ["poly:weather London", "poly:backtest London 7"]
    if first in ("fa", "load", "quote"):
        ticker = cmd.split()[1].upper() if len(cmd.split()) > 1 else ""
        if ticker:
            return [f"anr {ticker}", f"ee {ticker}", f"gp {ticker}", f"news {ticker}"]
    if first in ("anr", "ee", "rv", "own"):
        ticker = cmd.split()[1].upper() if len(cmd.split()) > 1 else ""
        if ticker:
            return [f"fa {ticker}", f"gp {ticker}", f"news {ticker}"]
    if first == "scan":
        return ["poly:weather London", "poly:weather Seoul", "poly:weather New York"]

    return ["help", "scan", "poly:weather London"]


# ---------------------------------------------------------------------------
# StreamingCommand sentinel
# ---------------------------------------------------------------------------

class StreamingCommand:
    """Sentinel returned by the command interceptor for long-running commands."""

    def __init__(self, raw_command: str, session: dict, app_state: Any = None):
        self.raw_command = raw_command
        self.session = session
        self.app_state = app_state


# ---------------------------------------------------------------------------
# Shared JS snippets
# ---------------------------------------------------------------------------

_SCROLL_CHAT_JS = "var m=document.getElementById('chat-messages');if(m)m.scrollTop=m.scrollHeight;"
_GUARD_ENABLE_JS = "window._aguiProcessing=true;"
_GUARD_DISABLE_JS = "window._aguiProcessing=false;"


# ---------------------------------------------------------------------------
# LogCapture — thread-safe logging handler
# ---------------------------------------------------------------------------

class LogCapture(logging.Handler):
    """Captures log records into a deque for streaming to the browser."""

    def __init__(self, maxlen=500):
        super().__init__()
        self.lines: collections.deque = collections.deque(maxlen=maxlen)
        self._lock = threading.Lock()
        self.setFormatter(logging.Formatter("%(asctime)s %(message)s", datefmt="%H:%M:%S"))

    def emit(self, record):
        try:
            msg = self.format(record)
            with self._lock:
                self.lines.append(msg)
        except Exception:
            self.handleError(record)

    def get_lines(self) -> list:
        with self._lock:
            return list(self.lines)

    def clear(self):
        with self._lock:
            self.lines.clear()


# ---------------------------------------------------------------------------
# UI renderer
# ---------------------------------------------------------------------------

class UI:
    """Renders chat components for a given thread."""

    def __init__(self, thread_id: str, autoscroll: bool = True):
        self.thread_id = thread_id
        self.autoscroll = autoscroll

    def _clear_input(self):
        return self._render_input_form(oob_swap=True)

    def _render_messages(self, messages: list[dict], oob: bool = False):
        attrs = {"id": "chat-messages", "cls": "chat-messages"}
        if oob:
            attrs["hx_swap_oob"] = "outerHTML"
        return Div(
            *[self._render_message(m) for m in messages],
            **attrs,
        )

    def _render_message(self, message: dict):
        role = message.get("role", "assistant")
        cls = "chat-user" if role == "user" else "chat-assistant"
        mid = message.get("message_id", str(uuid.uuid4()))
        return Div(
            Div(message.get("content", ""), cls="chat-message-content marked"),
            cls=f"chat-message {cls}",
            id=mid,
        )

    def _render_input_form(self, oob_swap=False):
        container_attrs = {"cls": "chat-input", "id": "chat-input-container"}
        if oob_swap:
            container_attrs["hx_swap_oob"] = "outerHTML"

        return Div(
            Div(id="suggestion-buttons"),
            Div(id="chat-status", cls="chat-status"),
            Form(
                Hidden(name="thread_id", value=self.thread_id),
                Textarea(
                    id="chat-input",
                    name="msg",
                    placeholder="Type a command or ask a question...",
                    autofocus=True,
                    autocomplete="off",
                    cls="chat-input-field",
                    rows="1",
                    onkeydown="handleKeyDown(this, event)",
                    oninput="autoResize(this)",
                ),
                Button("Send", type="submit", cls="chat-input-button",
                       onclick="if(window._aguiProcessing){event.preventDefault();return false;}"),
                cls="chat-input-form",
                id="chat-form",
                ws_send=True,
            ),
            Div(Span("Enter", cls="kbd"), " to send", cls="input-hint"),
            **container_attrs,
        )

    def _render_welcome(self):
        """Render the welcome hero with suggestion cards."""
        _ICON_CHAT = '<svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z"/></svg>'
        _ICON_WEATHER = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 10h-1.26A8 8 0 109 20h9a5 5 0 000-10z"/></svg>'
        _ICON_CHART = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 20V10M12 20V4M6 20v-6"/></svg>'
        _ICON_NEWS = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 22h16a2 2 0 002-2V4a2 2 0 00-2-2H8a2 2 0 00-2 2v16a2 2 0 01-2 2zm0 0a2 2 0 01-2-2v-9c0-1.1.9-2 2-2h2"/><path d="M18 14h-8M15 18h-5M10 6h8v4h-8z"/></svg>'
        _ICON_SEARCH = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><path d="M21 21l-4.35-4.35"/></svg>'

        cards = [
            ("Weather Markets", "Search Polymarket weather opportunities", "poly:weather London", "#3b82f6", _ICON_WEATHER),
            ("Run Backtest", "Backtest weather prediction strategy", "poly:backtest London 7", "#8b5cf6", _ICON_CHART),
            ("Stock Research", "Financials, analyst ratings, news", "fa AAPL", "#f59e0b", _ICON_NEWS),
            ("Scan Opportunities", "Find high-edge weather markets", "scan", "#10b981", _ICON_SEARCH),
        ]

        card_els = []
        for title, desc, cmd, color, icon_svg in cards:
            card_els.append(
                Div(
                    Div(NotStr(icon_svg), cls="welcome-card-icon",
                        style=f"background:{color}15;color:{color}"),
                    Div(title, cls="welcome-card-title"),
                    Div(desc, cls="welcome-card-desc"),
                    cls="welcome-card",
                    onclick=(
                        f"if(window._aguiProcessing)return;"
                        f"var ta=document.getElementById('chat-input');"
                        f"var fm=document.getElementById('chat-form');"
                        f"if(ta&&fm){{ta.value={repr(cmd)};fm.requestSubmit();}}"
                    ),
                )
            )

        return Div(
            Div(
                Div(NotStr(_ICON_CHAT), cls="welcome-icon"),
                Div("PolyTrade", cls="welcome-title"),
                Div("AI-powered financial research & prediction-market trading", cls="welcome-subtitle"),
                Div(*card_els, cls="welcome-grid"),
                cls="welcome-hero",
            ),
            id="welcome-screen",
        )

    def chat(self, **kwargs):
        """Return the full chat widget (messages + input + scripts)."""
        components = [
            get_chat_styles(),
            Div(
                self._render_welcome(),
                id="chat-messages",
                cls="chat-messages",
                hx_get=f"/agui/messages/{self.thread_id}",
                hx_trigger="load",
                hx_swap="outerHTML",
            ),
            self._render_input_form(),
            Script("""
                function autoResize(textarea) {
                    textarea.style.height = 'auto';
                    var maxH = 12 * 16;
                    var h = Math.min(textarea.scrollHeight, maxH);
                    textarea.style.height = h + 'px';
                    textarea.style.overflowY = textarea.scrollHeight > maxH ? 'auto' : 'hidden';
                }
                function handleKeyDown(textarea, event) {
                    autoResize(textarea);
                    if (event.key === 'Enter' && !event.shiftKey) {
                        event.preventDefault();
                        if (window._aguiProcessing) return;
                        var form = textarea.closest('form');
                        if (form && textarea.value.trim()) form.requestSubmit();
                    }
                }
                function renderMarkdown(elementId) {
                    setTimeout(function() {
                        var el = document.getElementById(elementId);
                        if (el && window.marked && el.classList.contains('marked')) {
                            var txt = el.textContent || el.innerText;
                            if (txt.trim()) {
                                el.innerHTML = marked.parse(txt);
                                el.classList.remove('marked');
                                el.classList.add('marked-done');
                                enhanceTables(el);
                            }
                        }
                    }, 100);
                }
                function tableToCSV(table) {
                    var rows = [];
                    table.querySelectorAll('tr').forEach(function(tr) {
                        var cells = [];
                        tr.querySelectorAll('th, td').forEach(function(td) {
                            var val = td.textContent.trim().replace(/"/g, '""');
                            cells.push('"' + val + '"');
                        });
                        rows.push(cells.join(','));
                    });
                    return rows.join('\\n');
                }
                function enhanceTables(container) {
                    container.querySelectorAll('table').forEach(function(table) {
                        if (table.dataset.enhanced) return;
                        table.dataset.enhanced = '1';
                        var toolbar = document.createElement('div');
                        toolbar.className = 'table-toolbar';
                        var copyBtn = document.createElement('button');
                        copyBtn.textContent = 'Copy CSV';
                        copyBtn.className = 'table-action-btn';
                        copyBtn.onclick = function() {
                            var csv = tableToCSV(table);
                            navigator.clipboard.writeText(csv).then(function() {
                                copyBtn.textContent = 'Copied!';
                                setTimeout(function(){ copyBtn.textContent = 'Copy CSV'; }, 1500);
                            });
                        };
                        toolbar.appendChild(copyBtn);
                        table.parentNode.insertBefore(toolbar, table);
                    });
                }
                // Auto-render .marked elements
                if (window.marked) {
                    new MutationObserver(function() {
                        document.querySelectorAll('.marked').forEach(function(el) {
                            var parent = el.parentElement;
                            if (parent) {
                                var cursor = parent.querySelector('.chat-streaming');
                                if (cursor && cursor.textContent) return;
                            }
                            var txt = el.textContent || el.innerText;
                            if (txt.trim() && !el.dataset.rendering) {
                                el.dataset.rendering = '1';
                                setTimeout(function() {
                                    if (!el.classList.contains('marked')) { delete el.dataset.rendering; return; }
                                    var finalTxt = el.textContent || el.innerText;
                                    if (finalTxt.trim()) {
                                        el.innerHTML = marked.parse(finalTxt);
                                        el.classList.remove('marked');
                                        el.classList.add('marked-done');
                                        enhanceTables(el);
                                    }
                                    delete el.dataset.rendering;
                                }, 150);
                            }
                        });
                    }).observe(document.body, {childList: true, subtree: true});
                }
            """),
        ]

        if self.autoscroll:
            components.append(Script("""
                (function() {
                    var obs = new MutationObserver(function() {
                        var m = document.getElementById('chat-messages');
                        if (m) m.scrollTop = m.scrollHeight;
                    });
                    var t = document.getElementById('chat-messages');
                    if (t) obs.observe(t, {childList: true, subtree: true});
                })();
            """))

        # Hidden div for OOB JS execution
        components.append(Div(id="agui-js", style="display:none"))

        return Div(
            *components,
            hx_ext="ws",
            ws_connect=f"/agui/ws/{self.thread_id}",
            cls="chat-container",
            **kwargs,
        )


# ---------------------------------------------------------------------------
# Thread (conversation)
# ---------------------------------------------------------------------------

class AGUIThread:
    """Single conversation thread with message history and LangGraph agent."""

    def __init__(self, thread_id: str, langgraph_agent, user_id: str = None):
        self.thread_id = thread_id
        self._agent = langgraph_agent
        self._user_id = user_id
        self._messages: list[dict] = []
        self._connections: Dict[str, Any] = {}
        self.ui = UI(self.thread_id, autoscroll=True)
        self._suggestions: list[str] = []
        self._command_interceptor = None

    def subscribe(self, connection_id, send):
        self._connections[connection_id] = send

    def unsubscribe(self, connection_id: str):
        self._connections.pop(connection_id, None)

    async def send(self, element):
        for _, send_fn in self._connections.items():
            await send_fn(element)

    async def _send_js(self, js_code: str):
        """Execute JS in the browser via OOB swap."""
        await self.send(Div(Script(js_code), id="agui-js", hx_swap_oob="innerHTML"))

    async def set_suggestions(self, suggestions: list[str]):
        self._suggestions = suggestions[:4]
        if self._suggestions:
            el = Div(
                *[
                    Button(
                        Span(s), Span("\u2192", cls="arrow"),
                        onclick=f"if(window._aguiProcessing)return;"
                        f"var ta=document.getElementById('chat-input');"
                        f"var fm=document.getElementById('chat-form');"
                        f"if(ta&&fm){{ta.value={repr(s)};fm.requestSubmit();}}",
                        cls="suggestion-btn",
                    )
                    for s in self._suggestions
                ],
                id="suggestion-buttons",
                hx_swap_oob="outerHTML",
            )
        else:
            el = Div(id="suggestion-buttons", hx_swap_oob="outerHTML")
        await self.send(el)

    async def _handle_message(self, msg: str, session):
        # Block double-submit
        await self._send_js(_GUARD_ENABLE_JS)

        # Hide welcome screen + clear suggestions
        await self.send(Div(id="welcome-screen", style="display:none", hx_swap_oob="outerHTML"))
        await self.set_suggestions([])

        # CLI command interception
        if self._command_interceptor:
            result = await self._command_interceptor(msg, session)
            if result is not None:
                if isinstance(result, StreamingCommand):
                    asyncio.create_task(
                        self._handle_streaming_command(msg, result, session)
                    )
                else:
                    await self._handle_command_result(msg, result, session)
                return

        # AI message — route to LangGraph
        await self._handle_ai_run(msg, session)

    async def _handle_ai_run(self, msg: str, session):
        """Stream a LangGraph agent response via astream_events(v2)."""
        from langchain_core.messages import HumanMessage, AIMessage

        _open_trace = (
            "var l=document.querySelector('.app-layout');"
            "if(l&&!l.classList.contains('right-open'))l.classList.add('right-open');"
            "setTimeout(function(){var tc=document.getElementById('trace-content');"
            "if(tc)tc.scrollTop=tc.scrollHeight;},100);"
        )

        user_mid = str(uuid.uuid4())
        asst_mid = str(uuid.uuid4())
        content_id = f"message-content-{asst_mid}"

        # 1. Save user message
        user_dict = {"role": "user", "content": msg, "message_id": user_mid}
        self._messages.append(user_dict)

        # 2. Send user bubble
        await self.send(Div(
            Div(
                Div(msg, cls="chat-message-content"),
                cls="chat-message chat-user",
                id=user_mid,
            ),
            id="chat-messages",
            hx_swap_oob="beforeend",
        ))

        # Clear input + disable
        await self.send(self.ui._clear_input())
        await self._send_js(
            "var b=document.querySelector('.chat-input-button'),t=document.getElementById('chat-input');"
            "if(b){b.disabled=true;b.classList.add('sending')}"
            "if(t){t.disabled=true;t.placeholder='Thinking...'}"
        )

        # 3. Create empty streaming assistant bubble
        await self.send(Div(
            Div(
                Div(
                    Span("", id=content_id),
                    Span("", cls="chat-streaming", id=f"streaming-{asst_mid}"),
                    cls="chat-message-content",
                ),
                cls="chat-message chat-assistant",
                id=f"message-{asst_mid}",
            ),
            id="chat-messages",
            hx_swap_oob="beforeend",
        ))

        # 4. Trace: run started
        run_trace_id = str(uuid.uuid4())
        await self.send(Div(
            Div(
                Span("AI run started", cls="trace-label"),
                cls="trace-entry trace-run-start",
                id=f"trace-run-{run_trace_id}",
            ),
            Script(_open_trace),
            id="trace-content",
            hx_swap_oob="beforeend",
        ))

        # 5. Convert message history to LangChain format
        lc_messages = []
        for m in self._messages:
            role = m.get("role", "user")
            content = m.get("content", "")
            if role == "user":
                lc_messages.append(HumanMessage(content=content))
            else:
                lc_messages.append(AIMessage(content=content))

        # 6. Stream via astream_events
        full_response = ""
        try:
            async for event in self._agent.astream_events(
                {"messages": lc_messages}, version="v2"
            ):
                kind = event.get("event", "")

                if kind == "on_chat_model_stream":
                    chunk = event.get("data", {}).get("chunk")
                    if chunk and hasattr(chunk, "content") and chunk.content:
                        token = chunk.content
                        full_response += token
                        await self.send(Span(
                            token,
                            id=content_id,
                            hx_swap_oob="beforeend",
                        ))

                elif kind == "on_tool_start":
                    tool_name = event.get("name", "tool")
                    tool_run_id = event.get("run_id", "")[:8]
                    await self.send(Div(
                        Div(
                            Span(f"Tool: {tool_name}", cls="trace-label"),
                            Span("running...", cls="trace-detail"),
                            cls="trace-entry trace-tool-active",
                            id=f"trace-tool-{tool_run_id}",
                        ),
                        Script(_open_trace),
                        id="trace-content",
                        hx_swap_oob="beforeend",
                    ))
                    await self.send(Div(
                        Div(
                            Div(f"Running {tool_name}...", cls="chat-message-content"),
                            cls="chat-message chat-tool",
                            id=f"tool-{tool_run_id}",
                        ),
                        id="chat-messages",
                        hx_swap_oob="beforeend",
                    ))

                elif kind == "on_tool_end":
                    tool_run_id = event.get("run_id", "")[:8]
                    await self.send(Div(
                        Div("Done", cls="chat-message-content"),
                        cls="chat-message chat-tool",
                        id=f"tool-{tool_run_id}",
                        hx_swap_oob="outerHTML",
                    ))
                    await self.send(Div(
                        Span("Tool complete", cls="trace-label"),
                        cls="trace-entry trace-tool-done",
                        id=f"trace-tool-{tool_run_id}",
                        hx_swap_oob="outerHTML",
                    ))

        except Exception as e:
            error_msg = str(e)
            full_response = f"Error: {error_msg}"
            await self.send(Span(
                f"\n\n**Error:** {error_msg}",
                id=content_id,
                hx_swap_oob="beforeend",
            ))
            await self.send(Div(
                Div(
                    Span("Error", cls="trace-label"),
                    Span(error_msg[:200], cls="trace-detail"),
                    cls="trace-entry trace-error",
                ),
                id="trace-content",
                hx_swap_oob="beforeend",
            ))

        # 7. Finalize: remove cursor, render markdown
        await self.send(Span("", id=f"streaming-{asst_mid}", hx_swap_oob="outerHTML"))
        await self._send_js(
            f"var el=document.getElementById('{content_id}');"
            f"if(el)el.classList.add('marked');"
            f"renderMarkdown('{content_id}');"
        )

        # Trace: run finished
        await self.send(Div(
            Div(
                Span("Run finished", cls="trace-label"),
                cls="trace-entry trace-run-end",
            ),
            id="trace-content",
            hx_swap_oob="beforeend",
        ))

        # 8. Save assistant message
        asst_dict = {"role": "assistant", "content": full_response, "message_id": asst_mid}
        self._messages.append(asst_dict)

        # Re-enable input
        await self.send(self.ui._clear_input())
        await self._send_js(
            _GUARD_DISABLE_JS +
            "var b=document.querySelector('.chat-input-button'),t=document.getElementById('chat-input');"
            "if(b){b.disabled=false;b.classList.remove('sending')}"
            "if(t){t.disabled=false;t.placeholder='Type a command or ask a question...';t.focus()}"
        )
        await self._send_js(_SCROLL_CHAT_JS)

    async def _handle_command_result(self, msg: str, result: str, session):
        """Display a CLI command result in chat with trace pane integration."""

        await self._send_js(
            "var b=document.querySelector('.chat-input-button'),t=document.getElementById('chat-input');"
            "if(b){b.disabled=true;b.classList.add('sending')}"
            "if(t){t.disabled=true;t.placeholder='Thinking...'}"
        )

        _open_trace = (
            "var l=document.querySelector('.app-layout');"
            "if(l&&!l.classList.contains('right-open'))l.classList.add('right-open');"
            "setTimeout(function(){var tc=document.getElementById('trace-content');"
            "if(tc)tc.scrollTop=tc.scrollHeight;},100);"
        )
        cmd_id = str(uuid.uuid4())

        # Append user message
        user_mid = str(uuid.uuid4())
        user_dict = {"role": "user", "content": msg, "message_id": user_mid}
        self._messages.append(user_dict)

        # Send user message + trace
        await self.send(Div(
            Div(
                Div(msg, cls="chat-message-content"),
                cls="chat-message chat-user",
                id=user_mid,
            ),
            id="chat-messages",
            hx_swap_oob="beforeend",
        ))
        await self.send(Div(
            Div(
                Span(f"Command: {msg}", cls="trace-label"),
                cls="trace-entry trace-run-start",
                id=f"trace-cmd-{cmd_id}",
            ),
            Script(_open_trace),
            id="trace-content",
            hx_swap_oob="beforeend",
        ))

        # Streaming cursor
        asst_id = str(uuid.uuid4())
        content_id = f"content-{asst_id}"
        await self.send(Div(
            Div(
                Div(
                    Span("", id=f"message-content-{asst_id}"),
                    Span("", cls="chat-streaming", id=f"streaming-{asst_id}"),
                    cls="chat-message-content",
                ),
                cls="chat-message chat-assistant",
                id=f"message-{asst_id}",
            ),
            id="chat-messages",
            hx_swap_oob="beforeend",
        ))

        await asyncio.sleep(0.15)

        # Remove streaming cursor, inject final content
        await self.send(Span("", id=f"streaming-{asst_id}", hx_swap_oob="outerHTML"))
        await self.send(Div(
            Div(result, cls="chat-message-content marked", id=content_id),
            cls="chat-message chat-assistant",
            id=f"message-{asst_id}",
            hx_swap_oob="outerHTML",
        ))
        await self._send_js(f"renderMarkdown('{content_id}');")
        await self._send_js(_SCROLL_CHAT_JS)

        # Trace: command complete
        await self.send(Div(
            Div(
                Span("Command complete", cls="trace-label"),
                cls="trace-entry trace-done",
            ),
            id="trace-content",
            hx_swap_oob="beforeend",
        ))

        # Store message
        asst_dict = {"role": "assistant", "content": result, "message_id": asst_id}
        self._messages.append(asst_dict)

        # Re-enable input + suggestions
        await self.send(self.ui._clear_input())
        await self._send_js(
            _GUARD_DISABLE_JS +
            "var b=document.querySelector('.chat-input-button'),t=document.getElementById('chat-input');"
            "if(b){b.disabled=false;b.classList.remove('sending')}"
            "if(t){t.disabled=false;t.placeholder='Type a command or ask a question...';t.focus()}"
        )
        await self.set_suggestions(_get_followup_suggestions(msg, result))

    async def _handle_streaming_command(self, msg: str, sc: StreamingCommand, session):
        """Run a long-running command in background, streaming logs via WS."""
        _open_trace = (
            "var l=document.querySelector('.app-layout');"
            "if(l&&!l.classList.contains('right-open'))l.classList.add('right-open');"
            "setTimeout(function(){var tc=document.getElementById('trace-content');"
            "if(tc)tc.scrollTop=tc.scrollHeight;},100);"
        )
        cmd_id = str(uuid.uuid4())
        asst_id = str(uuid.uuid4())
        log_pre_id = f"log-pre-{asst_id}"
        log_console_id = f"log-console-{asst_id}"
        content_id = f"content-{asst_id}"

        # 1. Send user message
        user_mid = str(uuid.uuid4())
        user_dict = {"role": "user", "content": msg, "message_id": user_mid}
        self._messages.append(user_dict)

        await self.send(Div(
            Div(
                Div(msg, cls="chat-message-content"),
                cls="chat-message chat-user",
                id=user_mid,
            ),
            id="chat-messages",
            hx_swap_oob="beforeend",
        ))

        # 2. Open trace
        await self.send(Div(
            Div(
                Span(f"Command: {msg}", cls="trace-label"),
                cls="trace-entry trace-run-start",
                id=f"trace-cmd-{cmd_id}",
            ),
            Script(_open_trace),
            id="trace-content",
            hx_swap_oob="beforeend",
        ))

        # 3. Log console bubble
        await self.send(Div(
            Div(
                Div(
                    Pre("Starting...", id=log_pre_id, cls="agui-log-pre"),
                    cls="agui-log-console",
                    id=log_console_id,
                ),
                cls="chat-message chat-assistant",
                id=f"message-{asst_id}",
            ),
            id="chat-messages",
            hx_swap_oob="beforeend",
        ))

        # 4. Disable input
        await self.send(self.ui._clear_input())
        await self._send_js(
            "setTimeout(function(){"
            "var b=document.querySelector('.chat-input-button'),ta=document.getElementById('chat-input');"
            "if(b){b.disabled=true;b.classList.add('sending')}"
            "if(ta){ta.disabled=true;ta.placeholder='Running command...';}"
            "}, 100);"
        )

        # 5. Attach LogCapture
        log_capture = LogCapture(maxlen=1000)
        root_logger = logging.getLogger()
        prev_level = root_logger.level
        if root_logger.level > logging.INFO:
            root_logger.setLevel(logging.INFO)
        root_logger.addHandler(log_capture)

        # 6. Launch command in background
        result_holder = {"value": None, "error": None, "done": False}

        async def _run_command():
            try:
                from components.command_processor import CommandProcessor
                from agent.agent import Agent
                from agent.types import AgentConfig
                import os
                config = AgentConfig(
                    model=os.getenv("MODEL"),
                    model_provider=os.getenv("MODEL_PROVIDER"),
                )
                agent = Agent.create(config)
                cp = CommandProcessor(agent)
                _, _ = await cp.process_command(sc.raw_command)
                result_holder["value"] = "Command executed."
            except Exception as e:
                import traceback
                result_holder["error"] = traceback.format_exc()
            finally:
                result_holder["done"] = True

        asyncio.create_task(_run_command())

        # 7. Poll loop
        prev_line_count = 0
        while not result_holder["done"]:
            await asyncio.sleep(0.5)
            lines = log_capture.get_lines()
            if len(lines) != prev_line_count:
                prev_line_count = len(lines)
                display_lines = lines[-100:]
                log_text = "\n".join(display_lines) if display_lines else "Initializing..."
                try:
                    await self.send(Pre(
                        log_text,
                        id=log_pre_id,
                        cls="agui-log-pre",
                        hx_swap_oob="outerHTML",
                    ))
                    await self._send_js(
                        f"var lc=document.getElementById('{log_console_id}');"
                        "if(lc)lc.scrollTop=lc.scrollHeight;"
                        + _SCROLL_CHAT_JS
                    )
                except Exception:
                    break

        # 8. Cleanup
        root_logger.removeHandler(log_capture)
        root_logger.setLevel(prev_level)

        # 9. Final result
        if result_holder["error"]:
            final_result = f"# Error\n\n```\n{result_holder['error']}\n```"
        else:
            final_result = result_holder["value"] or "Command executed."

        try:
            await self.send(Div(
                Div(final_result, cls="chat-message-content marked", id=content_id),
                cls="chat-message chat-assistant",
                id=f"message-{asst_id}",
                hx_swap_oob="outerHTML",
            ))
            await self._send_js(f"renderMarkdown('{content_id}');")
            await self._send_js(_SCROLL_CHAT_JS)

            # Trace: done
            await self.send(Div(
                Div(
                    Span("Command complete", cls="trace-label"),
                    Span(f"{prev_line_count} log lines", cls="trace-detail"),
                    cls="trace-entry trace-done",
                ),
                id="trace-content",
                hx_swap_oob="beforeend",
            ))

            asst_dict = {"role": "assistant", "content": final_result, "message_id": asst_id}
            self._messages.append(asst_dict)

            # Re-enable input
            await self.send(self.ui._clear_input())
            await self._send_js(
                _GUARD_DISABLE_JS +
                "setTimeout(function(){var ta=document.getElementById('chat-input');"
                "if(ta)ta.focus();}, 100);"
            )
            await self.set_suggestions(_get_followup_suggestions(msg, final_result))
        except Exception:
            try:
                await self._send_js(_GUARD_DISABLE_JS)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

class AGUISetup:
    """Wire AG-UI routes into a FastHTML app."""

    def __init__(self, app, langgraph_agent, command_interceptor=None):
        self.app = app
        self._agent = langgraph_agent
        self._threads: Dict[str, AGUIThread] = {}
        self._command_interceptor = command_interceptor
        self._setup_routes()

    def _setup_routes(self):
        @self.app.get("/agui/ui/{thread_id}/chat")
        async def agui_chat_ui(thread_id: str, session):
            session["thread_id"] = thread_id
            return self.thread(thread_id, session).ui.chat()

        @self.app.ws(
            "/agui/ws/{thread_id}",
            conn=self._on_conn,
            disconn=self._on_disconn,
        )
        async def agui_ws(thread_id: str, msg: str, session):
            await self._threads[thread_id]._handle_message(msg, session)

        @self.app.route("/agui/messages/{thread_id}")
        def agui_messages(thread_id: str, session):
            thread = self.thread(thread_id, session)
            if thread._messages:
                return thread.ui._render_messages(thread._messages)
            return Div(thread.ui._render_welcome(), id="chat-messages", cls="chat-messages")

    def thread(self, thread_id: str, session=None) -> AGUIThread:
        if thread_id not in self._threads:
            t = AGUIThread(thread_id=thread_id, langgraph_agent=self._agent)
            if self._command_interceptor:
                t._command_interceptor = self._command_interceptor
            self._threads[thread_id] = t
        return self._threads[thread_id]

    def _on_conn(self, ws, send, session):
        tid = session.get("thread_id", "default")
        self.thread(tid, session).subscribe(str(id(ws)), send)

    def _on_disconn(self, ws, session):
        tid = session.get("thread_id", "default")
        if tid in self._threads:
            self._threads[tid].unsubscribe(str(id(ws)))

    def chat(self, thread_id: str):
        """Return a loader div that fetches the chat UI."""
        return Div(
            hx_get=f"/agui/ui/{thread_id}/chat",
            hx_trigger="load",
            hx_swap="innerHTML",
        )


def setup_agui(app, langgraph_agent, command_interceptor=None) -> AGUISetup:
    """One-line setup: wire AG-UI into a FastHTML app."""
    return AGUISetup(app, langgraph_agent, command_interceptor=command_interceptor)
