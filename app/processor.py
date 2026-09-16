import yaml

from app.classifier import classify_article
from app.content import fetch_article_content
from app.db import get_unprocessed_articles, save_classification
from app.scoring import calculate_relevance_score


def _describe_features(
    feature_strengths: dict,
    profile: dict,
) -> str:
    matches = []

    for feature_id, strength in feature_strengths.items():
        if strength == 0:
            continue

        feature = profile["features"][feature_id]

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
            content = fetch_article_content(article["url"])

            if not content:
                print("Could not extract content")
                continue

            print(f"Extracted: {len(content)} characters")

            classification = classify_article(
                title=article["title"],
                content=content,
                profile=profile,
            )

            relevance_score = calculate_relevance_score(
                feature_strengths=classification["feature_strengths"],
                profile=profile,
                importance=classification["importance"],
            )

            result = {
                "relevance_score": relevance_score,
                "why_interesting": classification["why_interesting"],
                "topics": classification["topics"],
            }

            save_classification(
                article_id=article["id"],
                result=result,
            )

            print(f"Score: {relevance_score}")
            print(f"Importance: {classification['importance']}")
            print(f"Why: {classification['why_interesting']}")

            print(
                "Topics: "
                + ", ".join(classification["topics"])
            )

            print(
                "Features: "
                + _describe_features(
                    classification["feature_strengths"],
                    profile,
                )
            )

        except Exception as error:
            print(f"Failed: {error}")
