# Tech Digest

A self-hosted personal technology news filter powered by local AI.

Tech Digest collects articles from RSS and Atom feeds, analyzes them locally using an Ollama model, and assigns a personalized relevance score based on my interests.

The goal is not to summarize the internet.

The goal is to answer a much simpler question:

> Which links are actually worth opening?

## Goals

Tech Digest is designed to:

- collect technology articles automatically;
- avoid showing the same article twice;
- extract article content only temporarily;
- analyze articles locally using Ollama;
- rank articles based on a personal interest profile;
- eventually deliver a small daily digest through Telegram and/or email.

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
    v
Feed Collector
    |
    v
SQLite
    |
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
```

The extracted article text exists only during processing and is discarded afterward.

## Current Features

- RSS and Atom feed collection
- YAML-based source configuration
- SQLite persistence
- URL-based deduplication
- Dockerized collector
- Scheduled collection through cron
- Article extraction using HTTPX and Trafilatura
- Local classification using Ollama
- Structured model output using JSON Schema
- Deterministic relevance scoring
- Personalized interest profile
- Processing logs

## Project Structure

```text
tech-digest/
├── app/
│   ├── __init__.py
│   ├── main.py
│   ├── rss.py
│   ├── db.py
│   ├── content.py
│   ├── classifier.py
│   ├── processor.py
│   └── scoring.py
│
├── config/
│   ├── sources.yaml
│   └── interests.yaml
│
├── data/
│   └── digest.db
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

## Running the Collector

Create and activate a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Install dependencies:

```bash
python -m pip install -r requirements.txt
```

Run the feed collector:

```bash
python -m app.main
```

## Running with Docker

Build:

```bash
docker compose build
```

Run:

```bash
docker compose run --rm digest
```

The SQLite database is persisted outside the container through the `data/` directory.

## Article Processing

Articles can currently be processed manually with:

```bash
python -c "from app.processor import process_articles; process_articles()"
```

The processor:

1. selects unprocessed articles;
2. downloads the page;
3. extracts readable content;
4. sends the temporary text to the local Ollama model;
5. obtains a structured feature vector;
6. calculates a deterministic relevance score;
7. stores only the derived result.

Automatic article processing is planned as the next stage.

## Documentation

More details:

- [Architecture](docs/architecture.md)
- [Relevance Scoring](docs/relevance-scoring.md)
- [Operations](docs/operations.md)
- [Roadmap](docs/roadmap.md)

## Status

Tech Digest is currently under active development.

The collection and relevance-analysis pipeline is functional.

The next major milestone is automatic processing of newly collected articles followed by digest generation and Telegram delivery.
