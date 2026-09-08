"""
AG-UI renderer for the transport-neutral shared PolyTrade ChatService.

This module owns FastHTML/WebSocket presentation only. Agent execution,
commands, persistence, and authorization live behind ChatService.
"""

from typing import Any, Dict
import time
from fasthtml.common import (
    Div, Form, Hidden, Textarea, Button, Span, Script, Style, Pre, NotStr,
)
import uuid

from .styles import get_chat_styles
from chat.events import (
    AGENT_ROUTE,
    MESSAGE_COMPLETED,
    MESSAGE_DELTA,
    RUN_COMPLETED,
    RUN_FAILED,
    RUN_STARTED,
    TOOL_COMPLETED,
    TOOL_STARTED,
)


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
# Shared JS snippets
# ---------------------------------------------------------------------------

_SCROLL_CHAT_JS = "var m=document.getElementById('chat-messages');if(m)m.scrollTop=m.scrollHeight;"
_GUARD_ENABLE_JS = "window._aguiProcessing=true;"
_GUARD_DISABLE_JS = "window._aguiProcessing=false;"


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
        content = message.get("content", "")

        # Detect Rich table content — render as <pre> instead of markdown
        has_box = any(c in content for c in "\u2502\u2503\u2500\u2501\u250c\u2510\u2514\u2518\u2513\u251b")
        stripped = content.strip()
        is_code = stripped.startswith("```")

        if role != "user" and (has_box or is_code):
            # Extract raw text from code block wrapper
            if is_code:
                raw = stripped[3:]
                if raw.startswith("\n"):
                    raw = raw[1:]
                if raw.endswith("```"):
                    raw = raw[:-3]
                raw = raw.strip("\n")
            else:
                raw = content
            return Div(
                Div(Pre(raw, cls="agui-log-pre"), cls="chat-message-content"),
                cls=f"chat-message {cls}",
                id=mid,
            )

        return Div(
            Div(content, cls="chat-message-content marked"),
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
            ("Weather Markets", "Search Polymarket weather opportunities", "poly:weather London", "#10b981", _ICON_WEATHER),
            ("Run Backtest", "Backtest weather prediction strategy", "poly:backtest London 7", "#6366f1", _ICON_CHART),
            ("Stock Research", "Financials, analyst ratings, news", "fa AAPL", "#f59e0b", _ICON_NEWS),
            ("Scan Opportunities", "Find high-edge weather markets", "scan", "#059669", _ICON_SEARCH),
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
                function safeMarkdown(txt) {
                    if (window.marked && window.DOMPurify) {
                        return DOMPurify.sanitize(marked.parse(txt));
                    }
                    var fallback = document.createElement('pre');
                    fallback.textContent = txt;
                    return fallback.outerHTML;
                }
                function renderMarkdown(elementId) {
                    setTimeout(function() {
                        var el = document.getElementById(elementId);
                        if (el && window.marked && el.classList.contains('marked')) {
                            var txt = el.textContent || el.innerText;
                            if (txt.trim()) {
                                el.innerHTML = safeMarkdown(txt);
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
                // Convert Rich console table to clean CSV
                // Handles thin (│ U+2502), thick (┃ U+2503) Rich borders
                // Extracts ONLY table data rows — no text, no analysis
                var PIPE_RE = /[\u2502\u2503]/;
                var BORDER_RE = /^[\u2500-\u257f\u2550-\u256c\u2580-\u259f\u2500\u2501\u250c-\u2570─━┏┓┗┛┡┩╇╈╭╮╰╯+\\-=\\s]+$/;
                function richTextToCSV(text) {
                    var NL = String.fromCharCode(10);
                    var lines = text.split(NL);
                    var rows = [];
                    // Count table vs panel lines
                    var tableCount = 0, panelCount = 0;
                    for (var k = 0; k < lines.length; k++) {
                        if (!PIPE_RE.test(lines[k])) continue;
                        var p = lines[k].split(/[\u2502\u2503]/);
                        var n = 0;
                        for (var m = 0; m < p.length; m++) { if (p[m].trim() && !BORDER_RE.test(p[m].trim())) n++; }
                        if (n >= 2) tableCount++;
                        else if (n === 1) panelCount++;
                    }
                    if (panelCount > tableCount && tableCount < 2) {
                        // Panel mode (load AAPL etc.) — key:value pairs
                        rows.push('Field,Value');
                        for (var i = 0; i < lines.length; i++) {
                            if (!PIPE_RE.test(lines[i])) continue;
                            var c = lines[i].replace(/[\u2502\u2503]/g, '').trim();
                            if (!c || BORDER_RE.test(c)) continue;
                            var ci = c.indexOf(':');
                            if (ci < 0 || ci > 30) continue;
                            if (/^(Source|Business)/i.test(c.substring(0, ci).trim())) continue;
                            var key = c.substring(0, ci).trim().replace(/,/g, '');
                            var val = '"' + c.substring(ci + 1).trim().replace(/"/g, "'") + '"';
                            rows.push(key + ',' + val);
                        }
                    } else {
                        // Table mode — extract ONLY data rows (lines with pipe separators)
                        var prevColCount = 0;
                        for (var i = 0; i < lines.length; i++) {
                            if (!PIPE_RE.test(lines[i])) continue;
                            var parts = lines[i].split(/[\u2502\u2503]/);
                            var cells = [];
                            for (var j = 0; j < parts.length; j++) {
                                var raw = parts[j].trim();
                                if (!raw) continue;
                                if (BORDER_RE.test(raw)) continue;
                                cells.push('"' + raw.replace(/"/g, "'") + '"');
                            }
                            if (cells.length >= 2) {
                                // Blank separator between tables with different column counts
                                if (prevColCount > 0 && cells.length !== prevColCount && rows.length > 0) {
                                    rows.push('');
                                }
                                rows.push(cells.join(','));
                                prevColCount = cells.length;
                            }
                        }
                    }
                    return rows.join(NL);
                }
                function addToolbar(el, getText) {
                    if (el.dataset.enhanced === '1') return;
                    el.dataset.enhanced = '1';
                    // Remove any existing toolbar
                    var prev = el.previousElementSibling;
                    if (prev && prev.classList.contains('table-toolbar')) prev.remove();
                    var toolbar = document.createElement('div');
                    toolbar.className = 'table-toolbar';
                    var copyBtn = document.createElement('button');
                    copyBtn.textContent = 'Copy';
                    copyBtn.className = 'table-action-btn';
                    copyBtn.onclick = function() {
                        var data = getText();
                        if (!data) { copyBtn.textContent = 'Opening file...'; return; }
                        navigator.clipboard.writeText(data).then(function() {
                            copyBtn.textContent = 'Copied!';
                            setTimeout(function(){ copyBtn.textContent = 'Copy'; }, 1500);
                        });
                    };
                    var dlBtn = document.createElement('button');
                    dlBtn.textContent = 'Download CSV';
                    dlBtn.className = 'table-action-btn';
                    dlBtn.onclick = function() {
                        var data = getText();
                        if (!data) return; // Redirected to file download
                        var blob = new Blob([data], {type: 'text/csv;charset=utf-8'});
                        var url = URL.createObjectURL(blob);
                        var a = document.createElement('a');
                        a.href = url;
                        a.download = 'polytrade-data.csv';
                        a.click();
                        URL.revokeObjectURL(url);
                    };
                    toolbar.appendChild(copyBtn);
                    toolbar.appendChild(dlBtn);
                    el.parentNode.insertBefore(toolbar, el);
                }
                function enhanceTables(container) {
                    try {
                    var root = container || document;
                    // HTML tables
                    root.querySelectorAll('table').forEach(function(table) {
                        addToolbar(table, function(){ return tableToCSV(table); });
                    });
                    // Pre blocks with agui-log-pre class OR any pre with box chars
                    root.querySelectorAll('pre').forEach(function(pre) {
                        if (pre.dataset.enhanced === '1') return;
                        var txt = pre.textContent || '';
                        if (txt.length < 10) return;
                        // Check for any box-drawing character
                        if (PIPE_RE.test(txt) || /[\u250c\u2510\u2514\u2518\u2513\u251b\u2501\u2500\u256d\u256e\u256f\u2570]/.test(txt)) {
                            addToolbar(pre, function(){ return richTextToCSV(pre.textContent); });
                        }
                    });
                    } catch(e) { console.error('enhanceTables error:', e); }
                }
                // Run enhanceTables globally every 2s
                setInterval(function() { enhanceTables(document); }, 2000);
                // Also run after any HTMX swap
                document.body.addEventListener('htmx:afterSwap', function() {
                    setTimeout(function(){ enhanceTables(document); }, 300);
                });
                document.body.addEventListener('htmx:afterSettle', function() {
                    setTimeout(function(){ enhanceTables(document); }, 500);
                });
                // Run on page load
                setTimeout(function(){ enhanceTables(document); }, 1000);
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
                                        el.innerHTML = safeMarkdown(finalTxt);
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
    """Single conversation thread rendered from the shared ChatService."""

    def __init__(self, thread_id: str, chat_service, user_id: str = None):
        self.thread_id = thread_id
        self._chat_service = chat_service
        self._user_id = user_id
        self._messages: list[dict] = []
        self._connections: Dict[str, Any] = {}
        self.ui = UI(self.thread_id, autoscroll=True)
        self._suggestions: list[str] = []
        self._command_interceptor = None
        self._loaded = False

    async def _ensure_loaded(self):
        """Load messages from DB on first access."""
        if self._loaded:
            return
        self._loaded = True
        try:
            rows = await self._chat_service.get_messages(
                self._user_id, self.thread_id, limit=200
            )
            self._messages = rows
        except Exception:
            pass

    async def _refresh_conv_list(self):
        """Push an OOB swap to refresh the sidebar conversation list."""
        await self.send(Div(id="conv-list", hx_get="/agui-conv/list",
                            hx_trigger="load", hx_swap="innerHTML", hx_swap_oob="outerHTML"))

    def subscribe(self, connection_id, send):
        self._connections[connection_id] = send

    def unsubscribe(self, connection_id: str):
        self._connections.pop(connection_id, None)

    async def send(self, element):
        dead = []
        for cid, send_fn in self._connections.items():
            try:
                await send_fn(element)
            except Exception:
                dead.append(cid)
        for cid in dead:
            self._connections.pop(cid, None)

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
        await self._ensure_loaded()

        # Block double-submit
        await self._send_js(_GUARD_ENABLE_JS)
        try:
            # Hide welcome screen + clear suggestions
            await self.send(Div(id="welcome-screen", style="display:none", hx_swap_oob="outerHTML"))
            await self.set_suggestions([])

            # Commands and free-form questions both go through the shared backend.
            await self._handle_ai_run(msg, session)
        finally:
            # Network errors, cancelled streams, and rendering failures must never
            # leave the composer permanently locked.
            await self._send_js(
                _GUARD_DISABLE_JS +
                "var b=document.querySelector('.chat-input-button'),t=document.getElementById('chat-input');"
                "if(b){b.disabled=false;b.classList.remove('sending')}"
                "if(t){t.disabled=false;t.placeholder='Type a command or ask a question...'}"
            )

    async def _handle_ai_run(self, msg: str, session):
        """Render transport-neutral ChatService events over the existing WebSocket."""
        _open_trace = (
            "var l=document.querySelector('.app-layout');"
            "if(l&&!l.classList.contains('right-open'))l.classList.add('right-open');"
            "setTimeout(function(){var tc=document.getElementById('trace-content');"
            "if(tc)tc.scrollTop=tc.scrollHeight;},100);"
        )

        user_mid = str(uuid.uuid4())
        asst_mid = str(uuid.uuid4())
        content_id = f"message-content-{asst_mid}"

        # The service owns persistence. This local append is only for immediate UI state.
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

        await self.send(self.ui._clear_input())
        await self._send_js(
            "var b=document.querySelector('.chat-input-button'),t=document.getElementById('chat-input');"
            "if(b){b.disabled=true;b.classList.add('sending')}"
            "if(t){t.disabled=true;t.placeholder='Thinking...'}"
        )

        await self.send(Div(
            Div(
                Div(
                    Span("", id=content_id),
                    Span("Thinking", cls="chat-working-label", id=f"working-{asst_mid}"),
                    Span("", cls="chat-streaming", id=f"streaming-{asst_mid}"),
                    cls="chat-message-content",
                ),
                cls="chat-message chat-assistant",
                id=f"message-{asst_mid}",
            ),
            id="chat-messages",
            hx_swap_oob="beforeend",
        ))

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

        full_response = ""
        failed = False
        received_delta = False
        selected_agent = "DeepAgents"
        started_at = time.monotonic()
        try:
            async for event in self._chat_service.stream_message(
                user_id=self._user_id,
                thread_id=self.thread_id,
                content=msg,
                idempotency_key=str(uuid.uuid4()),
                client_message_id=user_mid,
                assistant_message_id=asst_mid,
            ):
                if event.event == AGENT_ROUTE:
                    selected_agent = event.data.get("agent", selected_agent)
                    fallback_note = " (fallback)" if event.data.get("fallback") else ""
                    await self.send(Div(
                        Div(
                            Span(f"Agent: {selected_agent}{fallback_note}", cls="trace-label"),
                            cls="trace-entry trace-agent-route",
                        ),
                        Script(_open_trace),
                        id="trace-content",
                        hx_swap_oob="beforeend",
                    ))
                elif event.event == MESSAGE_DELTA:
                    token = event.data.get("delta", "")
                    if token:
                        if not received_delta:
                            received_delta = True
                            await self.send(Span(
                                id=f"working-{asst_mid}", hx_swap_oob="delete"
                            ))
                            await self.send(Span(
                                id=f"streaming-{asst_mid}", hx_swap_oob="delete"
                            ))
                        await self.send(Span(
                            token,
                            id=content_id,
                            hx_swap_oob="beforeend",
                        ))
                elif event.event == TOOL_STARTED:
                    tool_name = event.data.get("name", "tool")
                    tool_run_id = event.data.get("tool_call_id", "")[:8]
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
                elif event.event == TOOL_COMPLETED:
                    tool_run_id = event.data.get("tool_call_id", "")[:8]
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
                elif event.event == MESSAGE_COMPLETED:
                    full_response = event.data.get("message", {}).get("content", "")
                elif event.event == RUN_FAILED:
                    failed = True
                    full_response = event.data.get(
                        "message", "I couldn't complete that request. Please try again."
                    )
                    await self.send(Div(
                        Div(
                            Span("Error", cls="trace-label"),
                            Span(
                                event.data.get("message", "Chat run failed.")[:200],
                                cls="trace-detail",
                            ),
                            cls="trace-entry trace-error",
                        ),
                        id="trace-content",
                        hx_swap_oob="beforeend",
                    ))
                elif event.event in {RUN_STARTED, RUN_COMPLETED}:
                    pass
        except Exception as e:
            failed = True
            full_response = "I couldn't complete that request. Please try again."
            await self.send(Div(
                Div(
                    Span("Error", cls="trace-label"),
                    Span(str(e)[:200], cls="trace-detail"),
                    cls="trace-entry trace-error",
                ),
                id="trace-content",
                hx_swap_oob="beforeend",
            ))

        if not full_response:
            full_response = "The agent returned an empty response. Please try again."
            failed = True

        # Replace streamed text with the authoritative persisted final message.
        elapsed = time.monotonic() - started_at
        await self.send(Div(
            Div(
                Div(
                    Span(selected_agent, cls="chat-agent-badge"),
                    Span(f"{elapsed:.1f}s", cls="chat-run-time"),
                    cls="chat-run-meta",
                ),
                Div(full_response, cls="marked", id=content_id),
                cls="chat-message-content",
            ),
            cls="chat-message chat-assistant",
            id=f"message-{asst_mid}",
            hx_swap_oob="outerHTML",
        ))
        await self._send_js(
            f"renderMarkdown('{content_id}');"
        )

        await self.send(Div(
            Div(
                Span("Run failed" if failed else "Run finished", cls="trace-label"),
                cls="trace-entry trace-error" if failed else "trace-entry trace-run-end",
            ),
            id="trace-content",
            hx_swap_oob="beforeend",
        ))

        try:
            self._messages = await self._chat_service.get_messages(
                self._user_id, self.thread_id, limit=200
            )
        except Exception:
            if not failed:
                self._messages.append(
                    {
                        "role": "assistant",
                        "content": full_response,
                        "message_id": asst_mid,
                    }
                )
        await self._refresh_conv_list()

        await self.send(self.ui._clear_input())
        await self._send_js(
            _GUARD_DISABLE_JS +
            "var b=document.querySelector('.chat-input-button'),t=document.getElementById('chat-input');"
            "if(b){b.disabled=false;b.classList.remove('sending')}"
            "if(t){t.disabled=false;t.placeholder='Type a command or ask a question...';t.focus()}"
        )
        await self._send_js(_SCROLL_CHAT_JS)


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

class AGUISetup:
    """Wire AG-UI routes into a FastHTML app."""

    def __init__(self, app, chat_service, command_interceptor=None):
        self.app = app
        self._chat_service = chat_service
        self._threads: Dict[str, AGUIThread] = {}
        # Retained only for source compatibility; routing now belongs to ChatService.
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
            await self.thread(thread_id, session)._handle_message(msg, session)

        @self.app.route("/agui/messages/{thread_id}")
        async def agui_messages(thread_id: str, session):
            thread = self.thread(thread_id, session)
            await thread._ensure_loaded()
            if thread._messages:
                return thread.ui._render_messages(thread._messages)
            return Div(thread.ui._render_welcome(), id="chat-messages", cls="chat-messages")

    def thread(self, thread_id: str, session=None) -> AGUIThread:
        user_id = None
        session_key = "anonymous"
        if session:
            user = session.get("user")
            if user:
                user_id = user.get("user_id")
                session_key = user_id
            else:
                session_key = session.get("chat_session_id")
                if not session_key:
                    session_key = str(uuid.uuid4())
                    session["chat_session_id"] = session_key
        cache_key = f"{session_key}:{thread_id}"
        if cache_key not in self._threads:
            t = AGUIThread(
                thread_id=thread_id,
                chat_service=self._chat_service,
                user_id=user_id,
            )
            self._threads[cache_key] = t
        return self._threads[cache_key]

    def _on_conn(self, ws, send, session):
        tid = session.get("thread_id", "default")
        thread = self.thread(tid, session)
        # Update user_id if user logged in after thread was created
        user = session.get("user")
        if user and not thread._user_id:
            thread._user_id = user.get("user_id")
        thread.subscribe(str(id(ws)), send)

    def _on_disconn(self, ws, session):
        tid = session.get("thread_id", "default")
        thread = self.thread(tid, session)
        thread.unsubscribe(str(id(ws)))
        if not thread._connections:
            for key, cached in list(self._threads.items()):
                if cached is thread:
                    self._threads.pop(key, None)

    def chat(self, thread_id: str):
        """Return a loader div that fetches the chat UI."""
        return Div(
            hx_get=f"/agui/ui/{thread_id}/chat",
            hx_trigger="load",
            hx_swap="innerHTML",
        )


def setup_agui(app, chat_service, command_interceptor=None) -> AGUISetup:
    """Wire the transport-neutral shared chat service into a FastHTML app."""
    return AGUISetup(app, chat_service, command_interceptor=command_interceptor)
