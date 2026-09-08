"""Settings and tenant-safe activity/usage pages for the PolyTrade chat app."""
from __future__ import annotations

import json
from fasthtml.common import (
    A, Button, Div, Form, H1, H2, Input, P, Span, Strong, Style,
    Table, Tbody, Td, Th, Thead, Title, Tr,
)
from starlette.responses import RedirectResponse

_CSS = """
body{background:#0a0d14;color:#e2e8f0;font-family:ui-monospace,monospace}
.account-page{max-width:1450px;margin:0 auto;padding:1.3rem}.page-head{display:flex;
justify-content:space-between;align-items:center;gap:1rem}.page-head a{color:#34d399}
.card{background:#0f1117;border:1px solid #1e2a3a;border-radius:12px;padding:1rem;
margin-top:1rem;overflow:auto}.muted{color:#94a3b8;font-size:.8rem}.safe{color:#34d399}
.key-form,.filter{display:flex;gap:.5rem;align-items:center;flex-wrap:wrap}
input{background:#141821!important;color:#e2e8f0!important;border:1px solid #2a3040!important}
button{background:#059669!important;border:0!important}.log-table{width:100%;font-size:.74rem;
border-collapse:collapse}.log-table th,.log-table td{padding:.5rem;border-bottom:1px solid #1e2a3a;
vertical-align:top;text-align:left}.log-table th{color:#94a3b8;font-size:.65rem;text-transform:uppercase}
.log-text{max-width:380px;white-space:pre-wrap;overflow-wrap:anywhere}.status{padding:.12rem .35rem;
border-radius:999px;background:#1a1f2e}.error{color:#f87171}
"""


def _time(value):
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return str(value or "—")


def _short(value, limit=500):
    text = str(value or "—")
    return text if len(text) <= limit else text[:limit] + "…"


def _table(headers, rows, empty):
    body = [Tr(*[Td(cell, cls="log-text") for cell in row]) for row in rows]
    if not body:
        body = [Tr(Td(empty, colspan=str(len(headers))))]
    return Table(Thead(Tr(*[Th(x) for x in headers])), Tbody(*body), cls="log-table")


