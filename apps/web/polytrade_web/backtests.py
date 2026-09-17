"""Server-rendered backtest library, replay, and launch forms."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from fasthtml.common import (
    H1,
    H2,
    H3,
    A,
    Button,
    Div,
    Fieldset,
    Form,
    Header,
    Input,
    Label,
    Legend,
    Main,
    NotStr,
    P,
    Section,
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
from polytrade_contracts import strategy_template_by_id

from .formatting import date_time, money, tone
from .icons import icon
from .paper import encode_market
from .workspace import error_card, page_title, shell


def backtests_page(
    runs: list[dict[str, Any]],
    selected: dict[str, Any] | None,
    envelope: dict[str, Any] | None,
    series: list[dict[str, Any]],
    trades: dict[str, Any] | None,
    *,
    error: str | None = None,
):
    rows = []
    for run in runs:
        run_id = str(run.get("runId", ""))
        config = run.get("config") or {}
        rows.append(
            Div(
                A(
                    Span(cls=f"run-state run-state-{run.get('status', 'queued')}"),
                    Span(
                        Strong(run.get("marketQuestion") or compact_id(run.get("marketId"))),
                        Small(
                            f"{strategy_label(config.get('strategy'))} · "
                            f"{run.get('phase', 'queued')} · {short_date(run.get('createdAt'))}"
                        ),
                    ),
                    href=f"/backtests/{quote(run_id)}",
                ),
                cls=(
                    "run-row run-row-selected"
                    if selected and selected.get("runId") == run_id
                    else "run-row"
                ),
            )
        )
    library = Section(
        Div(Div(Span("Run library", cls="eyebrow"), H2("Recent tapes")), Span(str(len(runs)))),
        Div(
            *(rows or [P("No backtests yet. Launch one from a resolved market.", cls="run-empty")]),
            cls="run-list",
        ),
        Div(icon("backtest"), "Select a replay tape", cls="compare-counter"),
        cls="run-library",
        aria_label="Recent backtests",
    )
    stage = replay_stage(selected, envelope, series, trades, error)
    return shell(
        "/backtests",
        Main(
            Header(
                Div(
                    Span("Historical market analysis", cls="eyebrow"),
                    H1("Backtest"),
                    P(
                        "Evaluate strategy performance against one-minute Polymarket history "
                        "with transparent execution assumptions and no wallet access."
                    ),
                ),
                Div(
                    A(
                        icon("refresh"),
                        "Refresh runs",
                        href="/backtests",
                        cls="button button-quiet",
                    ),
                    A(
                        "New backtest ",
                        icon("arrow-right"),
                        href="/backtests/new",
                        cls="button button-primary",
                    ),
                    cls="backtest-hero-actions",
                ),
                cls="backtest-hero",
            ),
            Div(library, stage, cls="backtest-layout"),
            cls="backtest-workspace",
        ),
    )


def replay_stage(
    run: dict[str, Any] | None,
    envelope: dict[str, Any] | None,
    series: list[dict[str, Any]],
    trades: dict[str, Any] | None,
    error: str | None,
):
    if not run:
        return Section(
            error_card("Backtests unavailable", error)
            if error
            else Div(
                icon("activity"),
                H2("Select a replay tape"),
                P("Choose a recent run to inspect its assumptions, fills, and result."),
                cls="stage-empty",
            ),
            cls="backtest-stage",
            aria_live="polite",
        )
    config = run.get("config") or {}
    header = Header(
        Div(
            Span("Replay tape", cls="eyebrow"),
            H2(run.get("marketQuestion") or compact_id(run.get("marketId"))),
        ),
        Span(
            str(run.get("status", "queued")).upper(),
            cls=f"backtest-badge backtest-badge-{run.get('status', 'queued')}",
        ),
        cls="run-header",
    )
    action_forms = Div(
        Form(
            Button("Cancel run", type="submit", cls="button button-quiet"),
            method="post",
            action=f"/backtests/{run.get('runId')}/cancel",
        )
        if run.get("status") in {"queued", "running"}
        else None,
        Form(
            Button("Delete", type="submit", cls="button button-danger"),
            method="post",
            action=f"/backtests/{run.get('runId')}/delete",
        )
        if run.get("status") not in {"queued", "running"}
        else None,
        Form(
            Input(type="hidden", name="market_id", value=run.get("marketId", "")),
            Button("Duplicate", type="submit", cls="button button-quiet"),
            method="post",
            action="/backtests/duplicate",
        ),
        cls="run-header-actions",
    )
    if run.get("status") == "completed" and envelope and envelope.get("result"):
        result = envelope["result"]
        metrics = result.get("metrics") or {}
        content = Div(
            Div(
                _metric(
                    "Return", f"{metrics.get('returnPct', '—')}%", tone(metrics.get("returnPct"))
                ),
                _metric("P&L", money(metrics.get("pnl")), tone(metrics.get("pnl"))),
                _metric("Win rate", f"{metrics.get('winRatePct', '—')}%"),
                _metric("Trades", metrics.get("tradeCount", "—")),
                _metric("Max drawdown", f"{metrics.get('maxDrawdownPct', '—')}%"),
                _metric("Exposure", f"{metrics.get('exposurePct', '—')}%"),
                cls="metric-ribbon",
            ),
            Section(H3("Equity replay"), static_chart(series), cls="replay-chart"),
            trade_ledger(trades),
            Section(
                H3("Execution assumptions"),
                Div(*(P(item) for item in result.get("assumptions", []))),
                cls="assumptions-card",
            ),
        )
    elif run.get("status") == "failed":
        content = Section(
            icon("alert"),
            H3("Replay failed"),
            P((run.get("failure") or {}).get("message") or "The backtest could not be completed."),
            cls="run-failure",
        )
    elif run.get("status") == "cancelled":
        content = Section(
            icon("alert"),
            H3("Replay cancelled"),
            P("No results were saved for this run."),
            cls="terminal-note",
        )
    else:
        content = Section(
            Span(str(run.get("progress", 0)), cls="progress-value"),
            H3("Replay in progress"),
            P(f"{run.get('phase', 'queued').title()} · {run.get('progress', 0)}% complete"),
            Div(Span(style=f"width:{run.get('progress', 0)}%"), cls="progress-track"),
            cls="run-progress",
        )
    return Section(
        header,
        action_forms,
        content,
        config_strip(config),
        cls="backtest-stage",
        aria_live="polite",
    )


def _metric(label: str, value: Any, cls: str = ""):
    return Div(Span(label), Strong(str(value), cls=cls), cls="result-metric")


def config_strip(config: dict[str, Any]):
    fields = [
        ("Strategy", strategy_label(config.get("strategy"))),
        ("Capital", money(config.get("initialCapital"))),
        ("Position size", config.get("positionSizePct", "—")),
        ("Take profit", config.get("takeProfit", "—")),
        ("Stop loss", config.get("stopLoss", "—")),
        ("Slippage", config.get("slippage", "—")),
    ]
    return Div(
        *(Div(Span(label), Strong(str(value))) for label, value in fields), cls="config-strip"
    )


def static_chart(points: list[dict[str, Any]]):
    if not points:
        return P("Equity data is not available for this run.", cls="pane-empty")
    values = [float(point.get("equity", 0)) for point in points]
    low, high = min(values), max(values)
    span = high - low or 1
    coords = " ".join(
        f"{index / max(1, len(values) - 1) * 100:.2f},{100 - (value - low) / span * 90 - 5:.2f}"
        for index, value in enumerate(values)
    )
    return NotStr(
        '<svg class="equity-chart" viewBox="0 0 100 100" role="img" '
        'aria-label="Equity curve"><polyline points="' + coords + '" />'
        "</svg>"
    )


def trade_ledger(payload: dict[str, Any] | None):
    items = (payload or {}).get("items", [])
    return Section(
        H3("Trade ledger"),
        Table(
            Thead(
                Tr(
                    Th("#"),
                    Th("Outcome"),
                    Th("Entry"),
                    Th("Exit"),
                    Th("Shares", cls="num"),
                    Th("P&L", cls="num"),
                    Th("Reason"),
                )
            ),
            Tbody(
                *(
                    Tr(
                        Td(item.get("tradeIndex", "—")),
                        Td(item.get("outcome", "—")),
                        Td(item.get("entryPrice", "—")),
                        Td(item.get("exitPrice", "—")),
                        Td(item.get("shares", "—"), cls="num"),
                        Td(item.get("pnl", "—"), cls=f"num {tone(item.get('pnl'))}"),
                        Td(str(item.get("exitReason", "—")).replace("_", " ")),
                    )
                    for item in items
                )
                if items
                else Tr(Td("No trades were recorded.", colspan="7", cls="table-empty")),
            ),
        ),
        cls="trade-ledger",
    )


def new_backtest_page(
    results: list[dict[str, Any]],
    *,
    query: str = "",
    selected: dict[str, Any] | None = None,
    strategy: str = "momentum_v1",
    template_id: str | None = None,
    error: str | None = None,
):
    template = strategy_template_by_id(template_id) if template_id else None
    selected_strategy = (
        strategy
        if strategy in {"momentum_v1", "mean_reversion_v1", "breakout_v1"}
        else "momentum_v1"
    )
    market_value = encode_market(selected) if selected else ""
    result_links = [
        A(
            Span(
                Strong(item.get("question") or "Untitled market"),
                Small(" / ".join(item.get("outcomes") or [])),
            ),
            icon("arrow-right"),
            href=f"/backtests/new?query={quote(query)}&market={quote(encode_market(item))}&strategy={quote(selected_strategy)}",
            cls="market-result",
        )
        for item in results
    ]
    common = {
        "initialCapital": "10000",
        "positionSizePct": "0.10",
        "takeProfit": "0.10",
        "stopLoss": "0.05",
        "maxHoldMinutes": "1440",
        "cooldownMinutes": "60",
        "slippage": "0.01",
        "maxFillDelayMinutes": "5",
    }
    strategy_fields = {
        "momentum_v1": (
            ("momentumWindowMinutes", "Momentum window (min)", "60"),
            ("momentumThreshold", "Momentum threshold", "0.05"),
        ),
        "mean_reversion_v1": (
            ("reversionWindowMinutes", "Trailing mean window (min)", "60"),
            ("reversionThreshold", "Discount to mean", "0.05"),
        ),
        "breakout_v1": (
            ("breakoutWindowMinutes", "Prior-high window (min)", "240"),
            ("breakoutThreshold", "Breakout buffer", "0.02"),
        ),
    }
    fields = [
        (name, label, default) for name, label, default in strategy_fields[selected_strategy]
    ] + [
        ("initialCapital", "Starting capital", common["initialCapital"]),
        ("positionSizePct", "Position size (0–1)", common["positionSizePct"]),
        ("takeProfit", "Take-profit move", common["takeProfit"]),
        ("stopLoss", "Stop-loss move", common["stopLoss"]),
        ("maxHoldMinutes", "Maximum hold (min)", common["maxHoldMinutes"]),
        ("cooldownMinutes", "Cooldown (min)", common["cooldownMinutes"]),
        ("slippage", "Slippage / side", common["slippage"]),
        ("maxFillDelayMinutes", "Maximum fill delay (min)", common["maxFillDelayMinutes"]),
    ]
    field_controls = [
        Label(label, Input(name=name, value=value, required=True)) for name, label, value in fields
    ]
    return shell(
        "/backtests",
        Main(
            page_title(
                "Strategies / New backtest",
                "Launch a backtest",
                "Choose a resolved binary market, then replay a strategy with transparent "
                "risk controls.",
                A("Back to runs", href="/backtests", cls="button button-quiet"),
            ),
            Div(
                Section(
                    Div(
                        Span("01"),
                        Div(
                            H2("Resolved market"),
                            P("Search resolved binary markets with normalized gateway data."),
                        ),
                        cls="form-section-heading",
                    ),
                    Form(
                        Input(
                            name="query",
                            value=query,
                            placeholder="Search election, Fed, sports…",
                            required=True,
                        ),
                        Button("Search", type="submit"),
                        method="get",
                        action="/backtests/new",
                        cls="market-search",
                    ),
                    Div(
                        *(result_links or [P("No eligible markets found yet.", cls="pane-empty")]),
                        cls="market-results",
                    ),
                    A(
                        Strong(selected.get("question")),
                        Small("Selected market"),
                        href=f"/backtests/new?query={quote(query)}&market={quote(market_value)}&strategy={quote(selected_strategy)}",
                        cls="market-focus",
                    )
                    if selected
                    else None,
                    cls="form-card",
                ),
                Section(
                    Div(
                        Span("02"),
                        Div(
                            H2("Strategy and configuration"),
                            P("Choose the entry signal, then tune its replay parameters."),
                        ),
                        cls="form-section-heading",
                    ),
                    P(f"Pre-filled from template · {template.name}", cls="template-chip")
                    if template
                    else None,
                    Fieldset(
                        Legend("Backtest strategy", cls="sr-only"),
                        Label(
                            Input(
                                type="radio",
                                name="strategy",
                                value="momentum_v1",
                                checked=selected_strategy == "momentum_v1",
                            ),
                            "Momentum",
                        ),
                        Label(
                            Input(
                                type="radio",
                                name="strategy",
                                value="mean_reversion_v1",
                                checked=selected_strategy == "mean_reversion_v1",
                            ),
                            "Mean reversion",
                        ),
                        Label(
                            Input(
                                type="radio",
                                name="strategy",
                                value="breakout_v1",
                                checked=selected_strategy == "breakout_v1",
                            ),
                            "Breakout",
                        ),
                        cls="strategy-selector",
                    ),
                    Form(
                        Input(type="hidden", name="market", value=market_value),
                        Div(*field_controls, cls="config-form-grid"),
                        P(error, cls="validation-summary", role="alert") if error else None,
                        Button(
                            "Launch backtest ",
                            icon("arrow-right"),
                            type="submit",
                            cls="button button-primary button-wide",
                        ),
                        method="post",
                        action="/backtests/new",
                    ),
                    cls="form-card",
                ),
                cls="new-backtest-grid",
            ),
            cls="detail-page new-backtest-page",
        ),
    )


def compact_id(value: str | None) -> str:
    if not value:
        return "Unknown market"
    return value if len(value) < 22 else f"{value[:10]}…{value[-8:]}"


def short_date(value: str | None) -> str:
    if not value:
        return "—"
    try:
        return date_time(value).split(", ")[0]
    except ValueError:
        return value


def strategy_label(value: str | None) -> str:
    return {
        "momentum_v1": "Momentum",
        "mean_reversion_v1": "Mean reversion",
        "breakout_v1": "Breakout",
    }.get(value or "", value or "Strategy")
