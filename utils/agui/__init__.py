"""
Vendored AG-UI integration for FastHTML + LangGraph.

Based on ft-agui (https://github.com/Novia-RDI-Seafaring/ft-ag-ui)
by Christoffer Bjorkskog / Novia University — MIT license.

Adapted for PolyTrade: 3-pane layout, LangGraph streaming.
"""

from .core import setup_agui, AGUISetup, AGUIThread, UI, StreamingCommand
from .styles import get_chat_styles, CHAT_UI_STYLES

__all__ = [
    "setup_agui",
    "AGUISetup",
    "AGUIThread",
    "UI",
    "StreamingCommand",
    "get_chat_styles",
    "CHAT_UI_STYLES",
]
