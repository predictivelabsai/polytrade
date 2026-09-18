"""Public, signed-out paper track record."""

from __future__ import annotations

from html import escape

from fasthtml.common import (
    H1,
    H2,
    Code,
    Dd,
    Div,
    Dl,
    Dt,
    Header,
    Input,
    Main,
    NotStr,
    P,
    Section,
    Small,
    Span,
    Table,
    Tbody,
    Td,
    Th,
    Thead,
    Tr,
)
from polytrade_contracts import PublicTrackRecord, PublicTrackRecordFill, PublicTrackRecordPosition

from .formatting import date_time, money, price, short_number, signed_money, tone
from .icons import icon

CURVE_WIDTH = 640
CURVE_HEIGHT = 200
CURVE_PAD_X = 10
CURVE_PAD_Y = 14


def summary_stat(label: str, value: str, value_tone: str = "") -> Span:
    return Span(
        NotStr(f'<strong class="{escape(value_tone)}">{escape(value)}</strong>'),
        Small(label),
    )


def equity_curve(record: PublicTrackRecord) -> NotStr:
    points = list(record.equityCurve)
    if not points:
        return NotStr('<p class="table-empty">No equity observations yet.</p>')
    if len(points) == 1:
        points.append(points[0])
    equities = [float(point.equity) for point in points]
    minimum, maximum = min(equities), max(equities)
    span = maximum - minimum or abs(maximum) or 1

    def x(index: int) -> float:
        return CURVE_PAD_X + index * (CURVE_WIDTH - 2 * CURVE_PAD_X) / (len(points) - 1)

    def y(value: float) -> float:
        return (
            CURVE_HEIGHT
            - CURVE_PAD_Y
            - ((value - minimum) / span * (CURVE_HEIGHT - 2 * CURVE_PAD_Y))
        )

    coordinates = [(x(index), y(value)) for index, value in enumerate(equities)]
    baseline = CURVE_HEIGHT - CURVE_PAD_Y
    line = " ".join(f"{cx:.1f},{cy:.1f}" for cx, cy in coordinates)
    area_line = " L ".join(f"{cx:.1f},{cy:.1f}" for cx, cy in coordinates)
    area = (
        f"M {coordinates[0][0]:.1f},{baseline} L {area_line} "
        f"L {coordinates[-1][0]:.1f},{baseline} Z"
    )
    last_x, last_y = coordinates[-1]
    title = f"Paper equity from {date_time(points[0].t)} to {date_time(points[-1].t)}"
    return NotStr(
        f'<svg class="equity-curve" viewBox="0 0 {CURVE_WIDTH} {CURVE_HEIGHT}" role="img" '
        f'preserveAspectRatio="none"><title>{escape(title)}</title>'
        f'<path class="equity-curve-area" d="{area}"/>'
        f'<polyline class="equity-curve-line" points="{line}"/>'
        f'<circle class="equity-curve-dot" cx="{last_x:.1f}" cy="{last_y:.1f}" r="4"/>'
        f'<text class="equity-curve-label" x="{CURVE_PAD_X}" y="{CURVE_PAD_Y + 4}">'
        f"{escape(signed_money(points[0].equity))}</text>"
        f'<text class="equity-curve-label equity-curve-label-end" '
        f'x="{CURVE_WIDTH - CURVE_PAD_X}" y="{last_y - 10:.1f}">'
        f"{escape(signed_money(points[-1].equity))}</text></svg>"
    )


def positions_table(positions: list[PublicTrackRecordPosition]) -> Section:
    rows = [
        Tr(
            Th(position.marketQuestion, Small(position.outcome)),
            Td(short_number(position.shares), cls="num"),
            Td(price(position.averageCost), cls="num"),
            Td(money(position.liquidationValue), cls="num"),
            Td(
                signed_money(position.unrealizedPnl),
                cls=f"num {tone(position.unrealizedPnl)}".strip(),
            ),
            Td(
                Span(
                    position.markStatus,
                    cls=f"paper-mark paper-mark-{position.markStatus}",
                )
            ),
        )
        for position in positions
    ]
    return Section(
        Header(H2("Open positions"), Span(str(len(positions)), cls="count-pill")),
        Div(
            Table(
                Thead(
                    Tr(
                        Th("Market / outcome"),
                        Th("Shares", cls="num"),
                        Th("Average cost", cls="num"),
                        Th("Liquidation", cls="num"),
                        Th("Unrealized P&L", cls="num"),
                        Th("Mark"),
                    )
                ),
                Tbody(*rows),
            ),
            cls="table-scroll",
        ),
        P("No open paper positions.", cls="table-empty") if not positions else None,
        cls="data-section paper-table-section",
    )


