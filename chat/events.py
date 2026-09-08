"""Transport-neutral events emitted by the shared chat service."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict


RUN_STARTED = "run.started"
AGENT_ROUTE = "agent.route"
TOOL_STARTED = "tool.started"
TOOL_COMPLETED = "tool.completed"
MESSAGE_DELTA = "message.delta"
MESSAGE_COMPLETED = "message.completed"
RUN_COMPLETED = "run.completed"
RUN_FAILED = "run.failed"

TERMINAL_EVENTS = {RUN_COMPLETED, RUN_FAILED}


@dataclass(frozen=True)
class ChatEvent:
    """A single event that can be rendered by a UI or serialized as SSE."""

    event: str
    data: Dict[str, Any]

    @property
    def terminal(self) -> bool:
        return self.event in TERMINAL_EVENTS

    def to_dict(self) -> Dict[str, Any]:
        return {"event": self.event, "data": self.data}

    def to_sse(self) -> str:
        payload = json.dumps(self.data, ensure_ascii=False, default=str)
        return f"event: {self.event}\ndata: {payload}\n\n"