def register_account_routes(app, rt):
    @rt("/settings", methods=["GET"])
    async def settings_get(session, msg: str = ""):
        user = session.get("user")
        if not user:
            return RedirectResponse("/signin", status_code=303)
        from utils.auth import get_provider_key_status
        status = await get_provider_key_status(user["user_id"], "xai")
        return (
            Title("Settings · PolyTrade"), Style(_CSS),
            Div(
                Div(H1("Settings"), A("Back to chat", href="/"), cls="page-head"),
                Div(
                    H2("Your xAI key"),
                    P("Configured: " + (status["hint"] if status["configured"] else "No"),
                      cls="safe" if status["configured"] else "muted"),
                    P("The key is encrypted before storage and is never written to activity or usage logs.", cls="muted"),
                    P("Your key funds DeepAgents requests. Hermes runs in its private sidecar and remains platform-funded.", cls="muted"),
                    P(msg, cls="safe") if msg else None,
                    Form(
                        Input(type="password", name="provider_key", placeholder="xai-…",
                              autocomplete="off", required=True),
                        Button("Save encrypted key", type="submit"),
                        method="post", action="/settings/provider-key", cls="key-form",
                    ),
                    Form(Button("Remove key", type="submit"), method="post",
                         action="/settings/provider-key/remove") if status["configured"] else None,
                    cls="card",
                ),
                Div(H2("Daily allowance"),
                    P("Use /usage in chat to see today's five-query allowance and estimated costs.", cls="muted"),
                    cls="card"),
                cls="account-page",
            ),
        )

    @rt("/settings/provider-key", methods=["POST"])
    async def provider_key_post(request, session):
        user = session.get("user")
        if not user:
            return RedirectResponse("/signin", status_code=303)
        form = await request.form()
        key = str(form.get("provider_key") or "").strip()
        if key:
            from utils.auth import store_provider_api_key
            await store_provider_api_key(user["user_id"], "xai", key)
        return RedirectResponse("/settings?msg=Key+saved", status_code=303)

    @rt("/settings/provider-key/remove", methods=["POST"])
    async def provider_key_remove(session):
        user = session.get("user")
        if not user:
            return RedirectResponse("/signin", status_code=303)
        from utils.auth import clear_provider_api_key
        await clear_provider_api_key(user["user_id"], "xai")
        return RedirectResponse("/settings?msg=Key+removed", status_code=303)

    @rt("/admin/logging", methods=["GET"])
    async def logging_get(session, email: str = ""):
        user = session.get("user")
        if not user:
            return RedirectResponse("/signin", status_code=303)
        from chat.activity import list_agent_logs, list_user_logs
        from chat.usage import list_usage, usage_summary
        admin = bool(user.get("is_admin"))
        uid = user["user_id"]
        user_logs = await list_user_logs(uid, is_admin=admin, email=email)
        agent_logs = await list_agent_logs(uid, is_admin=admin, email=email)
        calls = await list_usage(uid, is_admin=admin, email=email)
        totals = await usage_summary(uid, is_admin=admin, email=email)
        user_rows = [[_time(x.get("created_at")), x.get("email") or "—",
                      x.get("agent_framework") or "pending", x.get("status") or "—",
                      _short(x.get("request_text")), _short(x.get("response_text")),
                      _short(x.get("error"))] for x in user_logs]
        total_rows = [[x.get("email") or "—", x.get("agent_framework") or "—",
                       x.get("funding_source") or "—", str(x.get("calls") or 0),
                       f"{int(x.get('total_tokens') or 0):,}",
                       f"${float(x.get('estimated_cost_usd') or 0):.4f}"] for x in totals]
        call_rows = [[_time(x.get("created_at")), x.get("email") or "—",
                      x.get("agent_framework") or "—", x.get("provider") or "—",
                      x.get("model_name") or "—", x.get("funding_source") or "—",
                      f"{int(x.get('input_tokens') or 0):,}",
                      f"{int(x.get('output_tokens') or 0):,}",
                      f"${float(x.get('estimated_cost_usd') or 0):.4f}",
                      x.get("usage_quality") or "—"] for x in calls]
        agent_rows = [[_time(x.get("updated_at")), x.get("email") or "Unowned",
                       x.get("agent_framework") or "—", x.get("operation_type") or "—",
                       x.get("status") or "—", _short(x.get("job_id"), 80),
                       _short(x.get("run_id"), 80),
                       _short(json.dumps(x.get("details") or {}, default=str)),
                       _short(x.get("error"))] for x in agent_logs]
        filter_form = Form(
            Input(type="search", name="email", value=email, placeholder="Filter by email"),
            Button("Filter", type="submit"), A("Clear", href="/admin/logging"),
            method="get", action="/admin/logging", cls="filter",
        ) if admin else None
        return (
            Title("Usage & Logging · PolyTrade"), Style(_CSS),
            Div(
                Div(Div(H1("Admin / Logging" if admin else "Usage & Logging"),
                        P("All users" if admin else "Only activity owned by your account", cls="muted")),
                    Div(A("Settings", href="/settings"), " · ", A("Back to chat", href="/"),
                        filter_form), cls="page-head"),
                Div(H2("User activity"), _table(
                    ["Time","User","Agent","Status","Question","Response","Error"],
                    user_rows, "No user activity recorded."), cls="card"),
                Div(H2("LLM usage today"), _table(
                    ["User","Agent","Key source","Calls","Tokens","Est. cost"],
                    total_rows, "No LLM usage today."), cls="card"),
                Div(H2("LLM calls"), P("Measured or estimated tokens; keys are never logged.", cls="muted"),
                    _table(["Time","User","Agent","Provider","Model","Key source","Input","Output","Cost","Quality"],
                           call_rows, "No LLM calls recorded."), cls="card"),
                Div(H2("Agent activity"), _table(
                    ["Updated","User","Agent","Operation","Status","Job","Run","Details","Error"],
                    agent_rows, "No agent activity recorded."), cls="card"),
                cls="account-page",
            ),
        )
