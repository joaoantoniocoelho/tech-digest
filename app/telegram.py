import os

import httpx


TELEGRAM_API_BASE = "https://api.telegram.org"
TELEGRAM_MESSAGE_LIMIT = 4096
TELEGRAM_TIMEOUT_SECONDS = 15.0


def _get_bot_token() -> str:
    token = os.getenv("TELEGRAM_BOT_TOKEN")

    if not token:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is not configured"
        )

    return token


def _get_chat_id() -> str:
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not chat_id:
        raise RuntimeError(
            "TELEGRAM_CHAT_ID is not configured"
        )

    return chat_id


def _api_url(method: str) -> str:
    token = _get_bot_token()

    return (
        f"{TELEGRAM_API_BASE}"
        f"/bot{token}/{method}"
    )


def get_updates() -> list:
    response = httpx.get(
        _api_url("getUpdates"),
        timeout=TELEGRAM_TIMEOUT_SECONDS,
    )

    response.raise_for_status()

    payload = response.json()

    if not payload.get("ok"):
        raise RuntimeError(
            f"Telegram API error: {payload}"
        )

    return payload.get("result", [])


def find_chat_ids() -> list[dict]:
    updates = get_updates()

    chats = {}

    for update in updates:
        message = (
            update.get("message")
            or update.get("edited_message")
            or update.get("channel_post")
        )

        if not message:
            continue

        chat = message.get("chat")

        if not chat:
            continue

        chat_id = str(chat["id"])

        chats[chat_id] = {
            "id": chat_id,
            "type": chat.get("type"),
            "username": chat.get("username"),
            "first_name": chat.get("first_name"),
            "last_name": chat.get("last_name"),
            "title": chat.get("title"),
        }

    return list(chats.values())


def _split_message(text: str) -> list[str]:
    if len(text) <= TELEGRAM_MESSAGE_LIMIT:
        return [text]

    chunks = []
    remaining = text

    while remaining:
        if len(remaining) <= TELEGRAM_MESSAGE_LIMIT:
            chunks.append(remaining)
            break

        split_at = remaining.rfind(
            "\n",
            0,
            TELEGRAM_MESSAGE_LIMIT,
        )

        if split_at <= 0:
            split_at = TELEGRAM_MESSAGE_LIMIT

        chunk = remaining[:split_at].rstrip()

        chunks.append(chunk)

        remaining = remaining[split_at:].lstrip()

    return chunks


def send_message(
    text: str,
    chat_id: str | None = None,
):
    if chat_id is None:
        chat_id = _get_chat_id()

    messages = _split_message(text)

    results = []

    for message in messages:
        response = httpx.post(
            _api_url("sendMessage"),
            json={
                "chat_id": chat_id,
                "text": message,
                "disable_web_page_preview": True,
            },
            timeout=TELEGRAM_TIMEOUT_SECONDS,
        )

        response.raise_for_status()

        payload = response.json()

        if not payload.get("ok"):
            raise RuntimeError(
                f"Telegram API error: {payload}"
            )

        results.append(payload["result"])

    return results
