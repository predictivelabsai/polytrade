"""Authenticated server-side client for the PolyTrade gateway."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx
from starlette.requests import Request


class GatewayResponseError(RuntimeError):
    def __init__(self, message: str, status: int):
        super().__init__(message)
        self.status = status


def access_token(request: Request) -> str | None:
    authorization = request.headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip() or None
    return request.cookies.get("polytrade_access_token")


class GatewayClient:
    def __init__(self, base_url: str, http: httpx.AsyncClient):
        self.base_url = base_url.rstrip("/")
        self.http = http

    async def request(
        self,
        method: str,
        path: str,
        token: str,
        *,
        json: Mapping[str, Any] | None = None,
        params: Mapping[str, str | int] | None = None,
        idempotency_key: str | None = None,
        accept: str = "application/json",
    ) -> Any:
        headers = {"Authorization": f"Bearer {token}", "Accept": accept}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        try:
            response = await self.http.request(
                method,
                f"{self.base_url}{path}",
                headers=headers,
                json=json,
                params=params,
            )
        except httpx.HTTPError as exc:
            raise GatewayResponseError("The gateway did not answer this request.", 503) from exc
        if response.status_code == 204:
            return None
        if response.is_error:
            try:
                payload = response.json()
                message = payload.get("detail") or payload.get("error", {}).get("message")
            except (ValueError, AttributeError):
                message = None
            raise GatewayResponseError(
                str(message or f"Gateway request failed ({response.status_code})"),
                response.status_code,
            )
        if accept == "text/event-stream":
            return response.text
        return response.json()

    async def get(self, path: str, token: str, **kwargs: Any) -> Any:
        return await self.request("GET", path, token, **kwargs)

    async def post(self, path: str, token: str, **kwargs: Any) -> Any:
        return await self.request("POST", path, token, **kwargs)

    async def delete(self, path: str, token: str, **kwargs: Any) -> Any:
        return await self.request("DELETE", path, token, **kwargs)
