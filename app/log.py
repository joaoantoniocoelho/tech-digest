import os
import re
import threading


_EMAIL_RE = re.compile(
    r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"
)
_UNSUBSCRIBE_RE = re.compile(
    r"/unsubscribe/[A-Za-z0-9_\-]{10,}"
)
_SECRET_ENV_NAMES = (
    "TYPESAFE_API_KEY",
    "RESEND_API_KEY",
    "DIGEST_JOB_TOKEN",
    "TELEGRAM_BOT_TOKEN",
)
_PRINT_LOCK = threading.Lock()


def redact_text(value: str) -> str:
    text = str(value).replace("\n", " ").replace("\r", " ")

    for name in _SECRET_ENV_NAMES:
        secret = os.getenv(name, "").strip()
        if secret:
            text = text.replace(secret, "[redacted]")

    text = _EMAIL_RE.sub("[redacted-email]", text)
    text = _UNSUBSCRIBE_RE.sub(
        "/unsubscribe/[redacted]",
        text,
    )
    return text


def redact_email(email: str) -> str:
    local, separator, domain = email.partition("@")
    if not separator or not domain:
        return "[redacted-email]"

    prefix = local[:1]
    return f"{prefix}***@{domain}"


def _format_field(value) -> str:
    text = redact_text(value)
    if (
        not text
        or any(character.isspace() for character in text)
        or any(character in text for character in '="')
    ):
        escaped = text.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return text


def log_event(event: str, **fields) -> None:
    parts = [f"event={_format_field(event)}"]
    for key, value in fields.items():
        parts.append(f"{key}={_format_field(value)}")
    with _PRINT_LOCK:
        print(" ".join(parts), flush=True)
