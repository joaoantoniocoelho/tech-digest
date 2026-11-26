import argparse
from datetime import datetime
from pathlib import Path

import yaml

from app.classifier import is_duplicate_story
from app.db import (
    get_digest_candidates,
    get_recently_delivered,
    init_db,
)
from app.email import (
    FEATURED_LABEL,
    NOTE_CTA,
    NOTE_LABEL,
    SHARE_LINE,
    SHARE_LINK_LABEL,
    SHARE_TITLE,
    WELCOME_FIRST,
    WELCOME_LATEST,
    WELCOME_LINES,
    digest_share_links,
)
from app.filters import is_blocked_title, is_launch_post
from app.log import log_event
from app.notes import note_paragraphs

CONFIG_PATH = Path("config/digest.yaml")
INTERESTS_PATH = Path("config/interests.yaml")


def load_digest_config() -> dict:
    with CONFIG_PATH.open() as file:
        return yaml.safe_load(file)


def load_topic_groups() -> dict:
    """Map each feature label to its editorial group."""
    with INTERESTS_PATH.open() as file:
        features = yaml.safe_load(file)["features"]

    return {
        feature["label"]: feature["group"] for feature in features.values() if feature.get("group")
    }


def _primary_group(
    article: dict,
    topic_groups: dict,
) -> str | None:
    # Topics are ordered by score contribution, so the first one
    # is what the article is mainly about.
    topics = article.get("topics") or []

    if not topics:
        return None

    return topic_groups.get(topics[0])


