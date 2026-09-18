"""FastHTML application factory and public routes."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import quote
from uuid import uuid4

import httpx
from fasthtml.common import FastHTML, Link, Meta, RedirectResponse, Title
from polytrade_contracts import PublicTrackRecord, parse_backtest_config
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from .backtests import backtests_page, new_backtest_page
from .config import WebSettings
from .gateway_client import GatewayClient, GatewayResponseError, access_token
from .paper import decode_market, market_search_results, paper_page
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
    http = client or httpx.AsyncClient(
        timeout=0.25 if config.AUTH_BYPASS else 10,
        follow_redirects=False,
    )
    gateway = GatewayClient(config.API_URL, http)

    def request_token(request: Request) -> str | None:
        token = access_token(request)
        if token:
            return token
        if config.AUTH_BYPASS and request.url.hostname in {"localhost", "127.0.0.1"}:
            return "local-preview-token"
        return None

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
        token = request_token(request)
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
        token = request_token(request)
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
        token = request_token(request)
        if token:
            try:
                await gateway.delete(f"/v1/agent/threads/{quote(thread_id, safe='')}", token)
            except GatewayResponseError:
                pass
        return RedirectResponse("/chat", status_code=303)

    @app.get("/trades")
    async def trades(request: Request):
        token = request_token(request)
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
        token = request_token(request)
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
        token = request_token(request)
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
        token = request_token(request)
        if token:
            try:
                session = await gateway.get("/v1/wallet-sessions/current", token)
                await gateway.delete(f"/v1/wallet-sessions/{session['sessionId']}", token)
            except GatewayResponseError:
                pass
        return RedirectResponse("/settings", status_code=303)

    async def paper_context(
        token: str,
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None]:
        portfolio = fills = strategy = None
        try:
            portfolio = await gateway.get("/v1/paper/portfolio", token)
        except GatewayResponseError:
            pass
        try:
            fills = await gateway.get("/v1/paper/fills", token, params={"limit": 20, "offset": 0})
        except GatewayResponseError:
            pass
        try:
            strategy = await gateway.get("/v1/paper/strategy", token)
        except GatewayResponseError:
            pass
        return portfolio, fills, strategy

    @app.get("/paper")
    async def paper(request: Request):
        token = request_token(request)
        if not token:
            return Title("Sign in · PolyTrade"), auth_required()
        query = request.query_params.get("query", "")[:160]
        template_id = request.query_params.get("template") or None
        selected = decode_market(request.query_params.get("market"))
        selected_token = request.query_params.get("token_id", "")
        side = request.query_params.get("side", "BUY")
        shares = request.query_params.get("shares", "")
        portfolio, fills, strategy = await paper_context(token)
        results: list[dict[str, Any]] = []
        error = None
        if query:
            try:
                payload = await gateway.get(
                    "/v1/research/markets",
                    token,
                    params={"query": query, "state": "active", "limit": 20},
                )
                results = market_search_results(payload)
            except GatewayResponseError as exc:
                error = str(exc)
        if portfolio is None:
            error = error or "The paper ledger could not be loaded."
        return Title("Paper trading · PolyTrade"), paper_page(
            portfolio,
            fills,
            strategy,
            results,
            query=query,
            selected=selected,
            selected_token=selected_token,
            side=side if side in {"BUY", "SELL"} else "BUY",
            shares=shares,
            template_id=template_id,
            error=error,
        )

    async def paper_action_page(
        request: Request,
        path: str,
        *,
        method: str = "POST",
    ):
        token = request_token(request)
        if not token:
            return Title("Sign in · PolyTrade"), auth_required()
        form = await request.form()
        selected = decode_market(str(form.get("market", "")))
        selected_token = str(form.get("token_id", ""))
        side = str(form.get("side", "BUY"))
        shares = str(form.get("shares", ""))
        template_id = str(form.get("template", "")) or None
        try:
            if method == "QUOTE":
                quote_value = await gateway.post(
                    "/v1/paper/quotes",
                    token,
                    json={
                        "conditionId": (selected or {}).get("conditionId", ""),
                        "tokenId": selected_token,
                        "side": side,
                        "shares": shares,
                    },
                )
                portfolio, fills, strategy = await paper_context(token)
                return Title("Paper trade preview · PolyTrade"), paper_page(
                    portfolio,
                    fills,
                    strategy,
                    [],
                    selected=selected,
                    selected_token=selected_token,
                    side=side,
                    shares=shares,
                    quote=quote_value,
                    template_id=template_id,
                )
            await gateway.post(
                "/v1/paper/orders",
                token,
                json={
                    "conditionId": (selected or {}).get("conditionId", ""),
                    "tokenId": selected_token,
                    "side": side,
                    "shares": shares,
                    "limitPrice": str(form.get("limit_price", "")),
                },
                idempotency_key=str(uuid4()),
            )
            return RedirectResponse("/paper", status_code=303)
        except GatewayResponseError as exc:
            portfolio, fills, strategy = await paper_context(token)
            return Title("Paper trading · PolyTrade"), paper_page(
                portfolio,
                fills,
                strategy,
                [],
                selected=selected,
                selected_token=selected_token,
                side=side,
                shares=shares,
                template_id=template_id,
                error=str(exc),
            )

    @app.post("/paper/quote")
    async def paper_quote(request: Request):
        return await paper_action_page(request, "/v1/paper/quotes", method="QUOTE")

    @app.post("/paper/order")
    async def paper_order(request: Request):
        return await paper_action_page(request, "/v1/paper/orders")

    @app.post("/paper/strategy/start")
    async def paper_strategy_start(request: Request):
        token = request_token(request)
        if not token:
            return RedirectResponse("/paper", status_code=303)
        form = await request.form()
        market = decode_market(str(form.get("market", ""))) or {}
        try:
            await gateway.post(
                "/v1/paper/strategy",
                token,
                json={
                    "conditionId": market.get("conditionId", ""),
                    "tokenId": str(form.get("token_id", "")),
                    "entryPrice": str(form.get("entry_price", "")),
                    "exitPrice": str(form.get("exit_price", "")),
                    "sharesPerOrder": str(form.get("shares_per_order", "")),
                    "maxPosition": str(form.get("max_position", "")),
                    "intervalSeconds": int(str(form.get("interval_seconds", "15"))),
                },
                idempotency_key=str(uuid4()),
            )
        except (GatewayResponseError, ValueError):
            pass
        return RedirectResponse(
            f"/paper?market={quote(str(form.get('market', '')))}", status_code=303
        )

    @app.post("/paper/strategy/stop")
    async def paper_strategy_stop(request: Request):
        token = request_token(request)
        if token:
            try:
                await gateway.post("/v1/paper/strategy/stop", token)
            except GatewayResponseError:
                pass
        return RedirectResponse("/paper", status_code=303)

    async def backtest_list(token: str) -> list[dict[str, Any]]:
        payload = await gateway.get("/v1/backtests", token, params={"limit": 50})
        return list(payload.get("items", []))

    async def backtest_details(token: str, run_id: str | None):
        if not run_id:
            return None, None, [], None
        envelope = await gateway.get(f"/v1/backtests/{quote(run_id, safe='')}", token)
        series = await gateway.get(f"/v1/backtests/{quote(run_id, safe='')}/series", token)
        trades = await gateway.get(
            f"/v1/backtests/{quote(run_id, safe='')}/trades",
            token,
            params={"offset": 0, "limit": 50},
        )
        return envelope.get("run"), envelope, list(series.get("points", [])), trades

    @app.get("/backtests")
    async def backtests(request: Request):
        token = request_token(request)
        if not token:
            return Title("Sign in · PolyTrade"), auth_required()
        try:
            runs = await backtest_list(token)
            selected_id = request.query_params.get("runId") or (
                str(runs[0]["runId"]) if runs else None
            )
            selected, envelope, series, trades = await backtest_details(token, selected_id)
            return Title("Backtests · PolyTrade"), backtests_page(
                runs, selected, envelope, series, trades
            )
        except GatewayResponseError as exc:
            return Title("Backtests · PolyTrade"), backtests_page(
                [], None, None, [], None, error=str(exc)
            )

    @app.get("/backtests/{run_id}")
    async def backtest_run(run_id: str, request: Request):
        if run_id == "new":
            return await new_backtest(request)
        token = request_token(request)
        if not token:
            return Title("Sign in · PolyTrade"), auth_required()
        try:
            runs = await backtest_list(token)
            selected, envelope, series, trades = await backtest_details(token, run_id)
            return Title("Backtest replay · PolyTrade"), backtests_page(
                runs, selected, envelope, series, trades
            )
        except GatewayResponseError as exc:
            return Title("Backtests · PolyTrade"), backtests_page(
                [], None, None, [], None, error=str(exc)
            )

    @app.post("/backtests/{run_id}/cancel")
    async def cancel_backtest(run_id: str, request: Request):
        token = request_token(request)
        if token:
            try:
                await gateway.post(
                    f"/v1/backtests/{quote(run_id, safe='')}/cancel",
                    token,
                    idempotency_key=str(uuid4()),
                )
            except GatewayResponseError:
                pass
        return RedirectResponse(f"/backtests/{quote(run_id, safe='')}", status_code=303)

    @app.post("/backtests/{run_id}/delete")
    async def delete_backtest(run_id: str, request: Request):
        token = request_token(request)
        if token:
            try:
                await gateway.delete(f"/v1/backtests/{quote(run_id, safe='')}", token)
            except GatewayResponseError:
                pass
        return RedirectResponse("/backtests", status_code=303)

    @app.post("/backtests/duplicate")
    async def duplicate_backtest(request: Request):
        token = request_token(request)
        if token:
            form = await request.form()
            market_id = str(form.get("market_id", ""))
            # The selected run's exact configuration is re-used by the client
            # page in later iterations; this route keeps the operation explicit.
            if market_id:
                try:
                    await gateway.post(
                        "/v1/backtests",
                        token,
                        json={"marketId": market_id, "config": {"strategy": "momentum_v1"}},
                        idempotency_key=str(uuid4()),
                    )
                except GatewayResponseError:
                    pass
        return RedirectResponse("/backtests", status_code=303)

    @app.get("/backtests/new")
    async def new_backtest(request: Request):
        token = request_token(request)
        if not token:
            return Title("Sign in · PolyTrade"), auth_required()
        query = request.query_params.get("query", "")[:160]
        selected = decode_market(request.query_params.get("market"))
        strategy = request.query_params.get("strategy", "momentum_v1")
        template_id = request.query_params.get("template") or None
        results: list[dict[str, Any]] = []
        error = None
        if query:
            try:
                payload = await gateway.get(
                    "/v1/research/markets",
                    token,
                    params={"query": query, "state": "resolved", "limit": 20},
                )
                results = [
                    item
                    for item in market_search_results(payload, include_closed=True)
                    if set(str(value).upper() for value in item.get("outcomes", []))
                    == {"YES", "NO"}
                ]
            except GatewayResponseError as exc:
                error = str(exc)
        return Title("New backtest · PolyTrade"), new_backtest_page(
            results,
            query=query,
            selected=selected,
            strategy=strategy,
            template_id=template_id,
            error=error,
        )

    @app.post("/backtests/new")
    async def create_backtest(request: Request):
        token = request_token(request)
        if not token:
            return RedirectResponse("/backtests/new", status_code=303)
        form = await request.form()
        market = decode_market(str(form.get("market", ""))) or {}
        strategy = str(form.get("strategy", "momentum_v1"))
        config: dict[str, Any] = {
            "strategy": strategy,
            "initialCapital": str(form.get("initialCapital", "10000")),
            "positionSizePct": str(form.get("positionSizePct", "0.10")),
            "takeProfit": str(form.get("takeProfit", "0.10")),
            "stopLoss": str(form.get("stopLoss", "0.05")),
            "maxHoldMinutes": int(str(form.get("maxHoldMinutes", "1440"))),
            "cooldownMinutes": int(str(form.get("cooldownMinutes", "60"))),
            "slippage": str(form.get("slippage", "0.01")),
            "maxFillDelayMinutes": int(str(form.get("maxFillDelayMinutes", "5"))),
        }
        if strategy == "momentum_v1":
            config.update(
                momentumWindowMinutes=int(str(form.get("momentumWindowMinutes", "60"))),
                momentumThreshold=str(form.get("momentumThreshold", "0.05")),
            )
        elif strategy == "mean_reversion_v1":
            config.update(
                reversionWindowMinutes=int(str(form.get("reversionWindowMinutes", "60"))),
                reversionThreshold=str(form.get("reversionThreshold", "0.05")),
            )
        else:
            config.update(
                breakoutWindowMinutes=int(str(form.get("breakoutWindowMinutes", "240"))),
                breakoutThreshold=str(form.get("breakoutThreshold", "0.02")),
            )
        try:
            config = parse_backtest_config(config).model_dump(mode="json", by_alias=True)
            created = await gateway.post(
                "/v1/backtests",
                token,
                json={
                    "marketId": market.get("conditionId") or market.get("id", ""),
                    "config": config,
                },
                idempotency_key=str(uuid4()),
            )
            return RedirectResponse(f"/backtests/{created['run']['runId']}", status_code=303)
        except (GatewayResponseError, ValueError) as exc:
            return Title("New backtest · PolyTrade"), new_backtest_page(
                [], selected=market, strategy=strategy, error=str(exc)
            )

    return app


app = create_app()
