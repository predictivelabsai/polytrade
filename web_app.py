"""Legacy web entrypoint kept as a JavaScript-free FastHTML redirect."""

from __future__ import annotations

from fasthtml.common import RedirectResponse, fast_app

app, rt = fast_app()


@rt("/")
def index():
    return RedirectResponse("http://localhost:5173/templates", status_code=307)


@rt("/health")
def health():
    return {"status": "ok", "version": "4.0.0"}
