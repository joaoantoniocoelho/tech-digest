from app.db import mark_articles_delivered
from app.digest import (
    build_digest,
    load_digest_config,
    render_text_digest,
)
from app.telegram import send_message


def send_daily_digest():
    config = load_digest_config()

    digest = build_digest()

    articles = digest["articles"]

    if not articles:
        print("No articles selected for the digest.")
        return

    message = render_text_digest(
        digest=digest,
        show_score=config.get(
            "show_score",
            False,
        ),
        show_topics=config.get(
            "show_topics",
            False,
        ),
    )

    send_message(message)

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
        f"Marked {len(article_ids)} "
        f"articles as delivered."
    )


def main():
    send_daily_digest()


if __name__ == "__main__":
    main()
