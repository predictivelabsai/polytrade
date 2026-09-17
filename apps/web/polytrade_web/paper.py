"""FastHTML paper-trading dashboard and native form controls."""

from __future__ import annotations

import base64
import json
from typing import Any
from urllib.parse import quote as url_quote

from fasthtml.common import (
    H2,
    H3,
    A,
    Article,
    Button,
    Code,
    Dd,
    Div,
    Dl,
    Dt,
    Footer,
    Form,
    Header,
    Input,
    Label,
    Main,
    Option,
    P,
    Section,
    Select,
    Small,
    Span,
    Strong,
    Table,
    Tbody,
    Td,
    Th,
    Thead,
    Tr,
)
from polytrade_contracts import STRATEGY_TEMPLATES, strategy_template_by_id

from .formatting import date_time, money, price, signed_money, tone
from .icons import icon
from .workspace import page_title, shell

FILL_PAGE_SIZE = 20


def encode_market(market: dict[str, Any]) -> str:
    raw = json.dumps(market, separators=(",", ":"), ensure_ascii=True).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_market(value: str | None) -> dict[str, Any] | None:
    if not value:
        return None
    try:
        padded = value + "=" * (-len(value) % 4)
        parsed = json.loads(base64.urlsafe_b64decode(padded).decode())
        return parsed if isinstance(parsed, dict) else None
    except (ValueError, UnicodeError):
        return None


