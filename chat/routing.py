"""Framework-neutral, one-message agent override parsing."""

from __future__ import annotations

from dataclasses import dataclass


DEFAULT_RUNTIME = "deepagents"
_PREFIXES = {
    "/deepagent": "deepagents",
    "/deepagents": "deepagents",
    "/hermes": "hermes",
}


@dataclass(frozen=True)
class AgentRoute:
    runtime: str
    content: str
    requested_runtime: str | None = None


def route_message(message: str) -> AgentRoute:
    """Resolve a one-message runtime and remove a recognized leading prefix."""
    stripped = message.strip()
    first, separator, remainder = stripped.partition(" ")
    requested = _PREFIXES.get(first.lower())
    if requested is None:
        return AgentRoute(DEFAULT_RUNTIME, stripped)
    return AgentRoute(
        requested,
        remainder.strip() if separator else "",
        requested_runtime=requested,
    )

