# Tech Digest

A self-hosted personal technology news filter powered by local AI.

Tech Digest collects articles from RSS and Atom feeds, analyzes them locally using an Ollama model, and assigns a personalized relevance score based on my interests.

The goal is not to summarize the internet.

The goal is to answer a much simpler question:

> Which links are actually worth opening?

## Goals

Tech Digest is designed to:

- collect technology articles automatically during the day;
- classify every recent article once per day;
- avoid showing the same article twice;
- extract article content only temporarily;
- analyze articles locally using Ollama;
- rank articles based on a personal interest profile;
- deliver a small daily digest through Telegram.

The final digest is intentionally lightweight.

Each recommended article should contain roughly:

```text
Article title

Why it may be worth reading.

Source
Link
```

The original article remains the destination.

## Principles

### Local-first

Article analysis runs on my home server using Ollama.

No external LLM API is required.

### Do not republish articles

Full article text is used temporarily for classification and is not stored in the database.

The persisted output contains metadata and derived information such as:

- relevance score;
- topics;
- why the article may be interesting.

### Filter, do not summarize

The system is primarily a reading filter.

It should help reduce information overload instead of creating another large body of generated text.

### Explainable ranking

The language model does not directly choose the final relevance score.

Instead:

1. the model extracts a feature vector describing the article;
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
Ollama / Qwen
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
Telegram
```

Collection and classification are separate jobs.

The collector only stores article metadata. It does not call Ollama.

Once per day, the processor classifies every unprocessed article in the recent publication window, sequentially.

The extracted article text exists only during processing and is discarded afterward.

## Current Features

- RSS and Atom feed collection
- YAML-based source configuration
- SQLite persistence
- URL-based deduplication
- Dockerized collector and daily jobs
- Scheduled collection through cron
- Daily classification of all recent articles
- Article extraction using HTTPX and Trafilatura
- Local classification using Ollama
- Structured model output using JSON Schema
- Deterministic relevance scoring
- Personalized interest profile
- Daily ranked digest
- Telegram delivery
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
│   ├── processor.py
│   ├── scoring.py
│   ├── digest.py
│   ├── send_digest.py
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
Telegram
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

Send the digest through Telegram:

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

## Documentation

More details:

- [Architecture](docs/architecture.md)
- [Relevance Scoring](docs/relevance-scoring.md)
- [Operations](docs/operations.md)
- [Roadmap](docs/roadmap.md)

## Status

Tech Digest is currently under active development.

The daily newspaper pipeline is functional: periodic collection, daily local classification, ranked digest, and Telegram delivery.
