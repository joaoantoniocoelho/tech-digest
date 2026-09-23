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
# from app.telegram import send_message


def send_daily_digest():
    config = load_digest_config()

    digest = build_digest()

    articles = digest["articles"]

    if not articles:
        print("No articles selected for the digest.")
        return

    show_score = config.get(
        "show_score",
        False,
    )
    show_topics = config.get(
        "show_topics",
        False,
    )

    message = render_text_digest(
        digest=digest,
        show_score=show_score,
        show_topics=show_topics,
    )

    sent_at = datetime.now().astimezone()

    html = render_html_digest(
        digest=digest,
        show_score=show_score,
        show_topics=show_topics,
        sent_at=sent_at,
    )

    # send_message(message)
    result = send_email(
        subject=digest_subject(sent_at),
        html_body=html,
        text=message,
    )

    article_ids = [
        article["id"]
        for article in articles
    ]

    mark_articles_delivered(
        article_ids=article_ids,
    )

    print(
        f"Digest sent with "
        f"{len(articles)} articles."
    )

    print(
        f"Resend id: {result['id']}"
    )

    print(
        f"Marked {len(article_ids)} "
        f"articles as delivered."
    )


def main():
    send_daily_digest()


if __name__ == "__main__":
    main()
