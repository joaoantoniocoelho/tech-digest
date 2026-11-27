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

Known limit: this makes the score mostly a measure of topic. Two central features already add up to about 70, while quality moves it by 13 points at most, so scores bunch between 61 and 85. The classifier's answers are stored per article (`importance`, `feature_strengths`) so a formula that gives importance real weight can be tested offline with `python -m app.rescore` before it replaces this one (see [operations](operations.md#rescoring-stored-answers)).

The Why line lists at most two features, the strongest central ones (strength 2) by score contribution. Secondary ones appear only when no feature is central: a passing mention shown next to a link reads as a wrong topic. The classifier marks most matching features as central (articles at 70 or more had 4 to 9), so the strength filter alone still listed three. `topics` holds the same labels, because the site lists them next to each link.

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

The digest uses a minimum score (`minimum_score: 70`) plus a maximum number of articles (`maximum_articles: 5`). It does not fill the edition with weaker items, with one exception that keeps the digest daily: when fewer than `minimum_articles` (3) pass the cutoff, the edition is topped up to 3 with the best candidates at or above `fallback_minimum_score` (60).

## Edition Diversity

The relevance score judges one article at a time. It cannot tell that an edition already holds three AI stories.

Each positive feature therefore has an editorial `group` (`ai`, `engineering`, `security`, `business`, `systems`). An article's group is the group of its strongest topic.

Selection is greedy. At each step the digest picks the candidate with the highest adjusted score:

```text
adjusted = relevance_score - penalty × extra items already in its group
```

`free_per_group` in `config/digest.yaml` sets how many items a group gets before the penalty starts (default 1; `ai` and `engineering` get 2). With `penalty: 8`, a third AI article at 85 ranks as 77, and a fourth ranks as 69.

This is a soft preference, not a quota:

- only candidates at or above `minimum_score` are considered, so diversity never brings in a weak article;
- a much stronger article still wins over a weak one from an under-represented group;
- if a day only has AI candidates, the edition is still all AI.

Duplicate detection (Jev) runs on each pick as before. It also drops an article that covers the same launch from a very similar angle. Each pick is compared with the articles already chosen for the edition and with the stories delivered in the last `dedup_lookback_days` (3) days, so a story does not come back the next morning from another publisher.

## Editorial Rules

Fixed rules in `config/digest.yaml` run before ranking decides anything. They are deterministic and never call the model (`app/filters.py`):

- `source_limits`: at most this many articles per source in one edition (Hacker News 3, Lobsters 2). Aggregators send dozens of candidates a day and would otherwise fill every slot. A capped candidate is skipped and the next one is tried; sources without an entry are not capped.
- `aggregators`: a combined cap for the aggregators (Hacker News and Lobsters, 3 together). Each stays under its own limit, but together they could still fill a five-link edition, as on 2026-11-27 (3 + 2). The remaining slots go to primary sources; on a day with none above the cutoff, the edition is shorter.
- `title_filters.blocked_phrases`: job posts (`is hiring`).
- `title_filters.blocked_words`: profanity, matched at the start of a word.
- `title_filters.block_dated_titles`: titles ending in a past year, such as `(2004)`.
- `title_filters.max_launch_posts`: at most one `Show HN:` / `Launch HN:` post per edition.

Sources that publish a few times a week have a longer window (`lookback_hours: 72` per source in `config/sources.yaml`). With the 24-hour window, a blog post was eligible for one morning at most, and most never landed inside it. A post that stays unpicked can come back on the next two mornings; cross-day deduplication only covers delivered stories.

Blocked titles are also dropped at collection, so they are never classified. An edition can therefore be shorter than `maximum_articles` even when more candidates pass `minimum_score`.

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
