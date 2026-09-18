import yaml

from app.classifier import classify_article
from app.content import fetch_article_content
from app.db import (
    record_processing_error,
    save_classification,
)
from app.scoring import calculate_relevance_score


MAX_PROCESSING_ATTEMPTS = 3
MIN_FEED_EXCERPT_LENGTH = 200

MAX_CLASSIFICATION_CONTENT_LENGTH = 25000
CLASSIFICATION_CONTENT_HEAD_LENGTH = 20000
CLASSIFICATION_CONTENT_TAIL_LENGTH = 5000


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


def _prepare_classification_content(
    content: str,
) -> str:
    if (
        len(content)
        <= MAX_CLASSIFICATION_CONTENT_LENGTH
    ):
        return content

    head = content[
        :CLASSIFICATION_CONTENT_HEAD_LENGTH
    ]

    tail = content[
        -CLASSIFICATION_CONTENT_TAIL_LENGTH:
    ]

    return (
        head
        + "\n\n"
        + "[... article content omitted ...]"
        + "\n\n"
        + tail
    )


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


def _process_article(
    article: dict,
    profile: dict,
) -> bool:
    print()

    print(
        f"Processing: "
        f"[{article['source']}] "
        f"{article['title']}"
    )

    try:
        content = fetch_article_content(
            article["url"]
        )

        if content:
            print(
                f"Extracted: "
                f"{len(content)} characters"
            )

        else:
            content = _get_fallback_content(
                article
            )

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

                return False

        classification_content = (
            _prepare_classification_content(
                content
            )
        )

        if (
            len(classification_content)
            < len(content)
        ):
            print(
                "Classification content truncated: "
                f"{len(content)} -> "
                f"{len(classification_content)} characters"
            )

        classification = classify_article(
            title=article["title"],
            content=classification_content,
            profile=profile,
        )

        relevance_score = calculate_relevance_score(
            feature_strengths=(
                classification[
                    "feature_strengths"
                ]
            ),
            profile=profile,
            importance=(
                classification[
                    "importance"
                ]
            ),
        )

        result = {
            "relevance_score":
                relevance_score,

            "why_interesting":
                classification[
                    "why_interesting"
                ],

            "topics":
                classification[
                    "topics"
                ],
        }

        save_classification(
            article_id=article["id"],
            result=result,
        )

        print(
            f"Score: "
            f"{relevance_score}"
        )

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
                classification[
                    "feature_strengths"
                ],
                profile,
            )
        )

        return True

    except Exception as error:
        print(
            f"Processing failed: "
            f"{type(error).__name__}: "
            f"{error}"
        )

        _record_failure(
            article_id=article["id"],
            error=(
                f"{type(error).__name__}: "
                f"{error}"
            ),
        )

        return False


def process_articles(articles) -> dict:
    if not articles:
        return {
            "processed": 0,
            "failed": 0,
        }

    with open(
        "config/interests.yaml"
    ) as file:
        profile = yaml.safe_load(file)

    processed = 0
    failed = 0

    for article in articles:
        succeeded = _process_article(
            article=article,
            profile=profile,
        )

        if succeeded:
            processed += 1
        else:
            failed += 1

    return {
        "processed": processed,
        "failed": failed,
    }
