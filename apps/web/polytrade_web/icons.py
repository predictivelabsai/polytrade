"""Small inline SVG icons; no icon JavaScript bundle is required."""

from fasthtml.common import NotStr

PATHS = {
    "arrow-right": "M5 12h14M13 6l6 6-6 6",
    "check-list": "M9 11l3 3L22 4M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11",
    "cursor": "m3 3 7.07 16.97 2.51-7.39L20 10.07 3 3zM13 13l6 6",
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
