import hashlib
import hmac
import json
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from channels import service
from api.routes.channels import router


USER_ID = "00000000-0000-0000-0000-000000000010"


def test_channel_identity_uses_immutable_id_and_valid_polytrade_uuid(monkeypatch):
    monkeypatch.setenv("TELEGRAM_USER_MAP", json.dumps({"12345": USER_ID}))
    assert service.linked_user("telegram", "12345") == USER_ID
    assert service.linked_user("telegram", "raslen10") is None


def test_whatsapp_signature_is_verified_over_raw_body(monkeypatch):
    body = b'{"entry":[]}'
    monkeypatch.setenv("WHATSAPP_APP_SECRET", "test-secret")
    digest = hmac.new(b"test-secret", body, hashlib.sha256).hexdigest()
    assert service.verify_whatsapp_signature(body, f"sha256={digest}") is True
    assert service.verify_whatsapp_signature(body + b" ", f"sha256={digest}") is False


@pytest.mark.asyncio
async def test_channel_message_uses_stable_thread_and_provider_idempotency(monkeypatch):
    monkeypatch.setenv("TELEGRAM_USER_MAP", json.dumps({"12345": USER_ID}))
    chat = AsyncMock()

    async def stream_message(**kwargs):
        from chat.events import ChatEvent, MESSAGE_COMPLETED
        yield ChatEvent(MESSAGE_COMPLETED, {"message": {"content": "answer"}})

    chat.stream_message = stream_message
    monkeypatch.setattr(service, "get_chat_service", lambda: chat)
    first = await service.run_channel_message(
        provider="telegram", provider_message_id="update-1",
        provider_user_id="12345", conversation_id="chat-1", content="question",
    )
    second_thread = service.channel_thread_id("telegram", "chat-1", USER_ID)

    assert first == "answer"
    assert second_thread == service.channel_thread_id("telegram", "chat-1", USER_ID)


@pytest.mark.asyncio
async def test_unlinked_channel_sender_never_reaches_chat(monkeypatch):
    monkeypatch.setenv("TELEGRAM_USER_MAP", "{}")
    chat = AsyncMock()
    monkeypatch.setattr(service, "get_chat_service", lambda: chat)
    result = await service.run_channel_message(
        provider="telegram", provider_message_id="1", provider_user_id="999",
        conversation_id="999", content="question",
    )
    assert result is None
    chat.stream_message.assert_not_called()


def test_webhooks_fail_closed_and_whatsapp_verification_works(monkeypatch):
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "telegram-secret")
    monkeypatch.setenv("TELEGRAM_USER_MAP", "{}")
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "verify-secret")
    monkeypatch.setenv("WHATSAPP_APP_SECRET", "app-secret")

    assert client.post("/v1/channels/telegram", json={}).status_code == 401
    assert client.post(
        "/v1/channels/telegram", json={},
        headers={"X-Telegram-Bot-Api-Secret-Token": "telegram-secret"},
    ).status_code == 200
    assert client.get(
        "/v1/channels/whatsapp",
        params={"hub.mode": "subscribe", "hub.verify_token": "verify-secret",
                "hub.challenge": "challenge-value"},
    ).text == "challenge-value"
    assert client.post("/v1/channels/whatsapp", json={}).status_code == 401
