"""Script-free authenticated workspace views and form actions."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

from fasthtml.common import (
    H1,
    H2,
    A,
    Aside,
    Button,
    Code,
    Details,
    Div,
    Form,
    Header,
    Input,
    Label,
    Main,
    Nav,
    Option,
    P,
    Section,
    Select,
    Small,
    Span,
    Strong,
    Summary,
    Table,
    Tbody,
    Td,
    Textarea,
    Th,
    Thead,
    Tr,
)

from .formatting import date_time, money, price, tone
from .icons import icon

STARTER_QUESTIONS = (
    "Backtest mean reversion on a resolved election market",
    "Find a liquid active market for a paper-trading setup",
    "Research the leading Fed market and summarize its order book",
)


def compact(value: str | None, size: int = 7) -> str:
    if not value:
        return "—"
    return value if len(value) <= size * 2 + 1 else f"{value[:size]}…{value[-size:]}"


def relative_time(value: str | None) -> str:
    if not value:
        return "—"
    try:
        then = datetime.fromisoformat(value.replace("Z", "+00:00"))
        seconds = max(0, int((datetime.now(UTC) - then).total_seconds()))
    except ValueError:
        return value
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86400}d ago"


def _nav_link(path: str, label: str, icon_name: str, active: str):
    cls = "nav-active" if active == path or active.startswith(f"{path}/") else ""
    return A(icon(icon_name), label, href=path, cls=cls)


def shell(active: str, content: Any, *, profile: str = "Signed in"):
    links = (
        _nav_link("/chat", "Chat", "message", active),
        _nav_link("/trades", "Trades", "briefcase", active),
        _nav_link("/paper", "Paper", "activity", active),
        _nav_link("/backtests", "Backtests", "backtest", active),
    )
    return Div(
        Header(
            A(
                Span(icon("circle-dot"), cls="brand-glyph", aria_hidden="true"),
                Span("PolyTrade"),
                href="/chat",
                cls="app-brand",
                aria_label="PolyTrade chat",
            ),
            Nav(*links, cls="app-nav", aria_label="Primary navigation"),
            Div(
                A(
                    icon("settings"),
                    Span("Settings"),
                    href="/settings",
                    cls=f"header-settings {'nav-active' if active == '/settings' else ''}",
                ),
                Div(profile, cls="profile-control", aria_label="Profile"),
                Details(
                    Summary(icon("menu"), cls="mobile-menu-button", aria_label="Open navigation"),
                    Nav(*links, cls="app-nav", aria_label="Mobile navigation"),
                    cls="mobile-nav-details",
                ),
                cls="header-actions",
            ),
            cls="app-header",
        ),
        content,
        cls="app-shell",
    )


def page_title(eyebrow: str, title: str, description: str | None = None, *actions: Any):
    return Div(
        Div(Span(eyebrow, cls="eyebrow"), H1(title), P(description) if description else None),
        Div(*actions, cls="page-title-actions") if actions else None,
        cls="page-title",
    )


def auth_required():
    return shell(
        "",
        Main(
            Section(
                icon("shield"),
                H1("Sign in required"),
                P("Open PolyTrade through the authenticated application to continue."),
                A("View strategy templates", href="/templates", cls="button button-primary"),
                cls="empty-page-card",
            ),
            cls="detail-page",
        ),
        profile="Guest",
    )


def error_card(title: str, detail: str):
    return Section(icon("alert"), H2(title), P(detail), cls="empty-page-card")


def history_pane(threads: list[dict[str, Any]], active_id: str | None = None):
    rows = []
    for thread in threads:
        thread_id = str(thread.get("threadId", ""))
        rows.append(
            Div(
                A(
                    Span(thread.get("title") or "Untitled conversation"),
                    Small(relative_time(thread.get("updatedAt"))),
                    href=f"/chat/{quote(thread_id)}",
                ),
                Form(
                    Button(icon("trash"), type="submit", aria_label="Delete conversation"),
                    method="post",
                    action=f"/chat/{quote(thread_id)}/delete",
                ),
                cls=f"thread-row {'thread-active' if thread_id == active_id else ''}",
            )
        )
    return Aside(
        Div(Div(Span("Workspace", cls="eyebrow"), H2("Chat history")), cls="pane-heading"),
        A(icon("plus"), "New chat", href="/chat/new", cls="new-chat-button"),
        Div(*(rows or [P("No saved conversations yet.", cls="pane-empty")]), cls="thread-list"),
        A(icon("refresh"), "Refresh history", href="/chat", cls="history-refresh"),
        cls="history-pane",
        aria_label="Chat history",
    )


def activity_pane(usage: dict[str, Any] | None = None):
    return Aside(
        Div(Div(Span("Live context", cls="eyebrow"), H2("Activity")), cls="pane-heading"),
        Div(
            Div(
                Span(cls="tape-node", aria_hidden="true"),
                Section(
                    Div(icon("shield"), Span("Server session"), Strong("Active")),
                    P(
                        "Actions are submitted as signed server forms; "
                        "this page runs no JavaScript."
                    ),
                    cls="activity-card status-card",
                ),
                cls="activity-item",
            ),
            Div(
                Span(cls="tape-node", aria_hidden="true"),
                Section(
                    Div(Span("Daily agent usage"), cls="activity-card-heading"),
                    P(
                        f"{usage.get('used', 0)} / {usage.get('limit', '—')} queries used"
                        if usage
                        else "Usage is unavailable."
                    ),
                    cls="activity-card",
                ),
                cls="activity-item",
            ),
            cls="activity-tape",
        ),
        cls="activity-pane",
        aria_label="Context and activity",
    )


def chat_page(
    threads: list[dict[str, Any]],
    items: list[dict[str, Any]],
    usage: dict[str, Any] | None,
    *,
    thread_id: str | None = None,
    prompt: str = "",
    error: str | None = None,
):
    title = "New chat"
    if thread_id:
        title = next(
            (str(item.get("title")) for item in threads if item.get("threadId") == thread_id),
            "Chat",
        )
    messages = [item for item in items if item.get("kind") == "message"]
    if messages:
        body = Div(
            *(
                Section(
                    Span("You" if item.get("role") == "user" else "PolyTrade", cls="message-label"),
                    P(str(item.get("text", ""))),
                    cls=f"chat-message chat-message-{item.get('role', 'assistant')}",
                )
                for item in messages
                if str(item.get("text", "")).strip()
            ),
            cls="message-list",
        )
    else:
        body = Div(
            Span(icon("zap"), cls="empty-orbit", aria_hidden="true"),
            H2("Research a market or prepare an action."),
            P(
                "Ask about live Polymarket data, your account, or historical strategy backtests. "
                "Orders remain drafts until reviewed and signed."
            ),
            Div(
                *(
                    A(
                        icon("arrow-right"),
                        Span(question),
                        href=f"/chat/new?prompt={quote(question)}",
                    )
                    for question in STARTER_QUESTIONS
                ),
                cls="starter-list",
                aria_label="Starter prompts",
            ),
            cls="chat-empty",
        )
    action = f"/chat/{quote(thread_id)}" if thread_id else "/chat/new"
    workspace = Main(
        history_pane(threads, thread_id),
        Section(
            Div(
                Div(Span("Research workspace", cls="eyebrow"), H1(title)),
                cls="conversation-toolbar",
            ),
            Div(
                error_card("Conversation unavailable", error) if error else body,
                cls="message-scroll",
            ),
            Form(
                Label("Ask PolyTrade", fr="chat-question", cls="sr-only"),
                Textarea(
                    prompt,
                    id="chat-question",
                    name="message",
                    rows="2",
                    maxlength="2000",
                    placeholder="Ask about a market, account, order, or strategy backtest…",
                    required=True,
                ),
                Div(
                    Div(
                        Label(
                            Span("Runtime", cls="sr-only"),
                            Select(
                                Option("DeepSeek", value="deepseek"),
                                Option("Hermes", value="hermes"),
                                name="runtime",
                                aria_label="Agent runtime",
                            ),
                            cls="runtime-picker",
                        ),
                        Span(
                            f"{usage.get('used', 0)} / {usage.get('limit', '—')} queries used today"
                            if usage
                            else "Usage unavailable",
                            cls="usage-line",
                        ),
                        cls="composer-meta",
                    ),
                    Button(icon("send"), Span("Send"), type="submit", cls="send-button"),
                    cls="composer-footer",
                ),
                method="post",
                action=action,
                cls="composer",
            ),
            cls="conversation-pane",
            aria_labelledby="conversation-heading",
        ),
        activity_pane(usage),
        cls="chat-workspace",
    )
    return shell("/chat", workspace)


def _status_pill(value: str):
    return Span(value or "—", cls="status-pill")


def _empty_row(label: str, columns: int):
    return Tr(Td(label, colspan=str(columns), cls="table-empty"))


def trades_page(session: dict[str, Any] | None, account: dict[str, Any] | None):
    title = page_title(
        "Account execution",
        "Trades",
        "Review positions, live orders, and fills without mixing wallet "
        "verification into this page.",
        A(icon("refresh"), "Refresh account", href="/trades", cls="button button-quiet"),
    )
    if not session:
        content = error_card(
            "No wallet session",
            "Wallet verification lives in Settings. Connect a session before loading account data.",
        )
        return shell("/trades", Main(title, content, cls="detail-page trades-page"))
    account = account or {}
    positions = account.get("positions") or []
    orders = account.get("openOrders") or []
    fills = account.get("fills") or []
    summary = Section(
        _summary("Wallet", compact(session.get("walletAddress"))),
        _summary("Positions", len(positions)),
        _summary("Open orders", len(orders)),
        _summary("Fill history", len(fills)),
        _summary("Observed", relative_time(account.get("observedAt"))),
        cls="summary-strip",
        aria_label="Account summary",
    )
    positions_table = _data_section(
        "Positions",
        len(positions),
        Table(
            Thead(
                Tr(
                    Th("Market / outcome"),
                    Th("Size", cls="num"),
                    Th("Average", cls="num"),
                    Th("Current", cls="num"),
                    Th("Value", cls="num"),
                    Th("P&L", cls="num"),
                    Th("Status"),
                )
            ),
            Tbody(
                *(_position_row(item) for item in positions)
                if positions
                else _empty_row("No positions.", 7)
            ),
        ),
    )
    orders_table = _data_section(
        "Open orders",
        len(orders),
        Table(
            Thead(
                Tr(
                    Th("Market / outcome"),
                    Th("Side"),
                    Th("Remaining", cls="num"),
                    Th("Price", cls="num"),
                    Th("Type"),
                    Th("Created"),
                    Th("Action"),
                )
            ),
            Tbody(
                *(_order_row(item) for item in orders)
                if orders
                else _empty_row("No open orders.", 7)
            ),
        ),
    )
    fills_table = _data_section(
        "Fill history",
        len(fills),
        Table(
            Thead(
                Tr(
                    Th("Trade"),
                    Th("Market / outcome"),
                    Th("Side"),
                    Th("Size", cls="num"),
                    Th("Price", cls="num"),
                    Th("Matched"),
                    Th("Role"),
                    Th("Transaction"),
                )
            ),
            Tbody(
                *(_fill_row(item) for item in fills)
                if fills
                else _empty_row("No fills recorded.", 8)
            ),
        ),
    )
    return shell(
        "/trades",
        Main(
            title,
            summary,
            Div(
                Div(positions_table, orders_table, fills_table, cls="trade-data-column"),
                cls="trades-grid",
            ),
            cls="detail-page trades-page",
        ),
    )


def _summary(label: str, value: Any):
    return Div(Span(label), Strong(str(value)), cls="summary-stat")


def _data_section(title: str, count: int, table: Any):
    return Section(
        Div(H2(title), Span(str(count), cls="count-badge"), cls="data-section-heading"),
        Div(table, cls="table-scroll"),
        cls="data-section",
    )


def _position_row(item: dict[str, Any]):
    pnl = item.get("cashPnl")
    return Tr(
        Th(
            item.get("marketTitle") or compact(item.get("conditionId")),
            Small(item.get("outcome") or "—"),
        ),
        Td(item.get("size") or "—", cls="num"),
        Td(price(item.get("averagePrice")), cls="num"),
        Td(price(item.get("currentPrice")), cls="num"),
        Td(money(item.get("currentValue")), cls="num"),
        Td(money(pnl), Small(item.get("percentPnl") or "—"), cls=f"num {tone(pnl)}"),
        Td(_status_pill("Redeemable" if item.get("redeemable") else "Open")),
    )


def _order_row(item: dict[str, Any]):
    order_id = str(item.get("orderId", ""))
    return Tr(
        Th(compact(item.get("marketId") or order_id), Small(item.get("outcome") or "—")),
        Td(_status_pill(item.get("side") or "—")),
        Td(
            item.get("remainingSize") or "—",
            Small(f"{item.get('matchedSize') or '—'} matched"),
            cls="num",
        ),
        Td(price(item.get("price")), cls="num"),
        Td(item.get("orderType") or "—"),
        Td(date_time(item["createdAt"]) if item.get("createdAt") else "—"),
        Td(
            Form(
                Input(type="hidden", name="order_id", value=order_id),
                Button("Cancel", type="submit", cls="table-action"),
                method="post",
                action="/trades/cancel",
            )
        ),
    )


def _fill_row(item: dict[str, Any]):
    return Tr(
        Th(compact(item.get("tradeId"))),
        Td(compact(item.get("marketId")), Small(item.get("outcome") or "—")),
        Td(_status_pill(item.get("side") or "—")),
        Td(item.get("size") or "—", cls="num"),
        Td(price(item.get("price")), cls="num"),
        Td(date_time(item["matchedAt"]) if item.get("matchedAt") else "—"),
        Td(item.get("traderSide") or "—"),
        Td(Code(compact(item.get("transactionHash")))),
    )


def settings_page(session: dict[str, Any] | None, channels: list[dict[str, Any]]):
    if session:
        wallet = Div(
            _status("Wallet", session.get("walletAddress") or "—", mono=True),
            _status("Funder / maker", session.get("funderAddress") or "—", mono=True),
            _status("Structure", str(session.get("signatureType", "—"))),
            _status("Idle expiry", date_time(session["idleExpiresAt"]), mono=True),
            _status("Absolute expiry", date_time(session["expiresAt"]), mono=True),
            Form(
                Button("Disconnect session", type="submit", cls="button button-danger"),
                method="post",
                action="/settings/wallet/disconnect",
                cls="settings-actions",
            ),
            cls="session-details",
        )
        wallet_title = "Verified session"
    else:
        wallet = Div(
            P(
                "A wallet is not connected. The script-free signing flow "
                "is being migrated separately."
            ),
            P(
                "Public research, paper trading, backtests, and chat remain "
                "available without browser wallet code.",
                cls="settings-hint",
            ),
        )
        wallet_title = "Connect a wallet"
    channel_rows = [
        Div(
            Div(
                Strong(item.get("label") or item.get("kind", "Alert")),
                Small(item.get("destinationHint") or ""),
            ),
            _status_pill("Enabled" if item.get("enabled", True) else "Disabled"),
            cls="alert-channel-row",
        )
        for item in channels
    ]
    return shell(
        "/settings",
        Main(
            page_title("Security and access", "Settings"),
            Div(
                Div(
                    Section(
                        Div(
                            icon("shield"),
                            Div(Span("Connection status", cls="eyebrow"), H2("Eligibility")),
                            cls="settings-card-heading",
                        ),
                        _status("Application mode", "Server-rendered / script-free", good=True),
                        _status("Identity", "Authenticated gateway session", good=True),
                        cls="settings-card",
                    ),
                    Section(
                        Div(
                            icon("wallet"),
                            Div(Span("Wallet authority", cls="eyebrow"), H2(wallet_title)),
                            cls="settings-card-heading",
                        ),
                        wallet,
                        cls="settings-card",
                    ),
                    cls="settings-col",
                ),
                Section(
                    Div(
                        icon("activity"),
                        Div(Span("Notifications", cls="eyebrow"), H2("Strategy alerts")),
                        cls="settings-card-heading",
                    ),
                    Div(
                        *(channel_rows or [P("No alert channels configured.", cls="pane-empty")]),
                        cls="alert-channel-list",
                    ),
                    cls="settings-card alerts-settings-card",
                ),
                cls="settings-grid",
            ),
            cls="detail-page settings-page",
        ),
    )


def _status(label: str, value: str, *, mono: bool = False, good: bool = False):
    return Div(
        Span(label),
        Strong(value, cls="mono" if mono else ""),
        cls=f"status-line {'status-good' if good else ''}",
    )