def market_search_results(
    payload: dict[str, Any] | None,
    *,
    include_closed: bool = False,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for event in (payload or {}).get("events", []):
        for market in event.get("markets", []):
            condition = str(market.get("conditionId", ""))
            if (
                condition
                and condition not in seen
                and (include_closed or market.get("active"))
                and (include_closed or not market.get("closed"))
                and (include_closed or market.get("acceptingOrders"))
                and (include_closed or market.get("enableOrderBook"))
            ):
                seen.add(condition)
                results.append(market)
    return results


def paper_page(
    portfolio: dict[str, Any] | None,
    fills: dict[str, Any] | None,
    strategy: dict[str, Any] | None,
    results: list[dict[str, Any]],
    *,
    query: str = "",
    selected: dict[str, Any] | None = None,
    selected_token: str = "",
    side: str = "BUY",
    shares: str = "",
    quote: dict[str, Any] | None = None,
    template_id: str | None = None,
    error: str | None = None,
    notice: str | None = None,
):
    if selected:
        selected_token = selected_token or str((selected.get("clobTokenIds") or [""])[0])
    template = strategy_template_by_id(template_id) if template_id else None
    position_items = (portfolio or {}).get("positions", [])
    fill_items = (fills or {}).get("items", [])
    running = bool((strategy or {}).get("strategy") or {}) and (
        (strategy or {}).get("strategy", {}).get("status") == "RUNNING"
    )
    ledger = Section(
        Div(
            Span("Equity"),
            Strong(money((portfolio or {}).get("equity"))),
            Small(
                f"Observed {date_time((portfolio or {}).get('observedAt'))}"
                if (portfolio or {}).get("observedAt")
                else "Observed —"
            ),
            cls="paper-equity",
        ),
        Dl(
            Div(Dt("Cash"), Dd(money((portfolio or {}).get("cash")))),
            Div(Dt("Positions value"), Dd(money((portfolio or {}).get("positionsValue")))),
            Div(Dt("Realized P&L"), Dd(signed_money((portfolio or {}).get("realizedPnl")))),
            Div(Dt("Unrealized P&L"), Dd(signed_money((portfolio or {}).get("unrealizedPnl")))),
        ),
        Div(
            Span("Total P&L", cls="eyebrow"),
            Strong(
                signed_money((portfolio or {}).get("totalPnl")),
                cls=tone((portfolio or {}).get("totalPnl")),
            ),
            cls="paper-pnl-stamp",
        ),
        cls="paper-ledger",
        aria_label="Paper account ledger",
    )
    search_action = "/paper"
    selected_value = encode_market(selected) if selected else ""
    result_cards = []
    for market in results:
        market_value = encode_market(market)
        prices = market.get("outcomePrices") or []
        outcomes = market.get("outcomes") or []
        result_cards.append(
            A(
                Div(
                    Strong(market.get("question") or "Untitled market"),
                    Small(
                        " · ".join(
                            f"{outcome} {price(prices[index])}"
                            for index, outcome in enumerate(outcomes)
                            if index < len(prices)
                        )
                    ),
                ),
                icon("arrow-right"),
                href=f"/paper?query={url_quote(query)}&market={url_quote(market_value)}"
                + (f"&template={url_quote(template_id)}" if template_id else ""),
                cls="paper-market-result",
            )
        )
    market_panel = Section(
        Header(
            Div(Span("Find a contract", cls="eyebrow paper-eyebrow"), H2("Active markets")),
            icon("activity"),
        ),
        Form(
            Label("Search active Polymarket markets", fr="paper-market-query", cls="sr-only"),
            Input(
                id="paper-market-query",
                name="query",
                value=query,
                placeholder="Search elections, rates, crypto…",
                required=True,
            ),
            Input(type="hidden", name="template", value=template_id or ""),
            Button("Search", type="submit"),
            method="get",
            action=search_action,
            cls="paper-market-search",
        ),
        Div(
            *(
                result_cards
                or [
                    P(
                        "Search for a market, then choose an outcome in the paper ticket.",
                        cls="paper-empty",
                    )
                ]
            ),
            cls="paper-market-results",
        ),
        cls="paper-market-panel",
    )
    position_table = Section(
        Header(Span("Portfolio", cls="eyebrow paper-eyebrow"), H2("Paper positions")),
        Table(
            Thead(
                Tr(
                    Th("Market / outcome"),
                    Th("Shares", cls="num"),
                    Th("Average", cls="num"),
                    Th("Value", cls="num"),
                    Th("P&L", cls="num"),
                )
            ),
            Tbody(
                *(
                    Tr(
                        Th(item.get("marketQuestion") or "—", Small(item.get("outcome") or "—")),
                        Td(item.get("shares") or "—", cls="num"),
                        Td(price(item.get("averageCost")), cls="num"),
                        Td(money(item.get("liquidationValue")), cls="num"),
                        Td(
                            signed_money(item.get("unrealizedPnl")),
                            cls=f"num {tone(item.get('unrealizedPnl'))}",
                        ),
                    )
                    for item in position_items
                )
                if position_items
                else Tr(Td("No paper positions yet.", colspan="5", cls="table-empty")),
            ),
        ),
        cls="paper-holdings-panel data-section",
    )
    fill_table = Section(
        Header(Span("Execution tape", cls="eyebrow paper-eyebrow"), H2("Recent fills")),
        Table(
            Thead(
                Tr(
                    Th("Market / outcome"),
                    Th("Side"),
                    Th("Shares", cls="num"),
                    Th("Price", cls="num"),
                    Th("Created"),
                )
            ),
            Tbody(
                *(
                    Tr(
                        Th(item.get("marketQuestion") or "—", Small(item.get("outcome") or "—")),
                        Td(Span(item.get("kind") or "—", cls="status-pill")),
                        Td(item.get("shares") or "—", cls="num"),
                        Td(price(item.get("averagePrice")), cls="num"),
                        Td(date_time(item["createdAt"]) if item.get("createdAt") else "—"),
                    )
                    for item in fill_items
                )
                if fill_items
                else Tr(Td("No simulated fills yet.", colspan="5", cls="table-empty")),
            ),
        ),
        P(
            f"Showing {len(fill_items)} of {(fills or {}).get('total', len(fill_items))} fills",
            cls="paper-table-footnote",
        ),
        cls="paper-fills-panel data-section",
    )
    outcome_options = []
    if selected:
        for index, token_id in enumerate(selected.get("clobTokenIds") or []):
            outcome_options.append(
                Option(
                    (selected.get("outcomes") or [])[index]
                    if index < len(selected.get("outcomes") or [])
                    else token_id,
                    value=token_id,
                    selected=token_id == selected_token,
                )
            )
    selected_data = selected or {}
    ticket_body = (
        Div(
            Div(
                Strong(selected_data.get("question") or ""),
                Code(compact(selected_data.get("conditionId"))),
                cls="paper-selected-market",
            ),
            Form(
                Input(type="hidden", name="market", value=selected_value),
                Input(type="hidden", name="template", value=template_id or ""),
                Label(
                    "Side",
                    Select(
                        Option("BUY", value="BUY", selected=side == "BUY"),
                        Option("SELL", value="SELL", selected=side == "SELL"),
                        name="side",
                    ),
                ),
                Label("Outcome", Select(*outcome_options, name="token_id", required=True)),
                Label(
                    "Shares",
                    Input(
                        name="shares",
                        value=shares,
                        placeholder="0.000000",
                        inputmode="decimal",
                        required=True,
                    ),
                ),
                Button(
                    "Preview paper trade",
                    icon("arrow-right"),
                    type="submit",
                    cls="button button-primary button-wide paper-preview-button",
                ),
                method="post",
                action="/paper/quote",
                cls="paper-ticket-fields",
            ),
        )
        if not quote
        else Div(
            H3("Preview ready"),
            Div(Span("Average price", cls="eyebrow"), Strong(price(quote.get("averagePrice")))),
            Div(
                Span("Worst consumed price", cls="eyebrow"), Strong(price(quote.get("limitPrice")))
            ),
            Div(Span("Notional", cls="eyebrow"), Strong(money(quote.get("grossNotional")))),
            Form(
                Input(type="hidden", name="market", value=selected_value),
                Input(type="hidden", name="token_id", value=selected_token),
                Input(type="hidden", name="side", value=side),
                Input(type="hidden", name="shares", value=shares),
                Input(type="hidden", name="limit_price", value=quote.get("limitPrice") or ""),
                Button(
                    "Edit",
                    type="submit",
                    formaction="/paper",
                    formmethod="get",
                    cls="button button-quiet",
                ),
                Button("Confirm paper fill", type="submit", cls="button button-primary"),
                method="post",
                action="/paper/order",
                cls="paper-confirm-actions",
            ),
        )
    )
    ticket = Section(
        Header(
            Div(Span("Fill-or-kill simulation", cls="eyebrow paper-eyebrow"), H2("Paper ticket")),
            Span("Virtual", cls="paper-mode-chip"),
        ),
        ticket_body
        if selected
        else Div(
            icon("search"),
            Strong("Select a market"),
            P("Search active markets to prepare a simulated fill."),
            cls="paper-ticket-empty",
        ),
        Footer(
            "Price protection uses the preview’s worst consumed level. If the book moves "
            "beyond it, the complete order is rejected.",
        ),
        cls="paper-ticket",
    )
    strategy_panel = strategy_runner(strategy, selected, selected_token, template)
    share = Section(
        Span("Public results", cls="eyebrow paper-eyebrow"),
        H3("Share your paper track record"),
        P("Enable a read-only public link from the paper account controls."),
        A("Open Settings", href="/settings", cls="button button-quiet"),
        cls="share-card",
    )
    message = (
        Section(icon("alert"), P(error), cls="paper-warnings", role="alert") if error else None
    )
    success = P(notice, cls="paper-notice", role="status") if notice else None
    return shell(
        "/paper",
        Main(
            page_title(
                "Simulation ledger",
                "Paper trading",
                "Practice against the live public order book with virtual USDC. Every fill "
                "stays inside this sandbox.",
                A(icon("refresh"), "Refresh portfolio", href="/paper", cls="button button-quiet"),
            ),
            Section(
                icon("shield"),
                Strong("Paper only"),
                Span("No wallet, signature, real order, or withdrawable balance."),
                cls="paper-boundary",
            ),
            ledger,
            message,
            success,
            template_grid(template_id, running),
            Div(
                Div(market_panel, position_table, fill_table, cls="paper-data-column"),
                Div(strategy_panel, ticket, share, cls="paper-ticket-column"),
                cls="paper-grid",
            ),
            cls="detail-page paper-page",
        ),
    )


def template_grid(active_id: str | None, running: bool):
    cards = []
    for template in STRATEGY_TEMPLATES:
        cards.append(
            Article(
                Div(
                    H3(template.name),
                    Span("Illustrative", cls="template-card-kind"),
                    cls="template-card-heading",
                ),
                P(template.tagline, cls="template-card-tagline"),
                P(template.description, cls="template-card-description"),
                Div(
                    Div(Span("Return"), Strong(f"+{template.stats.returnPct}%")),
                    Div(Span("Win rate"), Strong(f"{template.stats.winRatePct}%")),
                    Div(Span("Trades"), Strong(str(template.stats.tradeCount))),
                    Div(Span("Max drawdown"), Strong(f"−{template.stats.maxDrawdownPct}%")),
                    cls="template-card-stats",
                ),
                P(Span("Evidence"), template.stats.basis, cls="template-card-basis"),
                A(
                    "Arm template ",
                    icon("arrow-right"),
                    href=f"/paper?template={template.id}",
                    cls="button button-primary",
                ),
                cls=f"template-card {'template-card-active' if active_id == template.id else ''}",
            )
        )
    return Section(
        Header(Span("One-click strategies", cls="eyebrow"), H2("Start from a proven template")),
        Div(*cards, cls="template-grid"),
        cls="template-grid-section",
    )


def strategy_runner(
    strategy_snapshot: dict[str, Any] | None,
    market: dict[str, Any] | None,
    token_id: str,
    template: Any,
):
    strategy = (strategy_snapshot or {}).get("strategy")
    running = strategy and strategy.get("status") == "RUNNING"
    if running:
        body = Div(
            Span("Running in the background"),
            Strong(strategy.get("marketQuestion") or "Selected market"),
            Small(
                f"{strategy.get('outcome') or 'Outcome'} · "
                f"{strategy.get('ordersPlaced', 0)} orders placed"
            ),
            Form(
                Button("Stop strategy", type="submit", cls="button button-danger"),
                method="post",
                action="/paper/strategy/stop",
            ),
            cls="paper-strategy-target",
        )
    elif market:
        body = Form(
            Input(type="hidden", name="market", value=encode_market(market)),
            Input(type="hidden", name="token_id", value=token_id),
            Label("Buy at or below", Input(name="entry_price", value="0.40", required=True)),
            Label("Sell at or above", Input(name="exit_price", value="0.60", required=True)),
            Label("Shares per order", Input(name="shares_per_order", value="10", required=True)),
            Label("Position cap", Input(name="max_position", value="50", required=True)),
            Label(
                "Cadence",
                Select(
                    Option("5 seconds", value="5"),
                    Option("15 seconds", value="15", selected=True),
                    Option("30 seconds", value="30"),
                    Option("60 seconds", value="60"),
                    name="interval_seconds",
                ),
            ),
            Button(
                "Start background strategy",
                icon("zap"),
                type="submit",
                cls="button button-primary button-wide",
            ),
            method="post",
            action="/paper/strategy/start",
            cls="paper-strategy-form",
        )
    else:
        body = Div(
            icon("activity"),
            Strong("Select an outcome to automate"),
            P("The strategy runner stays inside the paper sandbox."),
            cls="paper-ticket-empty",
        )
    return Section(
        Header(
            Div(Span("Server automation", cls="eyebrow paper-eyebrow"), H2("Price-band strategy")),
            Span("RUNNING" if running else "READY", cls="paper-strategy-chip"),
        ),
        Div(Span(f"Template · {template.name}") if template else None, cls="template-chip")
        if template
        else None,
        body,
        cls=f"paper-strategy {'paper-strategy-running' if running else ''}",
    )


def compact(value: str | None) -> str:
    if not value:
        return "—"
    return value if len(value) < 18 else f"{value[:8]}…{value[-6:]}"
