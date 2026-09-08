"""Transport adapters that delegate every message to the canonical ChatService."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

import httpx

from chat.events import MESSAGE_COMPLETED, RUN_FAILED
from chat.service import get_chat_service

logger = logging.getLogger(__name__)


def _identity_map(provider: str) -> dict[str, str]:
    raw = os.getenv(f"{provider.upper()}_USER_MAP", "{}")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.error("%s_USER_MAP is not valid JSON", provider.upper())
        return {}
    return {str(key): str(value) for key, value in parsed.items()}


def linked_user(provider: str, provider_user_id: str) -> str | None:
    """Resolve only immutable, explicitly configured provider identities."""
    value = _identity_map(provider).get(str(provider_user_id))
    try:
        return str(UUID(value)) if value else None
    except ValueError:
        logger.error("Ignoring invalid Polytrade user UUID in %s_USER_MAP", provider.upper())
        return None


def channel_thread_id(provider: str, conversation_id: str, user_id: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"polytrade:{provider}:{conversation_id}:{user_id}"))


def verify_whatsapp_signature(body: bytes, signature: str | None) -> bool:
    secret = os.getenv("WHATSAPP_APP_SECRET", "")
    if not secret or not signature or not signature.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature[7:], expected)


async def run_channel_message(
    *, provider: str, provider_message_id: str, provider_user_id: str,
    conversation_id: str, content: str,
) -> str | None:
    user_id = linked_user(provider, provider_user_id)
    if not user_id:
        return None
    thread_id = channel_thread_id(provider, conversation_id, user_id)
    final = None
    async for event in get_chat_service().stream_message(
        user_id=user_id,
        thread_id=thread_id,
        content=content,
        idempotency_key=f"{provider}:{provider_message_id}",
        create_thread_if_missing=True,
    ):
        if event.event == MESSAGE_COMPLETED:
            final = event.data["message"]["content"]
        elif event.event == RUN_FAILED:
            final = event.data.get("message", "Polytrade could not complete the request.")
    return final


async def send_telegram(chat_id: str, text: str) -> None:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text[:4096]},
        )
        response.raise_for_status()


async def send_whatsapp(recipient: str, text: str) -> None:
    phone_id = os.environ["WHATSAPP_PHONE_NUMBER_ID"]
    token = os.environ["WHATSAPP_ACCESS_TOKEN"]
    version = os.getenv("WHATSAPP_GRAPH_VERSION", "v23.0")
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            f"https://graph.facebook.com/{version}/{phone_id}/messages",
            headers={"Authorization": f"Bearer {token}"},
            json={"messaging_product": "whatsapp", "to": recipient,
                  "type": "text", "text": {"body": text[:4096]}},
        )
        response.raise_for_status()


async def process_telegram(payload: dict[str, Any]) -> None:
    message = payload.get("message") or {}
    sender = message.get("from") or {}
    chat = message.get("chat") or {}
    text = message.get("text")
    if (not text or chat.get("type") != "private" or sender.get("id") is None
            or payload.get("update_id") is None):
        return
    answer = await run_channel_message(
        provider="telegram", provider_message_id=str(payload.get("update_id")),
        provider_user_id=str(sender["id"]), conversation_id=str(chat.get("id")),
        content=str(text),
    )
    if answer:
        await send_telegram(str(chat.get("id")), answer)


async def process_whatsapp(payload: dict[str, Any]) -> None:
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value") or {}
            for message in value.get("messages", []):
                sender = message.get("from")
                text = (message.get("text") or {}).get("body")
                if not sender or not text or not message.get("id"):
                    continue
                answer = await run_channel_message(
                    provider="whatsapp", provider_message_id=str(message["id"]),
                    provider_user_id=str(sender), conversation_id=str(sender),
                    content=str(text),
                )
                if answer:
                    await send_whatsapp(str(sender), answer)
