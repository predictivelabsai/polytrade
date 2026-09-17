"""Display formatting shared by server-rendered financial views."""

from datetime import datetime


def money(value: str | None) -> str:
    if value is None:
        return "—"
    return f"{float(value):,.2f} USDC"


def signed_money(value: str | None) -> str:
    if value is None:
        return "—"
    number = float(value)
    return f"{'+' if number > 0 else ''}{number:,.2f} USDC"


def price(value: str | None) -> str:
    if not value:
        return "—"
    return f"{float(value):.4f}"


def short_number(value: str) -> str:
    return f"{float(value):,.6f}".rstrip("0").rstrip(".")


def date_time(value: str) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return f"{parsed.strftime('%b')} {parsed.day}, {parsed.year}, {parsed.strftime('%H:%M')}"


def tone(value: str | None) -> str:
    number = float(value or 0)
    return "value-positive" if number > 0 else "value-negative" if number < 0 else ""
