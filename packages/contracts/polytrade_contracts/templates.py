"""Built-in paper strategy templates."""

from .models import StrategyTemplate, StrategyTemplateList

STRATEGY_TEMPLATES = StrategyTemplateList.model_validate(
    {
        "items": [
            {
                "id": "base-rate-divergence",
                "name": "Base-rate divergence",
                "tagline": "Buy the favourite a few cents under the tape.",
                "description": (
                    "Bids below the going price on the market favourite and scales out as the "
                    "market prices in the base rate. Suited to liquid markets where the odds move "
                    "slowly and dips are noise rather than news."
                ),
                "suggestedSearchQuery": "fed decision",
                "outcomePick": "higher_price",
                "band": {
                    "entryOffset": "-0.03",
                    "exitOffset": "0.04",
                    "sharesPerOrder": "10",
                    "positionMultiplier": "5",
                    "intervalSeconds": 60,
                },
                "stats": {
                    "kind": "illustrative",
                    "returnPct": "6.40",
                    "winRatePct": "68.00",
                    "tradeCount": 124,
                    "maxDrawdownPct": "3.10",
                    "basis": (
                        "Illustrative backtest · 42 resolved markets · Jan-Jun 2026 · virtual USDC"
                    ),
                },
                "backtestHint": {
                    "strategy": "mean_reversion_v1",
                    "note": "Approximates dip-buying around a slow-moving favourite.",
                },
            },
            {
                "id": "longshot-fade",
                "name": "Longshot fade",
                "tagline": "Take the other side of longshot hype.",
                "description": (
                    "Buys the high-priced side (the favourite) of markets drawing speculative "
                    "longshot money, at a small discount, and exits into strength. This does not "
                    "short — it simply buys the favourite's token cheaply."
                ),
                "suggestedSearchQuery": "election winner",
                "outcomePick": "higher_price",
                "band": {
                    "entryOffset": "-0.02",
                    "exitOffset": "0.03",
                    "sharesPerOrder": "20",
                    "positionMultiplier": "4",
                    "intervalSeconds": 30,
                },
                "stats": {
                    "kind": "illustrative",
                    "returnPct": "8.90",
                    "winRatePct": "71.50",
                    "tradeCount": 96,
                    "maxDrawdownPct": "4.20",
                    "basis": (
                        "Illustrative backtest · 31 resolved markets · Jan-Jun 2026 · virtual USDC"
                    ),
                },
                "backtestHint": {
                    "strategy": "momentum_v1",
                    "note": "Backtests drift-following on the favourite's token.",
                },
            },
            {
                "id": "ev-sniping",
                "name": "EV sniping",
                "tagline": "Catch momentary dips below fair value.",
                "description": (
                    "Polls every five seconds for asks that dip below fair value in liquid "
                    "markets, buys the dip, and sells the snap-back. Needs a deep book — in thin "
                    "spread will eat the edge."
                ),
                "suggestedSearchQuery": "crypto above",
                "outcomePick": "lower_price",
                "band": {
                    "entryOffset": "-0.05",
                    "exitOffset": "0.10",
                    "sharesPerOrder": "10",
                    "positionMultiplier": "5",
                    "intervalSeconds": 5,
                },
                "stats": {
                    "kind": "illustrative",
                    "returnPct": "11.20",
                    "winRatePct": "59.00",
                    "tradeCount": 210,
                    "maxDrawdownPct": "5.80",
                    "basis": (
                        "Illustrative backtest · 55 resolved markets · Jan-Jun 2026 · virtual USDC"
                    ),
                },
                "backtestHint": {
                    "strategy": "mean_reversion_v1",
                    "note": "Backtests short-window reversion in liquid markets.",
                },
            },
            {
                "id": "overreaction-fade",
                "name": "Overreaction fade",
                "tagline": "Buy the scare, sell the settle.",
                "description": (
                    "Bids for outcomes that sold off sharply and holds for the reversion, in small "
                    "clips so a genuine news move costs little. Works best on markets where "
                    "headlines move prices more than fundamentals do."
                ),
                "suggestedSearchQuery": "court ruling",
                "outcomePick": "lower_price",
                "band": {
                    "entryOffset": "-0.04",
                    "exitOffset": "0.06",
                    "sharesPerOrder": "5",
                    "positionMultiplier": "8",
                    "intervalSeconds": 15,
                },
                "stats": {
                    "kind": "illustrative",
                    "returnPct": "5.10",
                    "winRatePct": "64.00",
                    "tradeCount": 88,
                    "maxDrawdownPct": "4.70",
                    "basis": (
                        "Illustrative backtest · 38 resolved markets · Jan-Jun 2026 · virtual USDC"
                    ),
                },
                "backtestHint": {
                    "strategy": "mean_reversion_v1",
                    "note": "Backtests reversion after sharp downside moves.",
                },
            },
            {
                "id": "resolution-grinder",
                "name": "Resolution grinder",
                "tagline": "Harvest near-certain favourites.",
                "description": (
                    "Thin entries, quick scale-outs, and a tight cap on near-certain favourites in "
                    "deep books. Small edge per trade, repeated — the slowest but steadiest of the "
                    "templates."
                ),
                "suggestedSearchQuery": "confirmation vote",
                "outcomePick": "higher_price",
                "band": {
                    "entryOffset": "-0.01",
                    "exitOffset": "0.02",
                    "sharesPerOrder": "25",
                    "positionMultiplier": "3",
                    "intervalSeconds": 60,
                },
                "stats": {
                    "kind": "illustrative",
                    "returnPct": "3.80",
                    "winRatePct": "82.00",
                    "tradeCount": 156,
                    "maxDrawdownPct": "1.90",
                    "basis": (
                        "Illustrative backtest · 47 resolved markets · Jan-Jun 2026 · virtual USDC"
                    ),
                },
                "backtestHint": {
                    "strategy": "breakout_v1",
                    "note": "Backtests grind-through behaviour on trending favourites.",
                },
            },
        ]
    }
).items


def strategy_template_by_id(template_id: str) -> StrategyTemplate | None:
    return next((template for template in STRATEGY_TEMPLATES if template.id == template_id), None)
