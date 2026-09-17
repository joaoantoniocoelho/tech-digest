import yaml

from app.classifier import classify_article
from app.content import fetch_article_content
from app.db import (
    get_unprocessed_articles,
    record_processing_error,
    save_classification,
)
from app.scoring import calculate_relevance_score


MAX_PROCESSING_ATTEMPTS = 3
MIN_FEED_EXCERPT_LENGTH = 200


def _describe_features(
    feature_strengths: dict,
    profile: dict,
) -> str:
    matches = []

    for feature_id, strength in feature_strengths.items():
        if strength == 0:
            continue

        strength_label = (
            "direct"
            if strength == 2
            else "related"
        )

        matches.append(
            f"{feature_id} [{strength_label}]"
        )

    if not matches:
        return "None"

    return ", ".join(matches)


def _get_fallback_content(article):
    feed_excerpt = article["feed_excerpt"]

    if not feed_excerpt:
        return None

    feed_excerpt = feed_excerpt.strip()

    if len(feed_excerpt) < MIN_FEED_EXCERPT_LENGTH:
        return None

    return feed_excerpt


def _record_failure(
    article_id: int,
    error: str,
):
    status = record_processing_error(
        article_id=article_id,
        error=error,
        fail_after_attempts=MAX_PROCESSING_ATTEMPTS,
    )

    attempts = status["processing_attempts"]

    if status["failed_at"]:
        print(
            f"Marked as failed after "
            f"{attempts} attempts."
        )
    else:
        print(
            f"Attempt {attempts}/"
            f"{MAX_PROCESSING_ATTEMPTS}."
        )


def process_articles():
    with open("config/interests.yaml") as file:
        profile = yaml.safe_load(file)

    articles = get_unprocessed_articles(limit=10)

    print()
    print(f"Processing {len(articles)} articles...")

    for article in articles:
        print()
        print(f"Processing: {article['title']}")

        try:
            content = fetch_article_content(
                article["url"]
            )

            if content:
                print(
                    f"Extracted: {len(content)} characters"
                )

            else:
                content = _get_fallback_content(article)

                if content:
                    print(
                        "Article extraction failed; "
                        f"using RSS excerpt "
                        f"({len(content)} characters)"
                    )

                else:
                    print(
                        "Content unavailable. "
                        "No usable RSS excerpt."
                    )

                    _record_failure(
                        article_id=article["id"],
                        error=(
                            "Could not extract article content "
                            "and no usable RSS excerpt was available"
                        ),
                    )

                    continue

            classification = classify_article(
                title=article["title"],
                content=content,
                profile=profile,
            )

            relevance_score = calculate_relevance_score(
                feature_strengths=(
                    classification["feature_strengths"]
                ),
                profile=profile,
                importance=classification["importance"],
            )

            result = {
                "relevance_score": relevance_score,
                "why_interesting": (
                    classification["why_interesting"]
                ),
                "topics": classification["topics"],
            }

            save_classification(
                article_id=article["id"],
                result=result,
            )

            print(f"Score: {relevance_score}")
            print(
                f"Importance: "
                f"{classification['importance']}"
            )
            print(
                f"Why: "
                f"{classification['why_interesting']}"
            )

            print(
                "Topics: "
                + ", ".join(
                    classification["topics"]
                )
            )

            print(
                "Features: "
                + _describe_features(
                    classification["feature_strengths"],
                    profile,
                )
            )

        except Exception as error:
            print(
                f"Processing failed: "
                f"{type(error).__name__}: {error}"
            )

            _record_failure(
                article_id=article["id"],
                error=(
                    f"{type(error).__name__}: {error}"
                ),
            )
