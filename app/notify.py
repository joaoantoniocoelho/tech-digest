import os

from app.log import log_event, redact_email, redact_text
from app.telegram import send_message


_JOB_LABELS = {
    "collect": "Busca de notícias",
    "process": "Classificação",
    "send": "Envio do digest",
}

_SUBSCRIBER_LABELS = {
    "created": "Cadastro",
    "reactivated": "Cadastro reativado",
    "unsubscribed": "Descadastro",
}


def _configured() -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    return bool(token and chat_id)


def notify(text: str) -> None:
    if not _configured():
        return

    try:
        send_message(text)
    except Exception as error:
        log_event(
            "telegram_notify",
            status="error",
            error=redact_text(
                f"{type(error).__name__}: {error}"
            ),
        )


def notify_job_start(job: str) -> None:
    label = _JOB_LABELS.get(job, job)
    notify(f"{label} começou")


def notify_job_finish(job: str, status: str, **fields) -> None:
    label = _JOB_LABELS.get(job, job)

    if status == "error":
        error = fields.get("error") or "erro"
        notify(f"{label} erro · {error}")
        return

    if job == "collect":
        notify(f"{label} ok · {fields.get('new_articles', 0)} novas")
        return

    if job == "process":
        notify(
            f"{label} ok · {fields.get('eligible', 0)} elegíveis · "
            f"{fields.get('processed', 0)} classificadas · "
            f"{fields.get('failed', 0)} falhas"
        )
        return

    if job == "send":
        notify(
            f"{label} ok · {fields.get('articles', 0)} artigos · "
            f"{fields.get('sent', 0)} enviados · "
            f"{fields.get('failed_sends', 0)} falhas"
        )
        return

    notify(f"{label} {status}")


def notify_subscriber(action: str, email: str) -> None:
    label = _SUBSCRIBER_LABELS.get(action, action)
    notify(f"{label} · {redact_email(email)}")
