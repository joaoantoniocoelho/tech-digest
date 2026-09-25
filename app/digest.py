import argparse
from pathlib import Path

import yaml

from app.classifier import is_duplicate_story
from app.db import (
    get_digest_candidates,
    init_db,
)
from app.email import (
    WELCOME_FIRST,
    WELCOME_LATEST,
    WELCOME_LINES,
)
from app.log import log_event


CONFIG_PATH = Path("config/digest.yaml")


def load_digest_config() -> dict:
    with CONFIG_PATH.open() as file:
        return yaml.safe_load(file)


def get_processing_lookback_hours() -> int:
    config = load_digest_config()

    return int(
        config.get(
            "processing_lookback_hours",
            24,
        )
    )


def build_digest() -> dict:
    init_db()

    config = load_digest_config()

    lookback_hours = config["lookback_hours"]
    minimum_score = config["minimum_score"]
    maximum_articles = config["maximum_articles"]

    candidates = get_digest_candidates(
        lookback_hours=lookback_hours,
        minimum_score=minimum_score,
    )

    articles = []
    skipped_title = 0
    skipped_semantic = 0

    for candidate in candidates:
        if len(articles) >= maximum_articles:
            break

        title = " ".join(candidate["title"].split()).casefold()

        if any(
            title == " ".join(article["title"].split()).casefold()
            for article in articles
        ):
            skipped_title += 1
            continue

        if articles and is_duplicate_story(candidate, articles):
            skipped_semantic += 1
            continue

        articles.append(candidate)

    log_event(
        "digest_build",
        candidates=len(candidates),
        selected=len(articles),
        skipped_title=skipped_title,
        skipped_semantic=skipped_semantic,
    )

    return {
        "lookback_hours": lookback_hours,
        "minimum_score": minimum_score,
        "articles": articles,
    }


def render_text_digest(
    digest: dict,
    show_score: bool = True,
    show_topics: bool = True,
    unsubscribe_url: str | None = None,
) -> str:
    articles = digest["articles"]
    lookback_hours = digest["lookback_hours"]

    lines = [
        "João Coelho Tech Digest",
        f"Best articles from the last {lookback_hours} hours",
        "",
    ]

    if not articles:
        lines.append(
            "No articles passed the relevance threshold."
        )
        if unsubscribe_url:
            lines.append("")
            lines.append(f"Unsubscribe: {unsubscribe_url}")

        return "\n".join(lines)

    lines.append(
        f"{len(articles)} article"
        + ("s" if len(articles) != 1 else "")
        + " selected"
    )

    lines.append("")

    for index, article in enumerate(
        articles,
        start=1,
    ):
        title_line = f"{index}. {article['title']}"

        if show_score:
            title_line += (
                f" [{article['relevance_score']}]"
            )

        lines.append(title_line)
        lines.append("")

        why_interesting = (
            article["why_interesting"] or ""
        ).strip()

        if why_interesting:
            lines.append(
                "Why: " + why_interesting
            )
            lines.append("")

        lines.append(
            f"Source: {article['source']}"
        )

        if show_topics and article["topics"]:
            lines.append(
                "Topics: "
                + ", ".join(article["topics"])
            )

        lines.append(article["url"])

        if index != len(articles):
            lines.append("")
            lines.append("-" * 60)
            lines.append("")

    if unsubscribe_url:
        lines.append("")
        lines.append(f"Unsubscribe: {unsubscribe_url}")

    return "\n".join(lines)


def render_text_welcome(
    edition: dict | None,
    show_score: bool = False,
    show_topics: bool = False,
    unsubscribe_url: str | None = None,
) -> str:
    lines = [
        "Welcome to João Coelho Tech Digest",
        "",
        *WELCOME_LINES,
        "",
    ]
    articles = (edition or {}).get("articles") or []

    if not articles:
        lines.append(WELCOME_FIRST)
        if unsubscribe_url:
            lines.append("")
            lines.append(f"Unsubscribe: {unsubscribe_url}")
        return "\n".join(lines)

    lines.append(WELCOME_LATEST)
    lines.append("")
    lines.append("=" * 60)
    lines.append("")
    lines.append(
        render_text_digest(
            digest={
                "lookback_hours": edition.get("lookback_hours", 24),
                "articles": articles,
            },
            show_score=show_score,
            show_topics=show_topics,
            unsubscribe_url=unsubscribe_url,
        )
    )
    return "\n".join(lines)


def sample_digest() -> dict:
    return {
        "lookback_hours": 24,
        "articles": [
            {
                "title": "Formal methods with Hillel Wayne",
                "url": "https://newsletter.pragmaticengineer.com/p/formal-methods",
                "source": "The Pragmatic Engineer",
                "published_at": "2026-09-22T15:00:00+00:00",
                "why_interesting": "Software engineering",
                "relevance_score": 91,
                "topics": ["Formal methods"],
            },
            {
                "title": "The last six months in LLMs, in five minutes",
                "url": "https://simonwillison.net/2026/Sep/22/llms/",
                "source": "Simon Willison",
                "published_at": "2026-09-22T18:30:00+00:00",
                "why_interesting": "AI models",
                "relevance_score": 88,
                "topics": ["Language models"],
            },
            {
                "title": "What changed in SQLite this year",
                "url": "https://sqlite.org/changes.html",
                "source": "SQLite",
                "published_at": "2026-09-23T11:00:00+00:00",
                "why_interesting": "Databases · Systems",
                "relevance_score": 76,
                "topics": ["Databases"],
            },
        ],
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Preview the daily Tech Digest.",
    )
    parser.add_argument(
        "--html",
        metavar="PATH",
        help="Write an HTML preview to this file.",
    )
    parser.add_argument(
        "--sample",
        action="store_true",
        help="Use sample articles instead of the database.",
    )
    args = parser.parse_args(argv)

    config = load_digest_config()
    show_score = config.get("show_score", True)
    show_topics = config.get("show_topics", True)

    if args.sample:
        digest = sample_digest()
    else:
        digest = build_digest()

    output = render_text_digest(
        digest=digest,
        show_score=show_score,
        show_topics=show_topics,
    )

    print()
    print(output)
    print()

    if not args.html:
        return

    from datetime import datetime

    from app.email import render_html_digest

    html_digest = render_html_digest(
        digest=digest,
        show_score=show_score,
        show_topics=show_topics,
        sent_at=datetime.now().astimezone(),
        unsubscribe_url=(
            "https://digest.joaoac.com/unsubscribe/preview"
        ),
    )
    path = Path(args.html)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html_digest, encoding="utf-8")
    print(f"HTML preview written to {path}")


if __name__ == "__main__":
    main()
