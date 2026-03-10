"""Type definitions for the PolyCode agent."""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Literal
from enum import Enum


class EventType(str, Enum):
    """Types of events emitted by the agent."""
    TOOL_START = "tool_start"
    TOOL_END = "tool_end"
    TOOL_ERROR = "tool_error"
    ANSWER_START = "answer_start"
    ANSWER_CHUNK = "answer_chunk"
    DONE = "done"


@dataclass
class ToolStartEvent:
    """Event emitted when a tool starts executing."""
    type: Literal["tool_start"] = "tool_start"
    tool: str = ""
    args: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolEndEvent:
    """Event emitted when a tool finishes executing."""
    type: Literal["tool_end"] = "tool_end"
    tool: str = ""
    result: str = ""


@dataclass
class ToolErrorEvent:
    """Event emitted when a tool encounters an error."""
    type: Literal["tool_error"] = "tool_error"
    tool: str = ""
    error: str = ""


@dataclass
class AnswerStartEvent:
    """Event emitted when the agent starts generating the final answer."""
    type: Literal["answer_start"] = "answer_start"


@dataclass
class AnswerChunkEvent:
    """Event emitted when a chunk of the answer is generated."""
    type: Literal["answer_chunk"] = "answer_chunk"
    chunk: str = ""


@dataclass
class LogEvent:
    """Event emitted for logging thought or activity."""
    type: Literal["log"] = "log"
    message: str = ""
    level: str = "info"  # info, thought, tool, error


@dataclass
class DoneEvent:
    """Event emitted when the agent finishes."""
    type: Literal["done"] = "done"
    answer: str = ""
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    iterations: int = 0


AgentEvent = (
    ToolStartEvent
    | ToolEndEvent
    | ToolErrorEvent
    | AnswerStartEvent
    | AnswerChunkEvent
    | DoneEvent
    | LogEvent
)


@dataclass
class AgentConfig:
    """Configuration for the Agent."""
    model: str = "gpt-4.1-mini"
    model_provider: str = "openai"
    max_iterations: int = 10
    signal: Optional[Any] = None


@dataclass
class ToolSummary:
    """Summary of a tool call and its result."""
    tool: str = ""
    args: Dict[str, Any] = field(default_factory=dict)
    result: str = ""
    timestamp: Optional[str] = None
