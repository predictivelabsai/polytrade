"""FastHTML application factory and public routes."""

from __future__ import annotations

from pathlib import Path

import httpx
from fasthtml.common import FastHTML, Link, Meta, RedirectResponse, Title
from polytrade_contracts import PublicTrackRecord
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from .config import WebSettings
from .templates_page import templates_page
from .track_record import track_record_page, track_record_unavailable

ROOT = Path(__file__).resolve().parents[3]
STYLES = ROOT / "apps" / "web" / "src"


def create_app(
    settings: WebSettings | None = None,
    client: httpx.AsyncClient | None = None,
) -> FastHTML:
    config = settings or WebSettings()
    http = client or httpx.AsyncClient(timeout=10, follow_redirects=False)
    app = FastHTML(
        hdrs=(
            Meta(charset="utf-8"),
            Meta(name="viewport", content="width=device-width, initial-scale=1"),
            Link(rel="stylesheet", href="/assets/styles.css"),
        ),
        default_hdrs=False,
        htmx=False,
        surreal=False,
    )

    @app.get("/assets/styles.css")
    async def styles():
        css = (STYLES / "styles.css").read_text(encoding="utf-8")
        css = css.replace(
            '@import "@fontsource-variable/manrope";\n'
            '@import "@fontsource/ibm-plex-mono/400.css";\n'
            '@import "@fontsource/ibm-plex-mono/500.css";',
            '@import url("https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=Manrope:wght@200..800&display=swap");',
        )
        css = css.replace('"Manrope Variable"', '"Manrope"')
        return Response(css, media_type="text/css")

    @app.get("/health")
    async def health():
        return JSONResponse({"status": "ok", "version": "4.0.0"})

    @app.get("/")
    async def index():
        return RedirectResponse("/templates", status_code=302)

    @app.get("/templates")
    async def templates():
        return Title("Strategy templates · PolyTrade"), templates_page()

    @app.get("/u/{token}")
    async def track_record(token: str, request: Request):
        if not 32 <= len(token) <= 64 or not all(char.isalnum() or char in "_-" for char in token):
            return (
                Title("Track record unavailable · PolyTrade"),
                Meta(name="robots", content="noindex"),
                track_record_unavailable(
                    "This track record is not available",
                    "The link may have been rotated or turned off by its owner. "
                    "Ask for a fresh link to view these paper results.",
                ),
            )
        try:
            response = await http.get(
                f"{config.API_URL.rstrip('/')}/v1/public/track-records/{token}"
            )
            if response.status_code == 404:
                return (
                    Title("Track record unavailable · PolyTrade"),
                    Meta(name="robots", content="noindex"),
                    track_record_unavailable(
                        "This track record is not available",
                        "The link may have been rotated or turned off by its owner. "
                        "Ask for a fresh link to view these paper results.",
                    ),
                )
            response.raise_for_status()
            record = PublicTrackRecord.model_validate(response.json())
        except (httpx.HTTPError, ValueError):
            return (
                Title("Track record unavailable · PolyTrade"),
                Meta(name="robots", content="noindex"),
                track_record_unavailable(
                    "Track record could not be loaded",
                    "The gateway did not answer this request. It may be a temporary outage — "
                    "try again in a moment.",
                ),
            )
        origin = str(request.base_url).rstrip("/")
        return (
            Title("Paper track record · PolyTrade"),
            Meta(name="robots", content="noindex"),
            track_record_page(record, f"{origin}/u/{token}"),
        )

    return app


app = create_app()
