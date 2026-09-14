"""Minimal Telegram Bot API client -- just enough to reply to a chat.

No polling here on purpose: app.py runs as a Vercel serverless function
(no long-running process to poll with), so the integration is a webhook
(see app.py's POST /telegram/webhook) that pushes updates to us instead.
"""
import os

import httpx

from logging_config import logger

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_WEBHOOK_SECRET = os.environ.get("TELEGRAM_WEBHOOK_SECRET")

_API_BASE = "https://api.telegram.org"

# Telegram rejects messages over 4096 UTF-16 code units; truncate defensively
# rather than let sendMessage fail outright on a long agent reply.
_MAX_MESSAGE_LENGTH = 4096


def is_configured() -> bool:
    return bool(TELEGRAM_BOT_TOKEN)


def extract_incoming_text(update: dict) -> tuple[int, str] | None:
    """Pull (chat_id, text) out of a Telegram update, or None if there's
    nothing to reply to (non-text message, channel post, join event, ...).
    """
    message = update.get("message") or update.get("edited_message")
    if not message or "text" not in message:
        return None
    return message["chat"]["id"], message["text"]


async def send_message(chat_id: int, text: str) -> None:
    if not TELEGRAM_BOT_TOKEN:
        logger.error("telegram: TELEGRAM_BOT_TOKEN not configured, cannot reply to chat_id=%s", chat_id)
        return

    url = f"{_API_BASE}/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    # Plain text on purpose: agent replies use GitHub-flavored Markdown
    # (tables, "**bold**", "###" headings) that doesn't survive Telegram's
    # "Markdown" parse mode -- unmatched "*"/"_" pairs make sendMessage fail
    # outright with a 400. Rendering the raw symbols is worse-looking but
    # never breaks delivery.
    payload = {
        "chat_id": chat_id,
        "text": text[:_MAX_MESSAGE_LENGTH],
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, json=payload)
        if response.status_code != 200:
            logger.error(
                "telegram: sendMessage failed chat_id=%s status=%s body=%s",
                chat_id, response.status_code, response.text[:300],
            )
    except httpx.HTTPError:
        logger.exception("telegram: sendMessage request failed chat_id=%s", chat_id)
