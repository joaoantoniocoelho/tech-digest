from datetime import datetime

from app.db import mark_articles_delivered
from app.digest import (
    build_digest,
    load_digest_config,
    render_text_digest,
)
from app.email import (
    digest_subject,
    render_html_digest,
    send_email,
)
from app.log import log_event, redact_email, redact_text
from app.subscribers import (
    list_active_subscribers,
    unsubscribe_url,
)
# from app.telegram import send_message


def send_daily_digest() -> dict:
    config = load_digest_config()
    digest = build_digest()
    articles = digest["articles"]
    summary = {
        "articles": len(articles),
        "subscribers": 0,
        "sent": 0,
        "failed_sends": 0,
    }

    if not articles:
        print("No articles selected for the digest.")
        log_event(
            "digest_send",
            status="skipped",
            reason="no_articles",
        )
        return summary

    subscribers = list_active_subscribers()
    summary["subscribers"] = len(subscribers)

    if not subscribers:
        print("No active subscribers. Digest was not sent.")
        log_event(
            "digest_send",
            status="skipped",
            reason="no_subscribers",
            articles=len(articles),
        )
        return summary

    show_score = config.get("show_score", False)
    show_topics = config.get("show_topics", False)
    sent_at = datetime.now().astimezone()
    subject = digest_subject(sent_at)
    sent = 0
    failed = 0

    for subscriber in subscribers:
        recipient = redact_email(subscriber["email"])
        link = unsubscribe_url(subscriber["unsubscribe_token"])
        try:
            # send_message(message)
            result = send_email(
                subject=subject,
                html_body=render_html_digest(
                    digest=digest,
                    show_score=show_score,
                    show_topics=show_topics,
                    sent_at=sent_at,
                    unsubscribe_url=link,
                ),
                text=render_text_digest(
                    digest=digest,
                    show_score=show_score,
                    show_topics=show_topics,
                    unsubscribe_url=link,
                ),
                to=[subscriber["email"]],
            )
        except Exception as error:
            failed += 1
            log_event(
                "email_send",
                status="error",
                recipient=recipient,
                error=redact_text(
                    f"{type(error).__name__}: {error}"
                ),
            )
            continue

        sent += 1
        log_event(
            "email_send",
            status="ok",
            recipient=recipient,
            resend_id=result.get("id", ""),
        )

    summary["sent"] = sent
    summary["failed_sends"] = failed
    log_event(
        "digest_send",
        status="complete",
        articles=len(articles),
        subscribers=len(subscribers),
        sent=sent,
        failed=failed,
    )

    if sent:
        article_ids = [
            article["id"]
            for article in articles
        ]
        mark_articles_delivered(article_ids=article_ids)
        print(f"Digest sent with {len(articles)} articles.")
        print(f"Emails sent: {sent}. Failed: {failed}.")
        print(
            f"Marked {len(article_ids)} articles as delivered."
        )
        return summary

    raise RuntimeError(
        f"Digest email failed for all {failed} subscribers"
    )


def main():
    from app.pipeline import run_cli

    run_cli("send")


if __name__ == "__main__":
    main()
