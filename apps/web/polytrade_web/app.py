"""FastHTML application factory and public routes."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import quote
from uuid import uuid4

import httpx
from fasthtml.common import FastHTML, Link, Meta, RedirectResponse, Title
from polytrade_contracts import PublicTrackRecord
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from .config import WebSettings
from .gateway_client import GatewayClient, GatewayResponseError, access_token
from .templates_page import templates_page
from .track_record import track_record_page, track_record_unavailable
from .workspace import auth_required, chat_page, settings_page, trades_page

ROOT = Path(__file__).resolve().parents[3]
STYLES = ROOT / "apps" / "web" / "src"


def create_app(
    settings: WebSettings | None = None,
    client: httpx.AsyncClient | None = None,
) -> FastHTML:
    config = settings or WebSettings()
    http = client or httpx.AsyncClient(timeout=10, follow_redirects=False)
    gateway = GatewayClient(config.API_URL, http)
    app = FastHTML(
        hdrs=(
            Meta(charset="utf-8"),
            Meta(name="viewport", content="width=device-width, initial-scale=1"),
            Link(rel="stylesheet", href="/assets/styles.css"),
        ),
        default_hdrs=False,
        htmx=False,
        surreal=False,
    )

    @app.get("/assets/styles.css")
    async def styles():
        css = (STYLES / "styles.css").read_text(encoding="utf-8")
        css = css.replace(
            '@import "@fontsource-variable/manrope";\n'
            '@import "@fontsource/ibm-plex-mono/400.css";\n'
            '@import "@fontsource/ibm-plex-mono/500.css";',
            '@import url("https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=Manrope:wght@200..800&display=swap");',
        )
        css = css.replace('"Manrope Variable"', '"Manrope"')
        return Response(css, media_type="text/css")

    @app.get("/health")
    async def health():
        return JSONResponse({"status": "ok", "version": "4.0.0"})

    @app.get("/")
    async def index():
        return RedirectResponse("/templates", status_code=302)

    @app.get("/templates")
    async def templates():
        return Title("Strategy templates · PolyTrade"), templates_page()

    @app.get("/u/{token}")
    async def track_record(token: str, request: Request):
        if not 32 <= len(token) <= 64 or not all(char.isalnum() or char in "_-" for char in token):
            return (
                Title("Track record unavailable · PolyTrade"),
                Meta(name="robots", content="noindex"),
                track_record_unavailable(
                    "This track record is not available",
                    "The link may have been rotated or turned off by its owner. "
                    "Ask for a fresh link to view these paper results.",
                ),
            )
        try:
            response = await http.get(
                f"{config.API_URL.rstrip('/')}/v1/public/track-records/{token}"
            )
            if response.status_code == 404:
                return (
                    Title("Track record unavailable · PolyTrade"),
                    Meta(name="robots", content="noindex"),
                    track_record_unavailable(
                        "This track record is not available",
                        "The link may have been rotated or turned off by its owner. "
                        "Ask for a fresh link to view these paper results.",
                    ),
                )
            response.raise_for_status()
            record = PublicTrackRecord.model_validate(response.json())
        except (httpx.HTTPError, ValueError):
            return (
                Title("Track record unavailable · PolyTrade"),
                Meta(name="robots", content="noindex"),
                track_record_unavailable(
                    "Track record could not be loaded",
                    "The gateway did not answer this request. It may be a temporary outage — "
                    "try again in a moment.",
                ),
            )
        origin = str(request.base_url).rstrip("/")
        return (
            Title("Paper track record · PolyTrade"),
            Meta(name="robots", content="noindex"),
            track_record_page(record, f"{origin}/u/{token}"),
        )

    async def workspace_context(
        request: Request,
        thread_id: str | None = None,
    ) -> tuple[
        str | None, list[dict[str, Any]], list[dict[str, Any]], dict[str, Any] | None, str | None
    ]:
        token = access_token(request)
        if not token:
            return None, [], [], None, None
        try:
            thread_payload = await gateway.get(
                "/v1/agent/threads", token, params={"limit": 50, "offset": 0}
            )
            threads = list(thread_payload.get("items", []))
            usage = await gateway.get("/v1/agent/usage", token)
            items: list[dict[str, Any]] = []
            if thread_id:
                payload = await gateway.get(
                    f"/v1/agent/threads/{quote(thread_id, safe='')}/messages", token
                )
                items = list(payload.get("items", []))
            return token, threads, items, usage, None
        except GatewayResponseError as exc:
            return token, [], [], None, str(exc)

    @app.get("/chat")
    async def chat_index(request: Request):
        token, threads, _items, _usage, error = await workspace_context(request)
        if not token:
            return Title("Sign in · PolyTrade"), auth_required()
        if not error and threads:
            return RedirectResponse(f"/chat/{threads[0]['threadId']}", status_code=303)
        return Title("New chat · PolyTrade"), chat_page([], [], None, error=error)

    @app.get("/chat/new")
    async def new_chat(request: Request, prompt: str = ""):
        token, threads, items, usage, error = await workspace_context(request)
        if not token:
            return Title("Sign in · PolyTrade"), auth_required()
        return Title("New chat · PolyTrade"), chat_page(
            threads, items, usage, prompt=prompt[:2000], error=error
        )

    @app.get("/chat/{thread_id}")
    async def chat_thread(thread_id: str, request: Request):
        token, threads, items, usage, error = await workspace_context(request, thread_id)
        if not token:
            return Title("Sign in · PolyTrade"), auth_required()
        return Title("Chat · PolyTrade"), chat_page(
            threads, items, usage, thread_id=thread_id, error=error
        )

    async def submit_chat(request: Request, thread_id: str | None = None):
        token = access_token(request)
        if not token:
            return RedirectResponse("/chat", status_code=303)
        form = await request.form()
        message = str(form.get("message", "")).strip()[:2000]
        runtime = str(form.get("runtime", "deepseek"))
        if not message:
            target = f"/chat/{thread_id}" if thread_id else "/chat/new"
            return RedirectResponse(target, status_code=303)
        try:
            if not thread_id:
                created = await gateway.post("/v1/agent/threads", token)
                thread_id = str(created["threadId"])
            body: dict[str, str] = {"message": message}
            if runtime in {"deepseek", "hermes"}:
                body["runtime"] = runtime
            await gateway.post(
                f"/v1/agent/threads/{quote(thread_id, safe='')}/runs/stream",
                token,
                json=body,
                accept="text/event-stream",
            )
            return RedirectResponse(f"/chat/{thread_id}", status_code=303)
        except GatewayResponseError as exc:
            token, threads, items, usage, _error = await workspace_context(request, thread_id)
            return Title("Chat · PolyTrade"), chat_page(
                threads,
                items,
                usage,
                thread_id=thread_id,
                prompt=message,
                error=str(exc),
            )

    @app.post("/chat/new")
    async def create_chat(request: Request):
        return await submit_chat(request)

    @app.post("/chat/{thread_id}")
    async def continue_chat(thread_id: str, request: Request):
        return await submit_chat(request, thread_id)

    @app.post("/chat/{thread_id}/delete")
    async def delete_chat(thread_id: str, request: Request):
        token = access_token(request)
        if token:
            try:
                await gateway.delete(f"/v1/agent/threads/{quote(thread_id, safe='')}", token)
            except GatewayResponseError:
                pass
        return RedirectResponse("/chat", status_code=303)

    @app.get("/trades")
    async def trades(request: Request):
        token = access_token(request)
        if not token:
            return Title("Sign in · PolyTrade"), auth_required()
        session = None
        account = None
        try:
            session = await gateway.get("/v1/wallet-sessions/current", token)
            account = await gateway.get("/v1/account/overview", token)
        except GatewayResponseError as exc:
            if exc.status not in {401, 404}:
                account = None
        return Title("Trades · PolyTrade"), trades_page(session, account)

    @app.post("/trades/cancel")
    async def cancel_trade(request: Request):
        token = access_token(request)
        if not token:
            return RedirectResponse("/trades", status_code=303)
        form = await request.form()
        order_id = str(form.get("order_id", ""))
        try:
            session = await gateway.get("/v1/wallet-sessions/current", token)
            await gateway.post(
                "/v1/cancellations",
                token,
                json={
                    "sessionId": session["sessionId"],
                    "selector": {"kind": "order", "orderId": order_id},
                    "confirmed": True,
                },
                idempotency_key=str(uuid4()),
            )
        except GatewayResponseError:
            pass
        return RedirectResponse("/trades", status_code=303)

    @app.get("/settings")
    async def settings(request: Request):
        token = access_token(request)
        if not token:
            return Title("Sign in · PolyTrade"), auth_required()
        session = None
        channels: list[dict[str, Any]] = []
        try:
            session = await gateway.get("/v1/wallet-sessions/current", token)
        except GatewayResponseError:
            pass
        try:
            payload = await gateway.get("/v1/alerts/channels", token)
            channels = list(payload.get("items", []))
        except GatewayResponseError:
            pass
        return Title("Settings · PolyTrade"), settings_page(session, channels)

    @app.post("/settings/wallet/disconnect")
    async def disconnect_wallet(request: Request):
        token = access_token(request)
        if token:
            try:
                session = await gateway.get("/v1/wallet-sessions/current", token)
                await gateway.delete(f"/v1/wallet-sessions/{session['sessionId']}", token)
            except GatewayResponseError:
                pass
        return RedirectResponse("/settings", status_code=303)

    return app


app = create_app()
