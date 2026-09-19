# Relevance Scoring

## Why Scoring Is Split in Two

An early version asked the language model to directly return a score from 0 to 100.

This worked, but scores were inconsistent.

The model could understand the article correctly while assigning an unexpectedly low or high score.

The current design separates two different questions.

### Question 1

What is this article about?

Answered by TypeSafe Jev (typed Score outputs).

### Question 2

How important are those subjects to this reader?

Answered deterministically by Python.

```text
Article
   |
   v
Jev Feature Extraction
   |
   v
Feature Vector
   |
   v
Python Weights
   |
   v
Relevance Score
```

## Feature Profile

Features are defined in:

```text
config/interests.yaml
```

Each feature contains:

```yaml
example_feature:
  label: Readable name
  weight: 50
  description: >
    What the feature means.
  include_when: >
    When to activate it.
  exclude_when: >
    When it must stay at 0.
```

Positive weights represent areas of interest.

Negative weights represent topics that should generally decrease relevance.

Examples of positive areas include:

- AI and major model developments;
- AI agents;
- AI-assisted software engineering;
- software engineering;
- developer tooling;
- security;
- Apple technologies;
- self-hosting and local AI;
- startups and SaaS.

Examples of lower-priority areas include:

- cryptocurrency and Web3;
- generic gadget reviews;
- entertainment;
- hardware projects with little software relevance.

## Feature Strength

For every article, Jev evaluates every feature.

Possible values after discretization:

```text
0 = does not meaningfully apply (default)
1 = explicitly present, but secondary
2 = central to the article
```

For example:

```json
{
  "ai_assisted_software_engineering": 2,
  "apple_ecosystem": 2,
  "open_source": 2,
  "hardware_project": 1
}
```

Jev returns a continuous score per feature. Python maps it to 0/1/2:

```text
< 0.75  → 0
< 1.50  → 1
otherwise 2
```

Importance uses a separate 0–3 scale (`< 0.50` / `< 1.50` / `< 2.50`).

The classifier is instructed to describe the article factually rather than trying to maximize its relevance.

`why_interesting` and `topics` are not free-form model text. Python takes at most three **positive** features, ordered by contribution to the score, and joins their labels with ` · `. Negative features never appear there.

## Importance

The classifier separately returns an article-level importance value:

```text
0 = routine or shallow
1 = normally useful or interesting
2 = notably interesting or insightful
3 = major or exceptional
```

Importance is intentionally independent of personal relevance.

A major event in an unrelated field may have:

```text
importance = 3
```

while still receiving a low personal relevance score.

Likewise, a very relevant but routine article may have:

```text
importance = 1
```

## Weighted Scoring

Feature weights are combined with strength multipliers.

Current strength multipliers:

```text
0 → 0.00
1 → 0.45
2 → 1.00
```

The strongest positive match contributes at full weight. Extra matches fall off quickly:

```text
1st match → 100%
2nd match → 28%
3rd match → 10%
4th match → 4%
5th match → 2%
further   → 1%
```

This keeps a stacked AI roundup highly ranked without pinning the ceiling at 100.

Importance is a small adjustment after feature scoring (`-5 / 0 / +5 / +8`).

Negative features apply a capped penalty (at most 25 points).

The final score is clamped to:

```text
0–100
```

## Interpretation

The score is intended to mean roughly:

```text
90–100  exceptional match
80–89   highly relevant
60–79   likely worth opening
40–59   potentially interesting
20–39   weak relevance
0–19    usually ignore
```

These ranges are guidelines, not hard editorial rules.

The digest already uses a minimum score (`minimum_score: 60`) plus a maximum number of articles (`maximum_articles: 8`). It does not fill a quota with weaker items.

## Important Design Rule

A high score should not mean:

> This article is objectively good.

It means:

> This article appears likely to be worth this specific reader's time.

The profile can therefore change without changing how article content is extracted.

## Model Responsibility

Jev should determine facts such as:

```text
Is this about Apple Silicon?

Is AI being materially used to build software?

Is security a major part of the article?

Is this primarily a hardware project?
```

Jev should not decide:

```text
How much does João care about Apple Silicon?

How many points should security add?

Should this article appear in today's digest?
```

Those decisions belong to deterministic application logic.
