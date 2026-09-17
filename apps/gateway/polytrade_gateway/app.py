"""Gateway application factory.

Route registration is split from service construction so unit tests can inject
the same fake ports that covered the former Fastify application.
"""

from fastapi import FastAPI


def create_app() -> FastAPI:
    return FastAPI(title="PolyTrade Gateway", version="4.0.0")
