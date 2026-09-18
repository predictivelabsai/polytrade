"""Small inline SVG icons; no icon JavaScript bundle is required."""

from fasthtml.common import NotStr

PATHS = {
    "activity": "M22 12h-4l-3 9L9 3l-3 9H2",
    "arrow-right": "M5 12h14M13 6l6 6-6 6",
    "backtest": "M3 3v18h18M7 16l4-4 3 3 5-7",
    "briefcase": (
        "M16 20V4a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16"
        "M4 7h16a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V9a2 2 0 0 1 2-2z"
    ),
    "check-list": "M9 11l3 3L22 4M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11",
    "circle-dot": "M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20zM12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z",
    "cursor": "m3 3 7.07 16.97 2.51-7.39L20 10.07 3 3zM13 13l6 6",
    "history": "M3 12a9 9 0 1 0 3-6.7L3 8M3 3v5h5M12 7v5l3 2",
    "message": "M21 15a4 4 0 0 1-4 4H8l-5 3V7a4 4 0 0 1 4-4h10a4 4 0 0 1 4 4z",
    "menu": "M4 6h16M4 12h16M4 18h16",
    "plus": "M12 5v14M5 12h14",
    "refresh": "M20 6v5h-5M4 18v-5h5M5.6 9a7 7 0 0 1 11.5-2.6L20 11M4 13l2.9 4.6A7 7 0 0 0 18.4 15",
    "send": "m22 2-7 20-4-9-9-4zM22 2 11 13",
    "search": "m21 21-4.35-4.35M19 11a8 8 0 1 1-16 0 8 8 0 0 1 16 0z",
    "settings": (
        "M12 15.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7z"
        "M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-2 3.4-.2-.1"
        "a1.7 1.7 0 0 0-1.9.3l-.6.4a1.7 1.7 0 0 0-.8 1.7v.3h-4v-.3"
        "a1.7 1.7 0 0 0-.8-1.7l-.6-.4a1.7 1.7 0 0 0-1.9-.3l-.2.1-2-3.4.1-.1"
        "a1.7 1.7 0 0 0 .3-1.9l-.3-.7a1.7 1.7 0 0 0-1.5-1H3v-4h.2"
        "a1.7 1.7 0 0 0 1.5-1l.3-.7a1.7 1.7 0 0 0-.3-1.9l-.1-.1 2-3.4.2.1"
        "a1.7 1.7 0 0 0 1.9-.3l.6-.4a1.7 1.7 0 0 0 .8-1.7V1h4v.3"
        "a1.7 1.7 0 0 0 .8 1.7l.6.4a1.7 1.7 0 0 0 1.9.3l.2-.1 2 3.4-.1.1"
        "a1.7 1.7 0 0 0-.3 1.9l.3.7a1.7 1.7 0 0 0 1.5 1h.2v4H21"
        "a1.7 1.7 0 0 0-1.5 1z"
    ),
    "shield": "M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10zM9 12l2 2 4-4",
    "trash": "M3 6h18M8 6V4h8v2M19 6l-1 15H6L5 6M10 11v6M14 11v6",
    "wallet": "M20 7V5a2 2 0 0 0-2-2H5a3 3 0 0 0 0 6h16v10a2 2 0 0 1-2 2H5a3 3 0 0 1-3-3V6M16 13h2",
    "zap": "M13 2 3 14h9l-1 8 10-12h-9l1-8z",
    "alert": (
        "M10.3 2.9 1.8 17a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3"
        "L13.7 2.9a2 2 0 0 0-3.4 0zM12 9v4M12 17h.01"
    ),
}


def icon(name: str) -> NotStr:
    path = PATHS[name]
    return NotStr(
        f'<svg aria-hidden="true" width="24" height="24" viewBox="0 0 24 24" '
        f'fill="none" stroke="currentColor" '
        f'stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="{path}"/></svg>'
    )
