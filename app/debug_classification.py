import argparse
import sys

import yaml

from app.classifier import (
    IMPORTANCE_ID,
    evaluate_article,
)
from app.content import fetch_article_content
from app.db import (
    get_article_by_id,
    get_article_by_url,
    init_db,
)
from app.processor import (
    MIN_FEED_EXCERPT_LENGTH,
    _prepare_classification_content,
    _sanitize_error,
)


def _load_profile() -> dict:
    with open("config/interests.yaml") as file:
        return yaml.safe_load(file)


def _article_content(article: dict) -> str:
    content = fetch_article_content(
        article["url"]
    )

    if content:
        return _prepare_classification_content(
            content
        )

    excerpt = (article.get("feed_excerpt") or "").strip()

    if len(excerpt) >= MIN_FEED_EXCERPT_LENGTH:
        print(
            "Article extraction failed; "
            f"using RSS excerpt ({len(excerpt)} characters)"
        )
        return excerpt

    raise ValueError(
        "Could not extract article content "
        "and no usable RSS excerpt was available"
    )


def _resolve_article(args) -> dict:
    init_db()

    if args.article_id is not None:
        article = get_article_by_id(
            args.article_id
        )

        if article is None:
            raise ValueError(
                f"No article with id {args.article_id}"
            )

        return article

    article = get_article_by_url(args.url)

    if article is not None:
        return article

    return {
        "id": None,
        "source": None,
        "title": args.url,
        "url": args.url,
        "feed_excerpt": None,
    }


def _probability_lines(answer) -> list[str]:
    probabilities = getattr(
        answer,
        "probabilities",
        None,
    ) or {}

    lines = ["  probabilities:"]

    items = []

    for key, value in probabilities.items():
        items.append((key, value))

    def sort_key(item):
        key = item[0]

        try:
            return (0, int(key))
        except (TypeError, ValueError):
            return (1, str(key))

    items.sort(key=sort_key)

    if not items:
        lines.append("    (none)")
        return lines

    for key, value in items:
        lines.append(
            f"    {key}: {value:.2f}"
        )

    return lines


def _format_feature_block(
    feature_id: str,
    label: str,
    answer,
    discrete: int,
) -> str:
    confidence = getattr(
        answer,
        "confidence",
        None,
    )

    lines = [
        feature_id,
        f"  label: {label}",
        f"  raw score: {answer.score:.2f}",
    ]

    lines.extend(_probability_lines(answer))

    if isinstance(confidence, (int, float)):
        lines.append(
            f"  confidence: {confidence:.2f}"
        )

    lines.append(f"  discrete: {discrete}")

    return "\n".join(lines)


def _print_debug(
    article: dict,
    result: dict,
    profile: dict,
):
    features = profile["features"]

    print()
    print(f"Article: {article['title']}")
    print()

    answers = result["answers"]

    for feature_id, feature in features.items():
        print(
            _format_feature_block(
                feature_id=feature_id,
                label=feature["label"],
                answer=answers[feature_id],
                discrete=result[
                    "feature_strengths"
                ][feature_id],
            )
        )
        print()

    importance_answer = answers[IMPORTANCE_ID]

    print("Importance:")
    print(
        f"  raw score: {importance_answer.score:.2f}"
    )
    print(
        "\n".join(
            _probability_lines(
                importance_answer
            )
        )
    )

    confidence = getattr(
        importance_answer,
        "confidence",
        None,
    )

    if isinstance(confidence, (int, float)):
        print(
            f"  confidence: {confidence:.2f}"
        )

    print(
        f"  discrete: {result['importance']}"
    )
    print()
    print(
        "Final relevance score: "
        f"{result['relevance_score']}"
    )

    why = result["why_interesting"]

    if why:
        print(f"Why: {why}")
    else:
        print("Why: (none)")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Classify one article with Jev without "
            "saving the result."
        )
    )
    source = parser.add_mutually_exclusive_group(
        required=True
    )
    source.add_argument("--url")
    source.add_argument(
        "--article-id",
        type=int,
    )

    args = parser.parse_args(argv)

    try:
        article = _resolve_article(args)
        profile = _load_profile()
        content = _article_content(article)

        print(
            f"Extracted: {len(content)} characters"
        )

        result = evaluate_article(
            title=article["title"],
            content=content,
            profile=profile,
        )
    except Exception as error:
        print(
            "Debug classification failed: "
            + _sanitize_error(error),
            file=sys.stderr,
        )
        return 1

    _print_debug(
        article=article,
        result=result,
        profile=profile,
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
