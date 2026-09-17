"""Production entrypoint for the FastAPI gateway."""

from __future__ import annotations

import os

import uvicorn

from .app import create_app
from .config import parse_config

settings = parse_config(dict(os.environ))
app = create_app(settings)


if __name__ == "__main__":
    uvicorn.run(  # noqa: S104 - container ingress binds every interface
        app,
        host="0.0.0.0",  # noqa: S104
        port=settings.PORT,
        proxy_headers=True,
        forwarded_allow_ips=",".join(settings.trusted_proxies),
    )
