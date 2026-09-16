# Relevance Scoring

## Why Scoring Is Split in Two

An early version asked the language model to directly return a score from 0 to 100.

This worked, but scores were inconsistent.

The model could understand the article correctly while assigning an unexpectedly low or high score.

The current design separates two different questions.

### Question 1

What is this article about?

Answered by the LLM.

### Question 2

How important are those subjects to this reader?

Answered deterministically by Python.

```text
Article
   |
   v
LLM Feature Extraction
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
  description: >
    Description used by the classifier.
  weight: 50
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

For every article, the model evaluates every feature.

Possible values:

```text
0 = no meaningful connection
1 = related or secondary
2 = directly relevant
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

The classifier is instructed to describe the article factually rather than trying to maximize its relevance.

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

The strongest positive matches contribute the most.

Additional matches have diminishing influence.

This prevents an article from receiving an artificially high score simply because the model marked many loosely related features.

Importance provides a small adjustment after feature scoring.

Negative features apply a capped penalty.

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

The future digest builder will likely use a minimum score plus a maximum number of articles rather than trying to fill a fixed quota.

## Important Design Rule

A high score should not mean:

> This article is objectively good.

It means:

> This article appears likely to be worth this specific reader's time.

The profile can therefore change without changing how article content is extracted.

## Model Responsibility

The model should determine facts such as:

```text
Is this about Apple Silicon?

Is AI being materially used to build software?

Is security a major part of the article?

Is this primarily a hardware project?
```

The model should not decide:

```text
How much does João care about Apple Silicon?

How many points should security add?

Should this article appear in today's digest?
```

Those decisions belong to deterministic application logic.
