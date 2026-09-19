from pathlib import Path

import yaml

from app.db import (
    get_digest_candidates,
    init_db,
)


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

    articles = get_digest_candidates(
        lookback_hours=lookback_hours,
        minimum_score=minimum_score,
        maximum_articles=maximum_articles,
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
) -> str:
    articles = digest["articles"]
    lookback_hours = digest["lookback_hours"]

    lines = [
        "Tech Digest",
        f"Best articles from the last {lookback_hours} hours",
        "",
    ]

    if not articles:
        lines.append(
            "No articles passed the relevance threshold."
        )

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

    return "\n".join(lines)


def main():
    config = load_digest_config()

    digest = build_digest()

    output = render_text_digest(
        digest=digest,
        show_score=config.get(
            "show_score",
            True,
        ),
        show_topics=config.get(
            "show_topics",
            True,
        ),
    )

    print()
    print(output)
    print()


if __name__ == "__main__":
    main()
