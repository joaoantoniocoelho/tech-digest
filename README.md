# Tech Digest

A self-hosted personal technology news filter with cloud-assisted classification.

Tech Digest collects articles from RSS and Atom feeds, sends article text to [TypeSafe Jev](https://typesafe.ai) for structured classification, and assigns a personalized relevance score based on my interests.

The goal is not to summarize the internet.

The goal is to answer a much simpler question:

> Which links are actually worth opening?

## Goals

Tech Digest is designed to:

- collect technology articles automatically during the day;
- classify every recent article once per day;
- avoid showing the same article twice;
- extract article content only temporarily;
- classify articles with TypeSafe Jev (typed Score decisions);
- rank articles based on a personal interest profile;
- deliver a small daily digest by email.

The final digest is intentionally lightweight.

Each recommended article should contain roughly:

```text
Article title

Why: AI agents · Developer tools · AI research

Source
Link
```

The original article remains the destination.

## Principles

### Self-hosted pipeline, cloud classifier

Collection, storage, scoring, and delivery run on my home server.

Article text is sent to the TypeSafe API for classification. Titles and short RSS excerpts are sent for digest duplicate checks. Full article content is not persisted in the local SQLite database. This project does not make claims about TypeSafe's own retention.

Email delivery uses the Resend API and the verified domain `digest.joaoac.com`.

### Do not republish articles

Full article text is used temporarily for classification and is not stored in the database.

The persisted output contains metadata and derived information such as:

- relevance score;
- topics;
- why the article may be interesting (compact feature labels).

### Filter, do not summarize

The system is primarily a reading filter.

It should help reduce information overload instead of creating another large body of generated text.

### Explainable ranking

The classifier does not directly choose the final relevance score.

Instead:

1. Jev assigns a feature strength and importance score for the article;
2. deterministic Python code converts those features into a relevance score.

This makes ranking easier to inspect and tune.

## Current Architecture

```text
RSS / Atom
    |
    |  every 4 hours
    v
Feed Collector
    |
    v
SQLite metadata
    |
    |  once per day
    v
Article URL
    |
    v
HTTPX
    |
    v
Trafilatura
    |
    v
Temporary article text
    |
    v
TypeSafe Jev (system_one)
    |
    v
Feature Vector
    |
    v
Python Scoring
    |
    v
SQLite
    |
    |  once per day
    v
Ranked digest
    |
    v
Email
```

Collection and classification are separate jobs.

The collector only stores article metadata. It does not call the classifier.

Once per day, the processor classifies every unprocessed article in the recent publication window, sequentially.

The extracted article text exists only during processing and is discarded afterward.

## Configuration

Set these in `.env` (see [Operations](docs/operations.md)):

- `RESEND_API_KEY` and `RESEND_TO` for email delivery;
- optional `RESEND_FROM` (default `Tech Digest <digest@digest.joaoac.com>`);
- `TYPESAFE_API_KEY` (required for classification and digest duplicate checks);
- optional `TYPESAFE_MODEL` (default `jev-1.13.0`).

Docker Compose loads `.env` automatically. A local Python shell does not, so export the same variables before `process_daily`, `send_digest`, or `debug_classification`.

## Current Features

- RSS and Atom feed collection
- YAML-based source configuration
- SQLite persistence
- URL-based deduplication
- Dockerized collector and daily jobs
- Scheduled collection through cron
- Daily classification of all recent articles
- Article extraction using HTTPX and Trafilatura
- TypeSafe Jev classification (typed Score outputs)
- Debug classification without writing to SQLite
- Deterministic relevance scoring
- Personalized interest profile
- Daily ranked digest
- Jev comparison of titles and RSS excerpts to remove repeated stories across sources before filling the digest
- Email delivery through Resend
- Processing logs

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
│   ├── email.py
│   └── telegram.py
│
├── config/
│   ├── sources.yaml
│   ├── interests.yaml
│   └── digest.yaml
│
├── data/
│   └── digest.db
│
├── tests/
│
├── logs/
│
├── docs/
│   ├── architecture.md
│   ├── relevance-scoring.md
│   ├── operations.md
│   └── roadmap.md
│
├── Dockerfile
├── compose.yaml
├── requirements.txt
└── README.md
```

## Daily Lifecycle

```text
periodic lightweight collection
        ↓
SQLite metadata
        ↓
daily classification of all recent articles
        ↓
daily ranked digest
        ↓
Email
```

The daily processor selects articles by publication time (`published_at`), falling back to `discovered_at` only when no usable publication timestamp exists.

That keeps a first import of an RSS source from treating old feed entries as today's news.

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

Classification talks to the TypeSafe API (`typesafe-sdk`). Export `TYPESAFE_API_KEY` first, for example:

```bash
set -a && source .env && set +a
```

Collect feeds without classifying anything:

```bash
python -m app.main
```

Classify every eligible article from the daily window:

```bash
python -m app.process_daily
```

Preview the digest:

```bash
python -m app.digest
```

Debug one article without saving classification:

```bash
python -m app.debug_classification --url "https://example.com/article"
python -m app.debug_classification --article-id 123
```

Send the digest by email:

```bash
python -m app.send_digest
```

## Running with Docker

Build:

```bash
docker compose build
```

Collect feeds:

```bash
docker compose run --rm digest
```

Classify the daily window:

```bash
docker compose run --rm digest python -m app.process_daily
```

Preview or send the digest:

```bash
docker compose run --rm digest python -m app.digest
docker compose run --rm digest python -m app.send_digest
```

The SQLite database is persisted outside the container through the `data/` directory.

Compose reads `.env` and does not use host networking. Classification needs outbound HTTPS to `api.typesafe.ai`; there is no local Ollama sidecar.

## Documentation

More details:

- [Architecture](docs/architecture.md)
- [Relevance Scoring](docs/relevance-scoring.md)
- [Operations](docs/operations.md)
- [Roadmap](docs/roadmap.md)

## Status

Tech Digest is currently under active development.

The daily newspaper pipeline is functional: periodic collection, daily classification, ranked digest, and email delivery.
