"""Request-local agent attribution inherited by chat commands and DB writes."""

from __future__ import annotations

from contextvars import ContextVar, Token


agent_framework: ContextVar[str] = ContextVar("agent_framework", default="unknown")
agent_name: ContextVar[str] = ContextVar("agent_name", default="Unknown")


def bind_agent(framework: str) -> tuple[Token, Token]:
    name = "Hermes" if framework == "hermes" else "DeepAgents"
    return agent_framework.set(framework), agent_name.set(name)


def reset_agent(tokens: tuple[Token, Token]) -> None:
    agent_framework.reset(tokens[0])
    agent_name.reset(tokens[1])