def _diversity_score(
    article: dict,
    group: str | None,
    group_counts: dict,
    diversity: dict,
) -> float:
    if group is None:
        return article["relevance_score"]

    free = diversity.get("free_per_group", {}).get(group, 1)
    repeats = max(0, group_counts.get(group, 0) - free + 1)

    return article["relevance_score"] - diversity.get("penalty", 0) * repeats


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

    minimum_articles = config.get("minimum_articles", 0)
    fallback_score = min(config.get("fallback_minimum_score", minimum_score), minimum_score)

    candidates = get_digest_candidates(
        lookback_hours=lookback_hours,
        minimum_score=fallback_score,
    )

    # Diversity only reorders candidates that already passed
    # minimum_score; it never pulls in a weaker article.
    diversity = config.get("diversity") or {}
    topic_groups = load_topic_groups() if diversity.get("penalty") else {}
    groups = {id(candidate): _primary_group(candidate, topic_groups) for candidate in candidates}
    group_counts: dict[str | None, int] = {}

    title_filters = config.get("title_filters") or {}
    source_limits = config.get("source_limits") or {}
    max_launch_posts = title_filters.get("max_launch_posts")
    dedup_days = config.get("dedup_lookback_days") or 0
    recently_delivered = get_recently_delivered(dedup_days) if dedup_days else []

    articles: list[dict] = []
    skipped_title = 0
    skipped_semantic = 0
    skipped_source = 0
    skipped_launch = 0
    source_counts: dict[str, int] = {}
    launch_posts = 0
    allowed = [
        candidate
        for candidate in candidates
        if not is_blocked_title(candidate["title"], title_filters)
    ]
    skipped_blocked = len(candidates) - len(allowed)
    remaining = [c for c in allowed if c["relevance_score"] >= minimum_score]
    fallback = [c for c in allowed if c["relevance_score"] < minimum_score]
    limit = maximum_articles
    used_fallback = False

    while len(articles) < limit:
        if not remaining:
            # Weak day: top up to the floor from below the cutoff, so a
            # higher cutoff never costs an edition the old one would send.
            if used_fallback or not fallback or len(articles) >= minimum_articles:
                break

            remaining, limit, used_fallback = fallback, minimum_articles, True
            continue

        # max() keeps the first of equal scores, preserving the
        # database order (score, then recency) on ties.
        candidate = max(
            remaining,
            key=lambda article: _diversity_score(
                article,
                groups[id(article)],
                group_counts,
                diversity,
            ),
        )
        remaining.remove(candidate)

        source = candidate.get("source") or ""
        source_limit = source_limits.get(source)

        if source_limit is not None and source_counts.get(source, 0) >= source_limit:
            skipped_source += 1
            continue

        is_launch = is_launch_post(candidate["title"])

        if is_launch and max_launch_posts is not None and launch_posts >= max_launch_posts:
            skipped_launch += 1
            continue

        title = " ".join(candidate["title"].split()).casefold()

        if any(title == " ".join(article["title"].split()).casefold() for article in articles):
            skipped_title += 1
            continue

        known_stories = articles + recently_delivered

        if known_stories and is_duplicate_story(candidate, known_stories):
            skipped_semantic += 1
            continue

        articles.append(candidate)
        source_counts[source] = source_counts.get(source, 0) + 1
        launch_posts += is_launch

        group = groups[id(candidate)]

        if group is not None:
            group_counts[group] = group_counts.get(group, 0) + 1

    log_event(
        "digest_build",
        candidates=len(candidates),
        selected=len(articles),
        skipped_title=skipped_title,
        skipped_semantic=skipped_semantic,
        skipped_blocked=skipped_blocked,
        skipped_source=skipped_source,
        skipped_launch=skipped_launch,
        below_cutoff=sum(article["relevance_score"] < minimum_score for article in articles),
        groups=",".join(f"{group}:{count}" for group, count in sorted(group_counts.items())),
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
    sent_at: datetime | None = None,
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
        lines.append("No articles passed the relevance threshold.")
        if unsubscribe_url:
            lines.append("")
            lines.append(f"Unsubscribe: {unsubscribe_url}")

        return "\n".join(lines)

    lines.append(f"{len(articles)} article" + ("s" if len(articles) != 1 else "") + " selected")

    lines.append("")

    note = digest.get("note")

    if note:
        lines.extend([NOTE_LABEL, note["title"], ""])
        for paragraph in note_paragraphs(note["intro"]):
            lines.extend([paragraph, ""])
        if note.get("url"):
            lines.extend([f"{NOTE_CTA}: {note['url']}", ""])
        lines.extend(["=" * 60, ""])

    for index, article in enumerate(
        articles,
        start=1,
    ):
        if index == 1 and len(articles) > 1:
            lines.append(FEATURED_LABEL)

        title_line = f"{index}. {article['title']}"

        if show_score:
            title_line += f" [{article['relevance_score']}]"

        lines.append(title_line)
        lines.append("")

        why_interesting = (article["why_interesting"] or "").strip()

        if why_interesting:
            lines.append("Why: " + why_interesting)
            lines.append("")

        lines.append(f"Source: {article['source']}")

        if show_topics and article["topics"]:
            lines.append("Topics: " + ", ".join(article["topics"]))

        lines.append(article["url"])

        if index != len(articles):
            lines.append("")
            lines.append("-" * 60)
            lines.append("")

    if sent_at:
        links = digest_share_links(sent_at)
        lines.extend(
            [
                "",
                "",
                SHARE_TITLE,
                SHARE_LINE,
                f"{SHARE_LINK_LABEL}: {links['link']}",
                f"X: {links['x']}",
                f"LinkedIn: {links['linkedin']}",
            ]
        )

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
    edition = edition or {}
    articles = edition.get("articles") or []

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


def sample_note() -> dict:
    return {
        "title": "The agent shouldn't judge its own code",
        "url": "https://www.joaoac.com/blog/the-agent-shouldnt-judge-its-own-code",
        "intro": (
            "Placeholder intro for the preview. The real note is written and approved "
            "by hand, in the author's own words.\n\n"
            "A second short paragraph shows how the spacing looks before the "
            "curated reads start."
        ),
    }


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
    parser.add_argument(
        "--note",
        action="store_true",
        help="Include a sample authorial note.",
    )
    args = parser.parse_args(argv)

    config = load_digest_config()
    show_score = config.get("show_score", True)
    show_topics = config.get("show_topics", True)

    digest = sample_digest() if args.sample else build_digest()

    if args.note:
        digest = {**digest, "note": sample_note()}

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
        unsubscribe_url=("https://digest.joaoac.com/unsubscribe/preview"),
    )
    path = Path(args.html)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html_digest, encoding="utf-8")
    print(f"HTML preview written to {path}")


if __name__ == "__main__":
    main()
