"""Authenticated Telegram and WhatsApp webhooks."""

from __future__ import annotations

import os
import secrets

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse

from channels.service import process_telegram, process_whatsapp, verify_whatsapp_signature

router = APIRouter(prefix="/v1/channels", tags=["channels"])


@router.post("/telegram")
async def telegram_webhook(
    payload: dict, background: BackgroundTasks,
    secret: str | None = Header(None, alias="X-Telegram-Bot-Api-Secret-Token"),
):
    expected = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")
    if not expected or not secret or not secrets.compare_digest(secret, expected):
        raise HTTPException(status_code=401, detail="Invalid Telegram webhook")
    background.add_task(process_telegram, payload)
    return {"ok": True}


@router.get("/whatsapp", response_class=PlainTextResponse)
async def verify_whatsapp_webhook(
    mode: str = Query("", alias="hub.mode"),
    token: str = Query("", alias="hub.verify_token"),
    challenge: str = Query("", alias="hub.challenge"),
):
    if mode != "subscribe" or not os.getenv("WHATSAPP_VERIFY_TOKEN") or token != os.getenv("WHATSAPP_VERIFY_TOKEN"):
        raise HTTPException(status_code=403, detail="Invalid WhatsApp verification")
    return challenge


@router.post("/whatsapp")
async def whatsapp_webhook(
    request: Request, background: BackgroundTasks,
    signature: str | None = Header(None, alias="X-Hub-Signature-256"),
):
    body = await request.body()
    if not verify_whatsapp_signature(body, signature):
        raise HTTPException(status_code=401, detail="Invalid WhatsApp webhook")
    payload = await request.json()
    background.add_task(process_whatsapp, payload)
    return {"ok": True}
