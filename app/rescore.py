import argparse
from pathlib import Path

import yaml

from app.db import get_scored_articles, init_db
from app.digest import load_digest_config
from app.filters import is_blocked_title, is_launch_post
from app.scoring import IMPORTANCE_ADJUSTMENTS, calculate_relevance_score

INTERESTS_PATH = Path("config/interests.yaml")


def parse_adjustments(value: str) -> dict[int, int]:
    parts = value.split(",")

    if len(parts) != len(IMPORTANCE_ADJUSTMENTS):
        raise ValueError("importance needs four comma-separated numbers, e.g. -30,-8,5,12")

    return {level: int(part) for level, part in enumerate(parts)}


def rescore(
    articles: list[dict],
    profile: dict,
    importance_adjustments: dict[int, int],
) -> list[dict]:
    known = profile["features"]

    return [
        {
            **article,
            "new_score": calculate_relevance_score(
                # Features removed from the profile since classification are ignored.
                feature_strengths={
                    feature_id: strength
                    for feature_id, strength in article["feature_strengths"].items()
                    if feature_id in known
                },
                profile=profile,
                importance=article["importance"],
                importance_adjustments=importance_adjustments,
            ),
        }
        for article in articles
    ]


def select_edition(
    articles: list[dict],
    minimum_score: int,
    maximum_articles: int,
    config: dict,
) -> list[dict]:
    """Deterministic part of the selection: score, title rules and source caps.

    Diversity and Jev deduplication are left out, so this is an upper bound
    on what an edition would contain.
    """
    title_filters = config.get("title_filters") or {}
    source_limits = config.get("source_limits") or {}
    max_launch_posts = title_filters.get("max_launch_posts")

    ranked = sorted(
        (
            article
            for article in articles
            if article["new_score"] >= minimum_score
            and not is_blocked_title(article["title"], title_filters)
        ),
        key=lambda article: -article["new_score"],
    )
    edition: list[dict] = []
    source_counts: dict[str, int] = {}
    launch_posts = 0

    for article in ranked:
        if len(edition) >= maximum_articles:
            break

        source = article["source"]
        limit = source_limits.get(source)

        if limit is not None and source_counts.get(source, 0) >= limit:
            continue

        is_launch = is_launch_post(article["title"])

        if is_launch and max_launch_posts is not None and launch_posts >= max_launch_posts:
            continue

        edition.append(article)
        source_counts[source] = source_counts.get(source, 0) + 1
        launch_posts += is_launch

    return edition


def render_report(
    articles: list[dict],
    minimum_score: int,
    maximum_articles: int,
    config: dict,
) -> str:
    if not articles:
        return "No stored classifier answers in this window yet."

    lines = []

    for day in sorted({article["day"] for article in articles}):
        day_articles = [article for article in articles if article["day"] == day]
        passing = sum(article["new_score"] >= minimum_score for article in day_articles)
        edition = select_edition(day_articles, minimum_score, maximum_articles, config)
        lines.append(
            f"{day}: {len(day_articles)} classified, {passing} at or above {minimum_score}, "
            f"{len(edition)} selected"
        )

        for article in edition:
            marker = "sent" if article["delivered"] else "new "
            lines.append(
                f"  [{marker}] {article['new_score']:>3} (was {article['relevance_score']:>3}, "
                f"importance {article['importance']}) {article['source']}: {article['title']}"
            )

        dropped = [
            article for article in day_articles if article["delivered"] and article not in edition
        ]

        for article in dropped:
            lines.append(
                f"  [drop] {article['new_score']:>3} (was {article['relevance_score']:>3}, "
                f"importance {article['importance']}) {article['source']}: {article['title']}"
            )

        lines.append("")

    return "\n".join(lines).rstrip()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Recompute stored scores with a candidate formula and show the "
            "editions it would pick. Reads only; never calls the classifier."
        ),
    )
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument(
        "--importance",
        type=parse_adjustments,
        default=dict(IMPORTANCE_ADJUSTMENTS),
        help=(
            "Points for importance 0,1,2,3, written with an equals sign: "
            "--importance=-30,-8,5,12 (default: the current formula)."
        ),
    )
    parser.add_argument("--minimum-score", type=int)
    parser.add_argument("--maximum-articles", type=int)
    args = parser.parse_args(argv)

    init_db()
    config = load_digest_config()

    with INTERESTS_PATH.open() as file:
        profile = yaml.safe_load(file)

    minimum_score = (
        args.minimum_score if args.minimum_score is not None else config["minimum_score"]
    )
    maximum_articles = args.maximum_articles or config["maximum_articles"]

    print(
        render_report(
            rescore(get_scored_articles(args.days), profile, args.importance),
            minimum_score,
            maximum_articles,
            config,
        )
    )


if __name__ == "__main__":
    main()
