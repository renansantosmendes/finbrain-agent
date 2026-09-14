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


def session_id_for(user_id: int) -> str:
    """The checkpointer thread_id for a Telegram user's conversation."""
    return f"telegram-{user_id}"


def _sender_id(message: dict, chat_id: int) -> int:
    """The Telegram user who actually sent `message` -- conversation memory
    (the checkpointer's thread_id) is keyed on this, not on chat_id.

    They coincide in a 1:1 chat with the bot, which is the common case, but
    diverge for a group (every member shares one chat_id -- keying on it
    would merge everyone's history into a single session) and for a channel
    post (no `from` at all, only `sender_chat`). Falling back to chat_id
    keeps those edge cases working instead of crashing on a missing key.
    """
    sender = message.get("from")
    if isinstance(sender, dict) and isinstance(sender.get("id"), int):
        return sender["id"]
    return chat_id


def extract_incoming_text(update: dict) -> tuple[int, int, str] | None:
    """Pull (chat_id, user_id, text) out of a Telegram update, or None if
    there's nothing to reply to (non-text message, channel post, join
    event, ...).
    """
    if not isinstance(update, dict):
        return None
    message = update.get("message") or update.get("edited_message")
    if not isinstance(message, dict) or "text" not in message:
        return None
    chat_id = message["chat"]["id"]
    return chat_id, _sender_id(message, chat_id), message["text"]


def extract_incoming_document(update: dict) -> tuple[int, int, dict, str] | None:
    """Pull (chat_id, user_id, document, caption) out of a Telegram update
    carrying a file attachment (e.g. a PDF invoice), or None if there isn't
    one. `document` is Telegram's raw dict (file_id, file_name, mime_type, ...).
    """
    if not isinstance(update, dict):
        return None
    message = update.get("message") or update.get("edited_message")
    if not isinstance(message, dict):
        return None
    document = message.get("document")
    if not isinstance(document, dict):
        return None
    chat_id = message["chat"]["id"]
    return chat_id, _sender_id(message, chat_id), document, message.get("caption") or ""


async def download_file(file_id: str) -> bytes | None:
    """Resolve a file_id to its bytes via getFile + the file download
    endpoint (two round trips -- that's how the Bot API works, no shortcut).
    Returns None on any failure; callers turn that into a chat message.
    """
    if not TELEGRAM_BOT_TOKEN:
        logger.error("telegram: TELEGRAM_BOT_TOKEN not configured, cannot download file_id=%s", file_id)
        return None

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(
                f"{_API_BASE}/bot{TELEGRAM_BOT_TOKEN}/getFile",
                params={"file_id": file_id},
            )
            resp.raise_for_status()
            file_path = resp.json()["result"]["file_path"]

            file_resp = await client.get(f"{_API_BASE}/file/bot{TELEGRAM_BOT_TOKEN}/{file_path}")
            file_resp.raise_for_status()
            return file_resp.content
    except (httpx.HTTPError, KeyError, ValueError):
        logger.exception("telegram: failed to download file_id=%s", file_id)
        return None


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
