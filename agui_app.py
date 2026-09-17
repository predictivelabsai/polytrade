"""Compatibility entrypoint for the retired AG-UI shell.

The product UI is now the server-rendered FastHTML app in ``apps/web``.  This
small shim keeps old deployment commands working without shipping a browser
JavaScript runtime.
"""

from __future__ import annotations

from fasthtml.common import A, Div, H1, Main, Title, fast_app

app, rt = fast_app()


@rt("/")
def index():
    return (
        Title("PolyTrade Chat"),
        Main(
            H1("PolyTrade Chat"),
            Div(
                "The workspace moved to the FastHTML app.",
                A("Open workspace", href="http://localhost:5173/chat"),
                A("Settings", href="/settings"),
                A("Usage", href="/usage"),
                A("Admin logging", href="/admin/logging"),
            ),
        ),
    )


@rt("/health")
def health():
    return {"status": "ok", "version": "4.0.0"}


@rt("/usage")
def usage():
    return Div("Usage is available from the workspace navigation.")
