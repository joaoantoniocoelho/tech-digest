import threading

from app.db import get_latest_edition
from app.digest import load_digest_config, render_text_welcome
from app.email import (
    WELCOME_SUBJECT,
    render_html_welcome,
    send_email,
)
from app.log import log_event, redact_email, redact_text
from app.subscribers import unsubscribe_url


def send_welcome_email(subscriber: dict) -> bool:
    recipient = redact_email(subscriber["email"])

    try:
        config = load_digest_config()
        show_score = config.get("show_score", False)
        show_topics = config.get("show_topics", False)
        edition = get_latest_edition()
        if edition:
            edition["lookback_hours"] = config.get("lookback_hours", 24)
        link = unsubscribe_url(subscriber["unsubscribe_token"])
        result = send_email(
            subject=WELCOME_SUBJECT,
            html_body=render_html_welcome(
                edition=edition,
                show_score=show_score,
                show_topics=show_topics,
                unsubscribe_url=link,
            ),
            text=render_text_welcome(
                edition=edition,
                show_score=show_score,
                show_topics=show_topics,
                unsubscribe_url=link,
            ),
            to=[subscriber["email"]],
        )
    except Exception as error:
        log_event(
            "welcome_send",
            status="error",
            recipient=recipient,
            error=redact_text(
                f"{type(error).__name__}: {error}"
            ),
        )
        return False

    log_event(
        "welcome_send",
        status="ok",
        recipient=recipient,
        articles=len((edition or {}).get("articles") or []),
        resend_id=result.get("id", ""),
    )
    return True


def queue_welcome_email(subscriber: dict) -> threading.Thread:
    thread = threading.Thread(
        target=send_welcome_email,
        args=(subscriber,),
        name="welcome-email",
        daemon=True,
    )
    thread.start()
    return thread
