from dataclasses import dataclass, field


@dataclass(frozen=True)
class AgentRunContext:
    """Request-only values available to tools but excluded from graph state."""

    principal_id: str
    scopes: tuple[str, ...]
    gateway_bearer: str = field(repr=False)
