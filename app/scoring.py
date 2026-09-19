STRENGTH_MULTIPLIERS = {
    0: 0.0,
    1: 0.45,
    2: 1.0,
}


IMPORTANCE_ADJUSTMENTS = {
    0: -5,
    1: 0,
    2: 5,
    3: 8,
}


POSITIVE_MATCH_MULTIPLIERS = [
    1.0,
    0.28,
    0.10,
    0.04,
    0.02,
]

EXTRA_MATCH_MULTIPLIER = 0.01


def calculate_relevance_score(
    feature_strengths: dict,
    profile: dict,
    importance: int,
) -> int:
    positive_contributions = []
    negative_penalty = 0.0

    for feature_id, strength in feature_strengths.items():
        feature = profile["features"][feature_id]
        weight = feature["weight"]

        contribution = abs(weight) * STRENGTH_MULTIPLIERS[strength]

        if weight > 0 and contribution > 0:
            positive_contributions.append(contribution)

        elif weight < 0 and contribution > 0:
            negative_penalty += contribution

    positive_contributions.sort(reverse=True)

    score = 0.0

    for index, contribution in enumerate(positive_contributions):
        if index < len(POSITIVE_MATCH_MULTIPLIERS):
            multiplier = POSITIVE_MATCH_MULTIPLIERS[index]
        else:
            multiplier = EXTRA_MATCH_MULTIPLIER

        score += contribution * multiplier

    score += IMPORTANCE_ADJUSTMENTS[importance]

    score -= min(negative_penalty, 25)

    return max(
        0,
        min(
            100,
            round(score),
        ),
    )
