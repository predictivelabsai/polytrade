"""Public strategy-template landing page."""

from fasthtml.common import (
    H1,
    H2,
    H3,
    A,
    Article,
    Dd,
    Div,
    Dl,
    Dt,
    Footer,
    Header,
    Main,
    P,
    Section,
    Span,
)
from polytrade_contracts import STRATEGY_TEMPLATES, StrategyTemplate

from .icons import icon

STEPS = (
    (
        "cursor",
        "Pick a template",
        "Five pre-tuned price-band strategies, each with an illustrative backtest on resolved "
        "Polymarket markets.",
    ),
    (
        "check-list",
        "Review the band",
        "We translate the template onto the market you pick — buy price, sell price, order size, "
        "and cadence are all editable before anything runs.",
    ),
    (
        "zap",
        "Deploy to paper",
        "The strategy runs on the gateway in the background with virtual USDC. Stop it any time "
        "from the paper dashboard.",
    ),
)

TEMPLATE_GUIDANCE = {
    "base-rate-divergence": "Liquid markets where prices move slowly",
    "longshot-fade": "Favourites attracting speculative longshot money",
    "ev-sniping": "Liquid markets with a deep order book",
    "overreaction-fade": "Headline-driven selloffs likely to revert",
    "resolution-grinder": "Near-certain favourites with deep liquidity",
}


def template_card(template: StrategyTemplate) -> Article:
    guidance = TEMPLATE_GUIDANCE.get(template.id, "Markets that match the backtest assumptions")
    return Article(
        Div(
            H3(template.name),
            Span("Illustrative", cls="template-card-kind"),
            cls="template-card-heading",
        ),
        P(template.tagline, cls="template-card-tagline"),
        P(
            Span("Best for", cls="template-card-fit-label"),
            guidance,
            cls="template-card-fit",
        ),
        P(template.description, cls="template-card-description"),
        Dl(
            Div(Dt("Return"), Dd(f"+{template.stats.returnPct}%")),
            Div(Dt("Win rate"), Dd(f"{template.stats.winRatePct}%")),
            Div(Dt("Trades"), Dd(str(template.stats.tradeCount))),
            Div(Dt("Max drawdown"), Dd(f"−{template.stats.maxDrawdownPct}%")),
            cls="template-card-stats",
        ),
        P(
            Span("Evidence", cls="template-card-basis-label"),
            f"{template.stats.basis} · not a forecast.",
            cls="template-card-basis",
        ),
        Div(
            A(
                "Deploy to paper ",
                icon("arrow-right"),
                href=f"/paper?template={template.id}",
                cls="button button-primary",
            ),
            A(
                "Review setup",
                href=f"/backtests/new?template={template.id}",
                cls="button button-quiet",
            ),
            cls="template-card-actions",
        ),
        id=f"template-{template.id}",
        cls="template-card",
    )


def templates_page() -> Main:
    return Main(
        Header(
            Span("PolyTrade paper trading", cls="eyebrow"),
            H1("Start paper trading in two minutes"),
            P(
                "Pick a pre-built strategy, point it at any Polymarket market, and deploy to a "
                "virtual-USDC paper account. No wallet, no real funds — every fill is simulated."
            ),
            A(
                "Browse strategy templates ",
                icon("arrow-right"),
                href="#strategy-templates",
                cls="button button-primary template-landing-cta",
            ),
            cls="template-landing-hero",
        ),
        Section(
            Header(
                Div(
                    Span("One-click strategies", cls="eyebrow"),
                    H2("Start from a proven template"),
                ),
                cls="template-grid-header",
            ),
            Div(
                Span("New to paper trading?", cls="template-selection-note-label"),
                " Resolution grinder has the lowest illustrative drawdown in this set. ",
                A("Start there", href="#template-resolution-grinder"),
                cls="template-selection-note",
            ),
            Div(*(template_card(item) for item in STRATEGY_TEMPLATES), cls="template-grid"),
            id="strategy-templates",
            cls="template-grid-section template-grid-landing",
            aria_label="Strategy templates",
        ),
        Section(
            *(Article(icon(icon_name), H2(title), P(body)) for icon_name, title, body in STEPS),
            cls="template-landing-steps",
            aria_label="How it works",
        ),
        Footer(
            P(
                "Stats are illustrative backtests on resolved markets with virtual USDC — not a "
                "promise of future results, and not investment advice. Review every band before "
                "deploying."
            ),
            A("Create your free paper account →", href="/paper"),
            cls="template-landing-footer",
        ),
        cls="detail-page templates-page",
    )
