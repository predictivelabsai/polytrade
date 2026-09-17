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


def template_card(template: StrategyTemplate) -> Article:
    return Article(
        Div(
            H3(template.name),
            Span("Illustrative", cls="template-card-kind"),
            cls="template-card-heading",
        ),
        P(template.tagline, cls="template-card-tagline"),
        P(template.description, cls="template-card-description"),
        Dl(
            Div(Dt("Return"), Dd(f"+{template.stats.returnPct}%")),
            Div(Dt("Win rate"), Dd(f"{template.stats.winRatePct}%")),
            Div(Dt("Trades"), Dd(str(template.stats.tradeCount))),
            Div(Dt("Max drawdown"), Dd(f"−{template.stats.maxDrawdownPct}%")),
            cls="template-card-stats",
        ),
        P(Span("Evidence"), template.stats.basis, cls="template-card-basis"),
        Div(
            A(
                "Deploy to paper ",
                icon("arrow-right"),
                href=f"/paper?template={template.id}",
                cls="button button-primary",
            ),
            A(
                "See the backtest setup",
                href=f"/backtests/new?template={template.id}",
                cls="button button-quiet",
            ),
            cls="template-card-actions",
        ),
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
                "Open the paper dashboard ",
                icon("arrow-right"),
                href="/paper",
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
