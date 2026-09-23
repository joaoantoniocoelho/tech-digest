# Tech Digest

A daily technology digest powered by AI classification, deterministic ranking, and semantic deduplication.

Tech Digest collects articles from selected technology sources, uses [TypeSafe Jev](https://typesafe.ai) for structured classification, ranks them against an explicit interest profile, removes repeated coverage of the same stories, and delivers a concise daily edition by email.

The goal is not to summarize the internet.

The goal is to answer a much simpler question:

> Which links are actually worth opening?

Public site: [digest.joaoac.com](https://digest.joaoac.com)

## Goals

Tech Digest is designed to:

- collect technology articles automatically throughout the day;
- classify recent articles using structured model decisions;
- rank them using deterministic scoring;
- make major developments difficult to miss;
- collapse repeated coverage of the same underlying story;
- avoid republishing or permanently storing article content;
- deliver a small daily digest by email.

The final digest is intentionally lightweight.

Each recommended article contains roughly:

```text
Article title

Why: AI agents · Developer tools · AI research

Source
Link
```

The original article remains the destination.

## Principles

### AI where it helps, deterministic logic where it matters

Jev is used for decisions that benefit from semantic understanding, such as:

- identifying which interests are present in an article;
- evaluating article importance;
- determining whether two articles cover the same underlying story.

The final relevance score is not generated directly by the model.

Instead, structured classification outputs are passed to deterministic Python scoring logic.

This keeps ranking inspectable, reproducible, and easy to tune.

### Do not republish articles

Full article text is used temporarily during classification and is not persisted in the database.

Persisted article data contains metadata and derived information such as:

- relevance score;
- topics;
- compact explanations of why the article is relevant;
- processing and delivery state.

Article content remains at the original publisher.

### Filter, do not summarize

Tech Digest is primarily a reading filter.

It is designed to reduce information overload instead of creating another large body of generated text.

The output should make it easier to decide what deserves attention, not replace the original article.

### Explicit editorial behavior

The system has an explicit interest profile rather than asking a model to decide vaguely whether something is "interesting."

Articles are classified against features such as:

- major AI model developments;
- AI agents;
- software engineering;
- developer tools;
- security;
- engineering culture;
- technology business strategy;
- AI research;
- infrastructure;
- privacy.

Feature definitions, weights, inclusion rules, exclusions, and selected score floors are stored in configuration and can be inspected directly.

### One digest, one audience

Tech Digest currently produces one shared daily edition.

Subscribers do not receive individually personalized rankings.

The editorial profile belongs to the digest itself, and every active subscriber receives the same final selection.

## Architecture

```text
Selected sources
      |
      |  periodic collection
      v
Feed Collector
      |
      v
SQLite metadata
      |
      |  daily processing window
      v
Article URL
      |
      v
HTTPX + Trafilatura
      |
      |  RSS excerpt fallback when needed
      v
Temporary article text
      |
      v
TypeSafe Jev
      |
      v
Structured feature scores
      |
      v
Deterministic Python scoring
      |
      v
Ranked candidates
      |
      v
Jev semantic deduplication
      |
      v
Top 8
      |
      v
HTML email
      |
      v
Active subscribers
```

Collection, classification, ranking, deduplication, and delivery are separate responsibilities.

The collector stores article metadata and RSS excerpts. It does not classify articles.

The daily processor selects eligible unprocessed articles from the configured publication window and classifies them sequentially.

Article text exists only during processing and is discarded afterward.

The digest builder ranks eligible articles, considers a larger candidate pool, removes repeated coverage of the same stories, and fills the final edition with up to eight articles.

## Classification

Each article is evaluated using TypeSafe Jev `system_one`.

Jev produces typed scores for the configured interests and for overall article importance.

Feature strengths are discretized into:

```text
0 = does not meaningfully apply
1 = explicitly present but secondary
2 = central to the article
```

Importance is classified separately.

The classifier is intentionally conservative:

- features require direct evidence;
- adjacent topics should not activate a feature;
- uncertainty favors the lower strength;
- article importance is independent from the interest profile.

The result is a structured feature vector rather than free-form model-generated ranking.

## Relevance Scoring

Relevance is calculated deterministically in Python.

The scoring layer combines:

- configured feature weights;
- feature strengths;
- diminishing contributions from additional matches;
- article importance;
- negative-interest penalties;
- explicit score floors for selected high-priority direct matches.

A direct match on a high-priority feature can define a minimum score without changing the classifier itself.

This is used for editorial rules such as making genuinely major AI model releases very likely to reach the final digest.

See [Relevance Scoring](docs/relevance-scoring.md) for the complete scoring model.

## Semantic Deduplication

Ranking and deduplication are separate stages.

The system first builds a ranked candidate pool.

Jev then compares candidate stories semantically to determine whether multiple links are primarily about the same underlying event, release, incident, or development.

Python keeps the preferred representative deterministically and continues filling the digest until it reaches the configured maximum.

This prevents repeated coverage from consuming multiple slots while still allowing distinct analysis or follow-up pieces to compete independently.

## Content Acquisition

The preferred path is:

```text
Article URL
→ HTTPX
→ Trafilatura
→ article text
```

If direct extraction fails, Tech Digest can use a sufficiently informative RSS excerpt.

If neither source is usable, the article is retried before falling back to conservative metadata-only classification using its title and URL.

This keeps the pipeline resilient without requiring browser automation or storing publisher content.

## Sources

Sources are configured in `config/sources.yaml`.

The current mix includes:

- Hacker News;
- engineering and technology newsletters;
- selected technical blogs;
- technology publications;
- official RSS feeds such as OpenAI's news feed.

RSS sources may optionally limit how many entries are imported from unusually large historical feeds.

URL-based deduplication prevents the same feed item from being stored twice.

Semantic story deduplication happens later, when the final digest is assembled.

## Subscribers

The public product has intentionally simple subscription behavior.

```text
email
→ subscribe
→ active subscriber
→ receives the next edition
```

There are no user accounts, passwords, dashboards, or per-user ranking profiles.

Subscribers can unsubscribe through a unique tokenized link included in each email.

Repeated subscriptions are idempotent, and a previously unsubscribed address can be reactivated by subscribing again.

## Delivery

Tech Digest generates one final edition and sends it to all active subscribers through Resend.

Each recipient gets an individual message so unsubscribe links remain private.

Delivery failures are isolated per recipient and do not prevent the remaining subscriber list from receiving the edition.

The final email contains:

- the article title;
- source and publication date;
- compact `Why` labels;
- a direct link to the original article.

## Configuration

Set production secrets and configuration through environment variables.

See `.env.example` and [Operations](docs/operations.md) for the current list.

Important values include:

- `TYPESAFE_API_KEY` — TypeSafe/Jev access;
- optional `TYPESAFE_MODEL` — Jev model selection;
- `RESEND_API_KEY` — email delivery;
- optional `RESEND_FROM` — sender identity;
- `PUBLIC_BASE_URL` — public URL used for unsubscribe links;
- optional `DIGEST_DB_PATH` — SQLite database path;
- optional job/API configuration used by the production deployment.

Never commit real secrets.

## Current Features

- RSS and Atom feed collection
- YAML-based source configuration
- configurable per-source entry limits
- SQLite persistence
- URL-based article deduplication
- daily publication-window processing
- article extraction using HTTPX and Trafilatura
- RSS excerpt fallback
- conservative metadata-only fallback
- TypeSafe Jev classification with typed `Score` outputs
- deterministic relevance scoring
- explicit editorial score floors
- configurable interest profile
- semantic story deduplication with Jev
- ranked top-8 daily digest
- HTML email delivery through Resend
- public subscribe/unsubscribe flow
- processing and delivery logs
- Docker-based local development
- production deployment support for Railway

## Project Structure

```text
tech-digest/
├── app/
│   ├── __init__.py
│   ├── main.py
│   ├── process_daily.py
│   ├── rss.py
│   ├── db.py
│   ├── content.py
│   ├── classifier.py
│   ├── debug_classification.py
│   ├── processor.py
│   ├── scoring.py
│   ├── digest.py
│   ├── send_digest.py
│   └── email.py
│
├── config/
│   ├── sources.yaml
│   ├── interests.yaml
│   └── digest.yaml
│
├── data/
├── tests/
├── logs/
├── docs/
│   ├── architecture.md
│   ├── relevance-scoring.md
│   ├── operations.md
│   └── roadmap.md
│
├── Dockerfile
├── compose.yaml
├── railway.toml
├── requirements.txt
└── README.md
```

## Daily Lifecycle

```text
periodic collection
        ↓
SQLite metadata
        ↓
daily classification of recent articles
        ↓
deterministic ranking
        ↓
semantic deduplication
        ↓
top 8
        ↓
email delivery
```

The daily processor selects articles primarily by publication time (`published_at`), falling back to `discovered_at` when no usable publication timestamp exists.

This prevents the first import of a feed from treating historical entries as current news.

## Running Locally

Create and activate a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Install dependencies:

```bash
python -m pip install -r requirements.txt
```

Load the environment variables used by the project:

```bash
set -a && source .env && set +a
```

Collect feeds:

```bash
python -m app.main
```

Classify eligible articles:

```bash
python -m app.process_daily
```

Preview the digest:

```bash
python -m app.digest
```

Debug one article without persisting a classification:

```bash
python -m app.debug_classification --url "https://example.com/article"
python -m app.debug_classification --article-id 123
```

Send the digest:

```bash
python -m app.send_digest
```

## Docker Development

Docker Compose can be used for local development and end-to-end testing.

Build and start the application:

```bash
docker compose up --build
```

One-shot pipeline commands can also be run through Compose:

```bash
docker compose run --rm digest
docker compose run --rm digest python -m app.process_daily
docker compose run --rm digest python -m app.digest
docker compose run --rm digest python -m app.send_digest
```

Use a development database path when testing destructive flows so production data is never affected.

## Production

The production deployment is designed around a single application instance with persistent SQLite storage.

The production environment is responsible for:

- exposing the subscription API;
- running scheduled collection and daily processing jobs;
- constructing the final digest;
- sending email through Resend;
- keeping the SQLite database on persistent storage.

A single-writer deployment keeps the SQLite architecture intentionally simple.

See [Operations](docs/operations.md) for deployment details, environment variables, health checks, manual job execution, and persistence verification.

## Testing

Run the full test suite with:

```bash
python -m unittest discover -s tests -v
```

Check whitespace errors with:

```bash
git diff --check
```

The test suite covers the main pipeline behavior, including:

- publication-window selection;
- processing retries;
- RSS excerpt fallback;
- metadata-only fallback;
- deterministic scoring;
- digest selection;
- semantic deduplication;
- subscriber lifecycle;
- delivery behavior.

## Documentation

More details:

- [Architecture](docs/architecture.md)
- [Relevance Scoring](docs/relevance-scoring.md)
- [Operations](docs/operations.md)
- [Roadmap](docs/roadmap.md)

## Status

Tech Digest is in production.

The current pipeline performs periodic collection, daily classification, deterministic ranking, semantic deduplication, final digest selection, and email delivery.

The public version is intentionally small: one editorial profile, one daily edition, and a simple subscribe/unsubscribe flow.
