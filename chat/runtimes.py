"""Agent factories for the canonical DeepAgents harness and isolated Hermes."""

from __future__ import annotations

import json
import os
from types import SimpleNamespace
from typing import Any, AsyncIterator, Callable

import httpx


class HermesAgent:
    """Expose Hermes through the LangGraph event interface used by ChatService."""

    def __init__(self, *, user_id: str | None, thread_id: str) -> None:
        self.user_id = user_id or "anonymous"
        self.thread_id = thread_id

    async def astream_events(
        self, payload: dict[str, Any], version: str
    ) -> AsyncIterator[dict[str, Any]]:
        del version
        key = os.getenv("HERMES_API_SERVER_KEY")
        if not key:
            raise RuntimeError("Hermes is not configured")
        source_messages = payload.get("messages", [])
        if os.getenv("HERMES_PERSIST_SESSIONS", "true").lower() in {"1", "true", "yes"}:
            source_messages = source_messages[-1:]
        messages = []
        for item in source_messages:
            role = "user" if item.__class__.__name__ == "HumanMessage" else "assistant"
            content = getattr(item, "content", "")
            if content:
                messages.append({"role": role, "content": str(content)})
        body = {
            "model": os.getenv("HERMES_API_MODEL", "hermes-agent"),
            "messages": messages,
            "stream": True,
        }
        headers = {
            "Authorization": f"Bearer {key}",
            "X-Hermes-Session-Id": f"polytrade:{self.thread_id}",
            "X-Hermes-Session-Key": f"polytrade-user:{self.user_id}",
        }
        base_url = os.getenv("HERMES_API_URL", "http://hermes:8642/v1").rstrip("/")
        timeout = float(os.getenv("HERMES_API_TIMEOUT_SECONDS", "180"))
        complete = ""
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream(
                "POST", f"{base_url}/chat/completions", headers=headers, json=body
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if not data or data == "[DONE]":
                        continue
                    chunk = json.loads(data)
                    text = chunk.get("choices", [{}])[0].get("delta", {}).get("content")
                    if text:
                        complete += text
                        yield {
                            "event": "on_chat_model_stream",
                            "data": {"chunk": SimpleNamespace(content=text)},
                        }
        yield {
            "event": "on_chain_end",
            "data": {"output": {"messages": [SimpleNamespace(content=complete)]}},
        }


def get_runtime_agent(
    runtime: str,
    *,
    user_id: str | None,
    thread_id: str,
    deepagent_factory: Callable[[], Any],
) -> Any:
    if runtime == "hermes":
        return HermesAgent(user_id=user_id, thread_id=thread_id)
    if runtime == "deepagents":
        return deepagent_factory()
    raise ValueError(f"Unsupported agent runtime: {runtime}")