def fills_table(fills: list[PublicTrackRecordFill]) -> Section:
    rows = [
        Tr(
            Td(date_time(fill.createdAt)),
            Th(fill.marketQuestion, Small(fill.outcome)),
            Td(
                Span(
                    fill.kind,
                    cls=f"paper-fill-kind paper-fill-{fill.kind.lower()}",
                )
            ),
            Td(short_number(fill.shares), cls="num"),
            Td(price(fill.averagePrice), cls="num"),
            Td(money(fill.fee), cls="num"),
            Td(signed_money(fill.cashEffect), cls=f"num {tone(fill.cashEffect)}".strip()),
            Td(
                "—" if fill.kind == "BUY" else signed_money(fill.realizedPnl),
                cls=f"num {tone(fill.realizedPnl)}".strip(),
            ),
        )
        for fill in fills
    ]
    return Section(
        Header(H2("Recent fills"), Span(str(len(fills)), cls="count-pill")),
        Div(
            Table(
                Thead(
                    Tr(
                        Th("Time"),
                        Th("Market / outcome"),
                        Th("Type"),
                        Th("Shares", cls="num"),
                        Th("VWAP", cls="num"),
                        Th("Fee", cls="num"),
                        Th("Cash effect", cls="num"),
                        Th("Realized P&L", cls="num"),
                    )
                ),
                Tbody(*rows),
            ),
            cls="table-scroll",
        ),
        P("No fills recorded yet.", cls="table-empty") if not fills else None,
        cls="data-section paper-table-section",
    )


def track_record_page(record: PublicTrackRecord, share_url: str) -> Main:
    stats = record.stats
    first_point = record.equityCurve[0].t if record.equityCurve else record.profile.startedAt
    return Main(
        Header(
            Div(
                Span("Public paper track record", cls="eyebrow"),
                H1(record.profile.displayName),
                P(
                    "Shared paper-trading performance. Virtual USDC only — no wallet, "
                    "no real funds."
                ),
            ),
            Div(
                Code(share_url),
                Input(value=share_url, readonly=True, aria_label="Track record link"),
                cls="public-share-button",
            ),
            cls="page-title",
        ),
        Section(
            Div(
                summary_stat("Equity", money(stats.equity)),
                summary_stat("Initial cash", money(stats.initialCash)),
                summary_stat("Total P&L", signed_money(stats.totalPnl), tone(stats.totalPnl)),
                summary_stat("Realized", signed_money(stats.realizedPnl), tone(stats.realizedPnl)),
                summary_stat(
                    "Unrealized", signed_money(stats.unrealizedPnl), tone(stats.unrealizedPnl)
                ),
                summary_stat("Fees paid", money(stats.totalFees)),
                summary_stat("Trades", str(stats.tradeCount)),
                summary_stat("Win rate", "—" if stats.winRate is None else f"{stats.winRate}%"),
                cls="summary-metrics",
            ),
            cls="data-section track-record-summary",
            aria_label="Track record summary",
        ),
        Section(
            Div(
                Span("Evidence context", cls="eyebrow"),
                H2("How to read this record", id="track-record-evidence-heading"),
            ),
            Dl(
                Div(Dt("Record type"), Dd("Paper ledger · virtual USDC")),
                Div(
                    Dt("Observed sample"),
                    Dd(f"{stats.tradeCount} fills · {len(record.positions)} open positions"),
                ),
                Div(Dt("Window begins"), Dd(date_time(first_point))),
                Div(Dt("Equity treatment"), Dd("Cash plus marked open positions")),
            ),
            P(
                "These results are a shareable simulation record, not a broker statement or a "
                "forecast. Fees are included; unsettled positions can change as the market moves."
            ),
            cls="track-record-evidence",
            aria_labelledby="track-record-evidence-heading",
        ),
        Section(
            Header(
                H2("Equity curve"),
                Span(str(len(record.equityCurve)), cls="count-pill"),
            ),
            equity_curve(record),
            P(
                "Curve tracks settled cash; the final point is live equity.",
                cls="track-record-footnote",
            ),
            cls="data-section track-record-curve",
            aria_label="Equity curve",
        ),
        positions_table(record.positions),
        fills_table(record.fills),
        P(f"Observed {date_time(record.observedAt)}.", cls="track-record-observed"),
        cls="detail-page track-record-page",
    )


def track_record_unavailable(title: str, body: str) -> Main:
    return Main(
        Section(icon("alert"), H1(title), P(body), cls="empty-page-card"),
        cls="detail-page track-record-page",
    )
