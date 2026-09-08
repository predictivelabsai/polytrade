"""UI-independent routing for the chat's direct research commands."""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from typing import Optional

from .agent_factory import get_tool_registry
from .errors import CommandUnavailable, UnsafeCommand
from .prompts import COMMAND_HELP


_COMMANDS = {
    "load",
    "news",
    "financials",
    "quote",
    "des",
    "fa",
    "anr",
    "ee",
    "rv",
    "own",
    "gp",
    "gip",
    "scan",
    "help",
    "h",
    "?",
}
_BLOCKED_SENSITIVE_COMMANDS = {"poly:buy", "poly:sell", "poly:portfolio"}
_AUTHENTICATED_COMMANDS = {
    "poly:pnl",
    "poly:report",
    "poly:trades",
    "poly:paperbuy",
    "poly:papersell",
    "poly:paperportfolio",
}

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CommandResult:
    content: str
    command: str


class CommandRouter:
    """Run supported CLI-style research commands without any UI dependencies."""

    async def run(self, content: str, user_id: Optional[str]) -> Optional[CommandResult]:
        stripped = content.strip()
        if not stripped:
            return None

        first = stripped.split()[0].lower()
        effective = first
        if first == "poly:":
            remainder = stripped[5:].strip().split()
            effective = f"poly:{remainder[0].lower()}" if remainder else "poly:"

        if effective in _BLOCKED_SENSITIVE_COMMANDS:
            raise UnsafeCommand(
                "Real-money trading and wallet access are not available through "
                "the research chat API."
            )
        if effective in _AUTHENTICATED_COMMANDS and not user_id:
            raise UnsafeCommand("Sign in before accessing portfolio or PnL commands.")

        is_command = first in _COMMANDS or first.startswith("poly:")
        if not is_command:
            return None

        if first in {"help", "h", "?"}:
            return CommandResult(COMMAND_HELP, first)

        # Shell/process-control commands must never be reachable from an API.
        if first in {"exit", "q", "cls", "reset", "r"} or stripped == "..":
            return None

        processor = None
        original_console = None
        try:
            from components.command_processor import CommandProcessor
            from rich.console import Console

            processor = CommandProcessor(
                get_tool_registry().command_agent,
                user_id=user_id,
            )
            output = io.StringIO()
            original_console = processor.console
            processor.console = Console(
                file=output,
                force_terminal=False,
                width=120,
                no_color=True,
            )
            handled, agent_query = await processor.process_command(stripped)
        except Exception as exc:
            logger.exception("PolyTrade command %s failed", first)
            raise CommandUnavailable(
                f"The `{first}` command is temporarily unavailable. Please try again."
            ) from exc
        finally:
            if processor is not None and original_console is not None:
                processor.console = original_console

        if not handled and agent_query:
            return None

        rendered = output.getvalue().strip() or "Command executed."
        return CommandResult(f"```\n{rendered}\n```", first)
